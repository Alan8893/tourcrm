"""EventSeries relationship-source mutation service (Issue #85 / TH-0082,
ADR-0030).

Canonical sources: ADR-0030 (field lists/invariants), ADR-0022 (cross-Club
ownership integrity — the enforcement pattern this module reuses
verbatim), database-schema-recurrence.md §5.

Matches app.events.service's and app.groups.service's shape exactly:
every mutating function here owns and commits its own transaction,
performs the mandatory Club-ownership check inside that same transaction
(never check-then-write across two transactions — ADR-0022 §6), records
the ADR-0030 audit action, and returns the mutated row. This module
performs no authorization/permission check itself — the caller (a future
API layer) must already have resolved and checked the applicable
canonical Event permission (event.update/event.manage) before invoking
anything here, exactly like app.events.service.

Only `create_*`/`end_*` operations are implemented. ADR-0030's audit
vocabulary reserves a `.changed` action per relationship type for a
future in-place "modify an active definition's non-identity fields"
operation, but no such operation exists anywhere in this codebase yet for
*any* interval-based relationship (EventStaffAssignment/EventGroupTarget/
GroupInstructorAssignment/GroupMembership/ClubMembership all only ever
support create + end — "closing an assignment period preserves the row"
— never an in-place field update); inventing one here, with no existing
pattern to follow and no field-level spec for what may change, would be
exactly the kind of undocumented business rule this task must not
invent. `end` followed by `create` is the established way to change an
active definition, matching every other interval-based relationship in
this codebase.

`SeriesParticipant` intentionally has no cross-Club/membership validation
here beyond ordinary FK integrity: `app.db.events.EventParticipation` —
the canonical Event participation model ADR-0030 explicitly says this
must follow — itself has no service-layer validation at all (no
`create_event_participation` function exists in app.events.service);
adding a stricter rule here than the model it is required to follow
would itself be an invented business rule.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authorization.club_ownership import user_has_active_club_membership
from app.db.event_recurrence import EventSeries
from app.db.event_recurrence_relationships import (
    SeriesGroupTarget,
    SeriesParticipant,
    SeriesStaffAssignment,
)
from app.db.groups import Group

_ONE_ACTIVE_PRIMARY_CONSTRAINT = "ck_event_series_staff_assignments_one_active_primary"


class SeriesRelationshipError(Exception):
    """Base class for this module's typed, expected failures."""


class SeriesStaffClubMembershipMissingError(SeriesRelationshipError):
    """ADR-0022 / ADR-0030: the assigned User's Person has no active
    ClubMembership in the Series' Club. A global `instructor` role
    assignment never substitutes for this."""

    def __init__(self, *, user_id: uuid.UUID, club_id: uuid.UUID) -> None:
        super().__init__(f"User {user_id} has no active ClubMembership in club {club_id}")
        self.user_id = user_id
        self.club_id = club_id


class SeriesStaffAssignmentPrimaryConflictError(SeriesRelationshipError):
    """ADR-0030: at most one active `is_primary=true` assignment may
    exist for a Series version at a given point in time; this
    assignment's validity interval overlaps an existing active primary
    assignment for the same Series version."""

    def __init__(self, *, event_series_id: uuid.UUID) -> None:
        super().__init__(
            f"EventSeries {event_series_id} already has an active primary "
            "SeriesStaffAssignment overlapping this validity interval"
        )
        self.event_series_id = event_series_id


class SeriesGroupTargetClubMismatchError(SeriesRelationshipError):
    """ADR-0022 / ADR-0030: `EventSeries.club_id` must equal
    `Group.club_id`."""

    def __init__(self, *, series_club_id: uuid.UUID, group_club_id: uuid.UUID) -> None:
        super().__init__(
            f"Series' club {series_club_id} does not match Group's club {group_club_id}"
        )
        self.series_club_id = series_club_id
        self.group_club_id = group_club_id


class InvalidSeriesRelationshipTransitionError(SeriesRelationshipError):
    """Raised by an `end_*` function when the relationship has already
    ended (`valid_to` already at or before now) — mirrors
    app.groups.service.InvalidGroupInstructorAssignmentTransitionError."""

    def __init__(self, *, relationship_id: uuid.UUID) -> None:
        super().__init__(f"Relationship {relationship_id} has already ended")
        self.relationship_id = relationship_id


