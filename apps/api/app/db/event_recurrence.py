"""Event recurrence persistence (Issue #79): `EventSeries` (versioned
recurrence definitions), `EventOccurrence` (materialized operational
instances) and `EventOccurrenceException` (current per-occurrence
reschedule/cancel override).

Canonical sources: docs/03-architecture/adr/ADR-0028-event-recurrence-
persistence-and-versioning.md (the physical model and every invariant
below follows it field-for-field), docs/03-architecture/database-schema-
recurrence.md (canonical physical addendum), ADR-0015 (materialization
strategy), ADR-0018 (Event lifecycle — occurrence lifecycle mirrors its
shape but is NOT the same vocabulary), ADR-0019 (Event field model —
`event_type` reuses app.events.vocabulary.CANONICAL_EVENT_TYPES), ADR-0024
as amended by ADR-0028 (audit vocabulary).

Dependency direction matches app.db.events: this module imports the pure
domain modules (app.events.series_lifecycle, app.events.series_vocabulary,
app.events.vocabulary) for `@validates` hooks and CHECK-constraint value
lists; neither of those may import anything from `app.db.*`.

`EventOccurrence` is a first-class operational entity, not a nullable
bridge to `Event` (ADR-0028 §13 / database-schema-recurrence.md §2/§5):
it carries its own snapshot fields and has no `event_id` column at all.
Existing non-recurring `Event` persistence (app.db.events) is untouched by
this module.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.events.lifecycle import validate_timezone
from app.events.series_vocabulary import (
    CANONICAL_EXCEPTION_TYPES,
    CANONICAL_OCCURRENCE_STATUSES,
    CANONICAL_SERIES_STATUSES,
)
from app.events.vocabulary import CANONICAL_EVENT_TYPES

_SERIES_STATUS_VALUES = ",".join(f"'{value}'" for value in CANONICAL_SERIES_STATUSES)
_OCCURRENCE_STATUS_VALUES = ",".join(f"'{value}'" for value in CANONICAL_OCCURRENCE_STATUSES)
_EXCEPTION_TYPE_VALUES = ",".join(f"'{value}'" for value in CANONICAL_EXCEPTION_TYPES)
_EVENT_TYPE_VALUES = ",".join(f"'{value}'" for value in CANONICAL_EVENT_TYPES)


class EventSeries(Base):
    """One immutable, versioned physical revision of a logical recurring
    schedule (ADR-0028 §2).

    `root_series_id` identifies the logical series across every version;
    for version 1, `root_series_id == id` and `supersedes_series_id` is
    NULL. Each later version inherits `root_series_id` from the root,
    sets `version = predecessor.version + 1`, and
    `supersedes_series_id = predecessor.id`.

    The *current* version is the terminal node of the chain (the version
    no other row's `supersedes_series_id` points to) — there is
    deliberately no stored `is_current` flag (ADR-0028 §2: "Reasoning:
    current state is derivable from the version chain and a stored flag
    introduces synchronization risk"). See app.events.versioning for the
    query that resolves it and for the transactional successor-creation
    algorithm.
    """

    __tablename__ = "event_series"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    root_series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("event_series.id", ondelete="RESTRICT"), nullable=False
    )
    supersedes_series_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("event_series.id", ondelete="RESTRICT"), nullable=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    series_start_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    series_end_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    occurrence_limit: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    # ADR-0028 §8: canonical persisted RRULE, never containing UNTIL.
    recurrence_rule: Mapped[str] = mapped_column(sa.Text, nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
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
            f"event_type IN ({_EVENT_TYPE_VALUES})", name="ck_event_series_event_type_valid"
        ),
        sa.CheckConstraint(
            f"status IN ({_SERIES_STATUS_VALUES})", name="ck_event_series_status_valid"
        ),
        sa.CheckConstraint("version > 0", name="ck_event_series_version_positive"),
        sa.CheckConstraint(
            "occurrence_limit IS NULL OR occurrence_limit > 0",
            name="ck_event_series_occurrence_limit_positive",
        ),
        sa.CheckConstraint(
            "series_end_at IS NULL OR series_end_at > series_start_at",
            name="ck_event_series_end_at_after_start_at",
        ),
        # ADR-0028 §2: "(root_series_id, version)" must be unique.
        sa.UniqueConstraint(
            "root_series_id", "version", name="uq_event_series_root_series_id_version"
        ),
        # ADR-0028 §2: "at most one successor for any series version" — a
        # plain UNIQUE constraint on the nullable FK is sufficient:
        # PostgreSQL treats every NULL as distinct from every other NULL,
        # so multiple version-1 rows (each with a NULL
        # `supersedes_series_id`) never conflict with each other, while
        # two different rows both claiming the same non-NULL predecessor
        # do.
        sa.UniqueConstraint(
            "supersedes_series_id", name="uq_event_series_one_successor_per_predecessor"
        ),
        sa.Index("ix_event_series_root_series_id", "root_series_id"),
        sa.Index("ix_event_series_club_id_status", "club_id", "status"),
    )

    @validates("timezone")
    def _validate_timezone(self, key: str, value: str) -> str:
        validate_timezone(value)
        return value


class EventOccurrence(Base):
    """A concrete, materialized, operational scheduled instance governed
    by one `EventSeries` version at a time (ADR-0028 §4).

    Not a nullable bridge to `Event` — this table carries its own
    operational snapshot fields (ADR-0028 §13). `id` never changes for the
    lifetime of the occurrence, including across a "this and following"
    rebind to a new `EventSeries` version (only `series_id` changes then;
    see app.events.versioning).

    `recurrence_anchor_at` is the deterministic, RRULE-derived instant this
    occurrence represents under the `EventSeries` version that first
    materialized it — distinct from `starts_at`, which is the *current
    effective* start and is updated in place when a reschedule exception
    changes the operational schedule (ADR-0028 §5's "effective_start_at").
    `recurrence_anchor_at` never changes after materialization: it is the
    materialization idempotency key required by ADR-0028 §4/§9 and
    database-schema-recurrence.md §2 ("a stable recurrence identity
    derived from the Series version and the canonical recurrence
    position/slot ... not relying on wall-clock execution time"), scoped
    to one `series_id` via the UNIQUE constraint below — the exact
    implementation-detail key ADR-0028 §14 leaves open.
    """

    __tablename__ = "event_occurrences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("event_series.id", ondelete="RESTRICT"), nullable=False
    )
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    recurrence_anchor_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
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
            f"event_type IN ({_EVENT_TYPE_VALUES})", name="ck_event_occurrences_event_type_valid"
        ),
        sa.CheckConstraint(
            f"status IN ({_OCCURRENCE_STATUS_VALUES})", name="ck_event_occurrences_status_valid"
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name="ck_event_occurrences_ends_at_after_starts_at"
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' OR cancellation_reason IS NOT NULL",
            name="ck_event_occurrences_cancellation_reason_required",
        ),
        # The materialization idempotency boundary (see class docstring).
        sa.UniqueConstraint(
            "series_id",
            "recurrence_anchor_at",
            name="uq_event_occurrences_series_id_recurrence_anchor_at",
        ),
        sa.Index("ix_event_occurrences_series_id", "series_id"),
        sa.Index("ix_event_occurrences_club_id_starts_at", "club_id", "starts_at"),
    )

    @validates("timezone")
    def _validate_timezone(self, key: str, value: str) -> str:
        validate_timezone(value)
        return value


class EventOccurrenceException(Base):
    """The current reschedule/cancel exception state for one
    `EventOccurrence` (ADR-0028 §5). At most one row per occurrence, ever
    — historical exception changes are represented by the immutable Audit
    stream (`event_occurrence.exception_created`/`.exception_changed`),
    never by additional rows here.
    """

    __tablename__ = "event_occurrence_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("event_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    exception_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    original_start_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    effective_start_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    effective_end_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    overrides: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
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
            f"exception_type IN ({_EXCEPTION_TYPE_VALUES})",
            name="ck_event_occurrence_exceptions_exception_type_valid",
        ),
        sa.CheckConstraint(
            "exception_type <> 'cancelled' OR cancellation_reason IS NOT NULL",
            name="ck_event_occurrence_exceptions_cancellation_reason_required",
        ),
        sa.CheckConstraint(
            "effective_start_at IS NULL OR effective_end_at IS NULL "
            "OR effective_end_at > effective_start_at",
            name="ck_event_occurrence_exceptions_effective_end_after_start",
        ),
    )


__all__ = ["EventSeries", "EventOccurrence", "EventOccurrenceException"]
