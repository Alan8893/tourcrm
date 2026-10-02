"""Inventory Foundation persistence (TH-0121 / Issue #230).

Canonical source: docs/04-domain/inventory.md.

- `inventory_categories`, `inventory_units`, `inventory_storage_locations`,
  `inventory_items` (nomenclature): Club-owned records with the shared
  closed `active` | `archived` lifecycle (inventory.md §17). Archiving is
  irreversible and there is no physical deletion — every FK is RESTRICT.
  `archived_at` is set exactly when `status = 'archived'`.
- System units (inventory.md §9, G11) have `is_system = true`, no Club and
  no creator; the CHECK below keeps them permanently `active`.
- Name uniqueness (G15, PO decisions A/B) is a partial unique index over
  `lower(name)` of *active* rows only, so an archived row never blocks its
  old name. For units the index spans system and custom units together,
  so a custom unit can never reuse a system unit name (B); TourCRM has a
  single Club, so this is also the per-Club rule.
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
  A `writeoff_reversal` (PO decision E) references the reversed
  `write_off` through `reverses_movement_id`: the CHECK ties the column to
  that type, the unique constraint allows one reversal per write-off, and
  `_require_reversed_write_off` refuses a reference to any other type.
  The reversal workflow itself belongs to later slices.
- `inventory_instances` (Slice 2, inventory.md §7): one physical object of
  an `instance`-mode item. `state` and `storage_location_id` are a
  projection of the movement journal — only app.inventory.instances
  changes them, in the same transaction as the movement that causes the
  change. The CHECK ties the location to the state (R3): set exactly for
  `available`/`in_repair`. `inventory_number` (`INV-000123`, Q2/Q3) and
  `item_id` (Q10) are immutable: the ORM refuses to change them
  (`_refuse_instance_identity_change`).
- Movement columns added with Slice 2: `instance_id`,
  `from_location_id`/`to_location_id`, `unit_cost_minor` (receipt cost,
  §11 п.6) and `comment` (the write-off reason, Q8).
- Slice 3 (quantity movements): `quantity` is set exactly on movements
  without an instance (CHECK) and is a positive integer. The current stock
  of a quantity item per storage location is the projection
  `inventory_item_stocks`, changed only by app.inventory.quantities in the
  same transaction as the movement that causes the change; the journal
  stays the historical source of truth.
- Slice 4 (issue / return, inventory.md §14): `inventory_issues` is the
  issue document (recipient, optional Event, planned return date, comment,
  `issued` | `cancelled`); `inventory_issue_lines` holds one active line
  per item of an issue (partial UNIQUE). A line with nothing outstanding
  can be removed from the issue (`removed_at`/`removed_by`); the row is
  never deleted, so its movements keep referencing it. What a line issued and
  got back is not stored on it: every `issue`/`return` movement references
  its line through `issue_line_id` (required for those types, CHECK), and
  the outstanding quantity / instances are derived from the journal. The
  composite FK `(issue_line_id, item_id)` ties a movement to a line of the
  same item. The write-off half of a lost instance also references the
  line; no other movement type does.

Plain FK columns, no ORM `relationship()` objects — the same shape as
app.db.news/app.db.groups.
"""

import uuid
from datetime import date, datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.inventory.vocabulary import (
    CANONICAL_ACCOUNTING_MODES,
    CANONICAL_INSTANCE_STATES,
    CANONICAL_INVENTORY_STATUSES,
    CANONICAL_ISSUE_STATUSES,
    CANONICAL_MOVEMENT_TYPES,
    CANONICAL_RECIPIENT_TYPES,
    INSTANCE_ONLY_MOVEMENT_TYPES,
    INSTANCE_STATES_IN_STORAGE,
    ISSUE_LINE_MOVEMENT_TYPES,
    MOVEMENT_RECEIPT,
    MOVEMENT_WRITE_OFF,
    RECIPIENT_GROUP,
    RECIPIENT_INSTRUCTOR,
    RECIPIENT_MEMBER,
)