def _lock_series_club_id(session: Session, event_series_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        select(EventSeries.club_id)
        .where(EventSeries.id == event_series_id)
        .with_for_update(read=True)
    ).scalar_one()


def _lock_group_club_id(session: Session, group_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        select(Group.club_id).where(Group.id == group_id).with_for_update(read=True)
    ).scalar_one()


def _is_one_active_primary_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _ONE_ACTIVE_PRIMARY_CONSTRAINT


def snapshot_copy_series_relationships(
    session: Session, *, source_series_id: uuid.UUID, target_series_id: uuid.UUID
) -> None:
    """ADR-0030 / database-schema-recurrence.md §5.4: "Creating a
    successor Series version creates a complete snapshot of the
    predecessor's relationship definitions ... before any successor-
    specific relationship change is applied." Copies *every*
    `SeriesStaffAssignment`/`SeriesGroupTarget`/`SeriesParticipant` row
    belonging to `source_series_id` — historical (already-ended) rows
    included, not only currently-active ones — into new rows owned by
    `target_series_id`, preserving every field (including the original
    `valid_from`/`valid_to`). The successor then "owns an independent
    definition set": nothing here links the copies back to their source
    rows, and changing/ending a copy never touches the predecessor's own
    row. Called by app.events.versioning.create_successor_version inside
    its own transaction — this function itself does not commit.
    """
    for staff_def in session.execute(
        select(SeriesStaffAssignment).where(
            SeriesStaffAssignment.event_series_id == source_series_id
        )
    ).scalars().all():
        session.add(
            SeriesStaffAssignment(
                event_series_id=target_series_id,
                user_id=staff_def.user_id,
                role_in_event=staff_def.role_in_event,
                is_primary=staff_def.is_primary,
                valid_from=staff_def.valid_from,
                valid_to=staff_def.valid_to,
            )
        )

    for group_def in session.execute(
        select(SeriesGroupTarget).where(SeriesGroupTarget.event_series_id == source_series_id)
    ).scalars().all():
        session.add(
            SeriesGroupTarget(
                event_series_id=target_series_id,
                group_id=group_def.group_id,
                valid_from=group_def.valid_from,
                valid_to=group_def.valid_to,
            )
        )

    for participant_def in session.execute(
        select(SeriesParticipant).where(SeriesParticipant.event_series_id == source_series_id)
    ).scalars().all():
        session.add(
            SeriesParticipant(
                event_series_id=target_series_id,
                person_id=participant_def.person_id,
                registration_status=participant_def.registration_status,
                valid_from=participant_def.valid_from,
                valid_to=participant_def.valid_to,
            )
        )
    session.flush()


