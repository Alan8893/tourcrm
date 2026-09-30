"""Inventory Slice 3 — quantity movements (docs/04-domain/inventory.md §6,
§11, §13, §16, §17; Issue #230).

No authorization here: the API router has already required the
Administrator (app.inventory.authorization).

Stock of a quantity item is kept per storage location in the projection
`inventory_item_stocks`; the movement journal stays the source of truth.
Each operation is one transaction:

    share-lock the item and every location involved (app.inventory.locking)
    -> lock the stock rows involved (`SELECT ... FOR UPDATE`, in location-id
       order; a missing destination row is created first with
       `INSERT ... ON CONFLICT DO NOTHING`)
    -> check the rules (app.inventory.lifecycle)
    -> insert the immutable movement -> change the projection -> COMMIT

Because every operation on a stock row holds that row's lock until
commit, concurrent receipts, transfers and write-offs of the same stock
are serialized and each one re-reads the committed quantity: stock can
never go negative (and `CHECK quantity >= 0` is the backstop). Locks are
always taken item -> locations -> stock rows, each group in id order, so
two operations can never wait on each other in a cycle.

Receipts never change the item's `current_cost_minor` (§11 п.4, G2);
`unit_cost_minor` is the receipt cost per unit (§11 п.6).
"""

import uuid
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.inventory import (
    TEXT_MAX_LENGTH,
    InventoryItem,
    InventoryItemStock,
    InventoryMovement,
)
from app.inventory.lifecycle import (
    InvalidInventoryDataError,
    InvalidReversalTargetError,
    ReversalLocationNotAllowedError,
    ReversalLocationRequiredError,
    WriteOffAlreadyReversedError,
    ensure_quantity_mode,
    ensure_selectable,
    ensure_sufficient_stock,
    normalize_optional_text,
    normalize_write_off_reason,
    validate_cost_minor,
    validate_quantity,
)
from app.inventory.locking import selectable_location, share_locked_item, share_locked_location
from app.inventory.service import KIND_ITEM
from app.inventory.vocabulary import (
    ACTIVE,
    MOVEMENT_RECEIPT,
    MOVEMENT_TRANSFER,
    MOVEMENT_WRITE_OFF,
    MOVEMENT_WRITEOFF_REVERSAL,
)


class InventoryWriteOffNotFoundError(Exception):
    """No movement with this id belongs to the item (§16)."""

    def __init__(self, movement_id: uuid.UUID) -> None:
        super().__init__(f"Inventory movement {movement_id} does not exist for this item")
        self.movement_id = movement_id


