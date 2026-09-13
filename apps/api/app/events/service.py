"""Canonical Club-ownership validation for EventStaffAssignment writes
(ADR-0022, ADR-0023 §1) — Issue #48 — and for EventGroupTarget writes
(ADR-0022, ADR-0023 §2) — Issue #49.

Pure Python + SQLAlchemy — no FastAPI import, no permission/scope check
(authorization stays a separate layer; see ADR-0022 §5: a role
assignment may grant permission to *attempt* an operation, but it never
substitutes for the Club-relationship invariant enforced here). This is
the single, shared mechanism ADR-0022 §3 requires for each of these two
relationships: any write path for EventStaffAssignment or
EventGroupTarget — a future Event API included — must call
create_event_staff_assignment()/create_event_group_target() below
rather than construct the ORM row directly or re-implement an
equivalent check per caller. This follows the same shape ADR-0022 §7
anticipated for Event relationships, already used by app.groups.service
for GroupMembership/GroupInstructorAssignment: resolve each side's
Club, lock the row(s) the decision depends on, compare, then write in
the same transaction.

Ownership check and write happen inside one transaction. For
EventStaffAssignment, the Event row is read with `SELECT ... FOR SHARE`
(`with_for_update(read=True)`), and the "does this User's Person have
an active ClubMembership in this Club?" half of the check is the shared
app.authorization.club_ownership.user_has_active_club_membership
function (also used by app.groups.service). For EventGroupTarget, both
the Event row and the Group row are read with `SELECT ... FOR SHARE` —
mirroring app.groups.service.create_group_membership's own
Group-vs-ClubMembership comparison exactly, including locking the
"other side" entity (Group, a different bounded context/module) with a
small private helper defined right here rather than importing
app.groups.service's own private `_lock_group_club_id` — the same
choice app.groups.service itself already made for `ClubMembership`
(also a different module), so this introduces no new shared
infrastructure, only the third application of an already-reviewed
pattern. Under PostgreSQL's default READ COMMITTED isolation, `FOR
SHARE` blocks a concurrent writer until this transaction ends and
re-reads the now-committed row before the check proceeds, so neither a
stale read nor a lost-update race is possible for either invariant —
see tests/integration/test_ownership_concurrency.py for the
real-Postgres proof of this for EventStaffAssignment, and the
EventGroupTarget-specific concurrency tests added for Issue #49.

The separate "at most one active primary assignment per Event"
invariant (ADR-0023 §1) is enforced by a GiST exclusion constraint on
`event_staff_assignments` itself (see app.db.events.EventStaffAssignment)
rather than by application-level locking: PostgreSQL rejects a
conflicting insert atomically regardless of transaction interleaving,
which is a stronger guarantee than anything this service layer could
add. `create_event_staff_assignment` below catches that specific
constraint violation and raises a typed
EventStaffAssignmentPrimaryConflictError instead of letting a raw
IntegrityError propagate. ADR-0023 §2 defines no equivalent overlap-
prevention rule for EventGroupTarget, so no such constraint or catch
exists for `create_event_group_target`.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.authorization.club_ownership import user_has_active_club_membership
from app.db.events import Event, EventGroupTarget, EventStaffAssignment
from app.db.groups import Group

_ONE_ACTIVE_PRIMARY_CONSTRAINT = "ck_event_staff_assignments_one_active_primary"


class EventStaffAssignmentError(Exception):
    """Base class for this module's typed, expected failures."""


class EventStaffClubMembershipMissingError(EventStaffAssignmentError):
    """ADR-0022 §7 / ADR-0023 §1: the assigned User's Person has no
    active ClubMembership in the Event's Club. A global `instructor`
    role assignment never substitutes for this — this error is raised
    regardless of any role the User holds.
    """

    def __init__(self, *, user_id: uuid.UUID, club_id: uuid.UUID) -> None:
        super().__init__(f"User {user_id} has no active ClubMembership in club {club_id}")
        self.user_id = user_id
        self.club_id = club_id


