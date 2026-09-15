"""Attendance persistence (Issue #94 / TH-0087).

Canonical source: docs/03-architecture/adr/ADR-0032-event-attendance.md
(the identity/status/absence-reason/comment invariants below follow it
field-for-field, except for the one implementation-detail deviation
documented under "Identity" below). Also relevant: ADR-0018 (Event
lifecycle), ADR-0028 §13 (EventOccurrence has no `event_id` bridge — see
"Identity"), ADR-0029/ADR-0030 (occurrence authorization/relationships),
ADR-0024 (audit).

## Identity

ADR-0032 §1 gives Attendance's canonical identity as `(occurrence_id,
person_id)` with a single field list and says "There is no direct
Event-level Attendance entity" — i.e. Attendance is one entity, not
duplicated per object type the way `EventStaffAssignment`/
`EventGroupTarget`/`EventParticipation` each have a separate, textually
distinct `EventOccurrence*` counterpart (app.db.event_recurrence_
relationships). At the same time, ADR-0032 §1 requires ordinary
non-recurring `Event`s to receive Attendance too ("attendance is attached
to the concrete event occurrence used by the existing Event API model"),
and ADR-0028 §13 established, with no exception anywhere in this
codebase, that `EventOccurrence` is a first-class entity with no
`event_id` bridge to `Event` — so a literal single FK column named
`occurrence_id` cannot reference "an Event or an EventOccurrence" through
one real foreign key.

This is a purely technical gap in ADR-0032's field list, not a
business-semantics decision: nothing about *which* concrete object types
can receive Attendance, how many Attendance rows may exist per
object/Person, or what Attendance means changes based on how the
identity column(s) are physically shaped. The resolution here keeps
ADR-0032's single-entity intent (one `Attendance` table, no parallel
`EventAttendance`/`EventOccurrenceAttendance` split) while giving each
row a real, FK-enforced reference to exactly one of the two possible
target tables:

- `event_id` — nullable FK to `events.id`, set only for ordinary,
  non-recurring Events.
- `occurrence_id` — nullable FK to `event_occurrences.id`, set only for
  recurring `EventOccurrence`s.
- `ck_attendance_exactly_one_target` enforces that exactly one of the two
  is set.

This is the standard PostgreSQL "polymorphic association" technique
(two nullable FKs + a mutual-exclusivity CHECK), not a second `event_id`
bridge on `EventOccurrence` itself — `EventOccurrence` still has no such
column; this table is a new, different entity referencing one of two
pre-existing identity spaces. `UNIQUE(occurrence_id, person_id)`
(ADR-0032 §1) is correspondingly enforced as two partial unique indexes,
one per target column, which together give the exact business guarantee
ADR-0032 asks for: at most one Attendance row per concrete object/Person
pair, regardless of which object type it is.

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
concrete object/Person pair, corrected in place (ADR-0032 §5/§12:
last-write-wins, no optimistic locking, no version field). The
correction contract (previous/new status, reason, actor, timestamp) is
carried entirely by the audit log (ADR-0024), never by a second
Attendance row or a persisted history column.
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
    """One Attendance mark for a concrete object (`Event` or
    `EventOccurrence`) and Person. See module docstring "Identity" for
    why `event_id`/`occurrence_id` are both nullable rather than a single
    polymorphic column.
    """

    __tablename__ = "attendance"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=True
    )
    occurrence_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("event_occurrences.id", ondelete="RESTRICT"),
        nullable=True,
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
        # Exactly one of the two target FKs — see module docstring
        # "Identity".
        sa.CheckConstraint(
            "(event_id IS NULL) <> (occurrence_id IS NULL)",
            name="ck_attendance_exactly_one_target",
        ),
        # ADR-0032 §3/§4: a `present` row must have both NULL; an
        # `absent` row is otherwise unconstrained by this check (reason
        # is optional even when absent).
        sa.CheckConstraint(
            "status = 'absent' OR (absence_reason IS NULL AND comment IS NULL)",
            name="ck_attendance_present_has_no_reason_or_comment",
        ),
        # ADR-0032 §1: "PostgreSQL must enforce uniqueness for this
        # pair" — one partial unique index per target column (see module
        # docstring "Identity" for why a single UNIQUE(occurrence_id,
        # person_id) cannot be expressed directly).
        sa.Index(
            "uq_attendance_event_id_person_id",
            "event_id",
            "person_id",
            unique=True,
            postgresql_where=sa.text("event_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_attendance_occurrence_id_person_id",
            "occurrence_id",
            "person_id",
            unique=True,
            postgresql_where=sa.text("occurrence_id IS NOT NULL"),
        ),
        sa.Index("ix_attendance_person_id", "person_id"),
    )


__all__ = ["Attendance", "CANONICAL_ATTENDANCE_STATUSES", "CANONICAL_ABSENCE_REASONS"]
