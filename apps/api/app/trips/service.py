"""Trip / TripParticipant mutations (Issue #245).

Canonical sources: docs/04-modules/trips-and-tourist-profile.md §2/§8/
§21/§27, docs/04-modules/events-and-schedule.md §22, ADR-0018 (Event
lifecycle), ADR-0032 (Attendance stays a separate operational fact),
ADR-0037 (EventParticipation is the registration source), and the
Issue #245 PO/CTO decisions.

## Trip creation

A Trip extends an existing ordinary Event. Allowed only when the Event's
`event_type` is `trip` and its status is not `cancelled`/`archived`; at
most one Trip per Event (`pk_trips`). The Trip has no lifecycle of its
own — every lifecycle question is answered by `Event.status`.

## TourismType (Issue #264, trips-and-tourist-profile.md §3)

A Trip references 0..1 TourismType catalog entry (`tourism_type_id`).
Assigning or changing it — at creation or through ordinary Trip editing
— requires the entry to exist and be active
(app.trips.tourism_types.resolve_assignable_tourism_type); clearing it is
always allowed while editing is open. Ordinary editing of the TourismType
is open while the Event is `draft`/`published`/`in_progress`; a
`completed` Trip's TourismType is a historical fact (changes go through
the future correction workflow), and `cancelled`/`archived` are closed
exactly as for Trip creation. Deactivating a catalog entry never touches
Trips already referencing it. The TourismType is never derived from any
other fact.

## actual_participation

Recorded per EventParticipation of the Trip's Event (any
`registration_status`: the registration and the tourism fact are
different facts, PO decision GAP-A). Lifecycle (PO decision GAP-B),
keyed on the Event's status:

- `in_progress`: create or change;
- `completed`: create a not-yet-recorded fact; an existing TripParticipant
  is historically closed — repeating its current value is an idempotent
  no-op, any change is rejected (the correction workflow is a future
  task, G9);
- in every open status, repeating the stored value writes nothing;
- `draft`/`published`/`cancelled`/`archived`: closed.

## Audit (ADR-0024 §4, amended by #247)

Each successful mutation records exactly one AuditLog row through
`app.audit.service.record_audit_event`, in the same transaction as the
mutation (fail-closed: an audit failure rolls the mutation back):

- Trip created -> `trip.created` (resource `trip` / `Trip.event_id`);
- first TripParticipant for an EventParticipation ->
  `trip_participant.actual_participation_recorded`;
- stored `actual_participation` changed ->
  `trip_participant.actual_participation_changed` with
  `details.changes.actual_participation.{from,to}`;

both TripParticipant actions use resource `trip_participant` /
`event_participation_id`. Writing the already stored value is a no-op
without an audit record, in every open Event status. Registration
changes stay `event_participation.status_changed`
(app.events.participation) and are never duplicated here.

Callers must already have loaded (and locked) the Event and verified
authorization; this module never performs authorization itself.
"""

import uuid
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.achievements import triggers as achievement_triggers
from app.audit.service import record_audit_event
from app.db.events import Event, EventParticipation
from app.db.trips import TRIP_EVENT_TYPE, TRIP_PRIMARY_KEY, Trip, TripParticipant
from app.trips.tourism_types import resolve_assignable_tourism_type

TRIP_CREATION_CLOSED_EVENT_STATUSES: tuple[str, ...] = ("cancelled", "archived")
TRIP_EDITING_OPEN_EVENT_STATUSES: tuple[str, ...] = ("draft", "published", "in_progress")
ACTUAL_PARTICIPATION_OPEN_EVENT_STATUSES: tuple[str, ...] = ("in_progress", "completed")
_COMPLETED_STATUS = "completed"

# ADR-0024 §2 resource identity: a Trip is identified by its Event's id
# (`trips.event_id`), a TripParticipant by its EventParticipation's id
# (`trip_participants.event_participation_id`).
TRIP_AUDIT_RESOURCE_TYPE = "trip"
TRIP_PARTICIPANT_AUDIT_RESOURCE_TYPE = "trip_participant"


