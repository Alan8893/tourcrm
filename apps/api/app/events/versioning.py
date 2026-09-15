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
5. snapshot-copies every relationship-source definition (`SeriesStaff
   Assignment`/`SeriesGroupTarget`/`SeriesParticipant`) from the current
   version onto the successor (ADR-0030 §"Versioning and this_and_
   following") — see app.events.series_relationships.
   snapshot_copy_series_relationships;
6. rebinds every already-materialized occurrence at or after the boundary
   (by `recurrence_anchor_at`, on the current version) to the successor —
   same `id`, same `recurrence_anchor_at`, never recreated (ADR-0028 §3:
   "Following occurrences belong to the new version" is not limited to the
   single selected boundary row). An occurrence without its own protected
   `EventOccurrenceException` also has its operational snapshot
   (`name`/`description`/`event_type`) and `ends_at` resynced to the
   successor version's own fields, so it reflects the new version exactly
   like a freshly materialized one would; an occurrence that already
   carries an exception keeps its own customized schedule/snapshot
   untouched — only `series_id` changes for it. Occurrences before the
   boundary are never touched and remain on the historical version. Each
   rebound occurrence's relationship categories are independently
   resynced from the successor's freshly-copied definitions unless
   protected by an occurrence-level override (ADR-0029/ADR-0030) — see
   app.events.occurrence_relationships.propagate_occurrence_relationships;
7. audit + commit are the caller's responsibility, matching every other
   service module in this codebase (app.role_assignments.service,
   app.groups.service): this module never calls
   app.audit.service.record_audit_event or session.commit()/rollback()
   itself, so the caller can compose it with its own audit calls inside
   one transaction.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.event_recurrence import EventOccurrence, EventOccurrenceException, EventSeries
from app.events.occurrence_relationships import propagate_occurrence_relationships
from app.events.series_lifecycle import validate_boundary_occurrence_status
from app.events.series_relationships import snapshot_copy_series_relationships


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
) -> tuple[EventSeries, list[EventOccurrence]]:
    """ADR-0028 §3/§10 "this and following": create a new EventSeries
    version and rebind `boundary_occurrence_id`, and every other
    already-materialized occurrence chronologically at or after it, to
    the new version.

    Returns `(successor, rebound_occurrences)` — `rebound_occurrences`
    always includes the boundary occurrence itself (first, since it is
    the earliest `recurrence_anchor_at` in the set) plus every later
    already-materialized occurrence that belonged to the source version.
    Each rebound row keeps its own `id` and `recurrence_anchor_at` — none
    is recreated. A rebound occurrence with no protected
    `EventOccurrenceException` has its `name`/`description`/`event_type`
    snapshot and `ends_at` resynced to the successor version's own values
    (`ends_at = starts_at + successor.duration_minutes`); a rebound
    occurrence that already carries an exception keeps its customized
    schedule/snapshot exactly as-is — only `series_id` changes for it.
    Occurrences before the boundary are left untouched on the historical
    version.

    Raises StaleSeriesVersionError if `source_series_id` is no longer
    current (409 at the API boundary), BoundaryOccurrenceNotInCurrentVersionError
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

    # ADR-0028 §3: "Following occurrences belong to the new version" is
    # not limited to the single selected boundary row — every
    # already-materialized occurrence on the current version at or after
    # the boundary's recurrence position must rebind too. Locked here
    # (before the successor even exists) so a concurrent
    # set_occurrence_exception on one of these rows can't race the rebind.
    following_occurrences: list[EventOccurrence] = list(
        session.execute(
            select(EventOccurrence)
            .where(
                EventOccurrence.series_id == current.id,
                EventOccurrence.recurrence_anchor_at >= boundary.recurrence_anchor_at,
            )
            .order_by(EventOccurrence.recurrence_anchor_at)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    protected_occurrence_ids = set(
        session.execute(
            select(EventOccurrenceException.occurrence_id).where(
                EventOccurrenceException.occurrence_id.in_(
                    [occurrence.id for occurrence in following_occurrences]
                )
            )
        )
        .scalars()
        .all()
    )

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

    # ADR-0030 / database-schema-recurrence.md §5.4: "all predecessor
    # relationship definitions are snapshot-copied into the successor
    # before any successor-specific relationship change is applied" — the
    # successor then owns a fully independent definition set.
    snapshot_copy_series_relationships(
        session, source_series_id=current.id, target_series_id=successor.id
    )

    # ADR-0028 §3: same id, no new occurrence created — only the
    # governing series version changes, for the boundary and every
    # already-materialized occurrence chronologically at/after it.
    duration = timedelta(minutes=duration_minutes)
    for occurrence in following_occurrences:
        occurrence.series_id = successor.id
        occurrence.updated_by = updated_by
        if occurrence.id not in protected_occurrence_ids:
            # No protected exception: resync the operational snapshot and
            # duration to the successor version, exactly as a fresh
            # materialization under this version would. `starts_at`
            # (== recurrence_anchor_at for an unprotected occurrence) is
            # never repositioned — only its governing-version-derived
            # fields change.
            occurrence.name = name
            occurrence.description = description
            occurrence.event_type = event_type
            occurrence.ends_at = occurrence.starts_at + duration
        # A protected occurrence (its own EventOccurrenceException) keeps
        # its customized schedule/snapshot untouched — only series_id
        # rebinds, per this round's ADR-0028 §3 clarification.

        # ADR-0030 point 5/6 / database-schema-recurrence.md §5.4/§7:
        # resync each relationship category that has no protected
        # (occurrence-level override) row, from the successor's
        # just-copied relationship-source definitions, evaluated at this
        # occurrence's own scheduled start instant. A category with a
        # protected override is left completely untouched — independent
        # of whether the occurrence as a whole has an EventOccurrenceException.
        propagate_occurrence_relationships(session, occurrence=occurrence, series=successor)
    session.flush()

    return successor, following_occurrences


__all__ = [
    "EventSeriesVersioningError",
    "StaleSeriesVersionError",
    "BoundaryOccurrenceNotInCurrentVersionError",
    "get_current_series_version",
    "create_successor_version",
]
