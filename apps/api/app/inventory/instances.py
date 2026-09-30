"""Inventory Slice 2 — instance operations (docs/04-domain/inventory.md §7,
§13, §16; Issue #230).

No authorization here: the API router has already required the
Administrator (app.inventory.authorization). Every operation on an existing
instance runs on an instance row the router loaded with
`SELECT ... FOR UPDATE`, so operations on one instance are serialized.

Each operation is one transaction:

    lock instance (router) -> check transition (app.inventory.lifecycle)
    -> insert the immutable movement -> update the instance projection
    (`state`, `storage_location_id`) -> COMMIT

`state` and `storage_location_id` are never written anywhere else.

Concurrency:

- the inventory number is `max + 1` under a transaction-scoped advisory
  lock, held until commit, so two receipts can never read the same
  maximum; the unique index is the backstop;
- a receipt takes a share lock on the item row, so an item can neither be
  archived nor switch accounting mode while an instance is being received
  (both hold the item's row lock);
- every operation that places an instance into a location takes a share
  lock on that location's row, so the location cannot be archived at the
  same moment (archiving holds its row lock);
- movement `created_at` is `clock_timestamp()` taken after the locks, so
  the history of one instance is strictly chronological.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.inventory import (
    MANUFACTURER_CODE_MAX_LENGTH,
    TEXT_MAX_LENGTH,
    InventoryInstance,
    InventoryItem,
    InventoryMovement,
)
from app.inventory.lifecycle import (
    InvalidInventoryDataError,
    InvalidReversalTargetError,
    ReversalLocationNotAllowedError,
    ReversalLocationRequiredError,
    WriteOffAlreadyReversedError,
    ensure_instance_editable,
    ensure_instance_mode,
    ensure_selectable,
    format_inventory_number,
    next_instance_state,
    normalize_optional_text,
    normalize_write_off_reason,
    validate_cost_minor,
)
from app.inventory.locking import selectable_location, share_locked_item, share_locked_location
from app.inventory.service import (
    KIND_ITEM,
)
from app.inventory.vocabulary import (
    ACTIVE,
    INSTANCE_AVAILABLE,
    MOVEMENT_RECEIPT,
    MOVEMENT_REPAIR_END,
    MOVEMENT_REPAIR_START,
    MOVEMENT_TRANSFER,
    MOVEMENT_WRITE_OFF,
    MOVEMENT_WRITEOFF_REVERSAL,
)

# Key of the transaction-scoped PostgreSQL advisory lock serializing
# inventory number generation. Nothing outside this module takes it.
_INVENTORY_NUMBER_LOCK_KEY = 121_230_020

_INVENTORY_NUMBER_PREFIX_LENGTH = len("INV-")


class InventoryMovementNotFoundError(Exception):
    def __init__(self, movement_id: uuid.UUID) -> None:
        super().__init__(f"Inventory movement {movement_id} does not exist")
        self.movement_id = movement_id


def _next_inventory_number(session: Session) -> str:
    session.execute(sa.select(sa.func.pg_advisory_xact_lock(_INVENTORY_NUMBER_LOCK_KEY)))
    current = session.execute(
        sa.select(
            sa.func.max(
                sa.cast(
                    sa.func.substr(
                        InventoryInstance.inventory_number, _INVENTORY_NUMBER_PREFIX_LENGTH + 1
                    ),
                    sa.BigInteger,
                )
            )
        )
    ).scalar_one()
    return format_inventory_number((current or 0) + 1)


def _movement(
    *,
    instance: InventoryInstance,
    movement_type: str,
    created_by: uuid.UUID,
    **fields: Any,
) -> InventoryMovement:
    return InventoryMovement(
        id=uuid.uuid4(),
        item_id=instance.item_id,
        instance_id=instance.id,
        movement_type=movement_type,
        created_by=created_by,
        created_at=sa.func.clock_timestamp(),
        **fields,
    )


def _finish(session: Session, instance: InventoryInstance) -> InventoryInstance:
    session.commit()
    session.refresh(instance)
    return instance


# --- receipt -----------------------------------------------------------------------


def receive_instance(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID,
    storage_location_id: uuid.UUID,
    unit_cost_minor: int | None,
    manufacturer_barcode: str | None,
    manufacturer_serial_number: str | None,
    description: str | None,
    created_by: uuid.UUID,
) -> InventoryInstance:
    """§7.3 (Q7): creating an instance is its `receipt` into an active
    storage location; the instance starts `available`."""
    barcode = normalize_optional_text(
        manufacturer_barcode, max_length=MANUFACTURER_CODE_MAX_LENGTH, field="manufacturer_barcode"
    )
    serial = normalize_optional_text(
        manufacturer_serial_number,
        max_length=MANUFACTURER_CODE_MAX_LENGTH,
        field="manufacturer_serial_number",
    )
    text = normalize_optional_text(description, max_length=TEXT_MAX_LENGTH, field="description")
    cost = validate_cost_minor(unit_cost_minor)

    item = share_locked_item(session, club_id=club_id, item_id=item_id)
    ensure_selectable(item.status, kind=KIND_ITEM)
    ensure_instance_mode(item.accounting_mode)
    location = selectable_location(session, club_id=club_id, location_id=storage_location_id)

    instance = InventoryInstance(
        id=uuid.uuid4(),
        item_id=item.id,
        inventory_number=_next_inventory_number(session),
        manufacturer_barcode=barcode,
        manufacturer_serial_number=serial,
        description=text,
        state=INSTANCE_AVAILABLE,
        storage_location_id=location.id,
        created_by=created_by,
    )
    session.add(instance)
    session.flush()
    session.add(
        _movement(
            instance=instance,
            movement_type=MOVEMENT_RECEIPT,
            created_by=created_by,
            to_location_id=location.id,
            unit_cost_minor=cost,
        )
    )
    return _finish(session, instance)


# --- editable fields ----------------------------------------------------------------

UPDATABLE_INSTANCE_FIELDS = frozenset(
    {"manufacturer_barcode", "manufacturer_serial_number", "description"}
)


def update_instance(
    session: Session, *, instance: InventoryInstance, fields: dict[str, Any], updated_by: uuid.UUID
) -> InventoryInstance:
    """§7.5: only the manufacturer codes and the description; never for a
    written-off instance (R5). `None`/blank clears a field."""
    unknown = set(fields) - UPDATABLE_INSTANCE_FIELDS
    if unknown:  # pragma: no cover - the request schema forbids these
        raise ValueError(f"Not updatable: {sorted(unknown)}")
    ensure_instance_editable(instance.state)
    limits = {
        "manufacturer_barcode": MANUFACTURER_CODE_MAX_LENGTH,
        "manufacturer_serial_number": MANUFACTURER_CODE_MAX_LENGTH,
        "description": TEXT_MAX_LENGTH,
    }
    values = {
        name: normalize_optional_text(value, max_length=limits[name], field=name)
        for name, value in fields.items()
    }
    for name, value in values.items():
        setattr(instance, name, value)
    instance.updated_by = updated_by
    return _finish(session, instance)


# --- movements on an existing instance ---------------------------------------------


def _club_id_of(session: Session, instance: InventoryInstance) -> uuid.UUID:
    return session.execute(
        sa.select(InventoryItem.club_id).where(InventoryItem.id == instance.item_id)
    ).scalar_one()


def transfer_instance(
    session: Session,
    *,
    instance: InventoryInstance,
    to_location_id: uuid.UUID,
    comment: str | None,
    created_by: uuid.UUID,
) -> InventoryInstance:
    """§7.4 (Q12, R2): `available`/`in_repair` -> same state, another active
    location."""
    note = normalize_optional_text(comment, max_length=TEXT_MAX_LENGTH, field="comment")
    next_instance_state(instance.state, MOVEMENT_TRANSFER)
    if to_location_id == instance.storage_location_id:
        raise InvalidInventoryDataError("The instance is already in this storage location")
    target = selectable_location(
        session, club_id=_club_id_of(session, instance), location_id=to_location_id
    )
    session.add(
        _movement(
            instance=instance,
            movement_type=MOVEMENT_TRANSFER,
            created_by=created_by,
            from_location_id=instance.storage_location_id,
            to_location_id=target.id,
            comment=note,
        )
    )
    instance.storage_location_id = target.id
    instance.updated_by = created_by
    return _finish(session, instance)


def _repair(
    session: Session, *, instance: InventoryInstance, movement_type: str, created_by: uuid.UUID
) -> InventoryInstance:
    """§7.4 (R1, R3): the state changes, the location is kept — so the
    movement carries no location."""
    new_state = next_instance_state(instance.state, movement_type)
    session.add(_movement(instance=instance, movement_type=movement_type, created_by=created_by))
    instance.state = new_state
    instance.updated_by = created_by
    return _finish(session, instance)


def start_repair(
    session: Session, *, instance: InventoryInstance, created_by: uuid.UUID
) -> InventoryInstance:
    return _repair(
        session, instance=instance, movement_type=MOVEMENT_REPAIR_START, created_by=created_by
    )


def end_repair(
    session: Session, *, instance: InventoryInstance, created_by: uuid.UUID
) -> InventoryInstance:
    return _repair(
        session, instance=instance, movement_type=MOVEMENT_REPAIR_END, created_by=created_by
    )


def write_off_instance(
    session: Session, *, instance: InventoryInstance, comment: str, created_by: uuid.UUID
) -> InventoryInstance:
    """§7.4 (Q8): `available`/`in_repair` -> `written_off` with a mandatory
    reason; the former location is kept on the movement."""
    reason = normalize_write_off_reason(comment, max_length=TEXT_MAX_LENGTH)
    new_state = next_instance_state(instance.state, MOVEMENT_WRITE_OFF)
    session.add(
        _movement(
            instance=instance,
            movement_type=MOVEMENT_WRITE_OFF,
            created_by=created_by,
            from_location_id=instance.storage_location_id,
            comment=reason,
        )
    )
    instance.state = new_state
    instance.storage_location_id = None
    instance.updated_by = created_by
    return _finish(session, instance)


# --- write-off reversal --------------------------------------------------------------


def get_movement(
    session: Session, *, club_id: uuid.UUID, movement_id: uuid.UUID
) -> InventoryMovement:
    movement = session.execute(
        sa.select(InventoryMovement)
        .join(InventoryItem, InventoryItem.id == InventoryMovement.item_id)
        .where(InventoryMovement.id == movement_id, InventoryItem.club_id == club_id)
    ).scalar_one_or_none()
    if movement is None:
        raise InventoryMovementNotFoundError(movement_id)
    return movement


def reverse_write_off(
    session: Session,
    *,
    club_id: uuid.UUID,
    movement_id: uuid.UUID,
    storage_location_id: uuid.UUID | None,
    created_by: uuid.UUID,
) -> InventoryInstance:
    """§16 (E, Q9, GAP-2): a `writeoff_reversal` referencing the instance
    `write_off`, once; the instance returns to `available` in the original
    location if it is active (no other location accepted), otherwise in the
    given active location."""
    write_off = get_movement(session, club_id=club_id, movement_id=movement_id)
    if write_off.movement_type != MOVEMENT_WRITE_OFF or write_off.instance_id is None:
        raise InvalidReversalTargetError()
    instance = session.execute(
        sa.select(InventoryInstance)
        .where(InventoryInstance.id == write_off.instance_id)
        .with_for_update()
    ).scalar_one()
    already_reversed = session.execute(
        sa.select(sa.exists().where(InventoryMovement.reverses_movement_id == write_off.id))
    ).scalar_one()
    if already_reversed:
        raise WriteOffAlreadyReversedError()
    new_state = next_instance_state(instance.state, MOVEMENT_WRITEOFF_REVERSAL)

    original = (
        share_locked_location(session, club_id=club_id, location_id=write_off.from_location_id)
        if write_off.from_location_id is not None
        else None
    )
    if original is not None and original.status == ACTIVE:
        if storage_location_id is not None and storage_location_id != original.id:
            raise ReversalLocationNotAllowedError()
        target = original
    else:
        if storage_location_id is None:
            raise ReversalLocationRequiredError()
        target = selectable_location(session, club_id=club_id, location_id=storage_location_id)

    session.add(
        _movement(
            instance=instance,
            movement_type=MOVEMENT_WRITEOFF_REVERSAL,
            created_by=created_by,
            to_location_id=target.id,
            reverses_movement_id=write_off.id,
        )
    )
    instance.state = new_state
    instance.storage_location_id = target.id
    instance.updated_by = created_by
    try:
        return _finish(session, instance)
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_inventory_movements_reverses_movement_id":  # pragma: no cover
            raise WriteOffAlreadyReversedError() from exc
        raise


__all__ = [
    "InventoryMovementNotFoundError",
    "UPDATABLE_INSTANCE_FIELDS",
    "receive_instance",
    "update_instance",
    "transfer_instance",
    "start_repair",
    "end_repair",
    "write_off_instance",
    "get_movement",
    "reverse_write_off",
]
