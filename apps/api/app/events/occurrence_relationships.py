"""EventOccurrence authorization-relationship materialization and
occurrence-level override mutation service (Issue #85 / TH-0082,
ADR-0029, ADR-0030).

Two distinct write paths populate `EventOccurrenceStaffAssignment`/
`EventOccurrenceGroupTarget`/`EventOccurrenceParticipant`:

1. **Materialization/propagation** (`materialize_occurrence_relationships`,
   `propagate_occurrence_relationships`) — copies applicable
   `SeriesStaffAssignment`/`SeriesGroupTarget`/`SeriesParticipant`
   definitions from the occurrence's governing `EventSeries` version,
   evaluated at the occurrence's own scheduled start instant (ADR-0030:
   "Relationship effectivity is evaluated using the occurrence's
   scheduled start instant"). Rows created this way have
   `is_override=False`. Called by app.events.materialization (fresh
   occurrence creation, same transaction — ADR-0029/ADR-0030: "the
   occurrence and all applicable authorization relationship snapshots are
   created atomically ... a partially materialized occurrence must not be
   visible to authorization queries") and by app.events.versioning (`this
   and following` rebind propagation to already-materialized future
   occurrences, ADR-0030 point 5/database-schema-recurrence.md §5.4/§7).

2. **Explicit occurrence-level override**
   (`create_occurrence_*`/`end_occurrence_*`) — an explicit mutation
   establishing a *protected* override (`is_override=True`) for one
   concrete occurrence, independent of its Series definition (ADR-0030
   "Occurrence-level overrides": "remains authoritative until explicitly
   changed or ended ... Later Series updates do not overwrite protected
   occurrence relationships"). No HTTP endpoint exists for this path yet
   — see the final implementation report's "residual API gap" note
   (event-recurrence-api.md's "Series relationship definitions" section
   requires this to be exposed "through the existing Event relationship
   API surface", but no such HTTP surface exists anywhere in this
   codebase for *any* Event relationship, recurring or not; per this
   task's own instruction not to invent undocumented public contracts,
   only the persistence/domain capability is implemented here).

`propagate_occurrence_relationships` never touches a category with an
existing `is_override=True` row for that occurrence — that is exactly
the "protected override" boundary. For a non-protected category, it ends
(`valid_to = now()`) every currently-active (`valid_to IS NULL`) row and
re-materializes from the new governing Series version — the same
"close, then create new" pattern every other interval-based relationship
in this codebase already uses, never an in-place field mutation.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authorization.club_ownership import user_has_active_club_membership
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceParticipant,
    EventOccurrenceStaffAssignment,
    SeriesGroupTarget,
    SeriesParticipant,
    SeriesStaffAssignment,
)
from app.db.groups import Group

_ONE_ACTIVE_PRIMARY_CONSTRAINT = "ck_event_occurrence_staff_assignments_one_active_primary"


class OccurrenceRelationshipError(Exception):
    """Base class for this module's typed, expected failures."""


class OccurrenceStaffClubMembershipMissingError(OccurrenceRelationshipError):
    def __init__(self, *, user_id: uuid.UUID, club_id: uuid.UUID) -> None:
        super().__init__(f"User {user_id} has no active ClubMembership in club {club_id}")
        self.user_id = user_id
        self.club_id = club_id


class OccurrenceStaffAssignmentPrimaryConflictError(OccurrenceRelationshipError):
    def __init__(self, *, occurrence_id: uuid.UUID) -> None:
        super().__init__(
            f"Occurrence {occurrence_id} already has an active primary staff "
            "assignment overlapping this validity interval"
        )
        self.occurrence_id = occurrence_id


class OccurrenceGroupTargetClubMismatchError(OccurrenceRelationshipError):
    def __init__(self, *, occurrence_club_id: uuid.UUID, group_club_id: uuid.UUID) -> None:
        super().__init__(
            f"Occurrence's club {occurrence_club_id} does not match Group's club {group_club_id}"
        )
        self.occurrence_club_id = occurrence_club_id
        self.group_club_id = group_club_id


class InvalidOccurrenceRelationshipTransitionError(OccurrenceRelationshipError):
    def __init__(self, *, relationship_id: uuid.UUID) -> None:
        super().__init__(f"Relationship {relationship_id} has already ended")
        self.relationship_id = relationship_id


def _effective_at(valid_from: Any, valid_to: Any, instant: datetime) -> sa.ColumnElement[bool]:
    return sa.and_(valid_from <= instant, sa.or_(valid_to.is_(None), instant < valid_to))