def _values(vocabulary: frozenset[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(vocabulary))


_STATUS_VALUES = _values(CANONICAL_INVENTORY_STATUSES)
_MODE_VALUES = _values(CANONICAL_ACCOUNTING_MODES)
_MOVEMENT_TYPE_VALUES = _values(CANONICAL_MOVEMENT_TYPES)
_INSTANCE_STATE_VALUES = _values(CANONICAL_INSTANCE_STATES)
_IN_STORAGE_STATE_VALUES = _values(INSTANCE_STATES_IN_STORAGE)
_INSTANCE_ONLY_MOVEMENT_VALUES = _values(INSTANCE_ONLY_MOVEMENT_TYPES)
_ISSUE_STATUS_VALUES = _values(CANONICAL_ISSUE_STATUSES)
_RECIPIENT_TYPE_VALUES = _values(CANONICAL_RECIPIENT_TYPES)
_ISSUE_LINE_MOVEMENT_VALUES = _values(ISSUE_LINE_MOVEMENT_TYPES)

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
        sa.Index(
            "uq_inventory_categories_active_name",
            "club_id",
            sa.func.lower(sa.column("name")),
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
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
            "uq_inventory_units_active_name",
            sa.func.lower(sa.column("name")),
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
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


INVENTORY_NUMBER_MAX_LENGTH = 32
MANUFACTURER_CODE_MAX_LENGTH = 255
TEXT_MAX_LENGTH = 2000


class InventoryInstance(Base):
    """One physical object of an `instance`-mode item (inventory.md §7)."""

    __tablename__ = "inventory_instances"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # The Club is the item's Club (no own column): TourCRM has a single Club,
    # so the unique inventory number is also unique within the Club (Q2).
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    inventory_number: Mapped[str] = mapped_column(
        sa.String(INVENTORY_NUMBER_MAX_LENGTH), nullable=False
    )
    manufacturer_barcode: Mapped[Optional[str]] = mapped_column(
        sa.String(MANUFACTURER_CODE_MAX_LENGTH), nullable=True
    )
    manufacturer_serial_number: Mapped[Optional[str]] = mapped_column(
        sa.String(MANUFACTURER_CODE_MAX_LENGTH), nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    storage_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_storage_locations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        sa.CheckConstraint(
            f"state IN ({_INSTANCE_STATE_VALUES})", name="ck_inventory_instances_state_valid"
        ),
        sa.CheckConstraint(
            f"(state IN ({_IN_STORAGE_STATE_VALUES})) = (storage_location_id IS NOT NULL)",
            name="ck_inventory_instances_location_matches_state",
        ),
        sa.CheckConstraint(
            "inventory_number ~ '^INV-[0-9]{6,}$'",
            name="ck_inventory_instances_inventory_number_format",
        ),
        sa.UniqueConstraint("inventory_number", name="uq_inventory_instances_inventory_number"),
        sa.Index("ix_inventory_instances_item_id_state", "item_id", "state"),
        sa.Index("ix_inventory_instances_storage_location_id", "storage_location_id"),
        sa.Index("ix_inventory_instances_state", "state"),
    )


class InventoryInstanceIdentityError(Exception):
    """An instance's item or Inventory ID was about to be changed."""

    def __init__(self, instance_id: uuid.UUID) -> None:
        super().__init__(
            f"Inventory instance {instance_id}: item and inventory number are immutable"
        )
        self.instance_id = instance_id


@event.listens_for(InventoryInstance, "before_update")
def _refuse_instance_identity_change(
    _mapper: Any, _connection: Any, target: InventoryInstance
) -> None:
    state = sa.inspect(target)
    for attribute in ("item_id", "inventory_number"):
        if state.attrs[attribute].history.has_changes():
            raise InventoryInstanceIdentityError(target.id)


class InventoryMovement(Base):
    """Append-only movement journal row (inventory.md §13). No `updated_at`
    / `updated_by`: a movement is never updated."""

    __tablename__ = "inventory_movements"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    movement_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_instances.id", ondelete="RESTRICT"),
        nullable=True,
    )
    from_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_storage_locations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    to_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_storage_locations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    quantity: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    unit_cost_minor: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    reverses_movement_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_movements.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Slice 4: the issue line an `issue`/`return` (or a lost instance's
    # `write_off`) belongs to; FK together with `item_id` (see table args).
    issue_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.CheckConstraint(
            f"movement_type IN ({_MOVEMENT_TYPE_VALUES})",
            name="ck_inventory_movements_movement_type_valid",
        ),
        sa.CheckConstraint(
            "(movement_type = 'writeoff_reversal') = (reverses_movement_id IS NOT NULL)",
            name="ck_inventory_movements_reversal_reference",
        ),
        sa.CheckConstraint(
            f"movement_type NOT IN ({_INSTANCE_ONLY_MOVEMENT_VALUES}) OR instance_id IS NOT NULL",
            name="ck_inventory_movements_instance_only_types",
        ),
        sa.CheckConstraint(
            f"unit_cost_minor IS NULL OR (movement_type = '{MOVEMENT_RECEIPT}' "
            "AND unit_cost_minor >= 0)",
            name="ck_inventory_movements_unit_cost_on_receipt",
        ),
        sa.CheckConstraint(
            "(instance_id IS NULL) = (quantity IS NOT NULL)",
            name="ck_inventory_movements_quantity_matches_instance",
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="ck_inventory_movements_quantity_positive",
        ),
        sa.CheckConstraint(
            f"(movement_type IN ({_ISSUE_LINE_MOVEMENT_VALUES}) AND issue_line_id IS NOT NULL)"
            f" OR (movement_type NOT IN ({_ISSUE_LINE_MOVEMENT_VALUES})"
            f" AND (issue_line_id IS NULL OR movement_type = '{MOVEMENT_WRITE_OFF}'))",
            name="ck_inventory_movements_issue_line_reference",
        ),
        sa.UniqueConstraint(
            "reverses_movement_id", name="uq_inventory_movements_reverses_movement_id"
        ),
        sa.ForeignKeyConstraint(
            ["issue_line_id", "item_id"],
            ["inventory_issue_lines.id", "inventory_issue_lines.item_id"],
            name="fk_inventory_movements_issue_line_id_item_id",
            ondelete="RESTRICT",
        ),
        sa.Index("ix_inventory_movements_item_id_created_at", "item_id", "created_at"),
        sa.Index("ix_inventory_movements_instance_id_created_at", "instance_id", "created_at"),
        sa.Index("ix_inventory_movements_issue_line_id", "issue_line_id"),
    )


class InventoryItemStock(Base):
    """Current stock of a quantity item in one storage location (Slice 3) —
    a projection of the movement journal, never edited directly."""

    __tablename__ = "inventory_item_stocks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    storage_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_storage_locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_item_stocks_quantity_non_negative"),
        sa.UniqueConstraint(
            "item_id",
            "storage_location_id",
            name="uq_inventory_item_stocks_item_id_storage_location_id",
        ),
        sa.Index("ix_inventory_item_stocks_storage_location_id", "storage_location_id"),
    )


