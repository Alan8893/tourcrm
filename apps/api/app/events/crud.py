"""Event CRUD and lifecycle service functions (Issue #40; occurrence sync
per ADR-0033).

Canonical sources: docs/03-architecture/adr/ADR-0018-event-lifecycle.md
(status graph, cancellation-requires-reason), docs/03-architecture/adr/
ADR-0019-event-field-model.md (field list), docs/02-requirements/
business-rules.md §10, docs/03-architecture/adr/ADR-0033-event-
occurrence-as-operational-instance.md (every Event has exactly one
EventOccurrence, created/kept in sync here).

Validation is delegated entirely to app.events.lifecycle — never
reimplemented here (that module already encodes the exact ADR-0018
transition graph and ADR-0019 field-level rules). This module performs
no authorization: the caller (the API router) must resolve a
ResourceContext and call Authorizer.check() before invoking any function
here, exactly as app.events.service and app.groups.service already keep
Club-ownership validation and authorization as two separate layers.

Row locking for concurrent mutations of the *same* Event (two overlapping
status transitions, or a transition racing an update) is the router's
responsibility: it must load the Event with `SELECT ... FOR UPDATE`
before calling update_event/transition_event_status/archive_event, so
these functions can assume the row is already exclusively locked for the
duration of the caller's transaction.

## Occurrence sync (ADR-0033)

`create_event` creates the Event's one linked `EventOccurrence` in the
same transaction (`event_id` set explicitly before either row is added,
so the FK is correct without depending on flush ordering — Python-side
`uuid.uuid4` defaults are only realized at flush time, not at
construction). `update_event`/`transition_event_status`/`archive_event`
each keep that same row's mirrored fields in sync in place — never a
second occurrence, never a deleted one.

`_EVENT_TO_OCCURRENCE_STATUS` maps `Event`'s status vocabulary onto
`EventOccurrence`'s narrower one (ADR-0033 §4): `published`/`in_progress`/
`completed`/`cancelled` map onto the identically-operational occurrence
status (`published -> scheduled`, matching the existing `published`/
`scheduled` operational-window pairing already established by
app.events.conflicts). `draft` maps to `scheduled` too — the occurrence
vocabulary has no pre-publication state, and eligibility for a `draft`
Event is decided by reading `Event.status` directly wherever it matters
(Calendar, Conflicts, Attendance's lifecycle gate), never by the mirrored
occurrence status — so this mapping never makes a draft Event
operationally visible through its occurrence. `archived` is intentionally
absent from the map: it is only reachable from `completed`/`cancelled`,
and archiving does not change the occurrence's own operational status
any further (there is no occurrence equivalent of "archived" to move
to).
"""

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventGroupTarget, EventStaffAssignment
from app.events.lifecycle import (
    validate_coordinates,
    validate_event_type,
    validate_status_transition,
    validate_time_range,
)
from app.events.service import build_event_group_target, build_event_staff_assignment

# ADR-0018: every Event starts as `draft`; no document describes a
# "create directly as published" path, and `status` is never
# client-controlled at creation.
INITIAL_EVENT_STATUS = "draft"

# ADR-0018 / roles-and-permissions.md §12: archiving is reachable only via
# the dedicated archive endpoint (`event.manage`), never through the
# general status-transition endpoint.
ARCHIVED_STATUS = "archived"

# TH-0108 / ADR-0037 §2: the free-form `role_in_event` value the Event API
# assigns to every responsible instructor/User it creates — the form has
# no per-assignment role selector, matching the dominant convention already
# used across this codebase's own EventStaffAssignment tests/fixtures.
DEFAULT_STAFF_ROLE_IN_EVENT = "instructor"

# The PATCH-writable Event fields (Issue #40): `status`/`cancellation_reason`
# only change through transition_event_status()/archive_event(); `id`,
# `club_id`, `created_by`/`created_at`/`updated_at` are immutable via PATCH;
# `updated_by` is always server-set from the authenticated principal.
UPDATABLE_EVENT_FIELDS = frozenset(
    {
        "event_type",
        "title",
        "description",
        "start_at",
        "end_at",
        "timezone",
        "location_type",
        "location_name",
        "location_address",
        "location_latitude",
        "location_longitude",
    }
)


