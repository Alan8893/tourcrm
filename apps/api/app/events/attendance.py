"""Event Attendance domain and service (Issue #94 / TH-0087).

Canonical source: docs/03-architecture/adr/ADR-0032-event-attendance.md
(every rule below follows it field-for-field unless a deviation is
explicitly documented — see app.db.attendance module docstring for the
one identity-level implementation detail this task had to resolve).
Also relevant: ADR-0018 (Event lifecycle), ADR-0024 (audit),
ADR-0028/ADR-0029/ADR-0030 (recurrence/occurrence authorization).

This module is pure domain/service: no FastAPI/HTTPException import. The
API router (app.api.v1.events) resolves the `{event_id}` path parameter
to a concrete object (`resolve_attendance_target`), performs the existing
404-existence-hiding authorization gate exactly like every other single-
object Event/Occurrence endpoint, and then calls into the functions
below — mirroring the split already established between
app.api.v1.events'/app.api.v1.events_series.py's own `_get_authorized_*`
helpers and app.events.occurrence_relationships' pure service functions.

## Object resolution: `{event_id}` names either an Event or an EventOccurrence

ADR-0032 §1 requires both ordinary non-recurring `Event`s and recurring
`EventOccurrence`s to receive Attendance through the *same* endpoint
family (`/events/{event_id}/attendance...`, docs/05-api/events-api.md
§22-25) — there is no separate occurrence-scoped attendance route.
`resolve_attendance_target` therefore tries both tables by primary key;
UUIDv4 identity spaces are disjoint in practice (app.db.attendance's own
`ck_attendance_exactly_one_target` CHECK depends on the same assumption).

## Lifecycle (ADR-0032 §5)

Eligible-for-normal-change statuses mirror app.events.conflicts'
`CONFLICT_EVENT_STATUSES`/`CONFLICT_OCCURRENCE_STATUSES` exactly — the
same "operational window" status pairing already established for that
feature (`published`/`in_progress` for `Event`, `scheduled`/`in_progress`
for `EventOccurrence`). `completed` closes the normal window (correction
endpoint only); every other status (`cancelled` for either object type,
plus `draft`/`archived` for `Event`, pre-/post-operational states ADR-0032
never discusses because the occurrence vocabulary has no equivalent)
closes Attendance entirely — a deliberate, precedent-consistent
generalization of ADR-0032 §5's occurrence-only wording to `Event`'s
larger status vocabulary, not a new business rule: none of those states
have eligible participants to mark either way.

## Participant visibility on GET (ADR-0032 §8/§9)

Authorization for the endpoint itself (attendance.read/attendance.update
against the resolved object) uses the existing single-object gate exactly
like every other Event/Occurrence endpoint (`build_event_resource_context`/
`build_occurrence_resource_context` + `Authorizer.is_allowed`) — this
governs whether the object is visible/actionable *at all* (404 otherwise).

Given that gate passes, `list_attendance`'s row-level restriction is a
*second*, finer-grained filter: `all`/`own_events`/`own_groups` grant the
full participant set (the same scopes that already grant full object
access); `self`/`children` restrict the visible rows to the requester's
own Person / their eligible children's Persons respectively
(roles-and-permissions.md §12's own Attendance-read row: "self ... self;
children ... children"). No prior feature needed this per-row-within-one-
already-authorized-object restriction (event_visibility_filter/
occurrence_visibility_filter both filter *which objects* are visible
across a list, never *which participants* within one already-visible
object) — `_attendance_row_visibility` below is new but reuses the exact
same `applicable_assignments`/`club_boundary_matches`/active-
GuardianRelationship-plus-ClubMembership building blocks as
app.events.authorization/app.events.series_authorization, composed for
this new shape rather than inventing a second authorization mechanism.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.audit.service import record_audit_event
from app.authorization.context import ResourceContext
from app.authorization.service import applicable_assignments, club_boundary_matches
from app.db.attendance import CANONICAL_ABSENCE_REASONS, CANONICAL_ATTENDANCE_STATUSES, Attendance
from app.db.event_recurrence import EventOccurrence
from app.db.event_recurrence_relationships import EventOccurrenceParticipant
from app.db.events import Event, EventParticipation
from app.db.identity import ClubMembership, GuardianRelationship, Person, User

ObjectType = Literal["event", "occurrence"]

# Mirrors app.events.conflicts.CONFLICT_EVENT_STATUSES/
# CONFLICT_OCCURRENCE_STATUSES exactly — see module docstring "Lifecycle".
ATTENDANCE_ELIGIBLE_EVENT_STATUSES: tuple[str, ...] = ("published", "in_progress")
ATTENDANCE_ELIGIBLE_OCCURRENCE_STATUSES: tuple[str, ...] = ("scheduled", "in_progress")
_COMPLETED_STATUS = "completed"
_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GUARDIAN_RELATIONSHIP_STATUS = "active"


class AttendanceError(Exception):
    """Base class for this module's typed, expected failures."""