ISSUE_RECIPIENT_TYPE_MAX_LENGTH = 16


class InventoryIssue(Base):
    """Issue document (inventory.md §14, Slice 4): the handover of property
    to one recipient — a Member (Person), an Instructor (User) or a Group.
    Issued on creation; `cancelled` is terminal. Exactly the recipient
    column of `recipient_type` is set (CHECK)."""

    __tablename__ = "inventory_issues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_type: Mapped[str] = mapped_column(
        sa.String(ISSUE_RECIPIENT_TYPE_MAX_LENGTH), nullable=False
    )
    recipient_person_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=True
    )
    recipient_user_id: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    recipient_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=True
    )
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=True
    )
    # Informational only: no overdue status, no automatic action.
    planned_return_date: Mapped[Optional[date]] = mapped_column(sa.Date, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    cancelled_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    updated_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        sa.CheckConstraint(
            f"status IN ({_ISSUE_STATUS_VALUES})", name="ck_inventory_issues_status_valid"
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)"
            " AND (status = 'cancelled') = (cancelled_by IS NOT NULL)",
            name="ck_inventory_issues_cancellation_matches_status",
        ),
        sa.CheckConstraint(
            f"recipient_type IN ({_RECIPIENT_TYPE_VALUES})",
            name="ck_inventory_issues_recipient_type_valid",
        ),
        sa.CheckConstraint(
            f"(recipient_type = '{RECIPIENT_MEMBER}') = (recipient_person_id IS NOT NULL)"
            f" AND (recipient_type = '{RECIPIENT_INSTRUCTOR}') = (recipient_user_id IS NOT NULL)"
            f" AND (recipient_type = '{RECIPIENT_GROUP}') = (recipient_group_id IS NOT NULL)",
            name="ck_inventory_issues_recipient_matches_type",
        ),
        sa.Index("ix_inventory_issues_club_id_status", "club_id", "status"),
        sa.Index("ix_inventory_issues_recipient_person_id", "recipient_person_id"),
        sa.Index("ix_inventory_issues_recipient_user_id", "recipient_user_id"),
        sa.Index("ix_inventory_issues_recipient_group_id", "recipient_group_id"),
        sa.Index("ix_inventory_issues_event_id", "event_id"),
    )


