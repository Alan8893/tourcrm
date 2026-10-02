"""Inventory Foundation domain rules (docs/04-domain/inventory.md).

Pure validation — no ORM, FastAPI or authorization import (mirroring
app.news.lifecycle). The DB-aware facts these rules need (does the item
already have a movement? is the referenced record archived?) are resolved
by app.inventory.service and passed in.
"""

import uuid
from collections.abc import Iterable

from app.inventory.vocabulary import (
    ACCOUNTING_MODE_INSTANCE,
    ACCOUNTING_MODE_QUANTITY,
    ACTIVE,
    ARCHIVED,
    CANONICAL_ACCOUNTING_MODES,
    CANONICAL_RECIPIENT_TYPES,
    INSTANCE_AVAILABLE,
    INSTANCE_IN_REPAIR,
    INSTANCE_ISSUED,
    INSTANCE_WRITTEN_OFF,
    ISSUE_CANCELLED,
    MOVEMENT_ISSUE,
    MOVEMENT_REPAIR_END,
    MOVEMENT_REPAIR_START,
    MOVEMENT_RETURN,
    MOVEMENT_TRANSFER,
    MOVEMENT_WRITE_OFF,
    MOVEMENT_WRITEOFF_REVERSAL,
)


class InventoryDomainError(Exception):
    """Base class for Inventory validation failures."""


class InvalidInventoryDataError(InventoryDomainError):
    """A field value is missing or out of range."""


class InventoryRecordArchivedError(InventoryDomainError):
    """G13: archived records are read-only."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"Archived {kind} is read-only")
        self.kind = kind


class ArchivedReferenceError(InventoryDomainError):
    """G12: an archived category/unit/location cannot be chosen for new use."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"Archived {kind} cannot be selected")
        self.kind = kind


class SystemUnitImmutableError(InventoryDomainError):
    """G11: system units cannot be changed or archived."""

    def __init__(self) -> None:
        super().__init__("System units cannot be changed or archived")


class AccountingModeLockedError(InventoryDomainError):
    """G3: accounting mode is fixed after the first movement."""

    def __init__(self) -> None:
        super().__init__("Accounting mode cannot change after the item's first movement")


class UnitLockedError(InventoryDomainError):
    """G14: the unit is fixed after the first movement."""

    def __init__(self) -> None:
        super().__init__("Unit cannot change after the item's first movement")


class LocationHasActiveChildrenError(InventoryDomainError):
    """G4: a location with active child locations cannot be archived."""

    def __init__(self) -> None:
        super().__init__("Storage location has active child locations")


class InvalidLocationParentError(InventoryDomainError):
    """The requested parent would make the location its own ancestor."""

    def __init__(self) -> None:
        super().__init__("A storage location cannot be placed under itself or its descendant")


class ItemNotInstanceModeError(InventoryDomainError):
    """Instances exist only for `instance`-mode nomenclature (§7)."""

    def __init__(self) -> None:
        super().__init__("Instances can be created only for instance-mode items")


class ItemNotQuantityModeError(InventoryDomainError):
    """Stock movements exist only for `quantity`-mode nomenclature (§6)."""

    def __init__(self) -> None:
        super().__init__("Stock operations are available only for quantity-mode items")


class InsufficientStockError(InventoryDomainError):
    """§6: stock of a location can never become negative."""

    def __init__(self, available: int, requested: int) -> None:
        super().__init__(
            f"Insufficient stock: {available} available, {requested} requested"
        )
        self.available = available
        self.requested = requested


class ItemHasStockError(InventoryDomainError):
    """§17 п.4: a quantity item with non-zero stock cannot be archived."""

    def __init__(self) -> None:
        super().__init__("Item has non-zero stock")


class LocationHasStockError(InventoryDomainError):
    """§17 п.5: a storage location holding quantity stock cannot be archived."""

    def __init__(self) -> None:
        super().__init__("Storage location holds non-zero quantity stock")


class ItemHasActiveInstancesError(InventoryDomainError):
    """§17 п.4: an item with instances not in `written_off` cannot be archived."""

    def __init__(self) -> None:
        super().__init__("Item has instances that are not written off")


class LocationHasInstancesError(InventoryDomainError):
    """§10 п.4 (GAP-3): a location holding available/in_repair instances
    cannot be archived."""

    def __init__(self) -> None:
        super().__init__("Storage location holds available or in-repair instances")


class InvalidInstanceTransitionError(InventoryDomainError):
    """§7.4: the movement is not allowed from the instance's current state."""

    def __init__(self, state: str, movement_type: str) -> None:
        super().__init__(f"Movement {movement_type!r} is not allowed for an instance in {state!r}")
        self.state = state
        self.movement_type = movement_type


