"""Transactional EventSeries successor-version creation and boundary
occurrence rebinding — "this and following" (Issue #79, ADR-0028 §3/§10,
database-schema-recurrence.md §6).

Pure orchestration over app.db.event_recurrence + app.events.series_lifecycle
+ app.events.rrule; no FastAPI import. Authorization is entirely the
caller's responsibility (matching app.events.crud/app.events.service): by
the time any function here runs, the caller must already have verified the
acting principal is allowed to update the series.

Concurrency (ADR-0028 §10 / database-schema-recurrence.md §6): the *only*
correct way to create a successor is `create_successor_version()`, which:

1. locks the current (terminal) version row for `root_series_id` with
   `SELECT ... FOR UPDATE`, serializing concurrent successor attempts for
   the same logical series;
2. verifies the caller's own `source_series_id` still names that locked
   row — otherwise another transaction already committed a successor
   first, and this call fails fast with StaleSeriesVersionError (mapped to
   409 Conflict at the API boundary) without creating anything;
3. validates the boundary occurrence (must belong to the now-locked
   current version and be `scheduled` — ADR-0028 §3: a cancelled
   occurrence can never be the boundary);
4. inserts the successor row — the DB's own
   `uq_event_series_one_successor_per_predecessor` UNIQUE constraint is a
   second, independent guarantee against a duplicate successor even if
   the row lock were somehow bypassed;
5. rebinds the boundary occurrence's `series_id` to the successor (same
   `id`, same `recurrence_anchor_at` — never recreated);
6. audit + commit are the caller's responsibility, matching every other
   service module in this codebase (app.role_assignments.service,
   app.groups.service): this module never calls
   app.audit.service.record_audit_event or session.commit()/rollback()
   itself, so the caller can compose it with its own audit calls inside
   one transaction.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.event_recurrence import EventOccurrence, EventSeries
from app.events.series_lifecycle import validate_boundary_occurrence_status


class EventSeriesVersioningError(Exception):
    """Base class for this module's typed, expected failures."""


class StaleSeriesVersionError(EventSeriesVersioningError):
    """ADR-0028 §10: the caller's `source_series_id` is no longer the
    current (terminal) version of the logical series — either a
    concurrent successor was already created, or the caller supplied a
    historical version. Maps to `409 Conflict`; nothing is persisted.
    """

    def __init__(self, *, source_series_id: uuid.UUID, current_series_id: uuid.UUID) -> None:
        super().__init__(
            f"source_series_id {source_series_id} is not the current version "
            f"(current is {current_series_id})"
        )
        self.source_series_id = source_series_id
        self.current_series_id = current_series_id


class BoundaryOccurrenceNotInCurrentVersionError(EventSeriesVersioningError):
    """The selected boundary occurrence does not belong to the (now
    locked) current series version."""

    def __init__(self, *, occurrence_id: uuid.UUID, current_series_id: uuid.UUID) -> None:
        super().__init__(
            f"Occurrence {occurrence_id} does not belong to current series version "
            f"{current_series_id}"
        )
        self.occurrence_id = occurrence_id
        self.current_series_id = current_series_id