class TripError(Exception):
    """Base class for this module's typed, expected failures."""


class EventNotTripError(TripError):
    """BR-TRIP-001: a Trip extends only an Event of type `trip`."""

    def __init__(self, *, event_id: uuid.UUID, event_type: str) -> None:
        super().__init__(f"Event {event_id} has type {event_type!r}, not {TRIP_EVENT_TYPE!r}")
        self.event_id = event_id
        self.event_type = event_type


class TripEventLifecycleClosedError(TripError):
    """A Trip cannot be created for a `cancelled`/`archived` Event."""

    def __init__(self, *, event_id: uuid.UUID, status: str) -> None:
        super().__init__(f"Cannot create a Trip for Event {event_id} with status {status!r}")
        self.event_id = event_id
        self.status = status


class TripAlreadyExistsError(TripError):
    """At most one Trip per Event."""

    def __init__(self, *, event_id: uuid.UUID) -> None:
        super().__init__(f"Event {event_id} already has a Trip")
        self.event_id = event_id


class TripEditingClosedError(TripError):
    """Ordinary Trip editing is closed for the Event's status
    (`completed` — historical; `cancelled`/`archived` — closed)."""

    def __init__(self, *, event_id: uuid.UUID, status: str) -> None:
        super().__init__(f"Trip of Event {event_id} with status {status!r} cannot be edited")
        self.event_id = event_id
        self.status = status


class TripParticipationMissingError(TripError):
    """The Person has no EventParticipation for the Trip's Event."""

    def __init__(self, *, event_id: uuid.UUID, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} has no participation for Event {event_id}")
        self.event_id = event_id
        self.person_id = person_id


class ActualParticipationLifecycleClosedError(TripError):
    """The Event's status does not allow recording actual participation."""

    def __init__(self, *, event_id: uuid.UUID, status: str) -> None:
        super().__init__(
            f"Actual participation cannot be recorded for Event {event_id} with status {status!r}"
        )
        self.event_id = event_id
        self.status = status


class TripParticipantHistoricallyClosedError(TripError):
    """The Event is `completed` and the fact is already recorded: no
    normal change (correction workflow not implemented)."""

    def __init__(self, *, event_id: uuid.UUID, person_id: uuid.UUID) -> None:
        super().__init__(
            f"Actual participation of Person {person_id} for completed Event {event_id} "
            "is historically closed"
        )
        self.event_id = event_id
        self.person_id = person_id


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def get_trip(session: Session, event_id: uuid.UUID) -> Optional[Trip]:
    return session.get(Trip, event_id)


