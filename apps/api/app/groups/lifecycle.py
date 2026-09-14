"""Group / GroupMembership lifecycle validation (Issue #71, closing the
Issue #69 specification gate).

Canonical source: docs/05-api/people-api.md §14.1 / §15.1 — the PO
decision posted on Issue #69 that closes the lifecycle vocabulary
ADR-0021 §1/§2 deliberately left open ("Group lifecycle transitions
require a separate business-policy decision").

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.people.lifecycle/app.events.lifecycle exactly.

`Group.status`: `active` -> `archived` only; `archived` is terminal — no
restore/unarchive transition exists (people-api.md §14.1). `PATCH
/api/v1/groups/{group_id}` never changes `status`; only
`POST /api/v1/groups/{group_id}/archive` does, via this module.

`GroupMembership.membership_status`: `active` -> `ended` only; `ended` is
terminal (people-api.md §15.1). Only
`POST /api/v1/group-memberships/{id}/end` changes it.
"""

from app.db.groups import CANONICAL_GROUP_MEMBERSHIP_STATUSES, CANONICAL_GROUP_STATUSES

GROUP_ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"archived"}),
    "archived": frozenset(),
}

GROUP_MEMBERSHIP_ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"ended"}),
    "ended": frozenset(),
}


class GroupLifecycleError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidGroupStatusError(GroupLifecycleError):
    """`value` is not one of CANONICAL_GROUP_STATUSES."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical Group status")
        self.value = value


class InvalidGroupStatusTransitionError(GroupLifecycleError):
    """people-api.md §14.1: the transition graph does not allow this
    (from_status -> to_status) transition — in practice, always
    `archived -> archived` (repeated archive)."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"{from_status!r} -> {to_status!r} is not an allowed Group transition")
        self.from_status = from_status
        self.to_status = to_status


class InvalidGroupMembershipStatusError(GroupLifecycleError):
    """`value` is not one of CANONICAL_GROUP_MEMBERSHIP_STATUSES."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical GroupMembership membership_status")
        self.value = value


class InvalidGroupMembershipStatusTransitionError(GroupLifecycleError):
    """people-api.md §15.1: the transition graph does not allow this
    (from_status -> to_status) transition — in practice, always
    `ended -> ended` (repeated end)."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"{from_status!r} -> {to_status!r} is not an allowed GroupMembership transition"
        )
        self.from_status = from_status
        self.to_status = to_status


def validate_group_status_transition(current_status: str, new_status: str) -> None:
    """Raises InvalidGroupStatusError if either status is not canonical,
    or InvalidGroupStatusTransitionError if the transition is not one of
    the allowed edges."""
    if current_status not in CANONICAL_GROUP_STATUSES:
        raise InvalidGroupStatusError(current_status)
    if new_status not in CANONICAL_GROUP_STATUSES:
        raise InvalidGroupStatusError(new_status)
    if new_status not in GROUP_ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidGroupStatusTransitionError(current_status, new_status)


def validate_group_membership_status_transition(current_status: str, new_status: str) -> None:
    """Raises InvalidGroupMembershipStatusError if either status is not
    canonical, or InvalidGroupMembershipStatusTransitionError if the
    transition is not one of the allowed edges."""
    if current_status not in CANONICAL_GROUP_MEMBERSHIP_STATUSES:
        raise InvalidGroupMembershipStatusError(current_status)
    if new_status not in CANONICAL_GROUP_MEMBERSHIP_STATUSES:
        raise InvalidGroupMembershipStatusError(new_status)
    if new_status not in GROUP_MEMBERSHIP_ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidGroupMembershipStatusTransitionError(current_status, new_status)


__all__ = [
    "GROUP_ALLOWED_STATUS_TRANSITIONS",
    "GROUP_MEMBERSHIP_ALLOWED_STATUS_TRANSITIONS",
    "GroupLifecycleError",
    "InvalidGroupStatusError",
    "InvalidGroupStatusTransitionError",
    "InvalidGroupMembershipStatusError",
    "InvalidGroupMembershipStatusTransitionError",
    "validate_group_status_transition",
    "validate_group_membership_status_transition",
]
