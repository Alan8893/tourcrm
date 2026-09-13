"""ClubMembership lifecycle validation (Issue #62).

Canonical sources: docs/02-requirements/business-rules.md §4 (status
vocabulary + "переход между статусами не должен уничтожать историю";
"человек может повторно вступить в клуб после периода inactive"),
docs/05-api/people-api.md (as reconciled), Issue #62 §14.

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.events.lifecycle's dependency direction exactly.

Issue #62 §14 is explicit that the transition graph below is *inferred*
from business-rules.md §4's prose, not laid out as an explicit canonical
table anywhere: `pending -> active`, `active -> suspended`,
`suspended -> active`, `active -> inactive`, `suspended -> inactive`,
`inactive -> active` (re-join), and any non-archived status `-> archived`.
No transition beyond this literal list is added (in particular, no direct
`pending -> inactive`/`pending -> suspended`, and `archived` is terminal)
— extending this graph is a business-policy decision, not something this
module invents.
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
    "inactive": frozenset({"active", "archived"}),
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
    """The inferred transition graph (see module docstring) does not allow
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