# ADR-0033 §4 — see module docstring "Occurrence sync" for the rationale.
_EVENT_TO_OCCURRENCE_STATUS: dict[str, str] = {
    "draft": "scheduled",
    "published": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
}

# Event field name -> EventOccurrence field name, for the mirrored
# snapshot fields (ADR-0033 §3). Only fields both models actually carry;
# Event's location fields have no occurrence equivalent.
_EVENT_TO_OCCURRENCE_FIELD = {
    "title": "name",
    "description": "description",
    "event_type": "event_type",
    "start_at": "starts_at",
    "end_at": "ends_at",
    "timezone": "timezone",
}


class EventTransitionNotAllowedError(Exception):
    """Raised when a caller attempts to reach `archived` through
    transition_event_status() instead of the dedicated archive_event().
    """


def _get_linked_occurrence(session: Session, *, event_id: uuid.UUID) -> EventOccurrence:
    """The one EventOccurrence ADR-0033 §1 guarantees exists for every
    Event, locked for the duration of the caller's transaction (the
    caller has already locked `event` itself; locking the occurrence too
    keeps the two rows' sync atomic under concurrent writers)."""
    return session.execute(
        sa.select(EventOccurrence).where(EventOccurrence.event_id == event_id).with_for_update()
    ).scalar_one()


def create_event(
    session: Session,
    *,
    club_id: uuid.UUID,
    event_type: str,
    title: str,
    description: Optional[str],
    start_at: datetime,
    end_at: datetime,
    timezone: str,
    location_type: Optional[str],
    location_name: Optional[str],
    location_address: Optional[str],
    location_latitude: Optional[float],
    location_longitude: Optional[float],
    created_by: uuid.UUID,
) -> Event:
    """Create a new Event, always starting in `draft` with no
    cancellation reason, `created_by == updated_by == created_by` — plus
    its one linked `EventOccurrence` (ADR-0033 §1/§3), in the same
    transaction.
    """
    validate_event_type(event_type)
    validate_time_range(start_at, end_at)
    validate_coordinates(location_latitude, location_longitude)

    # Assigned explicitly, not left to the ORM's flush-time default, so
    # the occurrence's `event_id` FK is correct without depending on
    # flush ordering (see module docstring "Occurrence sync").
    event_id = uuid.uuid4()
    event = Event(
        id=event_id,
        club_id=club_id,
        event_type=event_type,
        title=title,
        description=description,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
        location_type=location_type,
        location_name=location_name,
        location_address=location_address,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        status=INITIAL_EVENT_STATUS,
        cancellation_reason=None,
        created_by=created_by,
        updated_by=created_by,
    )
    occurrence = EventOccurrence(
        event_id=event_id,
        series_id=None,
        club_id=club_id,
        name=title,
        description=description,
        event_type=event_type,
        recurrence_anchor_at=start_at,
        starts_at=start_at,
        ends_at=end_at,
        timezone=timezone,
        status=_EVENT_TO_OCCURRENCE_STATUS[INITIAL_EVENT_STATUS],
        cancellation_reason=None,
        created_by=created_by,
        updated_by=created_by,
    )
    # Explicit flush between the two inserts: SQLAlchemy's unit-of-work
    # only auto-orders inserts across a mapped `relationship()`, not a
    # bare UUID value that happens to match another row's PK — without
    # this, `occurrence`'s FK insert could run before `event`'s.
    session.add(event)
    session.flush()
    session.add(occurrence)
    session.commit()
    return event


def _active_event_group_targets(
    session: Session, *, event_id: uuid.UUID
) -> dict[uuid.UUID, EventGroupTarget]:
    """Currently-active (validity-interval sense) EventGroupTarget rows
    for `event_id`, keyed by `group_id` — the same active-interval
    predicate app.events.authorization uses for visibility, duplicated
    here as a small one-liner per this codebase's own convention rather
    than importing across the crud/authorization boundary.
    """
    now = datetime.now(dt_timezone.utc)
    rows = (
        session.execute(
            sa.select(EventGroupTarget).where(
                EventGroupTarget.event_id == event_id,
                EventGroupTarget.valid_from <= now,
                sa.or_(EventGroupTarget.valid_to.is_(None), EventGroupTarget.valid_to > now),
            )
        )
        .scalars()
        .all()
    )
    return {row.group_id: row for row in rows}