# --- Materialization / propagation (Series -> Occurrence) ------------------


def materialize_occurrence_relationships(
    session: Session, *, occurrence: EventOccurrence, series: EventSeries
) -> None:
    """Copy every `series` relationship definition effective at
    `occurrence.starts_at` into new, unprotected (`is_override=False`)
    occurrence-level rows. Only ever called for a brand-new occurrence
    (app.events.materialization) — never touches a row that could already
    exist, so no ending/replacing logic is needed here (see
    `propagate_occurrence_relationships` for that).
    """
    instant = occurrence.starts_at

    staff_defs = session.execute(
        sa.select(SeriesStaffAssignment).where(
            SeriesStaffAssignment.event_series_id == series.id,
            _effective_at(
                SeriesStaffAssignment.valid_from, SeriesStaffAssignment.valid_to, instant
            ),
        )
    ).scalars().all()
    for staff_def in staff_defs:
        session.add(
            EventOccurrenceStaffAssignment(
                occurrence_id=occurrence.id,
                user_id=staff_def.user_id,
                role_in_event=staff_def.role_in_event,
                is_primary=staff_def.is_primary,
                valid_from=staff_def.valid_from,
                valid_to=staff_def.valid_to,
                is_override=False,
            )
        )

    group_defs = session.execute(
        sa.select(SeriesGroupTarget).where(
            SeriesGroupTarget.event_series_id == series.id,
            _effective_at(SeriesGroupTarget.valid_from, SeriesGroupTarget.valid_to, instant),
        )
    ).scalars().all()
    for group_def in group_defs:
        session.add(
            EventOccurrenceGroupTarget(
                occurrence_id=occurrence.id,
                group_id=group_def.group_id,
                valid_from=group_def.valid_from,
                valid_to=group_def.valid_to,
                is_override=False,
            )
        )

    participant_defs = session.execute(
        sa.select(SeriesParticipant).where(
            SeriesParticipant.event_series_id == series.id,
            _effective_at(SeriesParticipant.valid_from, SeriesParticipant.valid_to, instant),
        )
    ).scalars().all()
    for participant_def in participant_defs:
        session.add(
            EventOccurrenceParticipant(
                occurrence_id=occurrence.id,
                person_id=participant_def.person_id,
                registration_status=participant_def.registration_status,
                valid_from=participant_def.valid_from,
                valid_to=participant_def.valid_to,
                is_override=False,
            )
        )
    # Autoflush is disabled project-wide (app.db.session.get_session_factory)
    # — an explicit flush is required so a query later in *this same*
    # transaction (a second materialization/propagation call, or the
    # caller's own subsequent read) observes these new rows rather than a
    # stale pre-flush snapshot.
    session.flush()


