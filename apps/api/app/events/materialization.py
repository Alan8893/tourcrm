"""EventOccurrence materialization (Issue #79, ADR-0015, ADR-0028 §9).

Turns an `EventSeries` version's canonical RRULE into real, persisted
`EventOccurrence` rows for a bounded planning horizon.

- Default horizon: 180 days forward (ADR-0015 §3).
- The horizon extends automatically when a caller requests occurrences
  beyond the currently materialized range (`ensure_materialized(...,
  until=...)`) — ADR-0015 §4.
- Materialization only ever happens for the *terminal* (current) version
  of its logical series, and only when that version is `active`
  (ADR-0028 §3/§6). A historical version that a "this and following"
  change has since superseded is never re-materialized, even though its
  own `status` column is left untouched by versioning (ADR-0028 §3:
  "Following occurrences belong to the new version" — a superseded
  version's future slots belong to its successor, not to it; re-generating
  them under the historical version's `series_id` would create duplicate
  logical occurrence slots across versions and could silently
  under/over-count against that version's own `occurrence_limit`/`COUNT`,
  since the rows a successor already claimed via rebinding no longer
  count against it). A no-op — never an error — for a non-terminal or
  non-`active` series, since a caller may legitimately ask to materialize
  a series that has since been superseded or paused/cancelled.
- The terminal-version check reuses `app.events.versioning.
  get_current_series_version(..., lock=True)` — the same `SELECT ... FOR
  UPDATE` on the logical series' stable root row that
  `create_successor_version` itself takes before creating a successor —
  rather than an unlocked, race-prone "is this the highest version?"
  read: a materializer and a concurrent `create_successor_version` call
  for the same logical series serialize on that one root-row lock, so
  materialization can never win a race and insert new rows for a version
  a concurrent successor-creation is about to (or just did) supersede.
- Idempotent and safe under concurrent execution using PostgreSQL's own
  `INSERT ... ON CONFLICT DO NOTHING` against
  `uq_event_occurrences_series_id_recurrence_anchor_at` — never an
  application-level lock or a "check-then-insert" race (ADR-0028 §9/§10)
  *for the insert itself*; the root-row lock above is a separate,
  additional guard specifically against materializing a version that a
  concurrent versioning call is superseding. Two materializers racing the
  same series/window each attempt to insert the identical deterministic
  candidate set; whichever commits first wins each row, the other's
  conflicting rows are silently skipped, and both see the same final
  occurrence set.
- `ends_at = starts_at + duration_minutes`, `duration_minutes` read from
  the governing Series version snapshot (ADR-0028 §2 duration amendment) —
  never an implicit/default duration.

Not an audited business event: ADR-0024/ADR-0028's closed audit
vocabulary has no materialization action code, so this module never calls
app.audit.service.record_audit_event. Materialized rows have
`created_by`/`updated_by = NULL` (system-generated, matching
app.db.events.Event's own nullable-actor convention for non-user-driven
writes), mirroring how Event itself leaves these columns nullable for
exactly this reason (database-schema.md §4).
"""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.event_recurrence import EventOccurrence, EventSeries
from app.events.rrule import expand_occurrences
from app.events.versioning import get_current_series_version

DEFAULT_MATERIALIZATION_HORIZON_DAYS = 180

_UNIQUE_ANCHOR_INDEX = ("series_id", "recurrence_anchor_at")


def materialize_occurrences(
    session: Session, *, series: EventSeries, horizon_end: datetime
) -> list[EventOccurrence]:
    """Materialize every not-yet-materialized occurrence for `series` up
    to `horizon_end`, and return the newly created rows (empty if `series`
    is not the terminal/current version of its logical series, or is not
    `active`, or every candidate already existed).

    Deterministic: the same `series` state and `horizon_end` always
    produce the same candidate anchors (app.events.rrule.expand_occurrences
    never depends on wall-clock execution time), so calling this
    repeatedly — including concurrently, including for the same
    already-covered window — creates no duplicates and is safe to retry.
    """
    # ADR-0028 §3: only the terminal (current) version of the logical
    # series may still generate future occurrences — a version a
    # "this and following" change has superseded never does, regardless
    # of its own `status`. Locks the root row exactly like
    # create_successor_version() does, so this check can never race a
    # concurrent successor creation (see module docstring).
    current = get_current_series_version(session, root_series_id=series.root_series_id, lock=True)
    if current is None or current.id != series.id:
        return []

    if current.status != "active":
        return []

    already_materialized = session.execute(
        select(EventOccurrence.recurrence_anchor_at).where(EventOccurrence.series_id == current.id)
    ).scalars().all()
    already_generated_count = len(already_materialized)

    candidate_anchors = expand_occurrences(
        canonical_rrule=current.recurrence_rule,
        series_start_at=current.series_start_at,
        timezone=current.timezone,
        window_end=horizon_end,
        series_end_at=current.series_end_at,
        occurrence_limit=current.occurrence_limit,
        already_generated_count=already_generated_count,
    )
    if not candidate_anchors:
        return []

    duration = timedelta(minutes=current.duration_minutes)
    rows = [
        {
            "id": uuid.uuid4(),
            "series_id": current.id,
            "club_id": current.club_id,
            "name": current.name,
            "description": current.description,
            "event_type": current.event_type,
            "recurrence_anchor_at": anchor,
            "starts_at": anchor,
            "ends_at": anchor + duration,
            "timezone": current.timezone,
            "status": "scheduled",
            "created_by": None,
            "updated_by": None,
        }
        for anchor in candidate_anchors
    ]

    stmt = (
        pg_insert(EventOccurrence)
        .values(rows)
        .on_conflict_do_nothing(index_elements=_UNIQUE_ANCHOR_INDEX)
        .returning(EventOccurrence)
    )
    created = list(session.scalars(stmt).all())
    session.commit()
    return created


def ensure_materialized(
    session: Session, *, series: EventSeries, until: Optional[datetime] = None
) -> list[EventOccurrence]:
    """Materialize up to the default 180-day horizon, extended on demand
    to cover `until` when the caller needs occurrences further out
    (ADR-0015 §4). Returns the newly created rows.
    """
    now = datetime.now(dt_timezone.utc)
    default_horizon = now + timedelta(days=DEFAULT_MATERIALIZATION_HORIZON_DAYS)
    horizon_end = max(default_horizon, until) if until is not None else default_horizon
    return materialize_occurrences(session, series=series, horizon_end=horizon_end)


__all__ = [
    "DEFAULT_MATERIALIZATION_HORIZON_DAYS",
    "materialize_occurrences",
    "ensure_materialized",
]
