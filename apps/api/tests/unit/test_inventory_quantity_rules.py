"""Inventory Slice 3 pure quantity rules (Issue #230;
docs/04-domain/inventory.md §6, §17) — no database."""

import pytest

from app.inventory.lifecycle import (
    InsufficientStockError,
    InvalidInventoryDataError,
    ItemHasStockError,
    ItemNotQuantityModeError,
    LocationHasStockError,
    ensure_item_has_no_stock,
    ensure_location_has_no_stock,
    ensure_quantity_mode,
    ensure_sufficient_stock,
    validate_quantity,
)


@pytest.mark.parametrize("value", [1, 7, 1_000_000])
def test_positive_whole_quantities_are_accepted(value: int) -> None:
    assert validate_quantity(value) == value


@pytest.mark.parametrize("value", [0, -1, -100, 1.5, 2.0, True, "3", None])
def test_zero_negative_fractional_and_non_integers_are_rejected(value: object) -> None:
    with pytest.raises(InvalidInventoryDataError):
        validate_quantity(value)  # type: ignore[arg-type]


def test_only_quantity_mode_items_have_stock() -> None:
    ensure_quantity_mode("quantity")
    with pytest.raises(ItemNotQuantityModeError):
        ensure_quantity_mode("instance")


@pytest.mark.parametrize(("available", "requested"), [(5, 5), (5, 1), (7, 3)])
def test_whole_and_partial_withdrawals_within_stock(available: int, requested: int) -> None:
    ensure_sufficient_stock(available=available, requested=requested)


@pytest.mark.parametrize(("available", "requested"), [(0, 1), (5, 6), (3, 100)])
def test_withdrawal_beyond_stock_is_rejected(available: int, requested: int) -> None:
    with pytest.raises(InsufficientStockError) as raised:
        ensure_sufficient_stock(available=available, requested=requested)
    assert (raised.value.available, raised.value.requested) == (available, requested)


def test_item_with_stock_cannot_be_archived() -> None:
    ensure_item_has_no_stock(total_stock=0)
    with pytest.raises(ItemHasStockError):
        ensure_item_has_no_stock(total_stock=1)


def test_location_with_stock_cannot_be_archived() -> None:
    ensure_location_has_no_stock(has_stock=False)
    with pytest.raises(LocationHasStockError):
        ensure_location_has_no_stock(has_stock=True)
