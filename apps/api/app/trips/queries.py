"""Trip / TripParticipant reads (Issue #245).

Authorization is applied inside SQL, never fetch-then-filter:

- Trip list: `app.events.authorization.event_visibility_filter` with
  `trip.read` — the same per-grant scope predicates (`all`/`own_events`/
  `own_groups`/`self`/`children`) the Event list uses, evaluated against
  the Trip's Event. `archived` Events are excluded unless `status` asks
  for them explicitly (trips-and-tourist-profile-api.md §3.1).
- TripParticipant list: the caller has already passed the object-level
  `trip.read` check on the Trip's Event; which rows are then visible
  follows the same per-scope row rule the Event participant roster uses
  (`all`/`own_events`/`own_groups` the full set, `self` the requester's
  own row, `children` their active children's rows), reused from
  `app.events.attendance` with `permission_code="trip.read"`.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.db.events import Event, EventParticipation
from app.db.identity import Person
from app.db.trips import Trip, TripParticipant
from app.events.attendance import _attendance_row_visibility as _participant_row_visibility
from app.events.authorization import event_visibility_filter

TRIP_READ_PERMISSION = "trip.read"
_ARCHIVED_STATUS = "archived"


def list_trips_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    page: int,
    page_size: int,
    status: Optional[str] = None,
) -> tuple[list[Trip], int]:
    conditions: list[sa.ColumnElement[bool]] = [
        event_visibility_filter(session, user_id=user_id, permission_code=TRIP_READ_PERMISSION)
    ]
    if status is None:
        conditions.append(Event.status != _ARCHIVED_STATUS)
    else:
        conditions.append(Event.status == status)

    base = sa.select(Trip).join(Event, Event.id == Trip.event_id).where(*conditions)
    total = session.execute(sa.select(sa.func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        session.execute(
            base.order_by(Event.start_at, Event.id).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_trip_participants(
    session: Session,
    *,
    event: Event,
    resource_context: ResourceContext,
    user_id: uuid.UUID,
    page: int,
    page_size: int,
) -> tuple[list[tuple[TripParticipant, uuid.UUID]], int]:
    """Recorded TripParticipants of `event`'s Trip with the Person of
    each one's EventParticipation, ordered like the Event participant
    roster (`last_name`, `first_name`, `id`)."""
    visibility = _participant_row_visibility(
        session,
        club_id=event.club_id,
        resource_context=resource_context,
        user_id=user_id,
        permission_code=TRIP_READ_PERMISSION,
        participant_person_id_column=EventParticipation.person_id,
    )
    base = (
        sa.select(TripParticipant, EventParticipation.person_id)
        .join(
            EventParticipation,
            EventParticipation.id == TripParticipant.event_participation_id,
        )
        .join(Person, Person.id == EventParticipation.person_id)
        .where(TripParticipant.event_id == event.id, visibility)
    )
    total = session.execute(sa.select(sa.func.count()).select_from(base.subquery())).scalar_one()
    rows = session.execute(
        base.order_by(Person.last_name, Person.first_name, Person.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [(row[0], row[1]) for row in rows], total


__all__ = ["TRIP_READ_PERMISSION", "list_trips_page", "list_trip_participants"]