class AttendanceLifecycleClosedError(AttendanceError):
    """The object's current status allows neither a normal change nor a
    correction (`cancelled`, or `Event`'s `draft`/`archived`)."""


class AttendanceNormalWindowClosedError(AttendanceError):
    """The object is `completed`: normal PUT is closed, use the
    correction endpoint (ADR-0032 §5)."""


class AttendanceUseNormalEndpointError(AttendanceError):
    """The object is not `completed`: the correction endpoint does not
    apply, use the normal PUT endpoint instead."""


class AttendanceParticipationMissingError(AttendanceError):
    """ADR-0032 §6: the Person has no EventParticipation (Event) /
    active EventOccurrenceParticipant (EventOccurrence) for this object."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} has no participation for this object")
        self.person_id = person_id


class AttendanceInvalidDataError(AttendanceError):
    """status/absence_reason/comment violates ADR-0032 §2-4."""


class AttendanceDuplicatePersonError(AttendanceError):
    """ADR-0032 §7: duplicate `person_id` values in one bulk request."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Duplicate person_id {person_id} in one bulk request")
        self.person_id = person_id


class AttendanceCorrectionReasonRequiredError(AttendanceError):
    """ADR-0032 §5/§11: a correction requires a mandatory reason."""


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


# --- Object resolution -------------------------------------------------------


def resolve_attendance_target(
    session: Session, object_id: uuid.UUID
) -> Optional[tuple[ObjectType, Union[Event, EventOccurrence]]]:
    """Try `Event` first, then `EventOccurrence`, by primary key — see
    module docstring "Object resolution". `None` if neither exists."""
    event = session.get(Event, object_id)
    if event is not None:
        return "event", event
    occurrence = session.get(EventOccurrence, object_id)
    if occurrence is not None:
        return "occurrence", occurrence
    return None


def eligible_for_normal_change(object_type: ObjectType, status: str) -> bool:
    if object_type == "event":
        return status in ATTENDANCE_ELIGIBLE_EVENT_STATUSES
    return status in ATTENDANCE_ELIGIBLE_OCCURRENCE_STATUSES


def check_lifecycle_for_normal_change(object_type: ObjectType, status: str) -> None:
    if status == _COMPLETED_STATUS:
        raise AttendanceNormalWindowClosedError("Object is completed; use the correction endpoint")
    if not eligible_for_normal_change(object_type, status):
        raise AttendanceLifecycleClosedError(f"Attendance is closed for status {status!r}")


def check_lifecycle_for_correction(object_type: ObjectType, status: str) -> None:
    if eligible_for_normal_change(object_type, status):
        raise AttendanceUseNormalEndpointError(
            "Object is still open for normal changes; use the normal endpoint"
        )
    if status != _COMPLETED_STATUS:
        raise AttendanceLifecycleClosedError(f"Attendance is closed for status {status!r}")


# --- Participant dependency (ADR-0032 §6) -----------------------------------


