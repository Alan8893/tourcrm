"""Time-based Event lifecycle reconciliation (Issue #281).

Canonical sources: docs/03-architecture/adr/ADR-0018-event-lifecycle.md
"Time-based lifecycle synchronization", docs/04-modules/
events-and-schedule.md §4, docs/05-api/events-api.md §8 "Time-based
status synchronization".

Brings every Event whose schedule has already reached a time-driven
transition to the applicable status: `published -> in_progress` once
`start_at` is reached, `in_progress -> completed` once `end_at` is
reached. The service never has to be running at the exact moment — a
later run converges a missed transition in one pass (`published` after
`end_at` goes `published -> in_progress -> completed`).

No second lifecycle: which steps are due is decided by
app.events.lifecycle.time_based_status_transitions (validated against
ADR-0018's one ALLOWED_STATUS_TRANSITIONS graph), and every step is
applied through app.events.crud.apply_status_transition — the same
function the manual `POST /events/{id}/status` path uses, so the linked
EventOccurrence (ADR-0033 §4) is mirrored identically. `draft`,
`cancelled`, `completed` and `archived` are never selected or changed.

This is a system operation, not a user request: it performs no
authorization check (there is no principal) and records
`updated_by = NULL`, the codebase's convention for non-user writes
(see app.events.materialization). It is reachable only from the backend
process itself (app.cli.reconcile_event_lifecycle) — no HTTP endpoint
exposes it, so it cannot become a path around the status endpoint's
`event.update`/`event.cancel` authorization.

## Idempotency and concurrency

Each due Event is handled in its own transaction under the same
`SELECT ... FOR UPDATE` row lock the manual status endpoint takes
(Event first, then its occurrence — the same lock order, so the two
paths cannot deadlock each other). The due steps are recomputed from the
row *after* the lock is acquired, so:

- a concurrent manual transition (e.g. a cancellation) that commits
  first is seen by the reconciler, which then has nothing left to do —
  time never overrides an explicit `cancelled`/`archived`;
- a manual transition that waits on the reconciler's lock re-reads the
  reconciled row and is validated against it by the existing graph;
- two reconcilers running at once serialize on the row: the second finds
  the Event already in its time-based state and changes nothing.

Re-running is therefore a no-op for every already-synchronized Event: no
status change, no `updated_at` bump, no repeated achievement trigger.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.achievements import triggers as achievement_triggers
from app.db.events import Event
from app.events.crud import apply_status_transition
from app.events.lifecycle import time_based_status_transitions

logger = logging.getLogger("tourcrm.events.lifecycle_reconciliation")

_COMPLETED_STATUS = "completed"


@dataclass(frozen=True)
class LifecycleReconciliationResult:
    """Counts for one reconciliation run. `transitioned` counts Events
    whose status changed; `started`/`completed` count the individual
    `-> in_progress` / `-> completed` steps applied (a missed
    `published -> completed` contributes one to each)."""

    examined: int
    transitioned: int
    started: int
    completed: int
    failed: int


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")


def find_due_event_ids(session: Session, *, now: datetime) -> list[uuid.UUID]:
    """Ids of every Event with a time-driven transition due at `now`.

    A pre-filter only — the authoritative decision is recomputed per
    Event under its row lock (see reconcile_event).
    """
    _require_aware(now)
    return list(
        session.execute(
            sa.select(Event.id)
            .where(
                sa.or_(
                    sa.and_(Event.status == "published", Event.start_at <= now),
                    sa.and_(Event.status == "in_progress", Event.end_at <= now),
                )
            )
            .order_by(Event.start_at, Event.id)
        )
        .scalars()
        .all()
    )


def reconcile_event(session: Session, *, event_id: uuid.UUID, now: datetime) -> tuple[str, ...]:
    """Bring one Event to its time-based status at `now`, in one
    transaction. Returns the statuses applied, in order (empty when
    nothing was due — including when the Event no longer exists or was
    concurrently moved out of a time-driven status)."""
    _require_aware(now)
    event = session.execute(
        sa.select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if event is None:
        session.rollback()
        return ()

    steps = time_based_status_transitions(
        event.status, start_at=event.start_at, end_at=event.end_at, now=now
    )
    if not steps:
        session.rollback()
        return ()

    try:
        for new_status in steps:
            apply_status_transition(
                session,
                event=event,
                new_status=new_status,
                cancellation_reason=None,
                updated_by=None,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise

    if steps[-1] == _COMPLETED_STATUS:
        # Same post-commit hook the manual `-> completed` transition
        # fires (app.events.crud.transition_event_status); it never
        # blocks or rolls back the transition itself.
        achievement_triggers.event_completed(session, event_id=event_id)
    return steps


def reconcile_event_lifecycle(
    session: Session, *, now: Optional[datetime] = None
) -> LifecycleReconciliationResult:
    """Reconcile every due Event at `now` (default: the current UTC
    instant). A failure on one Event is logged and counted, rolled back
    on its own, and never prevents the others from being reconciled; the
    next run retries it."""
    if now is None:
        now = datetime.now(dt_timezone.utc)
    _require_aware(now)

    event_ids = find_due_event_ids(session, now=now)
    session.rollback()

    transitioned = started = completed = failed = 0
    for event_id in event_ids:
        try:
            steps = reconcile_event(session, event_id=event_id, now=now)
        except Exception:
            session.rollback()
            failed += 1
            logger.exception("events.lifecycle.reconcile.failed event_id=%s", event_id)
            continue
        if steps:
            transitioned += 1
            started += steps.count("in_progress")
            completed += steps.count(_COMPLETED_STATUS)
            logger.info(
                "events.lifecycle.reconcile.transitioned event_id=%s steps=%s",
                event_id,
                "->".join(steps),
            )

    result = LifecycleReconciliationResult(
        examined=len(event_ids),
        transitioned=transitioned,
        started=started,
        completed=completed,
        failed=failed,
    )
    logger.info(
        "events.lifecycle.reconciled examined=%s transitioned=%s started=%s completed=%s failed=%s",
        result.examined,
        result.transitioned,
        result.started,
        result.completed,
        result.failed,
    )
    return result


__all__ = [
    "LifecycleReconciliationResult",
    "find_due_event_ids",
    "reconcile_event",
    "reconcile_event_lifecycle",
]
