"""Event Attendance domain and service (Issue #94 / TH-0087).

Canonical source: docs/03-architecture/adr/ADR-0032-event-attendance.md
§1: "Attendance is a concrete record for exactly one `EventOccurrence`
and one `Person`." Also relevant: ADR-0015 (materialization), ADR-0018
(Event lifecycle), ADR-0024 (audit), ADR-0028/ADR-0029/ADR-0030
(recurrence/occurrence authorization/relationships), ADR-0033
(EventOccurrence as the operational instance for non-recurring Events
too — closes the Event->occurrence gap this module used to report).

This module is pure domain/service: no FastAPI/HTTPException import. The
API router (app.api.v1.events) resolves the `{event_id}` path parameter
to an `EventOccurrence` (`resolve_attendance_target`), performs the
existing 404-existence-hiding authorization gate exactly like every
other single-object Occurrence endpoint, and then calls into the
functions below — mirroring the split already established between
app.api.v1.events_series.py's own `_get_authorized_*` helpers and
app.events.occurrence_relationships' pure service functions.

## Object resolution: deterministic, not ambiguous UUID probing (ADR-0033 §5)

ADR-0033 closes the gap an earlier revision of this module used to
report: every non-recurring `Event` now has exactly one linked
`EventOccurrence` (`EventOccurrence.event_id`, app.events.crud), created
and kept in sync with it. `resolve_attendance_target` resolves the
`{event_id}` path parameter deterministically, in this order:

1. If `event_id` names an `Event`, its one linked `EventOccurrence` is
   used — guaranteed to exist by ADR-0033 §1's invariant, so this is a
   plain FK join, never a fallback.
2. Otherwise, if `event_id` directly names a genuinely recurring
   `EventOccurrence` (`series_id IS NOT NULL`), that occurrence is used.
3. Otherwise, `None` (404).

At most one of (1)/(2) can ever apply for a given id — this is not the
"try Event, then try EventOccurrence" probe a previous revision used
(rejected on review as relying on UUID-space disjointness as if it were
a business/security invariant, which it is not). A non-recurring
Event's own linked occurrence is deliberately *not* addressable by its
own internal id here (only by its Event's id) — it is an implementation
detail, not a second public identity for the same real-world event.

## Participation dependency (ADR-0032 §1/§6, ADR-0033 §5)

`has_participation`/`list_attendance` read from whichever of the two
existing, already-canonical participation tables actually applies to
the resolved occurrence — the same event_id/series_id duality as
`resolve_attendance_target` and the Calendar/Conflict Detection
candidate branches:

- Occurrence backing an ordinary `Event` (`occurrence.event_id` set):
  the Event-level `EventParticipation` table (app.db.events) —
  literally what ADR-0032's own prose means by "EventParticipation" in
  this case.
- Genuinely recurring occurrence (`occurrence.series_id` set):
  `EventOccurrenceParticipant` (app.db.event_recurrence_relationships),
  the same table app.events.series_authorization's own
  `_self_condition`/`_child_condition` already read for occurrence
  `self`/`children` authorization.

Neither branch is an invented parallel participation model — each reads
the one participation table that was already canonical for that object
kind before Attendance existed.

For the recurring branch, "has/had participation" is evaluated against
the occurrence's own `[starts_at, ends_at)` window
(`_participant_overlaps_occurrence`), not against "right now" — a person
who was a participant while the occurrence happened remains eligible for
correction and remains counted in the historical GET roster/summary even
after their `EventOccurrenceParticipant.valid_to` has since passed. See
the review discussion on PR #97 (2026-09-15) that identified the
`now()`-keyed version of this check as silently dropping accepted
Attendance history for ended participants.

## Lifecycle (ADR-0032 §5)

Eligible-for-normal-change statuses mirror app.events.conflicts'
`CONFLICT_OCCURRENCE_STATUSES` exactly (`scheduled`/`in_progress`).
`completed` closes the normal window (correction endpoint only);
`cancelled` closes Attendance entirely.

## Participant visibility on GET (ADR-0032 §8/§9)

Authorization for the endpoint itself (attendance.read/attendance.update
against the resolved occurrence) uses the existing single-object gate —
`app.api.v1.events._get_authorized_attendance_target_or_404` branches
between `build_event_resource_context` (occurrence backing an ordinary
Event — authorized through *that Event's* own relationships, never the
occurrence-level tables, which are never populated for a non-recurring
occurrence) and `build_occurrence_resource_context` (genuinely
recurring occurrence, unchanged), exactly mirroring the has_participation
duality above. Either way, this governs whether the object is
visible/actionable *at all* (404 otherwise).

Given that gate passes, `list_attendance`'s row-level restriction is a
*second*, finer-grained filter: `all`/`own_events`/`own_groups` grant the
full participant set (the same scopes that already grant full object
access); `self`/`children` restrict the visible rows to the requester's
own Person / their eligible children's Persons respectively
(roles-and-permissions.md §12's own Attendance-read row: "self ... self;
children ... children"). No prior feature needed this per-row-within-one-
already-authorized-object restriction (occurrence_visibility_filter
filters *which objects* are visible across a list, never *which
participants* within one already-visible object) —
`_attendance_row_visibility` below is new but reuses the exact same
`applicable_assignments`/`club_boundary_matches`/active-
GuardianRelationship-plus-ClubMembership building blocks as
app.events.series_authorization, composed for this new shape rather than
inventing a second authorization mechanism.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Optional

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

# Mirrors app.events.conflicts.CONFLICT_OCCURRENCE_STATUSES exactly — see
# module docstring "Lifecycle".
ATTENDANCE_ELIGIBLE_OCCURRENCE_STATUSES: tuple[str, ...] = ("scheduled", "in_progress")
_COMPLETED_STATUS = "completed"
_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GUARDIAN_RELATIONSHIP_STATUS = "active"


class AttendanceError(Exception):
    """Base class for this module's typed, expected failures."""