class EventStaffAssignmentPrimaryConflictError(EventStaffAssignmentError):
    """ADR-0023 §1: at most one active `is_primary=true` assignment may
    exist for an Event at a given point in time; this assignment's
    validity interval overlaps an existing active primary assignment
    for the same Event.
    """

    def __init__(self, *, event_id: uuid.UUID) -> None:
        super().__init__(
            f"Event {event_id} already has an active primary EventStaffAssignment "
            "overlapping this validity interval"
        )
        self.event_id = event_id


class EventGroupTargetError(Exception):
    """Base class for this module's typed, expected failures."""


class EventGroupTargetClubMismatchError(EventGroupTargetError):
    """ADR-0022 §7 / ADR-0023 §2: `Event.club_id` must equal
    `Group.club_id`.
    """

    def __init__(self, *, event_club_id: uuid.UUID, group_club_id: uuid.UUID) -> None:
        super().__init__(
            f"Event's club {event_club_id} does not match Group's club {group_club_id}"
        )
        self.event_club_id = event_club_id
        self.group_club_id = group_club_id


def _lock_event_club_id(session: Session, event_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        select(Event.club_id).where(Event.id == event_id).with_for_update(read=True)
    ).scalar_one()


def _lock_group_club_id(session: Session, group_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        select(Group.club_id).where(Group.id == group_id).with_for_update(read=True)
    ).scalar_one()


def _is_one_active_primary_violation(exc: IntegrityError) -> bool:
    """True only for a violation of
    `ck_event_staff_assignments_one_active_primary` — never for an
    unrelated IntegrityError, which must keep propagating unchanged.
    """
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _ONE_ACTIVE_PRIMARY_CONSTRAINT


def create_event_staff_assignment(
    session: Session,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    role_in_event: str,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    is_primary: bool = False,
) -> EventStaffAssignment:
    """ADR-0022 §7 / ADR-0023 §1: create an EventStaffAssignment only
    when the User's Person has an active ClubMembership in the target
    Event's Club.

    Raises EventStaffClubMembershipMissingError, and persists nothing,
    when that relationship is absent — regardless of any global
    `instructor` role the User may hold. Raises
    EventStaffAssignmentPrimaryConflictError, and persists nothing, when
    `is_primary=True` and its validity interval overlaps an existing
    active primary assignment for the same Event. The ownership check
    and the write happen in one transaction — see the module docstring
    for the concurrency rationale.
    """
    event_club_id = _lock_event_club_id(session, event_id)
    if not user_has_active_club_membership(session, user_id=user_id, club_id=event_club_id):
        session.rollback()
        raise EventStaffClubMembershipMissingError(user_id=user_id, club_id=event_club_id)

    assignment = EventStaffAssignment(
        event_id=event_id,
        user_id=user_id,
        role_in_event=role_in_event,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(assignment)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_one_active_primary_violation(exc):
            raise EventStaffAssignmentPrimaryConflictError(event_id=event_id) from exc
        raise
    return assignment


def create_event_group_target(
    session: Session,
    *,
    event_id: uuid.UUID,
    group_id: uuid.UUID,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
) -> EventGroupTarget:
    """ADR-0022 §7 / ADR-0023 §2: create an EventGroupTarget only when
    `Event.club_id == Group.club_id`.

    Raises EventGroupTargetClubMismatchError, and persists nothing, on a
    mismatch. The ownership check and the write happen in one
    transaction — see the module docstring for the concurrency
    rationale. Targeting only links Event and Group; it never creates
    or touches EventParticipation.
    """
    event_club_id = _lock_event_club_id(session, event_id)
    group_club_id = _lock_group_club_id(session, group_id)
    if event_club_id != group_club_id:
        session.rollback()
        raise EventGroupTargetClubMismatchError(
            event_club_id=event_club_id, group_club_id=group_club_id
        )

    target = EventGroupTarget(
        event_id=event_id,
        group_id=group_id,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(target)
    session.commit()
    return target


__all__ = [
    "EventStaffAssignmentError",
    "EventStaffClubMembershipMissingError",
    "EventStaffAssignmentPrimaryConflictError",
    "create_event_staff_assignment",
    "EventGroupTargetError",
    "EventGroupTargetClubMismatchError",
    "create_event_group_target",
]
