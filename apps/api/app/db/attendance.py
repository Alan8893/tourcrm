"""Attendance persistence (Issue #94 / TH-0087).

Canonical source: docs/03-architecture/adr/ADR-0032-event-attendance.md
§1: "Attendance is a concrete record for exactly one `EventOccurrence`
and one `Person`. Canonical identity: `(occurrence_id, person_id)`."
Also relevant: ADR-0015 (materialization), ADR-0018 (Event lifecycle),
ADR-0028 §13 (EventOccurrence has no `event_id` bridge), ADR-0029/
ADR-0030 (occurrence authorization/relationships), ADR-0024 (audit).

## Identity — occurrence-only, no `event_id`

`occurrence_id` is a real, `NOT NULL` FK to `event_occurrences.id`.
`UNIQUE(occurrence_id, person_id)` is a single, ordinary DB constraint —
there is no `event_id` column, no dual-nullable-FK/polymorphic-
association scheme, and no per-object-type discriminator on this table.

A previous revision of this module attempted to also support ordinary,
non-recurring `Event`s by adding a second nullable `event_id` FK plus a
mutual-exclusivity CHECK, reasoning that ADR-0032 §1's sentence "For
ordinary non-recurring Events, attendance is attached to the concrete
event occurrence used by the existing Event API model" required it. That
was rejected on review: no canonical source (ADR-0015, ADR-0028 §13,
ADR-0029, ADR-0030) defines any mapping from an ordinary `Event` to a
concrete `EventOccurrence` — ADR-0015's own materialization strategy is
scoped entirely to *recurring* series, and ADR-0028 §13 is explicit that
`EventOccurrence` "is a first-class operational entity, not a nullable
bridge to `Event`" and "has no `event_id` column at all". Inventing a
second FK/discriminator to paper over that gap changed ADR-0032's
canonical identity rather than resolving a purely technical detail, so
it was reverted. Attendance for an ordinary, non-recurring `Event` is
currently unsupported — this is a genuine specification gap, not
resolved by this implementation; see the module docstring in
app.events.attendance ("Object resolution") and the final implementation
report for the exact GAP description awaiting a PO decision.

## Status / absence reason / comment (ADR-0032 §2-4)

- `status`: exactly `present`/`absent` (`CANONICAL_ATTENDANCE_STATUSES`).
- `absence_reason`: nullable, closed vocabulary
  (`CANONICAL_ABSENCE_REASONS`) — not a CRUD entity, not club-configurable.
- `comment`: nullable free text, allowed only for `absent`.
- `present` requires both `absence_reason` and `comment` to be NULL
  (`ck_attendance_present_has_no_reason_or_comment` covers both: an
  `absent` row is unconstrained by this check; a `present` row must have
  both NULL).

No `valid_from`/`valid_to`: unlike `EventStaffAssignment`/
`EventGroupTarget`/`EventParticipation`, Attendance is not a historical
timeline of *relationship* validity — it is a single current mark per
occurrence/Person pair, corrected in place (ADR-0032 §5/§12: last-write-
wins, no optimistic locking, no version field). The correction contract
(previous/new status, reason, actor, timestamp) is carried entirely by
the audit log (ADR-0024), never by a second Attendance row or a
persisted history column.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ADR-0032 §2: exactly these two, no `late`/`excused`/`unknown`/`pending`.
CANONICAL_ATTENDANCE_STATUSES = ("present", "absent")

# ADR-0032 §3: closed MVP vocabulary, not club-configurable.
CANONICAL_ABSENCE_REASONS = ("sick", "family_reason", "injury", "education", "work", "other")

_STATUS_VALUES = ",".join(f"'{value}'" for value in CANONICAL_ATTENDANCE_STATUSES)
_REASON_VALUES = ",".join(f"'{value}'" for value in CANONICAL_ABSENCE_REASONS)


class Attendance(Base):
    """One Attendance mark for a concrete `EventOccurrence` and Person
    (ADR-0032 §1). See module docstring "Identity" for why there is no
    `event_id` column."""

    __tablename__ = "attendance"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("event_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    absence_reason: Mapped[Optional[str]] = mapped_column(sa.String(32), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
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
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_attendance_status_valid"),
        sa.CheckConstraint(
            f"absence_reason IS NULL OR absence_reason IN ({_REASON_VALUES})",
            name="ck_attendance_absence_reason_valid",
        ),
        # ADR-0032 §3/§4: a `present` row must have both NULL; an
        # `absent` row is otherwise unconstrained by this check (reason
        # is optional even when absent).
        sa.CheckConstraint(
            "status = 'absent' OR (absence_reason IS NULL AND comment IS NULL)",
            name="ck_attendance_present_has_no_reason_or_comment",
        ),
        # ADR-0032 §1: "PostgreSQL must enforce uniqueness for this pair."
        sa.UniqueConstraint(
            "occurrence_id", "person_id", name="uq_attendance_occurrence_id_person_id"
        ),
        sa.Index("ix_attendance_person_id", "person_id"),
    )


__all__ = ["Attendance", "CANONICAL_ATTENDANCE_STATUSES", "CANONICAL_ABSENCE_REASONS"]
