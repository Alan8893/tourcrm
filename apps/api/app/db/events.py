"""Event persistence foundation (Issue #36).

Canonical sources: docs/04-modules/events-and-schedule.md §5 (field list),
docs/02-requirements/business-rules.md §10, docs/03-architecture/adr/
ADR-0018-event-lifecycle.md (canonical statuses/transitions), docs/03-
architecture/adr/ADR-0019-event-field-model.md (canonical field list,
location model, `updated_by`), ADR-0010 (UUID primary keys), ADR-0003
(database strategy).

This module is persistence/domain foundation only. It deliberately does
not implement: Event API endpoints, EventSeries/recurrence,
EventOccurrence, EventParticipation, Attendance, groups/instructors
relations, notifications, calendar/iCalendar, or the Trip/Competition/
TourSlet extensions (all explicit Issue #36 non-goals). It also does not
implement or depend on authorization: no permission/scope check is
performed here, and no client-supplied value is ever treated as an
authorization decision by this module.

Dependency direction: this module (persistence) imports from
app.events.vocabulary and app.events.lifecycle (domain) — never the
reverse. Neither of those two modules imports anything from `app.db.*`;
domain code must stay independent of any particular persistence
implementation. See app.events.lifecycle for the reusable, FastAPI- and
ORM-independent lifecycle (status transition) validation that
complements the CHECK constraints below — a *transition* (old status ->
new status) cannot be expressed as a single-row CHECK constraint, so
that part of Data integrity is necessarily domain/application-layer
(ADR-0003 principle: "бизнес-инварианты, которые невозможно выразить
constraint'ами, проверяются application/domain layer"). The same is true
of IANA timezone validity, which Postgres CHECK constraints cannot
express at all — see the `_validate_timezone` hook below, which calls
into app.events.lifecycle.validate_timezone so an invalid timezone is
rejected on the actual persistence path (ORM attribute assignment),
not only when some future caller remembers to invoke that function
directly.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.events.lifecycle import validate_timezone
from app.events.vocabulary import CANONICAL_EVENT_STATUSES, CANONICAL_EVENT_TYPES

_EVENT_TYPE_VALUES = ",".join(f"'{value}'" for value in CANONICAL_EVENT_TYPES)
_EVENT_STATUS_VALUES = ",".join(f"'{value}'" for value in CANONICAL_EVENT_STATUSES)


class Event(Base):
    """A base calendar event.

    docs/04-modules/events-and-schedule.md §5; ADR-0019 (field model,
    location model, `updated_by`); ADR-0018 (status/lifecycle).
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    start_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    # events-and-schedule.md §6: an explicit IANA timezone (e.g.
    # "Europe/Moscow"), never the server's implicit local time. Enforced by
    # the `_validate_timezone` @validates hook below, not by a DB CHECK
    # constraint (Postgres CHECK constraints cannot query pg_timezone_names).
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # ADR-0019 location model: five discrete fields, never a single
    # `location` field. events-and-schedule.md §5: "координаты и адрес
    # опциональны" — location_address/latitude/longitude are nullable.
    # location_type/location_name nullability is not otherwise specified
    # by canonical documentation (ADR-0019), so neither is made required
    # here — see final report "Documentation follow-up".
    location_type: Mapped[Optional[str]] = mapped_column(sa.String(32), nullable=True)
    location_name: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    location_address: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    location_latitude: Mapped[Optional[float]] = mapped_column(sa.Numeric(9, 6), nullable=True)
    location_longitude: Mapped[Optional[float]] = mapped_column(sa.Numeric(9, 6), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # ADR-0019: nullable FK per the general cross-cutting-columns
    # convention (database-schema.md §4); actor references target User,
    # matching the actor-references-User pattern used elsewhere in the
    # canonical data model (e.g. AuditLog.actor_user_id).
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
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
        sa.CheckConstraint(
            f"event_type IN ({_EVENT_TYPE_VALUES})",
            name="ck_events_event_type_valid",
        ),
        sa.CheckConstraint(
            f"status IN ({_EVENT_STATUS_VALUES})",
            name="ck_events_status_valid",
        ),
        sa.CheckConstraint("end_at > start_at", name="ck_events_end_at_after_start_at"),
        # business-rules.md §10.2 / ADR-0018: cancellation requires a reason.
        sa.CheckConstraint(
            "status <> 'cancelled' OR cancellation_reason IS NOT NULL",
            name="ck_events_cancellation_reason_required_when_cancelled",
        ),
        # events-and-schedule.md §5: coordinates must be consistent when
        # supplied — either both present or both absent, never only one.
        sa.CheckConstraint(
            "(location_latitude IS NULL) = (location_longitude IS NULL)",
            name="ck_events_location_coordinates_consistent",
        ),
        # database-schema.md §23: "events by (club_id, start_at)" is a
        # required index category.
        sa.Index("ix_events_club_id_start_at", "club_id", "start_at"),
    )

    @validates("timezone")
    def _validate_timezone(self, key: str, value: str) -> str:
        # Fires on every attribute assignment (constructor kwarg included),
        # so an invalid IANA timezone is rejected before the row is even
        # flushed — strictly earlier, and therefore at least as strong a
        # guarantee, as rejecting it only at commit/flush time.
        validate_timezone(value)
        return value


__all__ = ["Event"]