class AttendanceLifecycleClosedError(AttendanceError):
    """The occurrence's current status (`cancelled`) allows neither a
    normal change nor a correction."""


class AttendanceNormalWindowClosedError(AttendanceError):
    """The occurrence is `completed`: normal PUT is closed, use the
    correction endpoint (ADR-0032 §5)."""


class AttendanceUseNormalEndpointError(AttendanceError):
    """The occurrence is not `completed`: the correction endpoint does
    not apply, use the normal PUT endpoint instead."""


class AttendanceParticipationMissingError(AttendanceError):
    """ADR-0032 §6: the Person has no participation for this occurrence
    (see module docstring "Participation dependency" for which of the
    two canonical participation tables applies)."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} has no participation for this occurrence")
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


class AttendanceNotFoundError(AttendanceError):
    """Correction requires an existing Attendance record — it never
    creates one (a correction is "previous value -> corrected value",
    which is meaningless with no previous record)."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"No Attendance record exists for person {person_id} on this occurrence")
        self.person_id = person_id


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _participant_overlaps_occurrence(
    valid_from: Any, valid_to: Any, *, occurrence: EventOccurrence
) -> sa.ColumnElement[bool]:
    """Whether a `[valid_from, valid_to)` participation interval overlaps
    the occurrence's own `[starts_at, ends_at)` window — i.e. whether the
    person *was* a participant of this occurrence at some point, as
    opposed to `_active_interval`'s "is a participant right now".

    Correction and the historical GET roster/summary must key off this,
    not `now()`: ending a participation after the occurrence happened
    must not retroactively remove the person from the occurrence's
    accepted attendance history, and a person whose participation only
    starts after the occurrence's window has closed must not silently
    enter that history as an "unmarked" participant. See PR #97 review
    (Nakagawa-master, 2026-09-15)."""
    return sa.and_(
        valid_from < occurrence.ends_at,
        sa.or_(valid_to.is_(None), valid_to > occurrence.starts_at),
    )


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


# --- Object resolution -------------------------------------------------------


def resolve_attendance_target(session: Session, event_id: uuid.UUID) -> Optional[EventOccurrence]:
    """Deterministically resolve the `{event_id}` path parameter to a
    concrete `EventOccurrence` — see module docstring "Object
    resolution" (ADR-0033 §5). `None` if `event_id` names neither an
    `Event` nor a genuinely recurring `EventOccurrence` (404)."""
    event = session.get(Event, event_id)
    if event is not None:
        return session.execute(
            sa.select(EventOccurrence).where(EventOccurrence.event_id == event.id)
        ).scalar_one()

    return session.execute(
        sa.select(EventOccurrence).where(
            EventOccurrence.id == event_id, EventOccurrence.series_id.is_not(None)
        )
    ).scalar_one_or_none()


def eligible_for_normal_change(status: str) -> bool:
    return status in ATTENDANCE_ELIGIBLE_OCCURRENCE_STATUSES


