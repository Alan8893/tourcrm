"""Trip / TripParticipant persistence — the tourism-fact foundation
(Issue #245).

Canonical sources: docs/04-modules/trips-and-tourist-profile.md §2/§8
(`Event(type=trip) -> Trip`, TripParticipant), docs/04-modules/events-
and-schedule.md §22 (`Trip.event_id` references the base Event),
docs/03-architecture/data-model.md §9 (`Event 1:0..1 Trip`), and the
Issue #245 PO/CTO decisions (G1-G9, GAP-A/GAP-B).

Trip is a 1:0..1 extension of an ordinary `Event`; it has no lifecycle
of its own (its lifecycle is the Event's, ADR-0018) and, in this slice,
no tourism attributes: tourism type, region, difficulty, route,
distance, duration, leader and result status are all deferred by the PO
decision because their canonical contracts conflict.

- `trips.event_id` is the primary key. Together with the `event_type`
  column (CHECK `= 'trip'`) it forms the composite FK
  `(event_id, event_type) -> events(id, event_type)` with ON UPDATE /
  ON DELETE RESTRICT: the database itself guarantees BR-TRIP-001 (a Trip
  only for an Event of type `trip`) and rejects changing the type of an
  Event that already has a Trip. `events` holds only ordinary Events —
  EventSeries/EventOccurrence live in their own tables — so a Trip can
  never be attached to a series or an occurrence.

TripParticipant is a 1:0..1 extension of an existing `EventParticipation`
of the Trip's Event — never a second registration source:

- `event_participation_id` is the primary key (at most one
  TripParticipant per EventParticipation);
- `(event_id, event_participation_id) -> event_participations(event_id,
  id)` guarantees the participation belongs to exactly this Trip's
  Event, and `event_id -> trips.event_id` that the Event has a Trip;
- no `person_id` (the Person is the participation's), no
  `registration_status`/`participation_status` (registration stays
  `EventParticipation.registration_status`, ADR-0037);
- `actual_participation` is the confirmed tourism fact. It is not
  Attendance (ADR-0032 stays the separate operational fact) and it is
  never reset or deleted when the registration is cancelled (GAP-A):
  `registration_status = cancelled` with `actual_participation = true`
  is a valid historical state. EventParticipation rows are never
  deleted, and every FK here is RESTRICT — no cascade exists.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TRIP_EVENT_TYPE = "trip"

# Names used by app.events.crud / app.trips.service to recognize these
# specific violations.
TRIP_EVENT_FK = "fk_trips_event_id_event_type"
TRIP_PRIMARY_KEY = "pk_trips"


class Trip(Base):
    """The tourism-specific extension of one ordinary `Event(type=trip)`."""

    __tablename__ = "trips"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, default=TRIP_EVENT_TYPE)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.PrimaryKeyConstraint("event_id", name=TRIP_PRIMARY_KEY),
        sa.CheckConstraint(f"event_type = '{TRIP_EVENT_TYPE}'", name="ck_trips_event_type_trip"),
        sa.ForeignKeyConstraint(
            ["event_id", "event_type"],
            ["events.id", "events.event_type"],
            name=TRIP_EVENT_FK,
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
    )


class TripParticipant(Base):
    """The tourism-specific extension of one `EventParticipation` of a
    Trip's Event."""

    __tablename__ = "trip_participants"

    event_participation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actual_participation: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.PrimaryKeyConstraint("event_participation_id", name="pk_trip_participants"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["trips.event_id"],
            name="fk_trip_participants_event_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "event_participation_id"],
            ["event_participations.event_id", "event_participations.id"],
            name="fk_trip_participants_event_participation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.Index("ix_trip_participants_event_id", "event_id"),
    )


__all__ = ["Trip", "TripParticipant", "TRIP_EVENT_TYPE", "TRIP_EVENT_FK", "TRIP_PRIMARY_KEY"]
