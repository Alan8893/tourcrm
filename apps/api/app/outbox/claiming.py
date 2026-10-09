"""Outbox job claiming, lease and fenced completion (Issue #325,
ADR-0046 §5.2-§5.4).

Every function here runs inside the caller's open transaction and never
commits; app.outbox.worker owns the short claim/finalize transactions and
never holds a row lock while a handler does channel work.

Claim (ADR-0046 §5.2/§5.3): eligible rows are `pending` with
`next_attempt_at <= now()`, or `processing` with an expired lease
(`locked_until < now()`), selected in deterministic
`(next_attempt_at, id)` order with `FOR UPDATE SKIP LOCKED` and leased in
the same transaction: `status = 'processing'`, `locked_by`,
`locked_until = now() + lease`, `attempts + 1`. A re-claimed expired
lease can therefore exceed the worker's `max_attempts`; the worker then
does not run the job again but ends it `dead` (app.outbox.worker), so a job
that keeps killing its worker stays bounded.

Fencing: `attempts` is incremented by every claim, so
(`id`, `locked_by`, `attempts`) identifies exactly one lease. Every write
after the claim is conditioned on that triple and on `status =
'processing'`; a worker whose lease expired and was re-claimed by another
worker therefore matches no row and cannot overwrite the new owner's
result. No extra column is needed for this.

All times come from the database clock (`now()`), never from a worker's
local clock.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.outbox import OutboxJob
from app.outbox.vocabulary import (
    OUTBOX_COMPLETED,
    OUTBOX_PENDING,
    OUTBOX_PROCESSING,
)

ERROR_CODE_MAX_LENGTH = 64
ERROR_MESSAGE_MAX_LENGTH = 1024

FinalStatus = Literal["completed", "pending", "dead"]


@dataclass(frozen=True)
class ClaimedJob:
    """One leased job. (`id`, `locked_by`, `attempts`) is the fencing token."""

    id: uuid.UUID
    job_type: str
    payload: dict[str, Any]
    attempts: int
    locked_by: str
    locked_until: datetime


@dataclass(frozen=True)
class FinalizedJob:
    status: str
    next_attempt_at: datetime
    finished_at: Optional[datetime]


def _owned(job: ClaimedJob) -> sa.ColumnElement[bool]:
    return sa.and_(
        OutboxJob.id == job.id,
        OutboxJob.status == OUTBOX_PROCESSING,
        OutboxJob.locked_by == job.locked_by,
        OutboxJob.attempts == job.attempts,
    )


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    return None if value is None else value[:limit]


def claim_jobs(
    session: Session,
    *,
    worker_id: str,
    batch_size: int,
    lease: timedelta,
) -> list[ClaimedJob]:
    """Lease up to `batch_size` eligible jobs for `worker_id`."""
    now = sa.func.now()
    eligible = sa.or_(
        sa.and_(OutboxJob.status == OUTBOX_PENDING, OutboxJob.next_attempt_at <= now),
        sa.and_(OutboxJob.status == OUTBOX_PROCESSING, OutboxJob.locked_until < now),
    )
    claimable = session.execute(
        sa.select(OutboxJob.id)
        .where(eligible)
        .order_by(OutboxJob.next_attempt_at, OutboxJob.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).scalars().all()
    if not claimable:
        return []
    claimed = session.execute(
        sa.update(OutboxJob)
        .where(OutboxJob.id.in_(claimable))
        .values(
            status=OUTBOX_PROCESSING,
            locked_by=worker_id,
            locked_until=now + lease,
            attempts=OutboxJob.attempts + 1,
        )
        .returning(
            OutboxJob.id,
            OutboxJob.job_type,
            OutboxJob.payload,
            OutboxJob.attempts,
            OutboxJob.locked_by,
            OutboxJob.locked_until,
            OutboxJob.next_attempt_at,
        )
        .execution_options(synchronize_session=False)
    ).all()
    return [
        ClaimedJob(
            id=row.id,
            job_type=row.job_type,
            payload=row.payload,
            attempts=row.attempts,
            locked_by=row.locked_by,
            locked_until=row.locked_until,
        )
        for row in sorted(claimed, key=lambda row: (row.next_attempt_at, row.id))
    ]


def lock_owned_job(session: Session, job: ClaimedJob) -> bool:
    """Row-lock the job for the rest of the caller's transaction if this
    worker still owns its lease. False means the lease was lost."""
    owned = session.execute(
        sa.select(OutboxJob.id).where(_owned(job)).with_for_update()
    ).scalar_one_or_none()
    return owned is not None


def finalize_job(
    session: Session,
    job: ClaimedJob,
    *,
    status: FinalStatus,
    retry_in: Optional[timedelta] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[FinalizedJob]:
    """Record the result of the leased attempt and release the lease.

    `pending` requires `retry_in` (the job becomes eligible again at
    `now() + retry_in`); `completed`/`dead` are terminal. Returns None —
    writing nothing — when this worker no longer owns the lease.
    """
    if (status == OUTBOX_PENDING) != (retry_in is not None):
        raise ValueError("retry_in is required for, and only for, a pending (retry) result")
    values: dict[str, Any] = {
        "status": status,
        "locked_by": None,
        "locked_until": None,
        "last_error_code": _truncate(error_code, ERROR_CODE_MAX_LENGTH),
        "last_error_message": _truncate(error_message, ERROR_MESSAGE_MAX_LENGTH),
    }
    if status == OUTBOX_PENDING:
        assert retry_in is not None
        values["next_attempt_at"] = sa.func.now() + retry_in
    else:
        values["finished_at"] = sa.func.now()
    if status == OUTBOX_COMPLETED:
        values["last_error_code"] = None
        values["last_error_message"] = None
    row = session.execute(
        sa.update(OutboxJob)
        .where(_owned(job))
        .values(**values)
        .returning(OutboxJob.status, OutboxJob.next_attempt_at, OutboxJob.finished_at)
        .execution_options(synchronize_session=False)
    ).one_or_none()
    if row is None:
        return None
    return FinalizedJob(
        status=row.status, next_attempt_at=row.next_attempt_at, finished_at=row.finished_at
    )


def release_job(session: Session, job: ClaimedJob) -> bool:
    """Hand a claimed but not yet started job back (graceful shutdown):
    `pending`, lease cleared, the claim's attempt not counted. False when
    the lease was already lost."""
    released = session.execute(
        sa.update(OutboxJob)
        .where(_owned(job))
        .values(
            status=OUTBOX_PENDING,
            locked_by=None,
            locked_until=None,
            attempts=OutboxJob.attempts - 1,
        )
        .returning(OutboxJob.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    return released is not None


def defer_job(
    session: Session,
    job: ClaimedJob,
    *,
    delay: timedelta,
    reason_code: Optional[str] = None,
) -> Optional[datetime]:
    """Hand a leased job back as `pending` until `now() + delay` WITHOUT
    counting the claim's attempt — the handler found the job temporarily
    not runnable (e.g. its channel is paused by policy), which is neither a
    success nor a failed attempt. Like `release_job` but delayed, so the job
    is not re-claimed in a busy loop. `reason_code` is kept as the job's
    last error code for observability. Returns the new `next_attempt_at`, or
    None — writing nothing — when this worker no longer owns the lease."""
    if delay <= timedelta(0):
        raise ValueError("delay must be positive")
    row = session.execute(
        sa.update(OutboxJob)
        .where(_owned(job))
        .values(
            status=OUTBOX_PENDING,
            locked_by=None,
            locked_until=None,
            attempts=OutboxJob.attempts - 1,
            next_attempt_at=sa.func.now() + delay,
            last_error_code=_truncate(reason_code, ERROR_CODE_MAX_LENGTH),
            last_error_message=None,
        )
        .returning(OutboxJob.next_attempt_at)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    return row


__all__ = [
    "ClaimedJob",
    "FinalizedJob",
    "claim_jobs",
    "lock_owned_job",
    "finalize_job",
    "release_job",
    "defer_job",
]
