"""Inventory Foundation domain rules (docs/04-domain/inventory.md).

Pure validation — no ORM, FastAPI or authorization import (mirroring
app.news.lifecycle). The DB-aware facts these rules need (does the item
already have a movement? is the referenced record archived?) are resolved
by app.inventory.service and passed in.
"""

from app.inventory.vocabulary import ACTIVE, ARCHIVED, CANONICAL_ACCOUNTING_MODES


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