def has_participation(
    session: Session, *, object_type: ObjectType, object_id: uuid.UUID, person_id: uuid.UUID
) -> bool:
    if object_type == "event":
        return bool(
            session.execute(
                sa.select(
                    sa.exists(
                        sa.select(EventParticipation.id).where(
                            EventParticipation.event_id == object_id,
                            EventParticipation.person_id == person_id,
                        )
                    )
                )
            ).scalar()
        )
    return bool(
        session.execute(
            sa.select(
                sa.exists(
                    sa.select(EventOccurrenceParticipant.id).where(
                        EventOccurrenceParticipant.occurrence_id == object_id,
                        EventOccurrenceParticipant.person_id == person_id,
                        _active_interval(
                            EventOccurrenceParticipant.valid_from,
                            EventOccurrenceParticipant.valid_to,
                        ),
                    )
                )
            )
        ).scalar()
    )


# --- Field validation (ADR-0032 §2-4) ---------------------------------------


def validate_attendance_fields(
    *, status: str, absence_reason: Optional[str], comment: Optional[str]
) -> None:
    if status not in CANONICAL_ATTENDANCE_STATUSES:
        raise AttendanceInvalidDataError(f"Invalid attendance status: {status!r}")
    if absence_reason is not None and absence_reason not in CANONICAL_ABSENCE_REASONS:
        raise AttendanceInvalidDataError(f"Invalid absence_reason: {absence_reason!r}")
    if status == "present" and (absence_reason is not None or comment is not None):
        raise AttendanceInvalidDataError(
            "A present Attendance must have no absence_reason and no comment"
        )


def _target_column(object_type: ObjectType) -> Any:
    return Attendance.event_id if object_type == "event" else Attendance.occurrence_id


def _new_attendance(
    *,
    object_type: ObjectType,
    object_id: uuid.UUID,
    person_id: uuid.UUID,
    status: str,
    absence_reason: Optional[str],
    comment: Optional[str],
) -> Attendance:
    return Attendance(
        event_id=object_id if object_type == "event" else None,
        occurrence_id=object_id if object_type == "occurrence" else None,
        person_id=person_id,
        status=status,
        absence_reason=absence_reason,
        comment=comment,
    )


# --- Single upsert (ADR-0032 §1/§5) -----------------------------------------


