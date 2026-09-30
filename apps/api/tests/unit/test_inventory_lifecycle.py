"""Inventory Foundation pure domain rules (TH-0121 / Issue #230;
docs/04-domain/inventory.md) — no database."""

import pytest

from app.inventory.lifecycle import (
    AccountingModeLockedError,
    ArchivedReferenceError,
    InvalidInventoryDataError,
    InventoryRecordArchivedError,
    LocationHasActiveChildrenError,
    SystemUnitImmutableError,
    UnitLockedError,
    ensure_accounting_mode_change_allowed,
    ensure_editable,
    ensure_location_archivable,
    ensure_selectable,
    ensure_unit_change_allowed,
    ensure_unit_mutable,
    normalize_name,
    validate_accounting_mode,
    validate_cost_minor,
)
from app.inventory.vocabulary import (
    CANONICAL_ACCOUNTING_MODES,
    CANONICAL_INVENTORY_STATUSES,
    CANONICAL_MOVEMENT_TYPES,
    SYSTEM_UNIT_NAMES,
)


def test_canonical_vocabularies_match_the_domain_document() -> None:
    assert CANONICAL_INVENTORY_STATUSES == {"active", "archived"}
    assert CANONICAL_ACCOUNTING_MODES == {"quantity", "instance"}
    assert CANONICAL_MOVEMENT_TYPES == {
        "receipt",
        "transfer",
        "issue",
        "return",
        "write_off",
        "adjustment",
        "writeoff_reversal",
    }
    assert SYSTEM_UNIT_NAMES == ("шт", "м", "комплект", "пара")


def test_normalize_name_trims_and_validates() -> None:
    assert normalize_name("  Карабин  ", max_length=255) == "Карабин"
    with pytest.raises(InvalidInventoryDataError):
        normalize_name("   ", max_length=255)
    with pytest.raises(InvalidInventoryDataError):
        normalize_name("x" * 65, max_length=64)


@pytest.mark.parametrize("mode", ["quantity", "instance"])
def test_accounting_modes_are_accepted(mode: str) -> None:
    assert validate_accounting_mode(mode) == mode


def test_unknown_accounting_mode_is_rejected() -> None:
    with pytest.raises(InvalidInventoryDataError):
        validate_accounting_mode("batch")


@pytest.mark.parametrize("value", [None, 0, 500_000, 650_000])
def test_cost_accepts_null_and_non_negative_kopecks(value: int | None) -> None:
    assert validate_cost_minor(value) == value


@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_cost_rejects_negative_fractional_and_bool(value: object) -> None:
    with pytest.raises(InvalidInventoryDataError):
        validate_cost_minor(value)  # type: ignore[arg-type]


def test_archived_record_is_read_only() -> None:
    ensure_editable("active", kind="category")
    with pytest.raises(InventoryRecordArchivedError):
        ensure_editable("archived", kind="category")


def test_archived_reference_cannot_be_selected() -> None:
    ensure_selectable("active", kind="unit")
    with pytest.raises(ArchivedReferenceError):
        ensure_selectable("archived", kind="unit")


def test_system_unit_is_immutable() -> None:
    ensure_unit_mutable(is_system=False)
    with pytest.raises(SystemUnitImmutableError):
        ensure_unit_mutable(is_system=True)


def test_unit_change_is_allowed_only_before_the_first_movement() -> None:
    ensure_unit_change_allowed(current="a", requested="b", has_movements=False)
    ensure_unit_change_allowed(current="a", requested="a", has_movements=True)
    with pytest.raises(UnitLockedError):
        ensure_unit_change_allowed(current="a", requested="b", has_movements=True)


def test_accounting_mode_change_is_allowed_only_before_the_first_movement() -> None:
    ensure_accounting_mode_change_allowed(
        current="quantity", requested="instance", has_movements=False
    )
    ensure_accounting_mode_change_allowed(
        current="quantity", requested="quantity", has_movements=True
    )
    with pytest.raises(AccountingModeLockedError):
        ensure_accounting_mode_change_allowed(
            current="quantity", requested="instance", has_movements=True
        )


def test_location_with_active_children_cannot_be_archived() -> None:
    ensure_location_archivable(has_active_children=False)
    with pytest.raises(LocationHasActiveChildrenError):
        ensure_location_archivable(has_active_children=True)