def get_current_series_version(
    session: Session, *, root_series_id: uuid.UUID, lock: bool = False
) -> Optional[EventSeries]:
    """Resolve the current (terminal) version of the logical series
    identified by `root_series_id` — the version no other row's
    `supersedes_series_id` points to. Since `version` increases by exactly
    1 per successor and `uq_event_series_one_successor_per_predecessor`
    guarantees the chain never forks, the terminal version is always the
    one with the maximum `version` — a simpler and cheaper query than
    walking `supersedes_series_id` links or anti-joining, while remaining
    exactly equivalent given those DB-enforced invariants.

    `lock=True` (required before any successor-creation decision) takes a
    `SELECT ... FOR UPDATE` on the *root* row (`id == root_series_id`,
    always present and stable) first, then reads the terminal version.
    Locking the root — not "whichever row currently has the highest
    version" — is deliberate: a `SELECT ... ORDER BY version DESC LIMIT 1
    FOR UPDATE` targets a specific existing row at plan time, and
    PostgreSQL's row-lock wait only re-checks *that* row once unblocked —
    it never re-runs the `ORDER BY`/`LIMIT` to discover a *new* row (the
    very successor a concurrent transaction just inserted), so a second
    writer blocked on that plan would wake up and still see the old
    "current" version as current. The root row never changes identity, so
    every successor-creation attempt for the same logical series
    contends on the exact same lock, giving genuine mutual exclusion.
    """
    if lock:
        session.execute(
            select(EventSeries.id).where(EventSeries.id == root_series_id).with_for_update()
        )
    stmt = (
        select(EventSeries)
        .where(EventSeries.root_series_id == root_series_id)
        .order_by(EventSeries.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def create_successor_version(
    session: Session,
    *,
    source_series_id: uuid.UUID,
    boundary_occurrence_id: uuid.UUID,
    name: str,
    description: Optional[str],
    event_type: str,
    series_start_at: datetime,
    series_end_at: Optional[datetime],
    occurrence_limit: Optional[int],
    duration_minutes: int,
    recurrence_rule: str,
    timezone: str,
    updated_by: uuid.UUID,
) -> tuple[EventSeries, EventOccurrence]:
    """ADR-0028 §3/§10 "this and following": create a new EventSeries
    version and rebind `boundary_occurrence_id` to it.

    Returns `(successor, rebound_occurrence)`. Raises
    StaleSeriesVersionError if `source_series_id` is no longer current
    (409 at the API boundary), BoundaryOccurrenceNotInCurrentVersionError
    if the occurrence does not belong to the now-locked current version,
    app.events.series_lifecycle.CancelledOccurrenceCannotBeBoundaryError
    if it is `cancelled`, or
    app.events.series_lifecycle.InvalidBoundaryOccurrenceStatusError if it
    is any other non-`scheduled` status (`in_progress`/`completed`) —
    `scheduled` is the only eligible boundary status (ADR-0028 §3) —
    persisting nothing in every failure case. Does not commit, rollback,
    or record audit — see module docstring.
    """
    # Lock the row this whole decision depends on before reading anything
    # else it implies (ADR-0028 §10 step 1).
    provisional_current = session.get(EventSeries, source_series_id)
    if provisional_current is None:
        raise StaleSeriesVersionError(
            source_series_id=source_series_id, current_series_id=source_series_id
        )
    current = get_current_series_version(
        session, root_series_id=provisional_current.root_series_id, lock=True
    )
    assert current is not None  # a root_series_id always has at least one version

    if current.id != source_series_id:
        raise StaleSeriesVersionError(
            source_series_id=source_series_id, current_series_id=current.id
        )

    boundary = session.get(EventOccurrence, boundary_occurrence_id, with_for_update=True)
    if boundary is None or boundary.series_id != current.id:
        raise BoundaryOccurrenceNotInCurrentVersionError(
            occurrence_id=boundary_occurrence_id, current_series_id=current.id
        )
    validate_boundary_occurrence_status(boundary.status)

    successor = EventSeries(
        root_series_id=current.root_series_id,
        supersedes_series_id=current.id,
        version=current.version + 1,
        club_id=current.club_id,
        name=name,
        description=description,
        event_type=event_type,
        series_start_at=series_start_at,
        series_end_at=series_end_at,
        occurrence_limit=occurrence_limit,
        duration_minutes=duration_minutes,
        recurrence_rule=recurrence_rule,
        timezone=timezone,
        status=current.status,
        created_by=updated_by,
        updated_by=updated_by,
    )
    session.add(successor)
    session.flush()

    # ADR-0028 §3: same id, no new occurrence created — only the
    # governing series version changes.
    boundary.series_id = successor.id
    boundary.updated_by = updated_by
    session.flush()

    return successor, boundary


__all__ = [
    "EventSeriesVersioningError",
    "StaleSeriesVersionError",
    "BoundaryOccurrenceNotInCurrentVersionError",
    "get_current_series_version",
    "create_successor_version",
]
