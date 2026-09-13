"""Pure-Python unit tests for the Issue #62 ClubMembership lifecycle
module — no database, no HTTP. Real PostgreSQL CHECK constraints are
covered separately in tests/integration/test_people_api.py.
"""

import pytest

from app.people.lifecycle import (
    ALLOWED_STATUS_TRANSITIONS,
    CANONICAL_MEMBERSHIP_STATUSES,
    ENDING_STATUSES,
    InvalidMembershipStatusError,
    InvalidMembershipStatusTransitionError,
    validate_membership_status,
    validate_membership_status_transition,
)


@pytest.mark.parametrize("status", sorted(CANONICAL_MEMBERSHIP_STATUSES))
def test_every_canonical_status_is_accepted(status: str) -> None:
    validate_membership_status(status)  # must not raise


@pytest.mark.parametrize("status", ["planned", "", "ACTIVE", "left", "banned"])
def test_non_canonical_status_is_rejected(status: str) -> None:
    with pytest.raises(InvalidMembershipStatusError):
        validate_membership_status(status)


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("pending", "active"),
        ("pending", "archived"),
        ("active", "suspended"),
        ("active", "inactive"),
        ("active", "archived"),
        ("suspended", "active"),
        ("suspended", "inactive"),
        ("suspended", "archived"),
        ("inactive", "active"),
        ("inactive", "archived"),
    ],
)
def test_allowed_transitions_pass(from_status: str, to_status: str) -> None:
    validate_membership_status_transition(from_status, to_status)  # must not raise


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("pending", "suspended"),
        ("pending", "inactive"),
        ("active", "pending"),
        ("suspended", "pending"),
        ("inactive", "suspended"),
        ("inactive", "inactive"),
        ("active", "active"),
        ("archived", "active"),
        ("archived", "pending"),
        ("archived", "archived"),
    ],
)
def test_disallowed_transitions_are_rejected(from_status: str, to_status: str) -> None:
    with pytest.raises(InvalidMembershipStatusTransitionError):
        validate_membership_status_transition(from_status, to_status)


def test_archived_is_terminal() -> None:
    assert ALLOWED_STATUS_TRANSITIONS["archived"] == frozenset()


def test_ending_statuses_are_inactive_and_archived() -> None:
    assert ENDING_STATUSES == frozenset({"inactive", "archived"})
