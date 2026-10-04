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
- `draft`/`published`/`cancelled`/`archived`: closed.

Callers must already have loaded (and locked) the Event and verified
authorization; this module never performs authorization itself.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.events import Event, EventParticipation
from app.db.trips import TRIP_EVENT_TYPE, TRIP_PRIMARY_KEY, Trip, TripParticipant

TRIP_CREATION_CLOSED_EVENT_STATUSES: tuple[str, ...] = ("cancelled", "archived")
ACTUAL_PARTICIPATION_OPEN_EVENT_STATUSES: tuple[str, ...] = ("in_progress", "completed")
_COMPLETED_STATUS = "completed"


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


def create_trip(session: Session, *, event: Event) -> Trip:
    """Attach a Trip to `event` (already loaded and locked by the caller)."""
    if event.event_type != TRIP_EVENT_TYPE:
        raise EventNotTripError(event_id=event.id, event_type=event.event_type)
    if event.status in TRIP_CREATION_CLOSED_EVENT_STATUSES:
        raise TripEventLifecycleClosedError(event_id=event.id, status=event.status)
    if get_trip(session, event.id) is not None:
        raise TripAlreadyExistsError(event_id=event.id)

    trip = Trip(event_id=event.id, event_type=event.event_type)
    session.add(trip)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == TRIP_PRIMARY_KEY:
            raise TripAlreadyExistsError(event_id=event.id) from exc
        raise
    return trip


def record_actual_participation(
    session: Session,
    *,
    event: Event,
    person_id: uuid.UUID,
    actual_participation: bool,
) -> TripParticipant:
    """Create or change the TripParticipant of `person_id`'s
    EventParticipation for `event` (which must already have a Trip; the
    caller loaded and locked the Event). See module docstring for the
    lifecycle rules. Never touches the EventParticipation itself."""
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

    if existing is not None and event.status == _COMPLETED_STATUS:
        if existing.actual_participation == actual_participation:
            return existing
        raise TripParticipantHistoricallyClosedError(event_id=event.id, person_id=person_id)

    try:
        if existing is None:
            row = TripParticipant(
                event_participation_id=participation_id,
                event_id=event.id,
                actual_participation=actual_participation,
            )
            session.add(row)
        else:
            row = existing
            row.actual_participation = actual_participation
        session.commit()
    except Exception:
        session.rollback()
        raise
    return row


__all__ = [
    "TRIP_CREATION_CLOSED_EVENT_STATUSES",
    "ACTUAL_PARTICIPATION_OPEN_EVENT_STATUSES",
    "TripError",
    "EventNotTripError",
    "TripEventLifecycleClosedError",
    "TripAlreadyExistsError",
    "TripParticipationMissingError",
    "ActualParticipationLifecycleClosedError",
    "TripParticipantHistoricallyClosedError",
    "get_trip",
    "create_trip",
    "record_actual_participation",
]