def upsert_attendance(
    session: Session,
    *,
    object_type: ObjectType,
    object_id: uuid.UUID,
    club_id: uuid.UUID,
    person_id: uuid.UUID,
    status: str,
    absence_reason: Optional[str],
    comment: Optional[str],
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[Attendance, bool]:
    """Idempotent create-or-update for one Person. Caller must already
    have verified object existence, authorization, lifecycle and
    participation (see module docstring) — this function performs field
    validation, then the mutation and its audit event in one transaction
    (mirrors app.events.occurrence_relationships' own commit/rollback
    shape). Returns `(row, created)`.
    """
    validate_attendance_fields(status=status, absence_reason=absence_reason, comment=comment)

    existing = session.execute(
        sa.select(Attendance)
        .where(_target_column(object_type) == object_id, Attendance.person_id == person_id)
        .with_for_update()
    ).scalar_one_or_none()
    previous_status = existing.status if existing is not None else None

    try:
        if existing is None:
            row = _new_attendance(
                object_type=object_type,
                object_id=object_id,
                person_id=person_id,
                status=status,
                absence_reason=absence_reason,
                comment=comment,
            )
            session.add(row)
            created = True
            action = "attendance.created"
        else:
            row = existing
            row.status = status
            row.absence_reason = absence_reason
            row.comment = comment
            created = False
            action = "attendance.changed"
        session.flush()
        record_audit_event(
            session,
            action=action,
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type="attendance",
            resource_id=row.id,
            outcome="success",
            request_id=request_id,
            details={
                "person_id": str(person_id),
                "status": {"from": previous_status, "to": status},
                "absence_reason": absence_reason,
                "comment_present": comment is not None,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return row, created


# --- Bulk upsert (ADR-0032 §7) -----------------------------------------------


@dataclass(frozen=True)
class AttendanceBulkItemInput:
    person_id: uuid.UUID
    status: str
    absence_reason: Optional[str]
    comment: Optional[str]


def bulk_upsert_attendance(
    session: Session,
    *,
    object_type: ObjectType,
    object_id: uuid.UUID,
    club_id: uuid.UUID,
    items: list[AttendanceBulkItemInput],
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> list[tuple[Attendance, bool]]:
    """Partial bulk upsert: supplied Person -> create/update; omitted
    Person -> unchanged; nothing is ever deleted. Validates every item
    (duplicate `person_id`, field invariants, participant dependency)
    before mutating anything, then mutates every item plus exactly one
    `attendance.bulk_changed` audit event in a single transaction — an
    invalid item fails the entire logical operation with no partial
    persistence (ADR-0032 §7).
    """
    seen_person_ids: set[uuid.UUID] = set()
    for item in items:
        if item.person_id in seen_person_ids:
            raise AttendanceDuplicatePersonError(person_id=item.person_id)
        seen_person_ids.add(item.person_id)
        validate_attendance_fields(
            status=item.status, absence_reason=item.absence_reason, comment=item.comment
        )
        if not has_participation(
            session, object_type=object_type, object_id=object_id, person_id=item.person_id
        ):
            raise AttendanceParticipationMissingError(person_id=item.person_id)

    target_column = _target_column(object_type)
    results: list[tuple[Attendance, bool]] = []
    change_summaries: list[dict[str, Any]] = []
    try:
        for item in items:
            existing = session.execute(
                sa.select(Attendance)
                .where(target_column == object_id, Attendance.person_id == item.person_id)
                .with_for_update()
            ).scalar_one_or_none()
            previous_status = existing.status if existing is not None else None
            if existing is None:
                row = _new_attendance(
                    object_type=object_type,
                    object_id=object_id,
                    person_id=item.person_id,
                    status=item.status,
                    absence_reason=item.absence_reason,
                    comment=item.comment,
                )
                session.add(row)
                created = True
            else:
                row = existing
                row.status = item.status
                row.absence_reason = item.absence_reason
                row.comment = item.comment
                created = False
            results.append((row, created))
            change_summaries.append(
                {
                    "person_id": str(item.person_id),
                    "action": "created" if created else "changed",
                    "status": {"from": previous_status, "to": item.status},
                }
            )
        session.flush()
        record_audit_event(
            session,
            action="attendance.bulk_changed",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            # One bulk operation touches several Attendance rows, so the
            # audited "resource" is the Event/Occurrence it was bulk-
            # updated for, not any single created/changed row (ADR-0024 §2
            # requires resource_type/resource_id to both be set or both be
            # None; the per-row detail lives in `details.changes` below).
            # "event_occurrence" matches the resource_type spelling already
            # used for EventOccurrence elsewhere (app.events.series_service).
            resource_type="event" if object_type == "event" else "event_occurrence",
            resource_id=object_id,
            outcome="success",
            request_id=request_id,
            details={
                "object_type": object_type,
                "object_id": str(object_id),
                "count": len(results),
                "changes": change_summaries,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return results


# --- Correction (ADR-0032 §5/§11) -------------------------------------------


def correct_attendance(
    session: Session,
    *,
    object_type: ObjectType,
    object_id: uuid.UUID,
    club_id: uuid.UUID,
    person_id: uuid.UUID,
    status: str,
    absence_reason: Optional[str],
    comment: Optional[str],
    reason: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[Attendance, Optional[str]]:
    """Correct (or, if none exists yet, create) the Attendance record for
    one Person after the normal window has closed. Requires
    `attendance.update` only (no `attendance.correct` permission exists —
    ADR-0032 §8) and a mandatory `reason`, checked by the caller's
    permission dependency and here respectively. Returns `(row,
    previous_status)`.
    """
    if not reason or not reason.strip():
        raise AttendanceCorrectionReasonRequiredError("A correction reason is required")
    validate_attendance_fields(status=status, absence_reason=absence_reason, comment=comment)
    if not has_participation(
        session, object_type=object_type, object_id=object_id, person_id=person_id
    ):
        raise AttendanceParticipationMissingError(person_id=person_id)

    existing = session.execute(
        sa.select(Attendance)
        .where(_target_column(object_type) == object_id, Attendance.person_id == person_id)
        .with_for_update()
    ).scalar_one_or_none()
    previous_status = existing.status if existing is not None else None

    try:
        if existing is None:
            row = _new_attendance(
                object_type=object_type,
                object_id=object_id,
                person_id=person_id,
                status=status,
                absence_reason=absence_reason,
                comment=comment,
            )
            session.add(row)
        else:
            row = existing
            row.status = status
            row.absence_reason = absence_reason
            row.comment = comment
        session.flush()
        record_audit_event(
            session,
            action="attendance.corrected",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type="attendance",
            resource_id=row.id,
            outcome="success",
            request_id=request_id,
            details={
                "person_id": str(person_id),
                "previous_status": previous_status,
                "new_status": status,
                "reason": reason,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return row, previous_status


# --- GET: full participant projection + derived summary (ADR-0032 §8/§9) ---


@dataclass(frozen=True)
class AttendanceEntry:
    person_id: uuid.UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]
    status: Optional[str]
    absence_reason: Optional[str]
    comment: Optional[str]


@dataclass(frozen=True)
class AttendanceSummary:
    total: int
    marked: int
    present: int
    absent: int
    unmarked: int


def _child_visibility_predicate(
    *, club_id: uuid.UUID, guardian_person_id: uuid.UUID, participant_person_id_column: Any
) -> sa.ColumnElement[bool]:
    """Mirrors app.events.authorization._child_condition's shape, but as
    a per-row predicate against `participant_person_id_column` (a real
    column of the enclosing participant query) rather than one fixed,
    already-known child id — see module docstring "Participant
    visibility on GET"."""
    guardian_has_membership = sa.exists(
        sa.select(ClubMembership.id).where(
            ClubMembership.person_id == guardian_person_id,
            ClubMembership.club_id == club_id,
            ClubMembership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )

    gr = aliased(GuardianRelationship)
    child_membership = aliased(ClubMembership)
    eligible_child_exists = sa.exists(
        sa.select(gr.id)
        .join(
            child_membership,
            sa.and_(
                child_membership.person_id == gr.child_person_id,
                child_membership.club_id == club_id,
                child_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
            ),
        )
        .where(
            gr.guardian_person_id == guardian_person_id,
            gr.status == _ACTIVE_GUARDIAN_RELATIONSHIP_STATUS,
            _active_interval(gr.valid_from, gr.valid_to),
            gr.child_person_id == participant_person_id_column,
        )
        .correlate_except(gr, child_membership)
    )
    return sa.and_(guardian_has_membership, eligible_child_exists)


def _attendance_row_visibility(
    session: Session,
    *,
    club_id: uuid.UUID,
    resource_context: ResourceContext,
    user_id: uuid.UUID,
    permission_code: str,
    participant_person_id_column: Any,
) -> sa.ColumnElement[bool]:
    """See module docstring "Participant visibility on GET": which
    Person rows, within one already object-level-authorized Event/
    Occurrence, the requester may see. `all`/`own_events`/`own_groups`
    (already true at the object level, per `resource_context`) grant the
    full set; `self`/`children` restrict to the requester's own/their
    children's rows; `none` contributes nothing."""
    assignments = applicable_assignments(session, user_id, permission_code)
    clauses: list[sa.ColumnElement[bool]] = []
    self_person_id: Optional[uuid.UUID] = None
    for assignment in assignments:
        if not club_boundary_matches(assignment.club_id, club_id):
            continue
        if assignment.scope_type == "all":
            clauses.append(sa.true())
        elif assignment.scope_type == "own_events" and resource_context.is_own_event is True:
            clauses.append(sa.true())
        elif assignment.scope_type == "own_groups" and resource_context.is_own_group is True:
            clauses.append(sa.true())
        elif assignment.scope_type == "self" and resource_context.is_self is True:
            if self_person_id is None:
                self_person_id = _person_id_for_user(session, user_id)
            clauses.append(participant_person_id_column == self_person_id)
        elif assignment.scope_type == "children" and resource_context.is_child is True:
            if self_person_id is None:
                self_person_id = _person_id_for_user(session, user_id)
            clauses.append(
                _child_visibility_predicate(
                    club_id=club_id,
                    guardian_person_id=self_person_id,
                    participant_person_id_column=participant_person_id_column,
                )
            )
        # 'none', and any scope whose object-level flag is False/None,
        # contribute nothing.
    if not clauses:
        return sa.false()
    return sa.or_(*clauses)


def list_attendance(
    session: Session,
    *,
    object_type: ObjectType,
    object_id: uuid.UUID,
    club_id: uuid.UUID,
    resource_context: ResourceContext,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
) -> tuple[list[AttendanceEntry], AttendanceSummary, int]:
    """The full EventParticipation-backed participant set visible to the
    requester (ADR-0032 §9), including participants without an
    Attendance record (`status=None`, "unmarked"). Authorization
    (`_attendance_row_visibility`) is applied inside the SQL query
    itself, before any count/summary/pagination/serialization — never
    fetch-then-filter, per ADR-0032 §8's explicit "no leak" requirement.
    """
    if object_type == "event":
        participants = (
            sa.select(EventParticipation.person_id.label("person_id"))
            .where(EventParticipation.event_id == object_id)
            .distinct()
            .subquery("participants")
        )
        attendance_target_col = Attendance.event_id
    else:
        participants = (
            sa.select(EventOccurrenceParticipant.person_id.label("person_id"))
            .where(
                EventOccurrenceParticipant.occurrence_id == object_id,
                _active_interval(
                    EventOccurrenceParticipant.valid_from, EventOccurrenceParticipant.valid_to
                ),
            )
            .distinct()
            .subquery("participants")
        )
        attendance_target_col = Attendance.occurrence_id

    visibility = _attendance_row_visibility(
        session,
        club_id=club_id,
        resource_context=resource_context,
        user_id=user_id,
        permission_code=permission_code,
        participant_person_id_column=participants.c.person_id,
    )

    base_query = (
        sa.select(
            Person.id.label("person_id"),
            Person.first_name.label("first_name"),
            Person.last_name.label("last_name"),
            Person.middle_name.label("middle_name"),
            Attendance.status.label("status"),
            Attendance.absence_reason.label("absence_reason"),
            Attendance.comment.label("comment"),
        )
        .select_from(participants)
        .join(Person, Person.id == participants.c.person_id)
        .outerjoin(
            Attendance,
            sa.and_(
                Attendance.person_id == participants.c.person_id,
                attendance_target_col == object_id,
            ),
        )
        .where(visibility)
    )

    total = session.execute(
        sa.select(sa.func.count()).select_from(base_query.subquery())
    ).scalar_one()
    marked = session.execute(
        sa.select(sa.func.count()).select_from(
            base_query.where(Attendance.status.is_not(None)).subquery()
        )
    ).scalar_one()
    present = session.execute(
        sa.select(sa.func.count()).select_from(
            base_query.where(Attendance.status == "present").subquery()
        )
    ).scalar_one()
    absent = session.execute(
        sa.select(sa.func.count()).select_from(
            base_query.where(Attendance.status == "absent").subquery()
        )
    ).scalar_one()

    rows = session.execute(
        base_query.order_by(Person.last_name, Person.first_name, Person.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    entries = [
        AttendanceEntry(
            person_id=row.person_id,
            first_name=row.first_name,
            last_name=row.last_name,
            middle_name=row.middle_name,
            status=row.status,
            absence_reason=row.absence_reason,
            comment=row.comment,
        )
        for row in rows
    ]
    summary = AttendanceSummary(
        total=total, marked=marked, present=present, absent=absent, unmarked=total - marked
    )
    return entries, summary, total


__all__ = [
    "ATTENDANCE_ELIGIBLE_EVENT_STATUSES",
    "ATTENDANCE_ELIGIBLE_OCCURRENCE_STATUSES",
    "AttendanceError",
    "AttendanceLifecycleClosedError",
    "AttendanceNormalWindowClosedError",
    "AttendanceUseNormalEndpointError",
    "AttendanceParticipationMissingError",
    "AttendanceInvalidDataError",
    "AttendanceDuplicatePersonError",
    "AttendanceCorrectionReasonRequiredError",
    "AttendanceBulkItemInput",
    "AttendanceEntry",
    "AttendanceSummary",
    "resolve_attendance_target",
    "eligible_for_normal_change",
    "check_lifecycle_for_normal_change",
    "check_lifecycle_for_correction",
    "has_participation",
    "validate_attendance_fields",
    "upsert_attendance",
    "bulk_upsert_attendance",
    "correct_attendance",
    "list_attendance",
]