def _quantity_item(session: Session, *, club_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
    """Active quantity-mode item of the Club, share-locked."""
    item = share_locked_item(session, club_id=club_id, item_id=item_id)
    ensure_selectable(item.status, kind=KIND_ITEM)
    ensure_quantity_mode(item.accounting_mode)
    return item


def _locked_stocks(
    session: Session, *, item_id: uuid.UUID, location_ids: Iterable[uuid.UUID], create: bool
) -> dict[uuid.UUID, InventoryItemStock]:
    """Row-lock the stock of `item_id` in each location, in location-id
    order. With `create`, a missing row is inserted with quantity 0 first
    (concurrent inserts of the same pair converge on one row)."""
    ordered = sorted(set(location_ids), key=str)
    if create:
        for location_id in ordered:
            session.execute(
                pg_insert(InventoryItemStock)
                .values(
                    id=uuid.uuid4(), item_id=item_id, storage_location_id=location_id, quantity=0
                )
                .on_conflict_do_nothing(
                    constraint="uq_inventory_item_stocks_item_id_storage_location_id"
                )
            )
    rows: dict[uuid.UUID, InventoryItemStock] = {}
    for location_id in ordered:
        row = session.execute(
            sa.select(InventoryItemStock)
            .where(
                InventoryItemStock.item_id == item_id,
                InventoryItemStock.storage_location_id == location_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is not None:
            rows[location_id] = row
    return rows


def _movement(
    *,
    item: InventoryItem,
    movement_type: str,
    quantity: int,
    created_by: uuid.UUID,
    **fields: Any,
) -> InventoryMovement:
    return InventoryMovement(
        id=uuid.uuid4(),
        item_id=item.id,
        movement_type=movement_type,
        quantity=quantity,
        created_by=created_by,
        created_at=sa.func.clock_timestamp(),
        **fields,
    )


def _finish(session: Session, movement: InventoryMovement) -> InventoryMovement:
    session.add(movement)
    session.commit()
    session.refresh(movement)
    return movement


def receive(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID,
    storage_location_id: uuid.UUID,
    quantity: int,
    unit_cost_minor: int | None,
    comment: str | None,
    created_by: uuid.UUID,
) -> InventoryMovement:
    """`receipt`: stock of the location grows by `quantity`."""
    amount = validate_quantity(quantity)
    cost = validate_cost_minor(unit_cost_minor)
    note = normalize_optional_text(comment, max_length=TEXT_MAX_LENGTH, field="comment")
    item = _quantity_item(session, club_id=club_id, item_id=item_id)
    location = selectable_location(session, club_id=club_id, location_id=storage_location_id)
    stock = _locked_stocks(session, item_id=item.id, location_ids=[location.id], create=True)
    stock[location.id].quantity += amount
    return _finish(
        session,
        _movement(
            item=item,
            movement_type=MOVEMENT_RECEIPT,
            quantity=amount,
            created_by=created_by,
            to_location_id=location.id,
            unit_cost_minor=cost,
            comment=note,
        ),
    )


def transfer(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID,
    from_location_id: uuid.UUID,
    to_location_id: uuid.UUID,
    quantity: int,
    comment: str | None,
    created_by: uuid.UUID,
) -> InventoryMovement:
    """`transfer`: `quantity` (possibly part of the stock) moves between two
    active locations."""
    amount = validate_quantity(quantity)
    note = normalize_optional_text(comment, max_length=TEXT_MAX_LENGTH, field="comment")
    if from_location_id == to_location_id:
        raise InvalidInventoryDataError("from_location_id and to_location_id must differ")
    item = _quantity_item(session, club_id=club_id, item_id=item_id)
    for location_id in sorted({from_location_id, to_location_id}, key=str):
        selectable_location(session, club_id=club_id, location_id=location_id)
    stock = _locked_stocks(
        session, item_id=item.id, location_ids=[from_location_id, to_location_id], create=True
    )
    ensure_sufficient_stock(available=stock[from_location_id].quantity, requested=amount)
    stock[from_location_id].quantity -= amount
    stock[to_location_id].quantity += amount
    return _finish(
        session,
        _movement(
            item=item,
            movement_type=MOVEMENT_TRANSFER,
            quantity=amount,
            created_by=created_by,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            comment=note,
        ),
    )


def write_off(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID,
    storage_location_id: uuid.UUID,
    quantity: int,
    comment: str,
    created_by: uuid.UUID,
) -> InventoryMovement:
    """`write_off`: `quantity` (possibly part of the stock) leaves the
    location; the reason is mandatory."""
    amount = validate_quantity(quantity)
    reason = normalize_write_off_reason(comment, max_length=TEXT_MAX_LENGTH)
    item = _quantity_item(session, club_id=club_id, item_id=item_id)
    location = selectable_location(session, club_id=club_id, location_id=storage_location_id)
    stock = _locked_stocks(session, item_id=item.id, location_ids=[location.id], create=False)
    available = stock[location.id].quantity if location.id in stock else 0
    ensure_sufficient_stock(available=available, requested=amount)
    stock[location.id].quantity -= amount
    return _finish(
        session,
        _movement(
            item=item,
            movement_type=MOVEMENT_WRITE_OFF,
            quantity=amount,
            created_by=created_by,
            from_location_id=location.id,
            comment=reason,
        ),
    )


def reverse_write_off(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID,
    movement_id: uuid.UUID,
    storage_location_id: uuid.UUID | None,
    created_by: uuid.UUID,
) -> InventoryMovement:
    """`writeoff_reversal` (§16, E): the whole quantity of a quantity
    `write_off` of this item returns — to the original location if it is
    still active (no other location accepted), otherwise to the given
    active location. A write-off is reversed at most once."""
    item = _quantity_item(session, club_id=club_id, item_id=item_id)
    original_write_off = session.execute(
        sa.select(InventoryMovement).where(
            InventoryMovement.id == movement_id, InventoryMovement.item_id == item.id
        )
    ).scalar_one_or_none()
    if original_write_off is None:
        raise InventoryWriteOffNotFoundError(movement_id)
    if (
        original_write_off.movement_type != MOVEMENT_WRITE_OFF
        or original_write_off.quantity is None
        or original_write_off.from_location_id is None
    ):
        raise InvalidReversalTargetError()

    original = share_locked_location(
        session, club_id=club_id, location_id=original_write_off.from_location_id
    )
    if original.status == ACTIVE:
        if storage_location_id is not None and storage_location_id != original.id:
            raise ReversalLocationNotAllowedError()
        target_id = original.id
    else:
        if storage_location_id is None:
            raise ReversalLocationRequiredError()
        target_id = selectable_location(
            session, club_id=club_id, location_id=storage_location_id
        ).id

    stock = _locked_stocks(session, item_id=item.id, location_ids=[target_id], create=True)
    already_reversed = session.execute(
        sa.select(
            sa.exists().where(InventoryMovement.reverses_movement_id == original_write_off.id)
        )
    ).scalar_one()
    if already_reversed:
        raise WriteOffAlreadyReversedError()
    stock[target_id].quantity += original_write_off.quantity
    try:
        return _finish(
            session,
            _movement(
                item=item,
                movement_type=MOVEMENT_WRITEOFF_REVERSAL,
                quantity=original_write_off.quantity,
                created_by=created_by,
                to_location_id=target_id,
                reverses_movement_id=original_write_off.id,
            ),
        )
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_inventory_movements_reverses_movement_id":
            raise WriteOffAlreadyReversedError() from exc
        raise


__all__ = [
    "InventoryWriteOffNotFoundError",
    "receive",
    "transfer",
    "write_off",
    "reverse_write_off",
]
