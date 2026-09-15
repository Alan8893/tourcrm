"""EventSeries relationship-source persistence and EventOccurrence
authorization-relationship persistence (Issue #85 / TH-0082).

Canonical sources: docs/03-architecture/adr/ADR-0030-event-series-
relationship-source.md (field lists/invariants below follow it verbatim),
docs/03-architecture/adr/ADR-0029-event-occurrence-authorization-
relationships.md, docs/03-architecture/database-schema-recurrence.md §5/§6,
docs/03-architecture/adr/ADR-0022-cross-club-ownership-integrity.md,
docs/03-architecture/adr/ADR-0023-event-relationships-and-guardian-
persistence.md (the canonical Event relationship field shapes/semantics
these tables mirror field-for-field, applied to the recurrence domain's
own version-owned/occurrence-owned resources instead of `Event`).

## Series-level relationship *source*

`SeriesStaffAssignment` / `SeriesGroupTarget` / `SeriesParticipant` each
have a direct FK to one immutable `event_series.id` (ADR-0030: "Each
definition has a direct FK to `event_series.id` and belongs to exactly
one Series version. There is no recurring-domain `event_id` bridge.").
They are the authoritative *source* future materialization reads from —
never a fallback to `event_series.club_id`, `created_by`, or any other
incidental field.

## Occurrence-level relationship *record*

`EventOccurrenceStaffAssignment` / `EventOccurrenceGroupTarget` /
`EventOccurrenceParticipant` each have a direct FK to one
`event_occurrences.id` — mirroring the Series-level shape field-for-field
(ADR-0029/database-schema-recurrence.md §6: "occurrence-level persistence
equivalent in semantics to the canonical Event relationships"). These are
the authoritative boundary for authorizing one concrete occurrence, not a
display cache and not derived at query time from the Series source.

`is_override` (an implementation detail database-schema-recurrence.md §8
explicitly leaves open — "exact physical table names for occurrence
relationship records") distinguishes a row materialized/propagated
verbatim from its governing Series definition (`is_override=False`) from
one an explicit occurrence-level relationship mutation established
(`is_override=True`, ADR-0030 "Occurrence-level overrides": "an explicit
occurrence-level relationship mutation establishes a protected occurrence-
level override ... not overwritten by later Series changes"). Without
this flag there would be no way to implement that "protected override"
rule — see app.events.occurrence_relationships for the read/write side.

## Effectivity

All six tables use the same `[valid_from, valid_to)` interval convention
already established by `EventStaffAssignment`/`EventGroupTarget`/
`GroupInstructorAssignment`/`GroupMembership`/`GuardianRelationship`
(`valid_from` required, `valid_to` nullable/open-ended). A relationship
definition is eligible for materialization onto an occurrence only when
effective *at that occurrence's scheduled start instant*
(`app.events.occurrence_relationships`), never at "now" — the decision is
made once, at materialization/propagation time, and the resulting
occurrence-level row is a snapshot, not a live join back to the Series
source. Authorization itself (`app.events.series_authorization`)
evaluates the occurrence-level row's own `[valid_from, valid_to)` against
"now", exactly like every other relationship-based scope in this codebase.

## Cross-Club integrity (ADR-0022)

`SeriesStaffAssignment.user_id` and `EventOccurrenceStaffAssignment.
user_id` are valid only when that User's Person has an active
ClubMembership in the governing Series'/occurrence's Club — enforced by
app.events.series_relationships/app.events.occurrence_relationships,
mirroring app.events.service's existing EventStaffAssignment check
exactly. `SeriesGroupTarget.group_id`/`EventOccurrenceGroupTarget.
group_id` are valid only when `Group.club_id` matches the Series'/
occurrence's `club_id` — same invariant as `EventGroupTarget`. Neither
invariant is expressible as a plain FK (the two sides are independently
keyed), so — exactly like `app.events.service` — it is enforced at the
application/service layer, not by a database trigger or a redundant
denormalized column on these tables.

Constructing a row directly through these ORM classes (bypassing
app.events.series_relationships/app.events.occurrence_relationships)
does not validate Club ownership — expected, not an oversight, per
ADR-0022 §3/§8; production write paths must go through those service
modules.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SeriesStaffAssignment(Base):
    """ADR-0030 §"SeriesStaffAssignment": the Series-version-owned
    staff/responsibility relationship source `own_events` authorization
    (via materialized occurrence rows) is ultimately resolved from.

    Field-for-field mirror of `app.db.events.EventStaffAssignment`
    (`role_in_event`, `is_primary`, `valid_from`, `valid_to`), keyed to
    `event_series_id` instead of `event_id`; no `club_id` column — the
    Club is resolved via `event_series_id -> EventSeries.club_id`,
    exactly like `EventStaffAssignment` resolves it via `event_id ->
    Event.club_id`.

    The same "at most one active primary assignment" invariant applies,
    scoped to `event_series_id` — database-schema-recurrence.md §5.1:
    "primary-assignment temporal invariants must preserve the existing
    Event staffing semantics rather than introduce a weaker unconditional
    uniqueness rule."
    """

    __tablename__ = "event_series_staff_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("event_series.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
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
            name="ck_event_series_staff_assignments_valid_to_after_valid_from",
        ),
        ExcludeConstraint(
            (sa.column("event_series_id"), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            where=sa.text("is_primary = true"),
            using="gist",
            name="ck_event_series_staff_assignments_one_active_primary",
        ),
        sa.Index("ix_event_series_staff_assignments_event_series_id", "event_series_id"),
        sa.Index("ix_event_series_staff_assignments_user_id", "user_id"),
    )


class SeriesGroupTarget(Base):
    """ADR-0030 §"SeriesGroupTarget": the Series-version-owned group-
    targeting relationship source `own_groups` is ultimately resolved
    from. Field-for-field mirror of `app.db.events.EventGroupTarget`,
    keyed to `event_series_id`. No overlap-prevention constraint, exactly
    like `EventGroupTarget` (ADR-0023 §2 defines none)."""

    __tablename__ = "event_series_group_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("event_series.id", ondelete="RESTRICT"), nullable=False
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
            name="ck_event_series_group_targets_valid_to_after_valid_from",
        ),
        sa.Index(
            "ix_event_series_group_targets_series_id_valid_from_valid_to",
            "event_series_id",
            "valid_from",
            "valid_to",
        ),
        sa.Index(
            "ix_event_series_group_targets_group_id_valid_from_valid_to",
            "group_id",
            "valid_from",
            "valid_to",
        ),
    )


class SeriesParticipant(Base):
    """ADR-0030 §"SeriesParticipant": the Series-version-owned
    participation relationship source `self`/`children` is ultimately
    resolved from. Carries `registration_status`, the same field
    `app.db.events.EventParticipation` uses (ADR-0020 §4's "documented
    reference values", no ratified CHECK vocabulary — see that module's
    docstring for why), plus the `[valid_from, valid_to)` interval ADR-
    0030 explicitly adds for this Series-owned source (unlike
    `EventParticipation` itself, which has no interval — a Series
    definition is a *source* with its own effectivity window, not a
    single current-state row)."""

    __tablename__ = "event_series_participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("event_series.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    registration_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
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
            name="ck_event_series_participants_valid_to_after_valid_from",
        ),
        sa.Index("ix_event_series_participants_event_series_id", "event_series_id"),
        sa.Index("ix_event_series_participants_person_id", "person_id"),
    )


class EventOccurrenceStaffAssignment(Base):
    """ADR-0029/ADR-0030: the occurrence-level staff/responsibility
    relationship record `own_events` authorization for one concrete
    `EventOccurrence` reads directly. Materialized (copied) from the
    applicable `SeriesStaffAssignment` in the same transaction as the
    occurrence itself (app.events.occurrence_relationships), or created
    directly as a protected override (`is_override=True`) through the
    occurrence-level relationship operation.
    """

    __tablename__ = "event_occurrence_staff_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("event_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role_in_event: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    # See module docstring "Occurrence-level relationship *record*".
    is_override: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_event_occurrence_staff_assignments_valid_to_after_valid_from",
        ),
        ExcludeConstraint(
            (sa.column("occurrence_id"), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            where=sa.text("is_primary = true"),
            using="gist",
            name="ck_event_occurrence_staff_assignments_one_active_primary",
        ),
        sa.Index("ix_event_occurrence_staff_assignments_occurrence_id", "occurrence_id"),
        sa.Index("ix_event_occurrence_staff_assignments_user_id", "user_id"),
    )


class EventOccurrenceGroupTarget(Base):
    """ADR-0029/ADR-0030: the occurrence-level group-targeting
    relationship record `own_groups` authorization reads directly."""

    __tablename__ = "event_occurrence_group_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("event_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    is_override: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_event_occurrence_group_targets_valid_to_after_valid_from",
        ),
        sa.Index(
            "ix_event_occurrence_group_targets_occ_id_valid_from_valid_to",
            "occurrence_id",
            "valid_from",
            "valid_to",
        ),
        sa.Index(
            "ix_event_occurrence_group_targets_group_id_valid_from_valid_to",
            "group_id",
            "valid_from",
            "valid_to",
        ),
    )


class EventOccurrenceParticipant(Base):
    """ADR-0029/ADR-0030: the occurrence-level participation relationship
    record `self`/`children` authorization reads directly. Does not
    introduce self-registration/attendance semantics (ADR-0030
    "Participation boundary")."""

    __tablename__ = "event_occurrence_participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("event_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    registration_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    is_override: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_event_occurrence_participants_valid_to_after_valid_from",
        ),
        sa.Index("ix_event_occurrence_participants_occurrence_id", "occurrence_id"),
        sa.Index("ix_event_occurrence_participants_person_id", "person_id"),
    )


__all__ = [
    "SeriesStaffAssignment",
    "SeriesGroupTarget",
    "SeriesParticipant",
    "EventOccurrenceStaffAssignment",
    "EventOccurrenceGroupTarget",
    "EventOccurrenceParticipant",
]