def _active_event_staff_assignments(
    session: Session, *, event_id: uuid.UUID
) -> dict[uuid.UUID, EventStaffAssignment]:
    """Currently-active EventStaffAssignment rows for `event_id`, keyed
    by `user_id` — see `_active_event_group_targets`'s docstring."""
    now = datetime.now(dt_timezone.utc)
    rows = (
        session.execute(
            sa.select(EventStaffAssignment).where(
                EventStaffAssignment.event_id == event_id,
                EventStaffAssignment.valid_from <= now,
                sa.or_(
                    EventStaffAssignment.valid_to.is_(None), EventStaffAssignment.valid_to > now
                ),
            )
        )
        .scalars()
        .all()
    )
    return {row.user_id: row for row in rows}


def _apply_event_group_targets(
    session: Session, *, event_id: uuid.UUID, group_ids: Sequence[uuid.UUID], now: datetime
) -> None:
    """Synchronizes EventGroupTarget to exactly `group_ids` (deduplicated,
    order-preserving): adds a new active row for each newly-desired
    Group, ends (`valid_to = now`, never deletes) each currently-active
    row whose Group is no longer desired, and leaves an already-active,
    still-desired row untouched — so a repeated call with the same
    `group_ids` is a no-op (Test 11's idempotency requirement) and never
    creates a duplicate active row for the same (event, group) pair.
    `group_ids=[]` means club-wide (ADR-0037 §1/§5): every currently
    active target is ended, none added.
    """
    desired = list(dict.fromkeys(group_ids))
    desired_set = set(desired)
    active = _active_event_group_targets(session, event_id=event_id)
    for group_id, row in active.items():
        if group_id not in desired_set:
            row.valid_to = now
    for group_id in desired:
        if group_id not in active:
            session.add(
                build_event_group_target(
                    session, event_id=event_id, group_id=group_id, valid_from=now
                )
            )


def _apply_event_staff_assignments(
    session: Session,
    *,
    event_id: uuid.UUID,
    user_ids: Sequence[uuid.UUID],
    role_in_event: str,
    now: datetime,
) -> None:
    """Synchronizes EventStaffAssignment to exactly `user_ids` — see
    `_apply_event_group_targets`'s docstring for the identical add/end/
    leave-untouched idempotency shape. Every newly-created assignment is
    `is_primary=False`: this API exposes no primary-instructor selector.
    """
    desired = list(dict.fromkeys(user_ids))
    desired_set = set(desired)
    active = _active_event_staff_assignments(session, event_id=event_id)
    for user_id, row in active.items():
        if user_id not in desired_set:
            row.valid_to = now
    for user_id in desired:
        if user_id not in active:
            session.add(
                build_event_staff_assignment(
                    session,
                    event_id=event_id,
                    user_id=user_id,
                    role_in_event=role_in_event,
                    valid_from=now,
                    is_primary=False,
                )
            )


