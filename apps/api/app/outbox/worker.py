"""The PostgreSQL-backed outbox worker runtime (Issue #325, ADR-0046).

A dedicated process (app.cli.run_outbox_worker), separate from FastAPI,
that consumes committed `outbox_jobs` rows. It never sees the business
transaction that enqueued a job: by the time a job is claimable that
transaction has committed, and nothing here can roll it back.

One cycle (`run_once`):

1. claim transaction — lease up to `batch_size` jobs (app.outbox.claiming,
   `FOR UPDATE SKIP LOCKED`), commit;
2. per job, outside any transaction the worker holds — dispatch to the
   handler registered for its `job_type`. A handler may run its own short
   fenced transactions through `JobLease.run_fenced` and does its
   channel/provider work with no row lock held;
3. finalize transaction — lock the job iff this worker still owns its
   lease, write the job result (`completed`, retry as `pending` with a
   backoff, or `dead`) together with the handler's own state change
   (`HandlerResult.record`), commit. A lost lease writes nothing.

Retries are bounded: `max_attempts` claims at most, with deterministic
exponential backoff `min(retry_base * 2**(attempt - 1), retry_max)`.
An unknown `job_type` and any permanent failure end `dead` at once.
A handler may instead defer a job that is temporarily not runnable
(`HandlerResult.deferred`): it goes back to `pending` after the given delay
with the claim's attempt not counted — not a failure, never `dead`.

Graceful shutdown: once `stop` is set the worker claims nothing new,
finishes the job it is executing, and hands every claimed-but-not-started
job back as `pending` without counting the attempt. A forced kill leaves
its leases to expire; the next claim recovers them.

Observability (ADR-0046 §5.7): one log line per job with job type, id,
attempt, result, duration and the safe error code — never the payload,
an error message or exception text, which could carry secrets.
"""

import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional, Protocol, TypeVar, cast

from sqlalchemy.orm import Session, sessionmaker

from app.outbox.claiming import (
    ClaimedJob,
    FinalStatus,
    claim_jobs,
    defer_job,
    finalize_job,
    lock_owned_job,
    release_job,
)
from app.outbox.vocabulary import OUTBOX_COMPLETED, OUTBOX_DEAD, OUTBOX_PENDING

logger = logging.getLogger("tourcrm.worker")

UNKNOWN_JOB_TYPE_ERROR_CODE = "unknown_job_type"
HANDLER_ERROR_CODE = "handler_error"
# A re-claimed expired lease whose attempts are already exhausted: the job
# is not run again.
LEASE_EXPIRED_ERROR_CODE = "lease_expired"
LEASE_LOST_RESULT = "lease_lost"
# The finalize transaction itself failed; the job stays leased and is
# recovered once its lease expires.
FINALIZE_FAILED_RESULT = "finalize_failed"
# The handler deferred the job (not an attempt): `pending` again later with
# its attempt count unchanged.
DEFERRED_RESULT = "deferred"

T = TypeVar("T")


class WorkerConfigurationError(ValueError):
    """A worker setting is missing a valid positive value."""


@dataclass(frozen=True)
class WorkerConfig:
    """Worker runtime settings (ADR-0046 §5.4: backoff and maximum attempts
    are implementation configuration). Environment variables, all
    optional: OUTBOX_WORKER_ID, OUTBOX_WORKER_POLL_INTERVAL_SECONDS,
    OUTBOX_WORKER_BATCH_SIZE, OUTBOX_WORKER_LEASE_SECONDS,
    OUTBOX_WORKER_MAX_ATTEMPTS, OUTBOX_WORKER_RETRY_BASE_SECONDS,
    OUTBOX_WORKER_RETRY_MAX_SECONDS, OUTBOX_WORKER_PAUSE_RECHECK_SECONDS."""

    worker_id: str
    poll_interval: timedelta = timedelta(seconds=5)
    batch_size: int = 1
    lease: timedelta = timedelta(seconds=300)
    max_attempts: int = 5
    retry_base: timedelta = timedelta(seconds=60)
    retry_max: timedelta = timedelta(seconds=3600)
    # How long a deferred (paused) job waits before it is claimable again.
    pause_recheck: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise WorkerConfigurationError("worker_id must not be blank")
        for name in ("poll_interval", "lease", "retry_base", "retry_max", "pause_recheck"):
            if getattr(self, name) <= timedelta(0):
                raise WorkerConfigurationError(f"{name} must be positive")
        for name in ("batch_size", "max_attempts"):
            if getattr(self, name) < 1:
                raise WorkerConfigurationError(f"{name} must be at least 1")
        if self.retry_max < self.retry_base:
            raise WorkerConfigurationError("retry_max must not be shorter than retry_base")

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        def seconds(name: str, default: timedelta) -> timedelta:
            return timedelta(seconds=_positive_int(name, int(default.total_seconds())))

        defaults = cls(worker_id="defaults")
        return cls(
            worker_id=os.getenv("OUTBOX_WORKER_ID") or default_worker_id(),
            poll_interval=seconds("OUTBOX_WORKER_POLL_INTERVAL_SECONDS", defaults.poll_interval),
            batch_size=_positive_int("OUTBOX_WORKER_BATCH_SIZE", defaults.batch_size),
            lease=seconds("OUTBOX_WORKER_LEASE_SECONDS", defaults.lease),
            max_attempts=_positive_int("OUTBOX_WORKER_MAX_ATTEMPTS", defaults.max_attempts),
            retry_base=seconds("OUTBOX_WORKER_RETRY_BASE_SECONDS", defaults.retry_base),
            retry_max=seconds("OUTBOX_WORKER_RETRY_MAX_SECONDS", defaults.retry_max),
            pause_recheck=seconds(
                "OUTBOX_WORKER_PAUSE_RECHECK_SECONDS", defaults.pause_recheck
            ),
        )


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise WorkerConfigurationError(f"{name} must be an integer") from None
    if value < 1:
        raise WorkerConfigurationError(f"{name} must be at least 1")
    return value