class InventoryIssueLine(Base):
    """One item of an issue (inventory.md §14): one *active* line per item
    (partial UNIQUE). A quantity line's issued/returned quantities and an
    instance line's instances are derived from the movements referencing it.

    A line with nothing outstanding can be removed from the issue's working
    composition: `removed_at`/`removed_by` are set once (both or neither,
    CHECK) and the row stays, so the movements keep referencing it and the
    history is intact. A removed line is never changed again and the row is
    never deleted (`_guard_issue_line_mutation`)."""

    __tablename__ = "inventory_issue_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("inventory_issues.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = _user_fk(nullable=False)
    created_at: Mapped[datetime] = _created_at()
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    removed_by: Mapped[Optional[uuid.UUID]] = _user_fk(nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "(removed_at IS NULL) = (removed_by IS NULL)",
            name="ck_inventory_issue_lines_removed_by_matches_removed_at",
        ),
        sa.Index(
            "uq_inventory_issue_lines_active_issue_id_item_id",
            "issue_id",
            "item_id",
            unique=True,
            postgresql_where=sa.text("removed_at IS NULL"),
        ),
        sa.Index("ix_inventory_issue_lines_issue_id", "issue_id"),
        # Target of the movements' composite FK (issue_line_id, item_id).
        sa.UniqueConstraint("id", "item_id", name="uq_inventory_issue_lines_id_item_id"),
        sa.Index("ix_inventory_issue_lines_item_id", "item_id"),
    )


class InventoryIssueLineImmutableError(Exception):
    """An issue line was about to be changed (other than being removed once)
    or deleted."""

    def __init__(self, line_id: uuid.UUID) -> None:
        super().__init__(
            f"Inventory issue line {line_id} is immutable except for its one-time removal"
            " and is never deleted"
        )
        self.line_id = line_id


_ISSUE_LINE_REMOVAL_FIELDS = ("removed_at", "removed_by")


@event.listens_for(InventoryIssueLine, "before_update")
def _guard_issue_line_mutation(_mapper: Any, _connection: Any, target: InventoryIssueLine) -> None:
    """The only allowed change is the one-time removal: `removed_at` and
    `removed_by` going from unset to set. Anything else is refused."""
    state = sa.inspect(target)
    for attribute in state.mapper.column_attrs:
        history = state.attrs[attribute.key].history
        if not history.has_changes():
            continue
        if attribute.key not in _ISSUE_LINE_REMOVAL_FIELDS or any(
            value is not None for value in history.deleted
        ):
            raise InventoryIssueLineImmutableError(target.id)


@event.listens_for(InventoryIssueLine, "before_delete")
def _refuse_issue_line_delete(_mapper: Any, _connection: Any, target: InventoryIssueLine) -> None:
    raise InventoryIssueLineImmutableError(target.id)


class InventoryMovementImmutableError(Exception):
    """An existing movement was about to be updated or deleted."""

    def __init__(self, movement_id: uuid.UUID) -> None:
        super().__init__(
            f"Inventory movement {movement_id} is immutable; record a compensating movement"
        )
        self.movement_id = movement_id


class InventoryReversalTargetError(Exception):
    """A `writeoff_reversal` references something other than a `write_off`."""

    def __init__(self, movement_id: uuid.UUID) -> None:
        super().__init__(f"Inventory movement {movement_id} is not a write-off")
        self.movement_id = movement_id


@event.listens_for(InventoryMovement, "before_insert")
def _require_reversed_write_off(_mapper: Any, connection: Any, target: InventoryMovement) -> None:
    if target.reverses_movement_id is None:
        return
    reversed_type = connection.execute(
        sa.select(InventoryMovement.movement_type).where(
            InventoryMovement.id == target.reverses_movement_id
        )
    ).scalar_one_or_none()
    if reversed_type is not None and reversed_type != MOVEMENT_WRITE_OFF:
        raise InventoryReversalTargetError(target.reverses_movement_id)


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
    "INVENTORY_NUMBER_MAX_LENGTH",
    "MANUFACTURER_CODE_MAX_LENGTH",
    "TEXT_MAX_LENGTH",
    "InventoryItem",
    "InventoryInstance",
    "InventoryInstanceIdentityError",
    "InventoryItemStock",
    "InventoryMovement",
    "InventoryMovementImmutableError",
    "ISSUE_RECIPIENT_TYPE_MAX_LENGTH",
    "InventoryIssue",
    "InventoryIssueLine",
    "InventoryIssueLineImmutableError",
    "InventoryReversalTargetError",
]
