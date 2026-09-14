"""ClubMembership lifecycle validation (Issue #62).

Canonical sources: Issue #62 "Accepted decisions -> ClubMembership
lifecycle" (the explicit, PO/architect-accepted transition graph — not
inferred from prose), docs/02-requirements/business-rules.md §4.

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.events.lifecycle's dependency direction exactly.

The transition graph below is the literal, accepted graph from Issue #62:
`pending -> active`, `pending -> archived`, `active -> suspended`,
`active -> inactive`, `active -> archived`, `suspended -> active`,
`suspended -> inactive`, `suspended -> archived`, `inactive -> archived`.
`archived` is terminal. Explicitly prohibited (not merely omitted):
`pending -> suspended`, `pending -> inactive`, `inactive -> active`, and
any transition out of `archived`.

One `ClubMembership` row represents one continuous membership period:
rejoining after `inactive` creates a **new** `ClubMembership` row (see
app.people.service.create_membership) rather than transitioning the old
row back to `active` — `inactive -> active` is deliberately not in the
graph below for this reason, not an oversight.
"""

CANONICAL_MEMBERSHIP_STATUSES: frozenset[str] = frozenset(
    {"pending", "active", "suspended", "inactive", "archived"}
)

# Statuses that represent a membership "ending" (matching Issue #62 §18's
# recommendation to emit `membership.ended` specifically for the
# `left_at`-setting transition, rather than for every status change).
ENDING_STATUSES: frozenset[str] = frozenset({"inactive", "archived"})

ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"active", "archived"}),
    "active": frozenset({"suspended", "inactive", "archived"}),
    "suspended": frozenset({"active", "inactive", "archived"}),
    "inactive": frozenset({"archived"}),
    "archived": frozenset(),
}


class MembershipDomainError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidMembershipStatusError(MembershipDomainError):
    """`value` is not one of CANONICAL_MEMBERSHIP_STATUSES."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical membership status")
        self.value = value


class InvalidMembershipStatusTransitionError(MembershipDomainError):
    """The accepted transition graph (see module docstring) does not allow
    this (from_status -> to_status) transition."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"{from_status!r} -> {to_status!r} is not an allowed transition")
        self.from_status = from_status
        self.to_status = to_status


class OverlappingActiveMembershipError(MembershipDomainError):
    """database-schema.md §5.4: an active membership already exists for
    this (person_id, membership_type) with an overlapping validity
    interval — enforced by the `ck_club_memberships_no_overlapping_active`
    GiST exclusion constraint, caught and re-raised as this typed error
    by app.people.service.create_membership."""


class InvalidMembershipDatesError(MembershipDomainError):
    """`left_at` would be set before `joined_at` — enforced by the
    `ck_club_memberships_left_at_after_joined_at` CHECK constraint."""


def validate_membership_status(value: str) -> None:
    if value not in CANONICAL_MEMBERSHIP_STATUSES:
        raise InvalidMembershipStatusError(value)


def validate_membership_status_transition(current_status: str, new_status: str) -> None:
    """Raises InvalidMembershipStatusError if either status is not
    canonical, or InvalidMembershipStatusTransitionError if the transition
    is not one of the allowed edges.
    """
    validate_membership_status(current_status)
    validate_membership_status(new_status)
    if new_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidMembershipStatusTransitionError(current_status, new_status)


__all__ = [
    "CANONICAL_MEMBERSHIP_STATUSES",
    "ENDING_STATUSES",
    "ALLOWED_STATUS_TRANSITIONS",
    "MembershipDomainError",
    "InvalidMembershipStatusError",
    "InvalidMembershipStatusTransitionError",
    "OverlappingActiveMembershipError",
    "InvalidMembershipDatesError",
    "validate_membership_status",
    "validate_membership_status_transition",
]