class InstanceWrittenOffError(InventoryDomainError):
    """§7.5 (R5): a written-off instance is read-only."""

    def __init__(self) -> None:
        super().__init__("Written-off instance is read-only")


class InvalidReversalTargetError(InventoryDomainError):
    """E: only an instance `write_off` can be reversed in Slice 2."""

    def __init__(self) -> None:
        super().__init__("Only an instance write-off movement can be reversed")


class WriteOffAlreadyReversedError(InventoryDomainError):
    """E: a write-off can be reversed only once."""

    def __init__(self) -> None:
        super().__init__("This write-off has already been reversed")


class ReversalLocationRequiredError(InventoryDomainError):
    """§16 п.3 (GAP-2): the original location is archived — another active
    location must be given."""

    def __init__(self) -> None:
        super().__init__(
            "The original storage location is archived; an active storage_location_id is required"
        )


class ReversalLocationNotAllowedError(InventoryDomainError):
    """§16 п.3 (GAP-2): the original location is active — the instance
    always returns there."""

    def __init__(self) -> None:
        super().__init__("The original storage location is active; the instance returns there")


class ItemHasOutstandingIssuesError(InventoryDomainError):
    """§17 п.4: an item with property still issued cannot be archived."""

    def __init__(self) -> None:
        super().__init__("Item has property that is still issued")


class IssueCancelledError(InventoryDomainError):
    """§14 (Slice 4): a cancelled issue is immutable."""

    def __init__(self) -> None:
        super().__init__("The issue is cancelled and cannot be changed")


class IssueFullyReturnedError(InventoryDomainError):
    """§14 (Slice 4): a fully returned issue is immutable and cannot be
    cancelled."""

    def __init__(self) -> None:
        super().__init__("Everything on this issue has been returned; it cannot be changed")


class ReturnExceedsOutstandingError(InventoryDomainError):
    """§14: more cannot be returned than is still issued on the line."""

    def __init__(self, outstanding: int, requested: int) -> None:
        super().__init__(
            f"Cannot return {requested}: only {outstanding} still issued on this line"
        )
        self.outstanding = outstanding
        self.requested = requested


class InstanceNotOutstandingError(InventoryDomainError):
    """§14: only an instance still issued on this issue can be returned or
    reported lost."""

    def __init__(self, instance_id: uuid.UUID) -> None:
        super().__init__(f"Instance {instance_id} is not issued on this issue")
        self.instance_id = instance_id


class IssueLineOutstandingError(InventoryDomainError):
    """§14: a line can be removed from an issue only when nothing on it is
    still issued."""

    def __init__(self, outstanding: int) -> None:
        super().__init__(f"The line still has {outstanding} issued; return it first")
        self.outstanding = outstanding


class IssueLineRemovedError(InventoryDomainError):
    """§14: a removed line is history only — it cannot be removed again or
    used by any further operation."""

    def __init__(self) -> None:
        super().__init__("The line has been removed from the issue")


class InvalidRecipientError(InventoryDomainError):
    """§14: the recipient is not a valid Member, Instructor or Group of the
    Club."""

    def __init__(self, recipient_type: str) -> None:
        super().__init__(f"The {recipient_type} recipient is not valid for this Club")
        self.recipient_type = recipient_type


# §7.4: movement -> (states it may start from, resulting state or None when
# the state is kept). `receipt` creates the instance and is not listed.
# `issue`/`return` (Slice 4, §14): available -> issued -> available.
INSTANCE_TRANSITIONS: dict[str, tuple[frozenset[str], str | None]] = {
    MOVEMENT_ISSUE: (frozenset({INSTANCE_AVAILABLE}), INSTANCE_ISSUED),
    MOVEMENT_RETURN: (frozenset({INSTANCE_ISSUED}), INSTANCE_AVAILABLE),
    MOVEMENT_TRANSFER: (frozenset({INSTANCE_AVAILABLE, INSTANCE_IN_REPAIR}), None),
    MOVEMENT_REPAIR_START: (frozenset({INSTANCE_AVAILABLE}), INSTANCE_IN_REPAIR),
    MOVEMENT_REPAIR_END: (frozenset({INSTANCE_IN_REPAIR}), INSTANCE_AVAILABLE),
    MOVEMENT_WRITE_OFF: (frozenset({INSTANCE_AVAILABLE, INSTANCE_IN_REPAIR}), INSTANCE_WRITTEN_OFF),
    MOVEMENT_WRITEOFF_REVERSAL: (frozenset({INSTANCE_WRITTEN_OFF}), INSTANCE_AVAILABLE),
}


