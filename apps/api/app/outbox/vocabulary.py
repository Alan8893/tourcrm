"""Canonical transactional-outbox job status vocabulary (Issue #318,
ADR-0046 §5).

Pure Python — no ORM/FastAPI import. app.db.outbox builds its CHECK
constraints from this set.

- pending    — eligible once `next_attempt_at` has passed (new jobs and
               retryable failures, ADR-0046 §5.4);
- processing — claimed by a worker until `locked_until` (lease, §5.3); an
               expired lease makes the job eligible again;
- completed  — finished successfully (terminal);
- dead       — permanent failure (terminal, observable, ADR-0046 §5.4 /
               ADR-0007 dead-letter state).
"""

OUTBOX_PENDING = "pending"
OUTBOX_PROCESSING = "processing"
OUTBOX_COMPLETED = "completed"
OUTBOX_DEAD = "dead"

CANONICAL_OUTBOX_STATUSES: frozenset[str] = frozenset(
    {OUTBOX_PENDING, OUTBOX_PROCESSING, OUTBOX_COMPLETED, OUTBOX_DEAD}
)

TERMINAL_OUTBOX_STATUSES: frozenset[str] = frozenset({OUTBOX_COMPLETED, OUTBOX_DEAD})

__all__ = [
    "OUTBOX_PENDING",
    "OUTBOX_PROCESSING",
    "OUTBOX_COMPLETED",
    "OUTBOX_DEAD",
    "CANONICAL_OUTBOX_STATUSES",
    "TERMINAL_OUTBOX_STATUSES",
]