def create_series_staff_assignment(
    session: Session,
    *,
    event_series_id: uuid.UUID,
    user_id: uuid.UUID,
    role_in_event: str,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    is_primary: bool = False,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> SeriesStaffAssignment:
    """ADR-0022 / ADR-0030: create a SeriesStaffAssignment only when the
    User's Person has an active ClubMembership in the Series' Club.

    Raises SeriesStaffClubMembershipMissingError, and persists nothing,
    when that relationship is absent. Raises
    SeriesStaffAssignmentPrimaryConflictError, and persists nothing, when
    `is_primary=True` and its validity interval overlaps an existing
    active primary assignment for the same Series version. Records
    `event_series_staff_assignment.created`.
    """
    series_club_id = _lock_series_club_id(session, event_series_id)
    if not user_has_active_club_membership(session, user_id=user_id, club_id=series_club_id):
        session.rollback()
        raise SeriesStaffClubMembershipMissingError(user_id=user_id, club_id=series_club_id)

    assignment = SeriesStaffAssignment(
        event_series_id=event_series_id,
        user_id=user_id,
        role_in_event=role_in_event,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(assignment)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series_staff_assignment.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=series_club_id,
            resource_type="event_series_staff_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_one_active_primary_violation(exc):
            raise SeriesStaffAssignmentPrimaryConflictError(
                event_series_id=event_series_id
            ) from exc
        raise
    return assignment


def end_series_staff_assignment(
    session: Session,
    *,
    assignment: SeriesStaffAssignment,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> SeriesStaffAssignment:
    """Sets `valid_to = now()`. Raises
    InvalidSeriesRelationshipTransitionError, and persists nothing, when
    `assignment` has already ended. Records
    `event_series_staff_assignment.ended`.
    """
    now = datetime.now(timezone.utc)
    if assignment.valid_to is not None and assignment.valid_to <= now:
        raise InvalidSeriesRelationshipTransitionError(relationship_id=assignment.id)

    old_valid_to = assignment.valid_to
    assignment.valid_to = now
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series_staff_assignment.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event_series_staff_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"valid_to": {"from": str(old_valid_to), "to": str(now)}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return assignment


def create_series_group_target(
    session: Session,
    *,
    event_series_id: uuid.UUID,
    group_id: uuid.UUID,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> SeriesGroupTarget:
    """ADR-0022 / ADR-0030: create a SeriesGroupTarget only when
    `EventSeries.club_id == Group.club_id`. Raises
    SeriesGroupTargetClubMismatchError, and persists nothing, on a
    mismatch. Records `event_series_group_target.created`.
    """
    series_club_id = _lock_series_club_id(session, event_series_id)
    group_club_id = _lock_group_club_id(session, group_id)
    if series_club_id != group_club_id:
        session.rollback()
        raise SeriesGroupTargetClubMismatchError(
            series_club_id=series_club_id, group_club_id=group_club_id
        )

    target = SeriesGroupTarget(
        event_series_id=event_series_id,
        group_id=group_id,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(target)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series_group_target.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=series_club_id,
            resource_type="event_series_group_target",
            resource_id=target.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return target


def end_series_group_target(
    session: Session,
    *,
    target: SeriesGroupTarget,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> SeriesGroupTarget:
    """Sets `valid_to = now()`. Raises
    InvalidSeriesRelationshipTransitionError, and persists nothing, when
    `target` has already ended. Records `event_series_group_target.ended`.
    """
    now = datetime.now(timezone.utc)
    if target.valid_to is not None and target.valid_to <= now:
        raise InvalidSeriesRelationshipTransitionError(relationship_id=target.id)

    old_valid_to = target.valid_to
    target.valid_to = now
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series_group_target.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event_series_group_target",
            resource_id=target.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"valid_to": {"from": str(old_valid_to), "to": str(now)}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return target


def create_series_participant(
    session: Session,
    *,
    event_series_id: uuid.UUID,
    person_id: uuid.UUID,
    registration_status: str,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> SeriesParticipant:
    """See module docstring for why this performs no cross-Club/
    membership validation beyond ordinary FK integrity. Records
    `event_series_participant.created`.
    """
    series_club_id = _lock_series_club_id(session, event_series_id)

    participant = SeriesParticipant(
        event_series_id=event_series_id,
        person_id=person_id,
        registration_status=registration_status,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(participant)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series_participant.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=series_club_id,
            resource_type="event_series_participant",
            resource_id=participant.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return participant


def end_series_participant(
    session: Session,
    *,
    participant: SeriesParticipant,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> SeriesParticipant:
    """Sets `valid_to = now()`. Raises
    InvalidSeriesRelationshipTransitionError, and persists nothing, when
    `participant` has already ended. Records
    `event_series_participant.ended`.
    """
    now = datetime.now(timezone.utc)
    if participant.valid_to is not None and participant.valid_to <= now:
        raise InvalidSeriesRelationshipTransitionError(relationship_id=participant.id)

    old_valid_to = participant.valid_to
    participant.valid_to = now
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series_participant.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event_series_participant",
            resource_id=participant.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"valid_to": {"from": str(old_valid_to), "to": str(now)}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return participant


__all__ = [
    "SeriesRelationshipError",
    "SeriesStaffClubMembershipMissingError",
    "SeriesStaffAssignmentPrimaryConflictError",
    "SeriesGroupTargetClubMismatchError",
    "InvalidSeriesRelationshipTransitionError",
    "snapshot_copy_series_relationships",
    "create_series_staff_assignment",
    "end_series_staff_assignment",
    "create_series_group_target",
    "end_series_group_target",
    "create_series_participant",
    "end_series_participant",
]
