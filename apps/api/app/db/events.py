"""Event persistence foundation (Issue #36), the EventStaffAssignment
responsibility relationship (Issue #48), and EventGroupTarget targeting
(Issue #49).

Canonical sources: docs/04-modules/events-and-schedule.md §5/§10/§11
(field lists), docs/02-requirements/business-rules.md §10, docs/03-
architecture/adr/ADR-0018-event-lifecycle.md (canonical statuses/
transitions), docs/03-architecture/adr/ADR-0019-event-field-model.md
(canonical Event field list, location model, `updated_by`), docs/03-
architecture/adr/ADR-0023-event-relationships-and-guardian-persistence.md
§1/§2 (EventStaffAssignment/EventGroupTarget field lists/invariants),
ADR-0022 (cross-Club ownership integrity), ADR-0010 (UUID primary keys),
ADR-0003 (database strategy).

This module is persistence/domain foundation only. It deliberately does
not implement: Event API endpoints, EventSeries/recurrence,
EventOccurrence, EventParticipation, Attendance, GuardianRelationship,
notifications, calendar/iCalendar, or the Trip/Competition/TourSlet
extensions (explicit Issue #36/#48/#49 non-goals). It also does not
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
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
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


class EventStaffAssignment(Base):
    """Explicit responsibility/staff assignment of a User to an Event
    (Issue #48).

    docs/03-architecture/adr/ADR-0023-event-relationships-and-guardian-
    persistence.md §1, which this model follows field-for-field. See also
    docs/03-architecture/domain-model.md §"EventStaffAssignment",
    docs/03-architecture/data-model.md §"EventStaffAssignment",
    docs/04-modules/events-and-schedule.md §10.

    `user_id`, not `person_id`: the authorization actor reference is a
    User, matching `GroupInstructorAssignment.user_id` (ADR-0021 §3).
    `role_in_event` is intentionally a plain, unconstrained string (ADR-
    0023 §1: "remains a string until a shared responsibility vocabulary
    is explicitly reconciled with GroupInstructorAssignment.role_in_group")
    — no enum/CHECK vocabulary is invented here.

    `Event.created_by` is creation metadata only and is never a
    substitute for this relationship or for `own_events` (ADR-0023 §1).

    No `club_id` column: the Event's Club is resolved via
    `EventStaffAssignment.event_id -> Event.club_id`, exactly like
    `GroupInstructorAssignment` resolves its Club via `group_id ->
    Group.club_id`. No `created_by`/`updated_by`: not part of this
    entity's canonical contract (ADR-0023 §1).

    Cross-Club integrity (ADR-0022): this table remains structurally
    independent from ClubMembership on purpose — no trigger and no
    redundant/denormalized `club_id` column. ADR-0022 §3 makes Club
    ownership an application/service-layer invariant instead, enforced
    by the shared mechanism in app.events.service. Constructing a row
    directly through this ORM class (bypassing app.events.service) does
    not validate Club ownership — expected per ADR-0022 §3/§8, not an
    oversight; production write paths must go through app.events.service.

    At most one *active* (validity-interval sense, not merely "current")
    `is_primary=true` assignment may exist per Event at any point in
    time — enforced by the `ck_event_staff_assignments_one_active_primary`
    GiST exclusion constraint below, the same mechanism already used for
    `ClubMembership`'s "no overlapping active memberships" invariant
    (app.db.identity.ClubMembership). This is a DB-level guarantee that
    holds under concurrent writes without any additional application-
    level locking for this specific invariant.
    """

    __tablename__ = "event_staff_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # ADR-0023 §1: intentionally not a closed enum.
    role_in_event: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_event_staff_assignments_valid_to_after_valid_from",
        ),
        # ADR-0023 §1 / data-model.md: "не более одной active primary
        # assignment для Event". A NULL valid_to is unbounded/ongoing
        # (tstzrange semantics), matching ClubMembership's own
        # no-overlapping-active-rows constraint exactly. Requires
        # btree_gist, already created by the identity foundation
        # migration (80dd15675404).
        ExcludeConstraint(
            (sa.column("event_id"), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            where=sa.text("is_primary = true"),
            using="gist",
            name="ck_event_staff_assignments_one_active_primary",
        ),
        sa.Index("ix_event_staff_assignments_event_id", "event_id"),
        sa.Index("ix_event_staff_assignments_user_id", "user_id"),
    )


class EventGroupTarget(Base):
    """Explicit historical Event-to-Group targeting: which Groups an
    Event addresses (Issue #49).

    docs/03-architecture/adr/ADR-0023-event-relationships-and-guardian-
    persistence.md §2, which this model follows field-for-field. See
    also docs/03-architecture/domain-model.md §"EventGroupTarget",
    docs/03-architecture/data-model.md §"EventGroupTarget",
    docs/04-modules/events-and-schedule.md §11.

    An Event may target multiple Groups and a Group may be targeted by
    multiple Events (M:N). Targeting is audience selection only: it
    never creates `EventParticipation`, registration or attendance, and
    this module makes no reference to any such entity.

    No `club_id` column: the Club for each side is resolved via
    `event_id -> Event.club_id` and `group_id -> Group.club_id`
    respectively, exactly like `EventStaffAssignment` and
    `GroupInstructorAssignment` resolve their Club through their own FK
    rather than a denormalized column.

    Cross-Club integrity (ADR-0022): this table remains structurally
    independent from `Event`/`Group` on purpose — no trigger and no
    redundant `club_id` column. ADR-0022 §3 makes
    `Event.club_id == Group.club_id` an application/service-layer
    invariant, enforced by app.events.service.create_event_group_target.
    Constructing a row directly through this ORM class (bypassing that
    service) does not validate Club ownership — expected per ADR-0022
    §3/§8, not an oversight; production write paths must go through
    app.events.service instead.

    ADR-0023 §2 defines no overlap-prevention rule for simultaneous
    targeting of the same Event/Group pair (unlike
    `EventStaffAssignment`'s primary-assignment invariant) — so, unlike
    that table, no exclusion constraint is introduced here.
    """

    __tablename__ = "event_group_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_event_group_targets_valid_to_after_valid_from",
        ),
        # database-schema.md §23's "all foreign keys used in joins/
        # filtering" plus the group_memberships precedent for a
        # validity-aware compound index — this relationship is queried
        # from both directions (own_groups resolution starts from a set
        # of Groups; a future Event page would list an Event's target
        # Groups), so both sides get the same compound shape.
        sa.Index(
            "ix_event_group_targets_event_id_valid_from_valid_to",
            "event_id",
            "valid_from",
            "valid_to",
        ),
        sa.Index(
            "ix_event_group_targets_group_id_valid_from_valid_to",
            "group_id",
            "valid_from",
            "valid_to",
        ),
    )


__all__ = ["Event", "EventStaffAssignment", "EventGroupTarget"]
