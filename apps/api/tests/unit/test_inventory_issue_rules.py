"""Inventory Slice 4 pure issue/return rules (Issue #236;
docs/04-domain/inventory.md §14) — no database."""

import uuid

import pytest

from app.inventory.issues import IssueLineRequest, build_line_balances, normalize_line_requests
from app.inventory.lifecycle import (
    InsufficientStockError,
    InvalidInventoryDataError,
    IssueCancelledError,
    IssueFullyReturnedError,
    IssueLineOutstandingError,
    IssueLineRemovedError,
    ReturnExceedsOutstandingError,
    allocate_quantity,
    ensure_issue_changeable,
    ensure_line_removable,
    ensure_returnable,
    outstanding_quantity,
    validate_recipient_type,
)
from app.inventory.vocabulary import (
    CANONICAL_ISSUE_STATUSES,
    CANONICAL_RECIPIENT_TYPES,
    ISSUE_LINE_MOVEMENT_TYPES,
)

A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
C = uuid.UUID("00000000-0000-0000-0000-00000000000c")


def test_vocabularies() -> None:
    assert CANONICAL_ISSUE_STATUSES == {"issued", "cancelled"}
    assert CANONICAL_RECIPIENT_TYPES == {"member", "instructor", "group"}
    assert ISSUE_LINE_MOVEMENT_TYPES == {"issue", "return"}


# --- allocation ------------------------------------------------------------------------


def test_allocation_takes_the_largest_stock_first() -> None:
    """The PO example: A = 7, B = 5, issue 10 -> A 7, B 3."""
    assert allocate_quantity([(B, 5), (A, 7)], 10) == [(A, 7), (B, 3)]


def test_allocation_breaks_ties_by_the_smaller_location_id() -> None:
    assert allocate_quantity([(C, 4), (B, 4), (A, 4)], 6) == [(A, 4), (B, 2)]


def test_allocation_from_one_location_when_it_suffices() -> None:
    assert allocate_quantity([(A, 3), (B, 9)], 9) == [(B, 9)]


def test_allocation_uses_every_location_for_the_whole_stock() -> None:
    assert allocate_quantity([(A, 2), (B, 3), (C, 1)], 6) == [(B, 3), (A, 2), (C, 1)]


def test_allocation_ignores_empty_locations() -> None:
    assert allocate_quantity([(A, 0), (B, 2)], 2) == [(B, 2)]


def test_allocation_is_deterministic_for_any_input_order() -> None:
    stock = [(A, 5), (B, 5), (C, 2)]
    expected = allocate_quantity(stock, 11)
    assert allocate_quantity(list(reversed(stock)), 11) == expected == [(A, 5), (B, 5), (C, 1)]


def test_allocation_refuses_more_than_the_total_stock() -> None:
    with pytest.raises(InsufficientStockError) as raised:
        allocate_quantity([(A, 7), (B, 5)], 13)
    assert (raised.value.available, raised.value.requested) == (12, 13)
    with pytest.raises(InsufficientStockError):
        allocate_quantity([], 1)


@pytest.mark.parametrize("requested", [0, -1, True])
def test_allocation_needs_a_positive_quantity(requested) -> None:
    with pytest.raises(InvalidInventoryDataError):
        allocate_quantity([(A, 5)], requested)


# --- remaining / returns ---------------------------------------------------------------


def test_outstanding_quantity_over_repeated_partial_returns() -> None:
    """10 issued -> 6 returned -> 3 -> 1 -> nothing left."""
    returned = 0
    for step, left in ((6, 4), (3, 1), (1, 0)):
        returned += step
        assert outstanding_quantity(issued=10, returned=returned) == left


def test_outstanding_quantity_never_negative() -> None:
    with pytest.raises(ValueError):
        outstanding_quantity(issued=3, returned=4)


