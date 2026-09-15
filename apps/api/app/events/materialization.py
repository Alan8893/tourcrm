"""EventOccurrence materialization (Issue #79, ADR-0015, ADR-0028 §9).

Turns an `EventSeries` version's canonical RRULE into real, persisted
`EventOccurrence` rows for a bounded planning horizon.

- Default horizon: 180 days forward (ADR-0015 §3).
- The horizon extends automatically when a caller requests occurrences
  beyond the currently materialized range (`ensure_materialized(...,
  until=...)`) — ADR-0015 §4.
- Materialization only ever happens for an `active` series (ADR-0028 §6:
  `paused`/`cancelled` stop *new* generation without touching existing
  occurrences) — a no-op for any other status, not an error, since a
  caller may legitimately ask to materialize a series that has since been
  paused.
- Idempotent and safe under concurrent execution using PostgreSQL's own
  `INSERT ... ON CONFLICT DO NOTHING` against
  `uq_event_occurrences_series_id_recurrence_anchor_at` — never an
  application-level lock or a "check-then-insert" race (ADR-0028 §9/§10).
  Two materializers racing the same series/window each attempt to insert
  the identical deterministic candidate set; whichever commits first wins
  each row, the other's conflicting rows are silently skipped, and both
  see the same final occurrence set.
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

DEFAULT_MATERIALIZATION_HORIZON_DAYS = 180

_UNIQUE_ANCHOR_INDEX = ("series_id", "recurrence_anchor_at")


def materialize_occurrences(
    session: Session, *, series: EventSeries, horizon_end: datetime
) -> list[EventOccurrence]:
    """Materialize every not-yet-materialized occurrence for `series` up
    to `horizon_end`, and return the newly created rows (empty if the
    series is not `active`, or every candidate already existed).

    Deterministic: the same `series` state and `horizon_end` always
    produce the same candidate anchors (app.events.rrule.expand_occurrences
    never depends on wall-clock execution time), so calling this
    repeatedly — including concurrently, including for the same
    already-covered window — creates no duplicates and is safe to retry.
    """
    if series.status != "active":
        return []

    already_materialized = session.execute(
        select(EventOccurrence.recurrence_anchor_at).where(EventOccurrence.series_id == series.id)
    ).scalars().all()
    already_generated_count = len(already_materialized)

    candidate_anchors = expand_occurrences(
        canonical_rrule=series.recurrence_rule,
        series_start_at=series.series_start_at,
        timezone=series.timezone,
        window_end=horizon_end,
        series_end_at=series.series_end_at,
        occurrence_limit=series.occurrence_limit,
        already_generated_count=already_generated_count,
    )
    if not candidate_anchors:
        return []

    duration = timedelta(minutes=series.duration_minutes)
    rows = [
        {
            "id": uuid.uuid4(),
            "series_id": series.id,
            "club_id": series.club_id,
            "name": series.name,
            "description": series.description,
            "event_type": series.event_type,
            "recurrence_anchor_at": anchor,
            "starts_at": anchor,
            "ends_at": anchor + duration,
            "timezone": series.timezone,
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