def create_event_with_targeting(
    session: Session,
    *,
    club_id: uuid.UUID,
    event_type: str,
    title: str,
    description: Optional[str],
    start_at: datetime,
    end_at: datetime,
    timezone: str,
    location_type: Optional[str],
    location_name: Optional[str],
    location_address: Optional[str],
    location_latitude: Optional[float],
    location_longitude: Optional[float],
    created_by: uuid.UUID,
    group_ids: Sequence[uuid.UUID] = (),
    instructor_user_ids: Sequence[uuid.UUID] = (),
    role_in_event: str = DEFAULT_STAFF_ROLE_IN_EVENT,
) -> Event:
    """TH-0108 / ADR-0037 §1-§2: `POST /events`'s actual operation —
    create the Event (plus its linked EventOccurrence, exactly like
    `create_event`) and its initial Group targeting / responsible-
    instructor assignments, all atomically in one transaction. Any
    failure — an invalid field, a cross-Club Group, a nonexistent Group/
    User, or a User without active ClubMembership in this Club — rolls
    back the Event (and its occurrence) too: no partially-created Event
    is ever left behind.

    Deliberately does not call `create_event()`/`create_event_group_target()`/
    `create_event_staff_assignment()`: each of those commits its own
    transaction (this module's and app.events.service's own established
    pattern), which would make the Event and its targeting independently
    committable and reopen the exact non-atomic gap this function closes
    — mirroring app.people.service.create_person_with_membership's
    identical rationale for Person + its initial ClubMembership.

    `group_ids=()` (the default) means club-wide (ADR-0037 §1/§5): zero
    EventGroupTarget rows are created. `instructor_user_ids=()` means no
    responsible User is assigned yet — both are valid, independent
    states. Group targeting and instructor assignment never create
    GroupMembership, EventParticipation, or any other relationship —
    this function touches only Event, EventOccurrence, EventGroupTarget
    and EventStaffAssignment.
    """
    validate_event_type(event_type)
    validate_time_range(start_at, end_at)
    validate_coordinates(location_latitude, location_longitude)

    event_id = uuid.uuid4()
    event = Event(
        id=event_id,
        club_id=club_id,
        event_type=event_type,
        title=title,
        description=description,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
        location_type=location_type,
        location_name=location_name,
        location_address=location_address,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        status=INITIAL_EVENT_STATUS,
        cancellation_reason=None,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(event)
    try:
        session.flush()
        occurrence = EventOccurrence(
            event_id=event_id,
            series_id=None,
            club_id=club_id,
            name=title,
            description=description,
            event_type=event_type,
            recurrence_anchor_at=start_at,
            starts_at=start_at,
            ends_at=end_at,
            timezone=timezone,
            status=_EVENT_TO_OCCURRENCE_STATUS[INITIAL_EVENT_STATUS],
            cancellation_reason=None,
            created_by=created_by,
            updated_by=created_by,
        )
        session.add(occurrence)

        now = datetime.now(dt_timezone.utc)
        _apply_event_group_targets(session, event_id=event_id, group_ids=group_ids, now=now)
        _apply_event_staff_assignments(
            session,
            event_id=event_id,
            user_ids=instructor_user_ids,
            role_in_event=role_in_event,
            now=now,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return event


def _apply_event_field_updates(
    session: Session,
    *,
    event: Event,
    updated_by: uuid.UUID,
    **fields,
) -> None:
    """The no-commit body of `update_event` — validates and applies a
    partial update of the client-writable Event fields, keeping the
    linked `EventOccurrence`'s mirrored fields in sync (ADR-0033 §3).
    Extracted so `update_event_with_targeting` can compose it with the
    EventGroupTarget/EventStaffAssignment sync below inside one shared
    transaction, the same shape `create_event_with_targeting` uses.
    """
    unknown_fields = set(fields) - UPDATABLE_EVENT_FIELDS
    if unknown_fields:
        raise ValueError(f"Fields not updatable via update_event: {sorted(unknown_fields)}")

    if "event_type" in fields:
        validate_event_type(fields["event_type"])

    if "start_at" in fields or "end_at" in fields:
        start_at = fields.get("start_at", event.start_at)
        end_at = fields.get("end_at", event.end_at)
        validate_time_range(start_at, end_at)

    if "location_latitude" in fields or "location_longitude" in fields:
        latitude = fields.get("location_latitude", event.location_latitude)
        longitude = fields.get("location_longitude", event.location_longitude)
        validate_coordinates(latitude, longitude)

    for field_name, value in fields.items():
        setattr(event, field_name, value)
    event.updated_by = updated_by

    occurrence = _get_linked_occurrence(session, event_id=event.id)
    for field_name, value in fields.items():
        occurrence_field = _EVENT_TO_OCCURRENCE_FIELD.get(field_name)
        if occurrence_field is not None:
            setattr(occurrence, occurrence_field, value)
    occurrence.updated_by = updated_by


def update_event(
    session: Session,
    *,
    event: Event,
    updated_by: uuid.UUID,
    **fields,
) -> Event:
    """Apply a partial update (PATCH) of the client-writable Event
    fields, keeping the linked `EventOccurrence`'s mirrored fields in
    sync in the same transaction (ADR-0033 §3 — the same row, in place;
    never a new occurrence, never a deleted one). `status`/
    `cancellation_reason` are never accepted here — see
    transition_event_status()/archive_event() for lifecycle changes.
    """
    _apply_event_field_updates(session, event=event, updated_by=updated_by, **fields)
    session.commit()
    return event


def update_event_with_targeting(
    session: Session,
    *,
    event: Event,
    updated_by: uuid.UUID,
    group_ids: Optional[Sequence[uuid.UUID]] = None,
    instructor_user_ids: Optional[Sequence[uuid.UUID]] = None,
    role_in_event: str = DEFAULT_STAFF_ROLE_IN_EVENT,
    **fields,
) -> Event:
    """TH-0108 / ADR-0037 §1-§2: PATCH counterpart of
    `create_event_with_targeting` — applies the same Event field updates
    as `update_event`, and additionally synchronizes EventGroupTarget/
    EventStaffAssignment to the given desired sets, all committed
    together in the one transaction this function owns; any failure
    (an invalid field, a cross-Club Group, a nonexistent Group/User, or
    a User without active ClubMembership) rolls back the field changes
    too — mirroring `create_event_with_targeting`'s identical rationale.

    `group_ids=None`/`instructor_user_ids=None` (the defaults, matching
    the router's `exclude_unset` PATCH semantics — the field was simply
    not present in the request body) leave the current targeting/
    assignments completely untouched. An explicit list — including an
    empty one, meaning "make this Event club-wide" / "unassign every
    instructor" — replaces the currently active set via
    `_apply_event_group_targets`/`_apply_event_staff_assignments`: see
    those functions' docstrings for the add/end/leave-untouched
    idempotency shape.
    """
    _apply_event_field_updates(session, event=event, updated_by=updated_by, **fields)

    try:
        now = datetime.now(dt_timezone.utc)
        if group_ids is not None:
            _apply_event_group_targets(session, event_id=event.id, group_ids=group_ids, now=now)
        if instructor_user_ids is not None:
            _apply_event_staff_assignments(
                session,
                event_id=event.id,
                user_ids=instructor_user_ids,
                role_in_event=role_in_event,
                now=now,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return event


def transition_event_status(
    session: Session,
    *,
    event: Event,
    new_status: str,
    cancellation_reason: Optional[str],
    updated_by: uuid.UUID,
) -> Event:
    """Move `event` to `new_status` per ADR-0018's exact transition
    graph. Raises EventTransitionNotAllowedError (persisting nothing) for
    a request targeting `archived` — that transition is reachable only
    through archive_event(), which alone requires `event.manage`.
    """
    if new_status == ARCHIVED_STATUS:
        raise EventTransitionNotAllowedError(
            "Archiving must go through the dedicated archive endpoint (event.manage)"
        )
    validate_status_transition(event.status, new_status, cancellation_reason=cancellation_reason)
    event.status = new_status
    event.cancellation_reason = cancellation_reason if new_status == "cancelled" else None
    event.updated_by = updated_by

    # ADR-0033 §4: mirror onto the occurrence's own (narrower)
    # vocabulary — see module docstring "Occurrence sync".
    occurrence = _get_linked_occurrence(session, event_id=event.id)
    occurrence.status = _EVENT_TO_OCCURRENCE_STATUS[new_status]
    occurrence.cancellation_reason = event.cancellation_reason
    occurrence.updated_by = updated_by

    session.commit()
    return event


def archive_event(session: Session, *, event: Event, updated_by: uuid.UUID) -> Event:
    """Move `event` to `archived`. Only reachable from `completed` or
    `cancelled` (ADR-0018); the caller must have already checked
    `event.manage`, since this permission is the only one archiving
    requires (roles-and-permissions.md §12) — no separate `event.archive`
    permission exists. The linked occurrence's own status is left
    unchanged (ADR-0033 §4: there is no occurrence-status equivalent of
    `archived` to move to — it already reflects the `completed`/
    `cancelled` state archiving started from).
    """
    validate_status_transition(event.status, ARCHIVED_STATUS)
    event.status = ARCHIVED_STATUS
    event.updated_by = updated_by

    occurrence = _get_linked_occurrence(session, event_id=event.id)
    occurrence.updated_by = updated_by

    session.commit()
    return event


__all__ = [
    "INITIAL_EVENT_STATUS",
    "ARCHIVED_STATUS",
    "UPDATABLE_EVENT_FIELDS",
    "DEFAULT_STAFF_ROLE_IN_EVENT",
    "EventTransitionNotAllowedError",
    "create_event",
    "create_event_with_targeting",
    "update_event",
    "update_event_with_targeting",
    "transition_event_status",
    "archive_event",
]
