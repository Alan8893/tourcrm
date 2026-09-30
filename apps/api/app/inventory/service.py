"""Inventory Foundation write operations (docs/04-domain/inventory.md).

No authorization here: the API router has already required the
Administrator (app.inventory.authorization) and locked the target row
(`SELECT ... FOR UPDATE`) for every mutation of an existing record.

Rules enforced here (pure checks live in app.inventory.lifecycle):

- archived records are read-only (G13) and archiving is irreversible (§17);
- system units cannot be changed or archived (G11);
- archived categories/units/locations cannot be selected (G12, §10 п.5);
- every reference must belong to the record's Club (ADR-0022) — system
  units belong to no Club and are selectable everywhere;
- accounting mode (G3) and unit (G14) are fixed after the item's first
  movement. The check reads `inventory_movements` under the item's row
  lock; the slice that writes movements must take the same item row lock
  before inserting, so the check and a first movement cannot interleave;
- active-name uniqueness, case-insensitive (G15, PO decisions A/B), is
  checked up front for a clean error and backed by the partial unique
  indexes of app.db.inventory. A custom unit also may not reuse the name
  of a system unit (B) — system units are always active, so the same
  active-name index covers them;
- storage-location tree changes (create/re-parent/archive) are serialized
  by one transaction-scoped advisory lock, so two concurrent re-parents
  can never together form a cycle and a child can never be added to a
  location that is being archived.

There is no physical deletion anywhere in this module.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.inventory import (
    NAME_MAX_LENGTH,
    UNIT_NAME_MAX_LENGTH,
    InventoryCategory,
    InventoryInstance,
    InventoryItem,
    InventoryItemStock,
    InventoryMovement,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.inventory.lifecycle import (
    InvalidLocationParentError,
    ensure_accounting_mode_change_allowed,
    ensure_editable,
    ensure_item_has_no_active_instances,
    ensure_item_has_no_stock,
    ensure_location_archivable,
    ensure_location_has_no_instances,
    ensure_location_has_no_stock,
    ensure_selectable,
    ensure_unit_change_allowed,
    ensure_unit_mutable,
    normalize_name,
    validate_accounting_mode,
    validate_cost_minor,
)
from app.inventory.vocabulary import (
    ACTIVE,
    ARCHIVED,
    INSTANCE_STATES_IN_STORAGE,
    INSTANCE_WRITTEN_OFF,
)

# Key of the transaction-scoped PostgreSQL advisory lock serializing
# storage-location tree mutations. Nothing outside this module takes it.
_LOCATION_TREE_LOCK_KEY = 121_230_010

_NAME_CONFLICT_CONSTRAINTS = frozenset(
    {
        "uq_inventory_categories_active_name",
        "uq_inventory_units_active_name",
        "uq_inventory_items_active_name",
        "uq_inventory_storage_locations_active_child_name",
        "uq_inventory_storage_locations_active_root_name",
    }
)

KIND_CATEGORY = "category"
KIND_UNIT = "unit"
KIND_LOCATION = "storage location"
KIND_ITEM = "item"


class InventoryReferenceNotFoundError(Exception):
    def __init__(self, kind: str, reference_id: uuid.UUID) -> None:
        super().__init__(f"{kind.capitalize()} {reference_id} does not exist")
        self.kind = kind
        self.reference_id = reference_id


class InventoryNameConflictError(Exception):
    """G15/A/B: an active record with the same name already exists."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"An active {kind} with this name already exists")
        self.kind = kind


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _commit(session: Session, *, kind: str) -> None:
    """Commit, turning a lost race on an active-name unique index into the
    same typed conflict the up-front check raises."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in _NAME_CONFLICT_CONSTRAINTS:
            raise InventoryNameConflictError(kind) from exc
        raise


def has_movements(session: Session, item_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(sa.exists().where(InventoryMovement.item_id == item_id))
    ).scalar_one()


def _archive(record: Any, *, updated_by: uuid.UUID) -> None:
    record.status = ARCHIVED
    record.archived_at = _now()
    record.updated_by = updated_by


# --- categories ----------------------------------------------------------------


def _active_category_name_taken(
    session: Session, *, club_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None
) -> bool:
    conditions = [
        InventoryCategory.club_id == club_id,
        InventoryCategory.status == ACTIVE,
        sa.func.lower(InventoryCategory.name) == name.lower(),
    ]
    if exclude_id is not None:
        conditions.append(InventoryCategory.id != exclude_id)
    return session.execute(sa.select(sa.exists().where(*conditions))).scalar_one()


def create_category(
    session: Session, *, club_id: uuid.UUID, name: str, created_by: uuid.UUID
) -> InventoryCategory:
    normalized = normalize_name(name, max_length=NAME_MAX_LENGTH)
    if _active_category_name_taken(session, club_id=club_id, name=normalized):
        raise InventoryNameConflictError(KIND_CATEGORY)
    category = InventoryCategory(
        id=uuid.uuid4(),
        club_id=club_id,
        name=normalized,
        status=ACTIVE,
        created_by=created_by,
    )
    session.add(category)
    _commit(session, kind=KIND_CATEGORY)
    session.refresh(category)
    return category


def update_category(
    session: Session, *, category: InventoryCategory, name: str, updated_by: uuid.UUID
) -> InventoryCategory:
    ensure_editable(category.status, kind=KIND_CATEGORY)
    normalized = normalize_name(name, max_length=NAME_MAX_LENGTH)
    if _active_category_name_taken(
        session, club_id=category.club_id, name=normalized, exclude_id=category.id
    ):
        raise InventoryNameConflictError(KIND_CATEGORY)
    category.name = normalized
    category.updated_by = updated_by
    _commit(session, kind=KIND_CATEGORY)
    session.refresh(category)
    return category


def archive_category(
    session: Session, *, category: InventoryCategory, updated_by: uuid.UUID
) -> InventoryCategory:
    """G12: allowed while items still reference the category; they keep it."""
    ensure_editable(category.status, kind=KIND_CATEGORY)
    _archive(category, updated_by=updated_by)
    session.commit()
    session.refresh(category)
    return category


# --- units ---------------------------------------------------------------------


def _active_unit_name_taken(
    session: Session,
    *,
    club_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    """B: an active custom unit of the Club or any system unit (always
    active) with the same name, compared case-insensitively."""
    conditions = [
        InventoryUnit.status == ACTIVE,
        sa.or_(InventoryUnit.is_system.is_(True), InventoryUnit.club_id == club_id),
        sa.func.lower(InventoryUnit.name) == name.lower(),
    ]
    if exclude_id is not None:
        conditions.append(InventoryUnit.id != exclude_id)
    return session.execute(sa.select(sa.exists().where(*conditions))).scalar_one()


def create_unit(
    session: Session, *, club_id: uuid.UUID, name: str, created_by: uuid.UUID
) -> InventoryUnit:
    normalized = normalize_name(name, max_length=UNIT_NAME_MAX_LENGTH)
    if _active_unit_name_taken(session, club_id=club_id, name=normalized):
        raise InventoryNameConflictError(KIND_UNIT)
    unit = InventoryUnit(
        id=uuid.uuid4(),
        club_id=club_id,
        name=normalized,
        is_system=False,
        status=ACTIVE,
        created_by=created_by,
    )
    session.add(unit)
    _commit(session, kind=KIND_UNIT)
    session.refresh(unit)
    return unit


def update_unit(
    session: Session, *, unit: InventoryUnit, name: str, updated_by: uuid.UUID
) -> InventoryUnit:
    ensure_unit_mutable(is_system=unit.is_system)
    ensure_editable(unit.status, kind=KIND_UNIT)
    normalized = normalize_name(name, max_length=UNIT_NAME_MAX_LENGTH)
    if _active_unit_name_taken(session, club_id=unit.club_id, name=normalized, exclude_id=unit.id):
        raise InventoryNameConflictError(KIND_UNIT)
    unit.name = normalized
    unit.updated_by = updated_by
    _commit(session, kind=KIND_UNIT)
    session.refresh(unit)
    return unit


def archive_unit(session: Session, *, unit: InventoryUnit, updated_by: uuid.UUID) -> InventoryUnit:
    """G12: allowed while items still reference the unit; they keep it."""
    ensure_unit_mutable(is_system=unit.is_system)
    ensure_editable(unit.status, kind=KIND_UNIT)
    _archive(unit, updated_by=updated_by)
    session.commit()
    session.refresh(unit)
    return unit


# --- storage locations ---------------------------------------------------------


def _lock_location_tree(session: Session) -> None:
    session.execute(sa.select(sa.func.pg_advisory_xact_lock(_LOCATION_TREE_LOCK_KEY)))


def _active_location_name_taken(
    session: Session,
    *,
    club_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    conditions = [
        InventoryStorageLocation.club_id == club_id,
        InventoryStorageLocation.status == ACTIVE,
        sa.func.lower(InventoryStorageLocation.name) == name.lower(),
        (
            InventoryStorageLocation.parent_id.is_(None)
            if parent_id is None
            else InventoryStorageLocation.parent_id == parent_id
        ),
    ]
    if exclude_id is not None:
        conditions.append(InventoryStorageLocation.id != exclude_id)
    return session.execute(sa.select(sa.exists().where(*conditions))).scalar_one()


def _selectable_parent(
    session: Session, *, club_id: uuid.UUID, parent_id: uuid.UUID
) -> InventoryStorageLocation:
    parent = session.get(InventoryStorageLocation, parent_id)
    if parent is None or parent.club_id != club_id:
        raise InventoryReferenceNotFoundError(KIND_LOCATION, parent_id)
    ensure_selectable(parent.status, kind=KIND_LOCATION)
    return parent


def _is_self_or_ancestor(
    session: Session, *, location_id: uuid.UUID, candidate_parent_id: uuid.UUID
) -> bool:
    """True if `location_id` is `candidate_parent_id` or one of its
    ancestors — i.e. re-parenting would close a cycle."""
    ancestors = (
        sa.select(InventoryStorageLocation.id, InventoryStorageLocation.parent_id)
        .where(InventoryStorageLocation.id == candidate_parent_id)
        .cte("ancestors", recursive=True)
    )
    ancestors = ancestors.union(
        sa.select(InventoryStorageLocation.id, InventoryStorageLocation.parent_id).join(
            ancestors, InventoryStorageLocation.id == ancestors.c.parent_id
        )
    )
    return session.execute(sa.select(sa.exists().where(ancestors.c.id == location_id))).scalar_one()


def create_location(
    session: Session,
    *,
    club_id: uuid.UUID,
    name: str,
    parent_id: uuid.UUID | None,
    created_by: uuid.UUID,
) -> InventoryStorageLocation:
    normalized = normalize_name(name, max_length=NAME_MAX_LENGTH)
    _lock_location_tree(session)
    if parent_id is not None:
        _selectable_parent(session, club_id=club_id, parent_id=parent_id)
    if _active_location_name_taken(session, club_id=club_id, parent_id=parent_id, name=normalized):
        raise InventoryNameConflictError(KIND_LOCATION)
    location = InventoryStorageLocation(
        id=uuid.uuid4(),
        club_id=club_id,
        parent_id=parent_id,
        name=normalized,
        status=ACTIVE,
        created_by=created_by,
    )
    session.add(location)
    _commit(session, kind=KIND_LOCATION)
    session.refresh(location)
    return location


UPDATABLE_LOCATION_FIELDS = frozenset({"name", "parent_id"})


def update_location(
    session: Session,
    *,
    location: InventoryStorageLocation,
    fields: dict[str, Any],
    updated_by: uuid.UUID,
) -> InventoryStorageLocation:
    """Rename and/or re-parent (`parent_id: null` makes it a root)."""
    unknown = set(fields) - UPDATABLE_LOCATION_FIELDS
    if unknown:  # pragma: no cover - the request schema forbids these
        raise ValueError(f"Not updatable: {sorted(unknown)}")
    ensure_editable(location.status, kind=KIND_LOCATION)
    _lock_location_tree(session)

    name = (
        normalize_name(fields["name"], max_length=NAME_MAX_LENGTH)
        if "name" in fields
        else location.name
    )
    parent_id = fields["parent_id"] if "parent_id" in fields else location.parent_id
    if parent_id is not None and parent_id != location.parent_id:
        _selectable_parent(session, club_id=location.club_id, parent_id=parent_id)
        if _is_self_or_ancestor(session, location_id=location.id, candidate_parent_id=parent_id):
            raise InvalidLocationParentError()
    if _active_location_name_taken(
        session,
        club_id=location.club_id,
        parent_id=parent_id,
        name=name,
        exclude_id=location.id,
    ):
        raise InventoryNameConflictError(KIND_LOCATION)

    location.name = name
    location.parent_id = parent_id
    location.updated_by = updated_by
    _commit(session, kind=KIND_LOCATION)
    session.refresh(location)
    return location


def archive_location(
    session: Session, *, location: InventoryStorageLocation, updated_by: uuid.UUID
) -> InventoryStorageLocation:
    ensure_editable(location.status, kind=KIND_LOCATION)
    _lock_location_tree(session)
    has_active_children = session.execute(
        sa.select(
            sa.exists().where(
                InventoryStorageLocation.parent_id == location.id,
                InventoryStorageLocation.status == ACTIVE,
            )
        )
    ).scalar_one()
    ensure_location_archivable(has_active_children=has_active_children)
    # GAP-3. Instance operations that bring an instance into a location
    # hold a share lock on that location's row; the router holds this
    # location's row lock, so the check cannot race them.
    has_instances = session.execute(
        sa.select(
            sa.exists().where(
                InventoryInstance.storage_location_id == location.id,
                InventoryInstance.state.in_(INSTANCE_STATES_IN_STORAGE),
            )
        )
    ).scalar_one()
    ensure_location_has_no_instances(has_instances=has_instances)
    # §17 п.5 (Slice 3): quantity operations take a share lock on every
    # location they touch, so this check cannot race them either.
    has_stock = session.execute(
        sa.select(
            sa.exists().where(
                InventoryItemStock.storage_location_id == location.id,
                InventoryItemStock.quantity > 0,
            )
        )
    ).scalar_one()
    ensure_location_has_no_stock(has_stock=has_stock)
    _archive(location, updated_by=updated_by)
    session.commit()
    session.refresh(location)
    return location


# --- nomenclature --------------------------------------------------------------


def _selectable_category(
    session: Session, *, club_id: uuid.UUID, category_id: uuid.UUID
) -> InventoryCategory:
    category = session.get(InventoryCategory, category_id)
    if category is None or category.club_id != club_id:
        raise InventoryReferenceNotFoundError(KIND_CATEGORY, category_id)
    ensure_selectable(category.status, kind=KIND_CATEGORY)
    return category


def _selectable_unit(session: Session, *, club_id: uuid.UUID, unit_id: uuid.UUID) -> InventoryUnit:
    unit = session.get(InventoryUnit, unit_id)
    if unit is None or (not unit.is_system and unit.club_id != club_id):
        raise InventoryReferenceNotFoundError(KIND_UNIT, unit_id)
    ensure_selectable(unit.status, kind=KIND_UNIT)
    return unit


def _active_item_name_taken(
    session: Session, *, club_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None
) -> bool:
    conditions = [
        InventoryItem.club_id == club_id,
        InventoryItem.status == ACTIVE,
        sa.func.lower(InventoryItem.name) == name.lower(),
    ]
    if exclude_id is not None:
        conditions.append(InventoryItem.id != exclude_id)
    return session.execute(sa.select(sa.exists().where(*conditions))).scalar_one()


def create_item(
    session: Session,
    *,
    club_id: uuid.UUID,
    name: str,
    category_id: uuid.UUID,
    unit_id: uuid.UUID,
    accounting_mode: str,
    current_cost_minor: int | None,
    created_by: uuid.UUID,
) -> InventoryItem:
    normalized = normalize_name(name, max_length=NAME_MAX_LENGTH)
    mode = validate_accounting_mode(accounting_mode)
    cost = validate_cost_minor(current_cost_minor)
    _selectable_category(session, club_id=club_id, category_id=category_id)
    _selectable_unit(session, club_id=club_id, unit_id=unit_id)
    if _active_item_name_taken(session, club_id=club_id, name=normalized):
        raise InventoryNameConflictError(KIND_ITEM)
    item = InventoryItem(
        id=uuid.uuid4(),
        club_id=club_id,
        name=normalized,
        category_id=category_id,
        unit_id=unit_id,
        accounting_mode=mode,
        current_cost_minor=cost,
        status=ACTIVE,
        created_by=created_by,
    )
    session.add(item)
    _commit(session, kind=KIND_ITEM)
    session.refresh(item)
    return item


UPDATABLE_ITEM_FIELDS = frozenset(
    {"name", "category_id", "unit_id", "accounting_mode", "current_cost_minor"}
)


def update_item(
    session: Session, *, item: InventoryItem, fields: dict[str, Any], updated_by: uuid.UUID
) -> InventoryItem:
    """Partial update; `fields` holds only keys the client sent. A reference
    left unchanged may stay archived (G12); a changed one must be active."""
    unknown = set(fields) - UPDATABLE_ITEM_FIELDS
    if unknown:  # pragma: no cover - the request schema forbids these
        raise ValueError(f"Not updatable: {sorted(unknown)}")
    ensure_editable(item.status, kind=KIND_ITEM)

    name = (
        normalize_name(fields["name"], max_length=NAME_MAX_LENGTH)
        if "name" in fields
        else item.name
    )
    category_id = fields.get("category_id", item.category_id)
    unit_id = fields.get("unit_id", item.unit_id)
    mode = (
        validate_accounting_mode(fields["accounting_mode"])
        if "accounting_mode" in fields
        else item.accounting_mode
    )
    cost = (
        validate_cost_minor(fields["current_cost_minor"])
        if "current_cost_minor" in fields
        else item.current_cost_minor
    )

    if category_id != item.category_id:
        _selectable_category(session, club_id=item.club_id, category_id=category_id)
    if unit_id != item.unit_id or mode != item.accounting_mode:
        movements_exist = has_movements(session, item.id)
        ensure_unit_change_allowed(
            current=str(item.unit_id), requested=str(unit_id), has_movements=movements_exist
        )
        ensure_accounting_mode_change_allowed(
            current=item.accounting_mode, requested=mode, has_movements=movements_exist
        )
    if unit_id != item.unit_id:
        _selectable_unit(session, club_id=item.club_id, unit_id=unit_id)
    if _active_item_name_taken(session, club_id=item.club_id, name=name, exclude_id=item.id):
        raise InventoryNameConflictError(KIND_ITEM)

    item.name = name
    item.category_id = category_id
    item.unit_id = unit_id
    item.accounting_mode = mode
    item.current_cost_minor = cost
    item.updated_by = updated_by
    _commit(session, kind=KIND_ITEM)
    session.refresh(item)
    return item


def archive_item(session: Session, *, item: InventoryItem, updated_by: uuid.UUID) -> InventoryItem:
    """inventory.md §17 п.4 forbids archiving with non-zero stock, instances
    not in `written_off` or unfinished issues. Instances (Slice 2) and stock
    (Slice 3) are checked here; issues add their precondition with Slice 4.
    Every receipt/transfer holds a share lock on the item row and the router
    holds this item's row lock, so neither can slip in during the check."""
    ensure_editable(item.status, kind=KIND_ITEM)
    has_active_instances = session.execute(
        sa.select(
            sa.exists().where(
                InventoryInstance.item_id == item.id,
                InventoryInstance.state != INSTANCE_WRITTEN_OFF,
            )
        )
    ).scalar_one()
    ensure_item_has_no_active_instances(has_active_instances=has_active_instances)
    total_stock = session.execute(
        sa.select(sa.func.coalesce(sa.func.sum(InventoryItemStock.quantity), 0)).where(
            InventoryItemStock.item_id == item.id
        )
    ).scalar_one()
    ensure_item_has_no_stock(total_stock=total_stock)
    _archive(item, updated_by=updated_by)
    session.commit()
    session.refresh(item)
    return item


__all__ = [
    "KIND_CATEGORY",
    "KIND_UNIT",
    "KIND_LOCATION",
    "KIND_ITEM",
    "InventoryReferenceNotFoundError",
    "InventoryNameConflictError",
    "has_movements",
    "create_category",
    "update_category",
    "archive_category",
    "create_unit",
    "update_unit",
    "archive_unit",
    "create_location",
    "update_location",
    "archive_location",
    "create_item",
    "update_item",
    "archive_item",
]