def check_lifecycle_for_normal_change(status: str) -> None:
    if status == _COMPLETED_STATUS:
        raise AttendanceNormalWindowClosedError("Object is completed; use the correction endpoint")
    if not eligible_for_normal_change(status):
        raise AttendanceLifecycleClosedError(f"Attendance is closed for status {status!r}")


def check_lifecycle_for_correction(status: str) -> None:
    if eligible_for_normal_change(status):
        raise AttendanceUseNormalEndpointError(
            "Object is still open for normal changes; use the normal endpoint"
        )
    if status != _COMPLETED_STATUS:
        raise AttendanceLifecycleClosedError(f"Attendance is closed for status {status!r}")


# --- Participant dependency (ADR-0032 §1/§6) --------------------------------


def has_participation(
    session: Session, *, occurrence: EventOccurrence, person_id: uuid.UUID
) -> bool:
    """ADR-0033 §5: for the occurrence backing an ordinary, non-recurring
    `Event` (`occurrence.event_id` set), the canonical participation
    source is the Event-level `EventParticipation` table — the same
    duality already established everywhere else in this codebase
    (`EventStaffAssignment`/`EventOccurrenceStaffAssignment`,
    `EventGroupTarget`/`EventOccurrenceGroupTarget`). For a genuinely
    recurring occurrence (`series_id` set), it remains
    `EventOccurrenceParticipant`, but eligibility is `EventOccurrenceParticipant`'s
    `[valid_from, valid_to)` *overlapping the occurrence's own
    `[starts_at, ends_at)` window* (`_participant_overlaps_occurrence`),
    not "is the person a participant right now". This is what
    `correct_attendance` relies on to remain possible after a
    participation has since ended — a correction is inherently about a
    completed occurrence in the past, so "was a participant of this
    occurrence" must be evaluated against the occurrence's own time, not
    the current moment."""
    if occurrence.event_id is not None:
        return bool(
            session.execute(
                sa.select(
                    sa.exists(
                        sa.select(EventParticipation.id).where(
                            EventParticipation.event_id == occurrence.event_id,
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
                        EventOccurrenceParticipant.occurrence_id == occurrence.id,
                        EventOccurrenceParticipant.person_id == person_id,
                        _participant_overlaps_occurrence(
                            EventOccurrenceParticipant.valid_from,
                            EventOccurrenceParticipant.valid_to,
                            occurrence=occurrence,
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


def _new_attendance(
    *,
    occurrence_id: uuid.UUID,
    person_id: uuid.UUID,
    status: str,
    absence_reason: Optional[str],
    comment: Optional[str],
) -> Attendance:
    return Attendance(
        occurrence_id=occurrence_id,
        person_id=person_id,
        status=status,
        absence_reason=absence_reason,
        comment=comment,
    )


# --- Single upsert (ADR-0032 §1/§5) -----------------------------------------


def upsert_attendance(
    session: Session,
    *,
    occurrence: EventOccurrence,
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
        .where(Attendance.occurrence_id == occurrence.id, Attendance.person_id == person_id)
        .with_for_update()
    ).scalar_one_or_none()
    previous_status = existing.status if existing is not None else None

    try:
        if existing is None:
            row = _new_attendance(
                occurrence_id=occurrence.id,
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
    occurrence: EventOccurrence,
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
        if not has_participation(session, occurrence=occurrence, person_id=item.person_id):
            raise AttendanceParticipationMissingError(person_id=item.person_id)

    results: list[tuple[Attendance, bool]] = []
    change_summaries: list[dict[str, Any]] = []
    try:
        for item in items:
            existing = session.execute(
                sa.select(Attendance)
                .where(
                    Attendance.occurrence_id == occurrence.id,
                    Attendance.person_id == item.person_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            previous_status = existing.status if existing is not None else None
            if existing is None:
                row = _new_attendance(
                    occurrence_id=occurrence.id,
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
            # audited "resource" is the Occurrence it was bulk-updated
            # for, not any single created/changed row (ADR-0024 §2
            # requires resource_type/resource_id to both be set or both
            # be None; the per-row detail lives in `details.changes`
            # below). "event_occurrence" matches the resource_type
            # spelling already used for EventOccurrence elsewhere
            # (app.events.series_service).
            resource_type="event_occurrence",
            resource_id=occurrence.id,
            outcome="success",
            request_id=request_id,
            details={
                "occurrence_id": str(occurrence.id),
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
    occurrence: EventOccurrence,
    club_id: uuid.UUID,
    person_id: uuid.UUID,
    status: str,
    absence_reason: Optional[str],
    comment: Optional[str],
    reason: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[Attendance, Optional[str]]:
    """Correct an *existing* Attendance record for one Person after the
    normal window has closed. Never creates a first record (a correction
    is "previous value -> corrected value"; with no previous value there
    is nothing to correct — raises `AttendanceNotFoundError` instead).
    Requires `attendance.update` only (no `attendance.correct` permission
    exists — ADR-0032 §8) and a mandatory `reason`, checked by the
    caller's permission dependency and here respectively. Returns `(row,
    previous_status)`.
    """
    if not reason or not reason.strip():
        raise AttendanceCorrectionReasonRequiredError("A correction reason is required")
    validate_attendance_fields(status=status, absence_reason=absence_reason, comment=comment)
    if not has_participation(session, occurrence=occurrence, person_id=person_id):
        raise AttendanceParticipationMissingError(person_id=person_id)

    existing = session.execute(
        sa.select(Attendance)
        .where(Attendance.occurrence_id == occurrence.id, Attendance.person_id == person_id)
        .with_for_update()
    ).scalar_one_or_none()
    if existing is None:
        raise AttendanceNotFoundError(person_id=person_id)
    previous_status = existing.status

    try:
        existing.status = status
        existing.absence_reason = absence_reason
        existing.comment = comment
        session.flush()
        record_audit_event(
            session,
            action="attendance.corrected",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type="attendance",
            resource_id=existing.id,
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
    return existing, previous_status


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
    """Mirrors app.events.series_authorization._child_condition's shape,
    but as a per-row predicate against `participant_person_id_column` (a
    real column of the enclosing participant query) rather than one
    fixed, already-known child id — see module docstring "Participant
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
    Person rows, within one already object-level-authorized Occurrence,
    the requester may see. `all`/`own_events`/`own_groups` (already true
    at the object level, per `resource_context`) grant the full set;
    `self`/`children` restrict to the requester's own/their children's
    rows; `none` contributes nothing."""
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
    occurrence: EventOccurrence,
    club_id: uuid.UUID,
    resource_context: ResourceContext,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
) -> tuple[list[AttendanceEntry], AttendanceSummary, int]:
    """The full participant set visible to the requester (ADR-0032 §9),
    including participants without an Attendance record (`status=None`,
    "unmarked"). Authorization (`_attendance_row_visibility`) is applied
    inside the SQL query itself, before any count/summary/pagination/
    serialization — never fetch-then-filter, per ADR-0032 §8's explicit
    "no leak" requirement.

    The participant roster source follows the same event_id/series_id
    duality as `has_participation` (ADR-0033 §5): `EventParticipation`
    for the occurrence backing an ordinary Event, `EventOccurrenceParticipant`
    for a genuinely recurring occurrence.

    For the recurring branch, roster membership is `EventOccurrenceParticipant`'s
    `[valid_from, valid_to)` *overlapping the occurrence's own
    `[starts_at, ends_at)` window*, not "is currently an active
    participant". Otherwise ending a participation after the occurrence
    happened would retroactively drop that person — and their already-
    persisted Attendance row — out of this occurrence's roster/summary,
    even though the row itself is still in the table (the persisted row
    surviving participation ending is not the same guarantee as it
    remaining *visible and counted* here). Symmetrically, a person whose
    participation only starts after the occurrence's window has already
    closed does not enter the denominator as "unmarked".
    """
    if occurrence.event_id is not None:
        participants = (
            sa.select(EventParticipation.person_id.label("person_id"))
            .where(EventParticipation.event_id == occurrence.event_id)
            .distinct()
            .subquery("participants")
        )
    else:
        participants = (
            sa.select(EventOccurrenceParticipant.person_id.label("person_id"))
            .where(
                EventOccurrenceParticipant.occurrence_id == occurrence.id,
                _participant_overlaps_occurrence(
                    EventOccurrenceParticipant.valid_from,
                    EventOccurrenceParticipant.valid_to,
                    occurrence=occurrence,
                ),
            )
            .distinct()
            .subquery("participants")
        )

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
                Attendance.occurrence_id == occurrence.id,
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
    "ATTENDANCE_ELIGIBLE_OCCURRENCE_STATUSES",
    "AttendanceError",
    "AttendanceLifecycleClosedError",
    "AttendanceNormalWindowClosedError",
    "AttendanceUseNormalEndpointError",
    "AttendanceParticipationMissingError",
    "AttendanceInvalidDataError",
    "AttendanceDuplicatePersonError",
    "AttendanceCorrectionReasonRequiredError",
    "AttendanceNotFoundError",
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