def test_return_up_to_the_outstanding_quantity() -> None:
    ensure_returnable(outstanding=4, requested=4)
    ensure_returnable(outstanding=4, requested=1)
    with pytest.raises(ReturnExceedsOutstandingError):
        ensure_returnable(outstanding=4, requested=5)
    with pytest.raises(ReturnExceedsOutstandingError):
        ensure_returnable(outstanding=0, requested=1)
    with pytest.raises(InvalidInventoryDataError):
        ensure_returnable(outstanding=4, requested=0)


# --- issue status ----------------------------------------------------------------------


def test_only_an_active_issue_with_outstanding_property_is_changeable() -> None:
    ensure_issue_changeable(status="issued", has_outstanding=True)
    with pytest.raises(IssueFullyReturnedError):
        ensure_issue_changeable(status="issued", has_outstanding=False)
    with pytest.raises(IssueCancelledError):
        ensure_issue_changeable(status="cancelled", has_outstanding=False)


@pytest.mark.parametrize("value", sorted(CANONICAL_RECIPIENT_TYPES))
def test_recipient_types(value: str) -> None:
    assert validate_recipient_type(value) == value


@pytest.mark.parametrize("value", ["guardian", "person", "", "Member"])
def test_unknown_recipient_type_is_rejected(value: str) -> None:
    with pytest.raises(InvalidInventoryDataError):
        validate_recipient_type(value)


# --- line requests ---------------------------------------------------------------------


def test_valid_line_requests() -> None:
    lines = [
        IssueLineRequest(item_id=A, quantity=2),
        IssueLineRequest(item_id=B, instance_ids=(C,)),
    ]
    assert normalize_line_requests(lines) == lines


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [IssueLineRequest(item_id=A, quantity=1), IssueLineRequest(item_id=A, quantity=2)],
        [IssueLineRequest(item_id=A)],
        [IssueLineRequest(item_id=A, quantity=1, instance_ids=(B,))],
        [IssueLineRequest(item_id=A, quantity=0)],
        [IssueLineRequest(item_id=A, instance_ids=(C, C))],
        [
            IssueLineRequest(item_id=A, instance_ids=(C,)),
            IssueLineRequest(item_id=B, instance_ids=(C,)),
        ],
    ],
)
def test_invalid_line_requests(lines) -> None:
    with pytest.raises(InvalidInventoryDataError):
        normalize_line_requests(lines)


# --- balances --------------------------------------------------------------------------


def test_quantity_line_balance() -> None:
    line = uuid.uuid4()
    balances = build_line_balances(
        [(line, A, "quantity")],
        [(line, None, "issue", 2, 10), (line, None, "return", 2, 9)],
    )
    balance = balances[line]
    assert (balance.issued, balance.returned, balance.outstanding) == (10, 9, 1)
    assert balance.outstanding_instance_ids == []


def test_instance_line_balance_tracks_each_instance() -> None:
    """X issued and returned, Y issued, Z issued, returned and re-issued."""
    line = uuid.uuid4()
    x, y, z = sorted(uuid.uuid4() for _ in range(3))
    balances = build_line_balances(
        [(line, A, "instance")],
        [
            (line, x, "issue", 1, None),
            (line, x, "return", 1, None),
            (line, y, "issue", 1, None),
            (line, z, "issue", 2, None),
            (line, z, "return", 1, None),
        ],
    )
    balance = balances[line]
    assert (balance.issued, balance.returned, balance.outstanding) == (4, 2, 2)
    assert balance.outstanding_instance_ids == sorted([y, z])


def test_line_without_movements_has_nothing_outstanding() -> None:
    line = uuid.uuid4()
    assert build_line_balances([(line, A, "quantity")], [])[line].outstanding == 0


# --- line removal ----------------------------------------------------------------------


def test_only_a_line_with_nothing_outstanding_is_removable() -> None:
    ensure_line_removable(removed=False, outstanding=0)
    with pytest.raises(IssueLineOutstandingError) as raised:
        ensure_line_removable(removed=False, outstanding=4)
    assert raised.value.outstanding == 4


def test_a_removed_line_is_not_removable_again() -> None:
    with pytest.raises(IssueLineRemovedError):
        ensure_line_removable(removed=True, outstanding=0)