def next_instance_state(state: str, movement_type: str) -> str:
    """The instance state after `movement_type`; raises when the movement
    is not allowed from `state`."""
    allowed_from, target = INSTANCE_TRANSITIONS[movement_type]
    if state not in allowed_from:
        raise InvalidInstanceTransitionError(state, movement_type)
    return state if target is None else target


def ensure_instance_editable(state: str) -> None:
    if state == INSTANCE_WRITTEN_OFF:
        raise InstanceWrittenOffError()


def ensure_quantity_mode(accounting_mode: str) -> None:
    if accounting_mode != ACCOUNTING_MODE_QUANTITY:
        raise ItemNotQuantityModeError()


def validate_quantity(value: int) -> int:
    """§6 п.1: a whole, positive number of units."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidInventoryDataError("quantity must be a positive integer")
    return value


def ensure_sufficient_stock(*, available: int, requested: int) -> None:
    if requested > available:
        raise InsufficientStockError(available, requested)


def allocate_quantity(
    stock: Iterable[tuple[uuid.UUID, int]], requested: int
) -> list[tuple[uuid.UUID, int]]:
    """§14 (Slice 4): spread `requested` over the item's stock, taking first
    from the location with the most stock, ties broken by the smaller
    location id. Returns `(location_id, quantity)` pairs in that order;
    raises when the total stock is short. Deterministic for equal input."""
    amount = validate_quantity(requested)
    ordered = sorted(
        ((location_id, quantity) for location_id, quantity in stock if quantity > 0),
        key=lambda pair: (-pair[1], pair[0]),
    )
    total = sum(quantity for _, quantity in ordered)
    ensure_sufficient_stock(available=total, requested=amount)
    allocation: list[tuple[uuid.UUID, int]] = []
    remaining = amount
    for location_id, quantity in ordered:
        if remaining == 0:
            break
        take = min(quantity, remaining)
        allocation.append((location_id, take))
        remaining -= take
    return allocation


def outstanding_quantity(*, issued: int, returned: int) -> int:
    """§14: what is still issued on a quantity line."""
    if issued < 0 or returned < 0 or returned > issued:
        raise ValueError("returned quantity cannot exceed the issued quantity")
    return issued - returned


def ensure_returnable(*, outstanding: int, requested: int) -> None:
    validate_quantity(requested)
    if requested > outstanding:
        raise ReturnExceedsOutstandingError(outstanding, requested)


def ensure_issue_changeable(*, status: str, has_outstanding: bool) -> None:
    """§14 (Slice 4): an issue can be edited, extended, returned against,
    cancelled or reported lost only while it is not cancelled and still
    holds issued property. Cancelled and fully returned issues are
    immutable."""
    if status == ISSUE_CANCELLED:
        raise IssueCancelledError()
    if not has_outstanding:
        raise IssueFullyReturnedError()


def ensure_line_removable(*, removed: bool, outstanding: int) -> None:
    """§14: a line leaves the issue's working composition only once and only
    when nothing on it is still issued."""
    if removed:
        raise IssueLineRemovedError()
    if outstanding > 0:
        raise IssueLineOutstandingError(outstanding)


def validate_recipient_type(value: str) -> str:
    if value not in CANONICAL_RECIPIENT_TYPES:
        raise InvalidInventoryDataError(
            f"recipient_type must be one of {sorted(CANONICAL_RECIPIENT_TYPES)}"
        )
    return value


def ensure_item_has_no_outstanding_issues(*, has_outstanding_issues: bool) -> None:
    if has_outstanding_issues:
        raise ItemHasOutstandingIssuesError()


def ensure_item_has_no_stock(*, total_stock: int) -> None:
    if total_stock > 0:
        raise ItemHasStockError()


def ensure_location_has_no_stock(*, has_stock: bool) -> None:
    if has_stock:
        raise LocationHasStockError()


def ensure_instance_mode(accounting_mode: str) -> None:
    if accounting_mode != ACCOUNTING_MODE_INSTANCE:
        raise ItemNotInstanceModeError()


def format_inventory_number(sequence: int) -> str:
    """Q2: `INV-000123`; widens past six digits (`INV-1000000`)."""
    if sequence < 1:
        raise ValueError("inventory number sequence starts at 1")
    return f"INV-{sequence:06d}"


def normalize_optional_text(value: str | None, *, max_length: int, field: str) -> str | None:
    """Trim; an empty value clears the field."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise InvalidInventoryDataError(f"{field} must be at most {max_length} characters")
    return text