def propagate_occurrence_relationships(
    session: Session,
    *,
    occurrence: EventOccurrence,
    series: EventSeries,
    now: Optional[datetime] = None,
) -> None:
    """ADR-0030 point 5 / database-schema-recurrence.md §5.4/§7: for an
    already-materialized occurrence rebound to a new governing `series`
    version (`this_and_following`), resync each relationship category
    that has no protected (`is_override=True`) row for `occurrence` —
    ending its currently-active unprotected rows and re-materializing
    from `series` effective at `occurrence.starts_at`. A category with
    any protected row is left completely untouched.
    """
    close_at = now if now is not None else datetime.now(timezone.utc)
    instant = occurrence.starts_at

    has_staff_override = session.execute(
        sa.select(
            sa.exists(
                sa.select(EventOccurrenceStaffAssignment.id).where(
                    EventOccurrenceStaffAssignment.occurrence_id == occurrence.id,
                    EventOccurrenceStaffAssignment.is_override.is_(True),
                )
            )
        )
    ).scalar()
    if not has_staff_override:
        active_staff = session.execute(
            sa.select(EventOccurrenceStaffAssignment).where(
                EventOccurrenceStaffAssignment.occurrence_id == occurrence.id,
                EventOccurrenceStaffAssignment.valid_to.is_(None),
            )
        ).scalars().all()
        for staff_row in active_staff:
            staff_row.valid_to = close_at
        staff_defs = session.execute(
            sa.select(SeriesStaffAssignment).where(
                SeriesStaffAssignment.event_series_id == series.id,
                _effective_at(
                    SeriesStaffAssignment.valid_from, SeriesStaffAssignment.valid_to, instant
                ),
            )
        ).scalars().all()
        for staff_def in staff_defs:
            session.add(
                EventOccurrenceStaffAssignment(
                    occurrence_id=occurrence.id,
                    user_id=staff_def.user_id,
                    role_in_event=staff_def.role_in_event,
                    is_primary=staff_def.is_primary,
                    valid_from=staff_def.valid_from,
                    valid_to=staff_def.valid_to,
                    is_override=False,
                )
            )

    has_group_override = session.execute(
        sa.select(
            sa.exists(
                sa.select(EventOccurrenceGroupTarget.id).where(
                    EventOccurrenceGroupTarget.occurrence_id == occurrence.id,
                    EventOccurrenceGroupTarget.is_override.is_(True),
                )
            )
        )
    ).scalar()
    if not has_group_override:
        active_groups = session.execute(
            sa.select(EventOccurrenceGroupTarget).where(
                EventOccurrenceGroupTarget.occurrence_id == occurrence.id,
                EventOccurrenceGroupTarget.valid_to.is_(None),
            )
        ).scalars().all()
        for group_row in active_groups:
            group_row.valid_to = close_at
        group_defs = session.execute(
            sa.select(SeriesGroupTarget).where(
                SeriesGroupTarget.event_series_id == series.id,
                _effective_at(SeriesGroupTarget.valid_from, SeriesGroupTarget.valid_to, instant),
            )
        ).scalars().all()
        for group_def in group_defs:
            session.add(
                EventOccurrenceGroupTarget(
                    occurrence_id=occurrence.id,
                    group_id=group_def.group_id,
                    valid_from=group_def.valid_from,
                    valid_to=group_def.valid_to,
                    is_override=False,
                )
            )

    has_participant_override = session.execute(
        sa.select(
            sa.exists(
                sa.select(EventOccurrenceParticipant.id).where(
                    EventOccurrenceParticipant.occurrence_id == occurrence.id,
                    EventOccurrenceParticipant.is_override.is_(True),
                )
            )
        )
    ).scalar()
    if not has_participant_override:
        active_participants = session.execute(
            sa.select(EventOccurrenceParticipant).where(
                EventOccurrenceParticipant.occurrence_id == occurrence.id,
                EventOccurrenceParticipant.valid_to.is_(None),
            )
        ).scalars().all()
        for participant_row in active_participants:
            participant_row.valid_to = close_at
        participant_defs = session.execute(
            sa.select(SeriesParticipant).where(
                SeriesParticipant.event_series_id == series.id,
                _effective_at(SeriesParticipant.valid_from, SeriesParticipant.valid_to, instant),
            )
        ).scalars().all()
        for participant_def in participant_defs:
            session.add(
                EventOccurrenceParticipant(
                    occurrence_id=occurrence.id,
                    person_id=participant_def.person_id,
                    registration_status=participant_def.registration_status,
                    valid_from=participant_def.valid_from,
                    valid_to=participant_def.valid_to,
                    is_override=False,
                )
            )
    # See materialize_occurrence_relationships's own comment on why this
    # explicit flush is required.
    session.flush()


# --- Explicit occurrence-level overrides (protected) ------------------------


def _lock_occurrence_club_id(session: Session, occurrence_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        sa.select(EventOccurrence.club_id)
        .where(EventOccurrence.id == occurrence_id)
        .with_for_update(read=True)
    ).scalar_one()


def _lock_group_club_id(session: Session, group_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        sa.select(Group.club_id).where(Group.id == group_id).with_for_update(read=True)
    ).scalar_one()


def _is_one_active_primary_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _ONE_ACTIVE_PRIMARY_CONSTRAINT


