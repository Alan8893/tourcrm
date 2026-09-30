"""Inventory Foundation persistence (TH-0121 / Issue #230).

Canonical source: docs/04-domain/inventory.md.

- `inventory_categories`, `inventory_units`, `inventory_storage_locations`,
  `inventory_items` (nomenclature): Club-owned records with the shared
  closed `active` | `archived` lifecycle (inventory.md §17). Archiving is
  irreversible and there is no physical deletion — every FK is RESTRICT.
  `archived_at` is set exactly when `status = 'archived'`.
- System units (inventory.md §9, G11) have `is_system = true`, no Club and
  no creator; the CHECK below keeps them permanently `active`.
- Name uniqueness (G15) is a partial unique index over `lower(name)` of
  *active* rows only, so an archived row never blocks its old name.
- `inventory_items` deliberately has no quantity/stock/state/location
  column (inventory.md §5 п.4-5, §13): the stock of an item is derived
  from its movements, never edited directly.
- `inventory_movements` is the append-only movement journal
  (inventory.md §13). The Foundation fixes only what the canonical model
  defines — the item, the closed movement type vocabulary, who and when;
  quantities, locations, instances, recipients and receipt cost belong to
  the slices that implement each movement workflow. Rows are immutable:
  the ORM refuses any UPDATE/DELETE of a loaded movement (see
  `_refuse_movement_mutation`); corrections are compensating movements.

Plain FK columns, no ORM `relationship()` objects — the same shape as
app.db.news/app.db.groups.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.inventory.vocabulary import (
    CANONICAL_ACCOUNTING_MODES,
    CANONICAL_INVENTORY_STATUSES,
    CANONICAL_MOVEMENT_TYPES,
)


def _values(vocabulary: frozenset[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(vocabulary))


_STATUS_VALUES = _values(CANONICAL_INVENTORY_STATUSES)
_MODE_VALUES = _values(CANONICAL_ACCOUNTING_MODES)
_MOVEMENT_TYPE_VALUES = _values(CANONICAL_MOVEMENT_TYPES)

NAME_MAX_LENGTH = 255
UNIT_NAME_MAX_LENGTH = 64


def _lifecycle_checks(table: str) -> tuple[sa.CheckConstraint, sa.CheckConstraint]:
    return (
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name=f"ck_{table}_status_valid"),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name=f"ck_{table}_archived_at_matches_status",
        ),
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _user_fk(*, nullable: bool) -> Any:
    return mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class InventoryCategory(Base):
    """Editable internal category reference (inventory.md §8)."""

    __tablename__ = "inventory_categories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        *_lifecycle_checks("inventory_categories"),
        sa.Index("ix_inventory_categories_club_id_status", "club_id", "status"),
    )


class InventoryUnit(Base):
    """Unit of measure (inventory.md §9). System units (G11) are
    installation-wide seed rows (`club_id`/`created_by` NULL) that can never
    be archived; custom units belong to the Club and are created by an
    Administrator."""

    __tablename__ = "inventory_units"

    id: Mapped[uuid.UUID] = _uuid_pk()
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(sa.String(UNIT_NAME_MAX_LENGTH), nullable=False)
    is_system: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    updated_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        *_lifecycle_checks("inventory_units"),
        sa.CheckConstraint(
            "NOT is_system OR (club_id IS NULL AND created_by IS NULL AND status = 'active')",
            name="ck_inventory_units_system_unit_shape",
        ),
        sa.CheckConstraint(
            "is_system OR (club_id IS NOT NULL AND created_by IS NOT NULL)",
            name="ck_inventory_units_custom_unit_shape",
        ),
        sa.Index(
            "uq_inventory_units_system_name",
            sa.func.lower(sa.column("name")),
            unique=True,
            postgresql_where=sa.text("is_system"),
        ),
        sa.Index("ix_inventory_units_club_id_status", "club_id", "status"),
    )


class InventoryStorageLocation(Base):
    """One node of the storage-location tree (inventory.md §10). Depth is
    unbounded; acyclicity beyond the direct self-reference CHECK is a
    service-layer invariant (app.inventory.service), serialized by a
    transaction-scoped advisory lock."""

    __tablename__ = "inventory_storage_locations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_storage_locations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        *_lifecycle_checks("inventory_storage_locations"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_inventory_storage_locations_not_own_parent",
        ),
        # G15: unique among active siblings. Root locations have no parent,
        # so their sibling set is the Club's roots.
        sa.Index(
            "uq_inventory_storage_locations_active_child_name",
            "parent_id",
            sa.func.lower(sa.column("name")),
            unique=True,
            postgresql_where=sa.text("status = 'active' AND parent_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_inventory_storage_locations_active_root_name",
            "club_id",
            sa.func.lower(sa.column("name")),
            unique=True,
            postgresql_where=sa.text("status = 'active' AND parent_id IS NULL"),
        ),
        sa.Index("ix_inventory_storage_locations_parent_id", "parent_id"),
        sa.Index("ix_inventory_storage_locations_club_id_status", "club_id", "status"),
    )


class InventoryItem(Base):
    """Nomenclature (inventory.md §5). `current_cost_minor` is the manually
    maintained current item cost in RUB kopecks (§11, G1/G2)."""

    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("inventory_units.id", ondelete="RESTRICT"), nullable=False
    )
    accounting_mode: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    current_cost_minor: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        *_lifecycle_checks("inventory_items"),
        sa.CheckConstraint(
            f"accounting_mode IN ({_MODE_VALUES})", name="ck_inventory_items_accounting_mode_valid"
        ),
        sa.CheckConstraint(
            "current_cost_minor IS NULL OR current_cost_minor >= 0",
            name="ck_inventory_items_current_cost_minor_non_negative",
        ),
        sa.Index(
            "uq_inventory_items_active_name",
            "club_id",
            sa.func.lower(sa.column("name")),
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
        sa.Index("ix_inventory_items_club_id_status", "club_id", "status"),
        sa.Index("ix_inventory_items_category_id", "category_id"),
        sa.Index("ix_inventory_items_unit_id", "unit_id"),
    )


class InventoryMovement(Base):
    """Append-only movement journal row (inventory.md §13). No `updated_at`
    / `updated_by`: a movement is never updated."""

    __tablename__ = "inventory_movements"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    movement_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.CheckConstraint(
            f"movement_type IN ({_MOVEMENT_TYPE_VALUES})",
            name="ck_inventory_movements_movement_type_valid",
        ),
        sa.Index("ix_inventory_movements_item_id_created_at", "item_id", "created_at"),
    )


class InventoryMovementImmutableError(Exception):
    """An existing movement was about to be updated or deleted."""

    def __init__(self, movement_id: uuid.UUID) -> None:
        super().__init__(
            f"Inventory movement {movement_id} is immutable; record a compensating movement"
        )
        self.movement_id = movement_id


@event.listens_for(InventoryMovement, "before_update")
@event.listens_for(InventoryMovement, "before_delete")
def _refuse_movement_mutation(_mapper: Any, _connection: Any, target: InventoryMovement) -> None:
    raise InventoryMovementImmutableError(target.id)


__all__ = [
    "NAME_MAX_LENGTH",
    "UNIT_NAME_MAX_LENGTH",
    "InventoryCategory",
    "InventoryUnit",
    "InventoryStorageLocation",
    "InventoryItem",
    "InventoryMovement",
    "InventoryMovementImmutableError",
]