def create_trip(
    session: Session,
    *,
    event: Event,
    actor_user_id: uuid.UUID,
    tourism_type_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
) -> Trip:
    """Attach a Trip to `event` (already loaded and locked by the caller),
    optionally with an active TourismType, and record `trip.created` in
    the same transaction (see module docstring "Audit")."""
    if event.event_type != TRIP_EVENT_TYPE:
        raise EventNotTripError(event_id=event.id, event_type=event.event_type)
    if event.status in TRIP_CREATION_CLOSED_EVENT_STATUSES:
        raise TripEventLifecycleClosedError(event_id=event.id, status=event.status)
    if get_trip(session, event.id) is not None:
        raise TripAlreadyExistsError(event_id=event.id)
    if tourism_type_id is not None:
        resolve_assignable_tourism_type(session, tourism_type_id)

    event_id = event.id
    try:
        trip = Trip(event_id=event_id, event_type=event.event_type, tourism_type_id=tourism_type_id)
        session.add(trip)
        session.flush()
        record_audit_event(
            session,
            action="trip.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=event.club_id,
            resource_type=TRIP_AUDIT_RESOURCE_TYPE,
            resource_id=event_id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == TRIP_PRIMARY_KEY:
            raise TripAlreadyExistsError(event_id=event_id) from exc
        raise
    except Exception:
        session.rollback()
        raise
    return trip


def set_trip_tourism_type(
    session: Session, *, event: Event, trip: Trip, tourism_type_id: Optional[uuid.UUID]
) -> Trip:
    """Ordinary Trip editing of the TourismType (see module docstring).
    `event` is already loaded and locked by the caller. Setting the value
    already stored is a no-op."""
    if event.status not in TRIP_EDITING_OPEN_EVENT_STATUSES:
        raise TripEditingClosedError(event_id=event.id, status=event.status)
    if trip.tourism_type_id == tourism_type_id:
        return trip
    if tourism_type_id is not None:
        resolve_assignable_tourism_type(session, tourism_type_id)
    try:
        trip.tourism_type_id = tourism_type_id
        session.commit()
    except Exception:
        session.rollback()
        raise
    return trip


def record_actual_participation(
    session: Session,
    *,
    event: Event,
    person_id: uuid.UUID,
    actual_participation: bool,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> TripParticipant:
    """Create or change the TripParticipant of `person_id`'s
    EventParticipation for `event` (which must already have a Trip; the
    caller loaded and locked the Event), recording the matching audit
    action in the same transaction. Writing the already stored value is
    an idempotent no-op: nothing is written and no audit record is
    created. See module docstring for the lifecycle and audit rules.
    Never touches the EventParticipation itself."""
    if event.status not in ACTUAL_PARTICIPATION_OPEN_EVENT_STATUSES:
        raise ActualParticipationLifecycleClosedError(event_id=event.id, status=event.status)

    participation_id = session.execute(
        sa.select(EventParticipation.id).where(
            EventParticipation.event_id == event.id,
            EventParticipation.person_id == person_id,
        )
    ).scalar_one_or_none()
    if participation_id is None:
        raise TripParticipationMissingError(event_id=event.id, person_id=person_id)

    existing = session.execute(
        sa.select(TripParticipant)
        .where(TripParticipant.event_participation_id == participation_id)
        .with_for_update()
    ).scalar_one_or_none()

    if existing is not None and existing.actual_participation == actual_participation:
        return existing
    if existing is not None and event.status == _COMPLETED_STATUS:
        raise TripParticipantHistoricallyClosedError(event_id=event.id, person_id=person_id)

    try:
        if existing is None:
            row = TripParticipant(
                event_participation_id=participation_id,
                event_id=event.id,
                actual_participation=actual_participation,
            )
            session.add(row)
            action = "trip_participant.actual_participation_recorded"
            details: dict[str, Any] = {
                "event_id": str(event.id),
                "event_participation_id": str(participation_id),
                "person_id": str(person_id),
                "actual_participation": actual_participation,
            }
        else:
            row = existing
            previous = row.actual_participation
            row.actual_participation = actual_participation
            action = "trip_participant.actual_participation_changed"
            details = {
                "event_id": str(event.id),
                "event_participation_id": str(participation_id),
                "person_id": str(person_id),
                "changes": {"actual_participation": {"from": previous, "to": actual_participation}},
            }
        session.flush()
        record_audit_event(
            session,
            action=action,
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=event.club_id,
            resource_type=TRIP_PARTICIPANT_AUDIT_RESOURCE_TYPE,
            resource_id=participation_id,
            outcome="success",
            request_id=request_id,
            details=details,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    # Issue #220 (A7): `actual_participation` feeds the `completed_trips`
    # achievement metric — evaluated after commit, never blocking this
    # canonical fact.
    achievement_triggers.trip_participation_changed(session, person_id=person_id)
    return row


__all__ = [
    "TRIP_CREATION_CLOSED_EVENT_STATUSES",
    "TRIP_EDITING_OPEN_EVENT_STATUSES",
    "ACTUAL_PARTICIPATION_OPEN_EVENT_STATUSES",
    "TripError",
    "EventNotTripError",
    "TripEventLifecycleClosedError",
    "TripAlreadyExistsError",
    "TripEditingClosedError",
    "TripParticipationMissingError",
    "ActualParticipationLifecycleClosedError",
    "TripParticipantHistoricallyClosedError",
    "get_trip",
    "create_trip",
    "set_trip_tourism_type",
    "record_actual_participation",
]