def create_occurrence_staff_assignment(
    session: Session,
    *,
    occurrence_id: uuid.UUID,
    user_id: uuid.UUID,
    role_in_event: str,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    is_primary: bool = False,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceStaffAssignment:
    """Establishes a protected (`is_override=True`) occurrence-level
    staff override. Raises OccurrenceStaffClubMembershipMissingError, and
    persists nothing, when the User's Person has no active ClubMembership
    in the occurrence's Club. Records
    `event_occurrence_staff_assignment.created`.
    """
    occurrence_club_id = _lock_occurrence_club_id(session, occurrence_id)
    if not user_has_active_club_membership(session, user_id=user_id, club_id=occurrence_club_id):
        session.rollback()
        raise OccurrenceStaffClubMembershipMissingError(user_id=user_id, club_id=occurrence_club_id)

    assignment = EventOccurrenceStaffAssignment(
        occurrence_id=occurrence_id,
        user_id=user_id,
        role_in_event=role_in_event,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
        is_override=True,
    )
    session.add(assignment)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence_staff_assignment.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=occurrence_club_id,
            resource_type="event_occurrence_staff_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_one_active_primary_violation(exc):
            raise OccurrenceStaffAssignmentPrimaryConflictError(
                occurrence_id=occurrence_id
            ) from exc
        raise
    return assignment


def end_occurrence_staff_assignment(
    session: Session,
    *,
    assignment: EventOccurrenceStaffAssignment,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceStaffAssignment:
    """Sets `valid_to = now()` AND marks the row a protected override
    (`is_override=True`) — an explicit end is itself an explicit
    occurrence-level relationship mutation (ADR-0030), so a later Series
    propagation must never resurrect this ended row. Raises
    InvalidOccurrenceRelationshipTransitionError, and persists nothing,
    when already ended. Records `event_occurrence_staff_assignment.ended`.
    """
    now = datetime.now(timezone.utc)
    if assignment.valid_to is not None and assignment.valid_to <= now:
        raise InvalidOccurrenceRelationshipTransitionError(relationship_id=assignment.id)

    old_valid_to = assignment.valid_to
    assignment.valid_to = now
    assignment.is_override = True
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence_staff_assignment.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event_occurrence_staff_assignment",
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


def create_occurrence_group_target(
    session: Session,
    *,
    occurrence_id: uuid.UUID,
    group_id: uuid.UUID,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceGroupTarget:
    """Establishes a protected occurrence-level group-target override.
    Raises OccurrenceGroupTargetClubMismatchError, and persists nothing,
    when the Group's Club does not match the occurrence's Club. Records
    `event_occurrence_group_target.created`.
    """
    occurrence_club_id = _lock_occurrence_club_id(session, occurrence_id)
    group_club_id = _lock_group_club_id(session, group_id)
    if occurrence_club_id != group_club_id:
        session.rollback()
        raise OccurrenceGroupTargetClubMismatchError(
            occurrence_club_id=occurrence_club_id, group_club_id=group_club_id
        )

    target = EventOccurrenceGroupTarget(
        occurrence_id=occurrence_id,
        group_id=group_id,
        valid_from=valid_from,
        valid_to=valid_to,
        is_override=True,
    )
    session.add(target)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence_group_target.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=occurrence_club_id,
            resource_type="event_occurrence_group_target",
            resource_id=target.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return target


def end_occurrence_group_target(
    session: Session,
    *,
    target: EventOccurrenceGroupTarget,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceGroupTarget:
    now = datetime.now(timezone.utc)
    if target.valid_to is not None and target.valid_to <= now:
        raise InvalidOccurrenceRelationshipTransitionError(relationship_id=target.id)

    old_valid_to = target.valid_to
    target.valid_to = now
    target.is_override = True
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence_group_target.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event_occurrence_group_target",
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


def create_occurrence_participant(
    session: Session,
    *,
    occurrence_id: uuid.UUID,
    person_id: uuid.UUID,
    registration_status: str,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceParticipant:
    """See app.events.series_relationships module docstring for why this
    performs no cross-Club/membership validation beyond ordinary FK
    integrity — the same rationale applies at occurrence level. Records
    `event_occurrence_participant.created`.
    """
    occurrence_club_id = _lock_occurrence_club_id(session, occurrence_id)

    participant = EventOccurrenceParticipant(
        occurrence_id=occurrence_id,
        person_id=person_id,
        registration_status=registration_status,
        valid_from=valid_from,
        valid_to=valid_to,
        is_override=True,
    )
    session.add(participant)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence_participant.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=occurrence_club_id,
            resource_type="event_occurrence_participant",
            resource_id=participant.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return participant


def end_occurrence_participant(
    session: Session,
    *,
    participant: EventOccurrenceParticipant,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceParticipant:
    now = datetime.now(timezone.utc)
    if participant.valid_to is not None and participant.valid_to <= now:
        raise InvalidOccurrenceRelationshipTransitionError(relationship_id=participant.id)

    old_valid_to = participant.valid_to
    participant.valid_to = now
    participant.is_override = True
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence_participant.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event_occurrence_participant",
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
    "OccurrenceRelationshipError",
    "OccurrenceStaffClubMembershipMissingError",
    "OccurrenceStaffAssignmentPrimaryConflictError",
    "OccurrenceGroupTargetClubMismatchError",
    "InvalidOccurrenceRelationshipTransitionError",
    "materialize_occurrence_relationships",
    "propagate_occurrence_relationships",
    "create_occurrence_staff_assignment",
    "end_occurrence_staff_assignment",
    "create_occurrence_group_target",
    "end_occurrence_group_target",
    "create_occurrence_participant",
    "end_occurrence_participant",
]