def default_worker_id() -> str:
    """Unique per process: a restarted worker never reuses an old lease."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def retry_delay(attempt: int, config: WorkerConfig) -> timedelta:
    """Deterministic exponential backoff after failed attempt `attempt`
    (1-based): retry_base * 2**(attempt - 1), capped at retry_max."""
    delay = config.retry_base * (2 ** (attempt - 1))
    return min(delay, config.retry_max)


@dataclass(frozen=True)
class Disposition:
    """The job state the worker is about to commit, handed to
    `HandlerResult.record` so handler-owned state agrees with it."""

    status: FinalStatus
    # Set for a retry: when the job becomes eligible again.
    next_attempt_at: Optional[datetime]


RecordFn = Callable[[Session, Disposition], None]


@dataclass(frozen=True)
class HandlerResult:
    """What a handler reports for one attempt. `record`, when given, runs
    inside the finalize transaction, after the lease check, so handler
    state commits atomically with the job result — or not at all."""

    kind: Literal["success", "retryable_failure", "permanent_failure", "deferred"]
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    record: Optional[RecordFn] = None
    # `deferred` only: how long to wait before the job is claimable again.
    defer_for: Optional[timedelta] = None

    @classmethod
    def success(cls, record: Optional[RecordFn] = None) -> "HandlerResult":
        return cls("success", record=record)

    @classmethod
    def retryable(
        cls, code: str, message: Optional[str] = None, record: Optional[RecordFn] = None
    ) -> "HandlerResult":
        return cls("retryable_failure", code, message, record)

    @classmethod
    def permanent(
        cls, code: str, message: Optional[str] = None, record: Optional[RecordFn] = None
    ) -> "HandlerResult":
        return cls("permanent_failure", code, message, record)

    @classmethod
    def deferred(
        cls, code: str, defer_for: timedelta, record: Optional[RecordFn] = None
    ) -> "HandlerResult":
        """The job is temporarily not runnable (e.g. its channel is paused
        by policy): not an attempt. The worker hands it back as `pending`
        after `defer_for`, without counting the claim's attempt, so pausing
        never exhausts the retry budget (ADR-0048 §2.8)."""
        return cls("deferred", code, None, record, defer_for)


class JobLease:
    """The handler's view of its leased job."""

    def __init__(self, job: ClaimedJob, session_factory: sessionmaker) -> None:
        self.job = job
        self._session_factory = session_factory

    def run_fenced(self, fn: Callable[[Session], T]) -> Optional[T]:
        """Run `fn` in its own short transaction that first row-locks the
        job iff this worker still owns the lease, and commit. Returns None
        (rolling back) when the lease was lost."""
        with self._session_factory() as session:
            if not lock_owned_job(session, self.job):
                session.rollback()
                return None
            result = fn(session)
            session.commit()
            return result


class JobHandler(Protocol):
    def __call__(self, lease: JobLease) -> HandlerResult: ...


class AbandonAwareHandler(JobHandler, Protocol):
    """A handler that owns state besides the job may implement `abandon`:
    called instead of `__call__` when an expired lease is re-claimed with
    no attempts left; its result's `record` closes that state as the job
    ends `dead`."""

    def abandon(self, lease: JobLease) -> HandlerResult: ...


@dataclass(frozen=True)
class JobReport:
    job_id: uuid.UUID
    job_type: str
    attempt: int
    result: str
    error_code: Optional[str]


class OutboxWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        handlers: Mapping[str, JobHandler],
        config: WorkerConfig,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = dict(handlers)
        self.config = config

    def claim(self) -> list[ClaimedJob]:
        with self._session_factory() as session:
            jobs = claim_jobs(
                session,
                worker_id=self.config.worker_id,
                batch_size=self.config.batch_size,
                lease=self.config.lease,
            )
            session.commit()
        return jobs

    def run_once(self, stop: Optional[threading.Event] = None) -> list[JobReport]:
        """One claim + process cycle. Returns a report per claimed job."""
        if stop is not None and stop.is_set():
            return []
        reports: list[JobReport] = []
        jobs = self.claim()
        for index, job in enumerate(jobs):
            if stop is not None and stop.is_set():
                for unstarted in jobs[index:]:
                    reports.append(self._release(unstarted))
                break
            reports.append(self.process(job))
        return reports

    def run(self, stop: threading.Event) -> None:
        """Poll until `stop` is set. Sleeps `poll_interval` only when a
        cycle claimed nothing; `stop` interrupts the sleep."""
        logger.info("outbox worker started worker_id=%s", self.config.worker_id)
        while not stop.is_set():
            if not self.run_once(stop):
                stop.wait(self.config.poll_interval.total_seconds())
        logger.info("outbox worker stopped worker_id=%s", self.config.worker_id)

    def process(self, job: ClaimedJob) -> JobReport:
        started = time.monotonic()
        handler = self._handlers.get(job.job_type)
        lease = JobLease(job, self._session_factory)
        try:
            if handler is None:
                result = HandlerResult.permanent(UNKNOWN_JOB_TYPE_ERROR_CODE)
            elif job.attempts > self.config.max_attempts:
                abandon = getattr(handler, "abandon", None)
                result = (
                    abandon(lease)
                    if abandon is not None
                    else HandlerResult.permanent(LEASE_EXPIRED_ERROR_CODE)
                )
            else:
                result = handler(lease)
        except Exception as exc:  # noqa: BLE001 - a handler bug must not kill the worker
            # Only the exception type is kept: its text may carry secrets.
            result = HandlerResult.retryable(HANDLER_ERROR_CODE, type(exc).__name__)
        try:
            outcome = self._finalize(job, result)
        except Exception:  # noqa: BLE001 - recovered by lease expiry, never fatal
            outcome = FINALIZE_FAILED_RESULT
        report = JobReport(job.id, job.job_type, job.attempts, outcome, result.error_code)
        logger.info(
            "outbox job job_type=%s job_id=%s attempt=%d result=%s duration_ms=%d error_code=%s",
            job.job_type,
            job.id,
            job.attempts,
            outcome,
            int((time.monotonic() - started) * 1000),
            result.error_code or "-",
        )
        return report

    def _defer(self, job: ClaimedJob, result: HandlerResult) -> str:
        assert result.defer_for is not None
        with self._session_factory() as session:
            next_attempt_at = defer_job(
                session, job, delay=result.defer_for, reason_code=result.error_code
            )
            if next_attempt_at is None:
                session.rollback()
                return LEASE_LOST_RESULT
            if result.record is not None:
                result.record(
                    session, Disposition(cast(FinalStatus, OUTBOX_PENDING), next_attempt_at)
                )
            session.commit()
        return DEFERRED_RESULT

    def _finalize(self, job: ClaimedJob, result: HandlerResult) -> str:
        if result.kind == "deferred":
            return self._defer(job, result)
        status: FinalStatus
        if result.kind == "success":
            status = cast(FinalStatus, OUTBOX_COMPLETED)
        elif result.kind == "retryable_failure" and job.attempts < self.config.max_attempts:
            status = cast(FinalStatus, OUTBOX_PENDING)
        else:
            status = cast(FinalStatus, OUTBOX_DEAD)
        retry_in = retry_delay(job.attempts, self.config) if status == OUTBOX_PENDING else None

        with self._session_factory() as session:
            finalized = finalize_job(
                session,
                job,
                status=status,
                retry_in=retry_in,
                error_code=result.error_code,
                error_message=result.error_message,
            )
            if finalized is None:
                session.rollback()
                return LEASE_LOST_RESULT
            if result.record is not None:
                next_attempt_at = finalized.next_attempt_at if status == OUTBOX_PENDING else None
                result.record(session, Disposition(status, next_attempt_at))
            session.commit()
        return status

    def _release(self, job: ClaimedJob) -> JobReport:
        with self._session_factory() as session:
            released = release_job(session, job)
            session.commit()
        outcome = "released" if released else LEASE_LOST_RESULT
        logger.info(
            "outbox job job_type=%s job_id=%s attempt=%d result=%s",
            job.job_type,
            job.id,
            job.attempts,
            outcome,
        )
        return JobReport(job.id, job.job_type, job.attempts, outcome, None)


__all__ = [
    "UNKNOWN_JOB_TYPE_ERROR_CODE",
    "HANDLER_ERROR_CODE",
    "LEASE_EXPIRED_ERROR_CODE",
    "AbandonAwareHandler",
    "LEASE_LOST_RESULT",
    "FINALIZE_FAILED_RESULT",
    "DEFERRED_RESULT",
    "WorkerConfigurationError",
    "WorkerConfig",
    "default_worker_id",
    "retry_delay",
    "Disposition",
    "RecordFn",
    "HandlerResult",
    "JobLease",
    "JobHandler",
    "JobReport",
    "OutboxWorker",
]