def normalize_write_off_reason(value: str, *, max_length: int) -> str:
    """Q8: the write-off reason is mandatory free text."""
    reason = normalize_optional_text(value, max_length=max_length, field="comment")
    if reason is None:
        raise InvalidInventoryDataError("comment (write-off reason) is required")
    return reason


def ensure_location_has_no_instances(*, has_instances: bool) -> None:
    if has_instances:
        raise LocationHasInstancesError()


def ensure_item_has_no_active_instances(*, has_active_instances: bool) -> None:
    if has_active_instances:
        raise ItemHasActiveInstancesError()


def normalize_name(value: str, *, max_length: int) -> str:
    name = value.strip()
    if not name:
        raise InvalidInventoryDataError("name must not be empty")
    if len(name) > max_length:
        raise InvalidInventoryDataError(f"name must be at most {max_length} characters")
    return name


def validate_accounting_mode(value: str) -> str:
    if value not in CANONICAL_ACCOUNTING_MODES:
        raise InvalidInventoryDataError(
            f"accounting_mode must be one of {sorted(CANONICAL_ACCOUNTING_MODES)}"
        )
    return value


def validate_cost_minor(value: int | None) -> int | None:
    """inventory.md §11: integer kopecks, `>= 0`, optional."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidInventoryDataError("current_cost_minor must be a non-negative integer")
    return value


def ensure_editable(status: str, *, kind: str) -> None:
    """G13: an archived record cannot be edited or archived again."""
    if status == ARCHIVED:
        raise InventoryRecordArchivedError(kind)


def ensure_unit_mutable(*, is_system: bool) -> None:
    if is_system:
        raise SystemUnitImmutableError()


def ensure_selectable(status: str, *, kind: str) -> None:
    """G12: an archived reference cannot be chosen for new use."""
    if status != ACTIVE:
        raise ArchivedReferenceError(kind)


def ensure_accounting_mode_change_allowed(
    *, current: str, requested: str, has_movements: bool
) -> None:
    if requested != current and has_movements:
        raise AccountingModeLockedError()


def ensure_unit_change_allowed(*, current: str, requested: str, has_movements: bool) -> None:
    if requested != current and has_movements:
        raise UnitLockedError()


def ensure_location_archivable(*, has_active_children: bool) -> None:
    if has_active_children:
        raise LocationHasActiveChildrenError()


__all__ = [
    "InventoryDomainError",
    "InvalidInventoryDataError",
    "InventoryRecordArchivedError",
    "ArchivedReferenceError",
    "SystemUnitImmutableError",
    "AccountingModeLockedError",
    "UnitLockedError",
    "LocationHasActiveChildrenError",
    "InvalidLocationParentError",
    "ItemNotInstanceModeError",
    "ItemNotQuantityModeError",
    "InsufficientStockError",
    "ItemHasStockError",
    "LocationHasStockError",
    "ensure_quantity_mode",
    "validate_quantity",
    "ensure_sufficient_stock",
    "ensure_item_has_no_stock",
    "ensure_location_has_no_stock",
    "ItemHasActiveInstancesError",
    "LocationHasInstancesError",
    "InvalidInstanceTransitionError",
    "InstanceWrittenOffError",
    "InvalidReversalTargetError",
    "WriteOffAlreadyReversedError",
    "ReversalLocationRequiredError",
    "ReversalLocationNotAllowedError",
    "ItemHasOutstandingIssuesError",
    "IssueCancelledError",
    "IssueFullyReturnedError",
    "ReturnExceedsOutstandingError",
    "InstanceNotOutstandingError",
    "IssueLineOutstandingError",
    "IssueLineRemovedError",
    "InvalidRecipientError",
    "allocate_quantity",
    "outstanding_quantity",
    "ensure_returnable",
    "ensure_issue_changeable",
    "ensure_line_removable",
    "validate_recipient_type",
    "ensure_item_has_no_outstanding_issues",
    "INSTANCE_TRANSITIONS",
    "next_instance_state",
    "ensure_instance_editable",
    "ensure_instance_mode",
    "format_inventory_number",
    "normalize_optional_text",
    "normalize_write_off_reason",
    "ensure_location_has_no_instances",
    "ensure_item_has_no_active_instances",
    "normalize_name",
    "validate_accounting_mode",
    "validate_cost_minor",
    "ensure_editable",
    "ensure_unit_mutable",
    "ensure_selectable",
    "ensure_accounting_mode_change_allowed",
    "ensure_unit_change_allowed",
    "ensure_location_archivable",
]
