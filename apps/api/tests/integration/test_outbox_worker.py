"""Integration tests for the PostgreSQL outbox worker and the
`notification.delivery` handler (Issue #325, ADR-0046 §5) against a real
PostgreSQL instance.

Concurrency is made deterministic with open transactions, events and
barriers — never with sleeps: a claim that would block instead of skipping
a locked row fails on `lock_timeout` rather than hanging.

The channel adapters here are test doubles of the ChannelAdapter boundary;
no SMTP/Telegram adapter exists in the application.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from app.cli.run_outbox_worker import build_worker
from app.db.identity import Club, Person, User
from app.db.notifications import NotificationDelivery
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.notifications.delivery import (
    ADAPTER_ERROR_CODE,
    CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE,
    DELIVERY_NOT_FOUND_ERROR_CODE,
    INVALID_PAYLOAD_ERROR_CODE,
    ChannelResult,
    DeliveryRequest,
    NotificationDeliveryHandler,
)
from app.notifications.repository import add_delivery, create_notification
from app.notifications.vocabulary import (
    NOTIFICATION_DELIVERY_JOB_TYPE,
    notification_delivery_deduplication_key,
)
from app.outbox.claiming import claim_jobs, finalize_job
from app.outbox.service import enqueue_outbox_job
from app.outbox.worker import (
    HANDLER_ERROR_CODE,
    LEASE_EXPIRED_ERROR_CODE,
    LEASE_LOST_RESULT,
    UNKNOWN_JOB_TYPE_ERROR_CODE,
    HandlerResult,
    JobLease,
    OutboxWorker,
    WorkerConfig,
)

from ._schema_reset import API_ROOT
from .conftest import requires_postgres

# --- Test doubles of the ChannelAdapter boundary ---------------------------------


class _Adapter:
    def __init__(self, *results: ChannelResult, on_call: Any = None) -> None:
        self._results = list(results) or [ChannelResult.delivered("provider-1")]
        self.calls: list[DeliveryRequest] = []
        self._on_call = on_call
        self._lock = threading.Lock()

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        with self._lock:
            self.calls.append(request)
        if self._on_call is not None:
            self._on_call(request)
        return self._results[min(len(self.calls), len(self._results)) - 1]


class _Raising:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        raise self.exc


# --- Factories -----------------------------------------------------------------


def _config(**overrides: Any) -> WorkerConfig:
    fields: dict[str, Any] = {
        "worker_id": f"worker-{uuid.uuid4().hex[:6]}",
        "poll_interval": timedelta(seconds=1),
        "batch_size": 10,
        "lease": timedelta(minutes=5),
        "max_attempts": 3,
        "retry_base": timedelta(seconds=30),
        "retry_max": timedelta(minutes=10),
    }
    fields.update(overrides)
    return WorkerConfig(**fields)


def _worker(adapter: Any = None, channel: str = "email", **config: Any) -> OutboxWorker:
    adapters = {} if adapter is None else {channel: adapter}
    return OutboxWorker(
        session_factory=get_session_factory(),
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler(adapters)},
        config=_config(**config),
    )


def _delivery_job(channel: str = "email") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Business mutation + Notification + Delivery + outbox job, committed
    together. Returns (club_id, delivery_id, job_id)."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([club, user])
        session.flush()
        notification, _ = create_notification(
            session,
            idempotency_key=f"test:{uuid.uuid4()}",
            event_type="test.event",
            subject_type="test_subject",
            recipient_user_id=user.id,
            club_id=club.id,
        )
        delivery, _ = add_delivery(
            session,
            notification=notification,
            channel=channel,
            destination_type="user",
            destination_id=user.id,
        )
        job, _ = enqueue_outbox_job(
            session,
            job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
            payload={"delivery_id": str(delivery.id)},
            deduplication_key=notification_delivery_deduplication_key(delivery.id),
        )
        session.commit()
        return club.id, delivery.id, job.id


def _raw_job(job_type: str, payload: dict[str, Any]) -> uuid.UUID:
    with session_scope() as session:
        job, _ = enqueue_outbox_job(session, job_type=job_type, payload=payload)
        session.commit()
        return job.id


def _job(job_id: uuid.UUID) -> OutboxJob:
    with session_scope() as session:
        job = session.get(OutboxJob, job_id)
        assert job is not None
        return job


def _delivery(delivery_id: uuid.UUID) -> NotificationDelivery:
    with session_scope() as session:
        delivery = session.get(NotificationDelivery, delivery_id)
        assert delivery is not None
        return delivery


def _make_due(job_id: uuid.UUID) -> None:
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob)
            .where(OutboxJob.id == job_id)
            .values(next_attempt_at=sa.func.now() - timedelta(seconds=1))
        )
        session.commit()


def _expire_lease(job_id: uuid.UUID) -> None:
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob)
            .where(OutboxJob.id == job_id)
            .values(locked_until=sa.func.now() - timedelta(seconds=1))
        )
        session.commit()


def _db_now() -> Any:
    with session_scope() as session:
        return session.execute(sa.select(sa.func.now())).scalar_one()


# --- Claiming / SKIP LOCKED / lease ------------------------------------------------


@requires_postgres
def test_claim_leases_one_job() -> None:
    _, _, job_id = _delivery_job()
    with session_scope() as session:
        (claimed,) = claim_jobs(
            session, worker_id="worker-a", batch_size=5, lease=timedelta(minutes=5)
        )
        session.commit()

    job = _job(job_id)
    assert claimed.id == job_id and claimed.attempts == 1
    assert (job.status, job.locked_by, job.attempts) == ("processing", "worker-a", 1)
    assert job.locked_until is not None and job.locked_until > _db_now()


@requires_postgres
def test_two_workers_never_claim_the_same_job_and_skip_locked_does_not_block() -> None:
    _delivery_job()
    _delivery_job()
    factory = get_session_factory()
    first, second = factory(), factory()
    try:
        for session in (first, second):
            # A blocking claim would fail fast here instead of hanging.
            session.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
        claimed_a = claim_jobs(first, worker_id="a", batch_size=1, lease=timedelta(minutes=5))
        claimed_b = claim_jobs(second, worker_id="b", batch_size=5, lease=timedelta(minutes=5))
        assert len(claimed_a) == 1 and len(claimed_b) == 1
        assert claimed_a[0].id != claimed_b[0].id
        third = factory()
        try:
            third.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
            assert claim_jobs(third, worker_id="c", batch_size=5, lease=timedelta(minutes=5)) == []
        finally:
            third.rollback()
            third.close()
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


@requires_postgres
def test_concurrent_workers_process_every_job_exactly_once() -> None:
    delivery_ids = {_delivery_job()[1] for _ in range(6)}
    adapter = _Adapter()
    workers = [_worker(adapter, batch_size=2) for _ in range(3)]
    barrier = threading.Barrier(len(workers), timeout=30)
    errors: list[BaseException] = []

    def run(worker: OutboxWorker) -> None:
        try:
            barrier.wait()
            while worker.run_once():
                pass
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(w,)) for w in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors
    assert sorted(c.delivery_id for c in adapter.calls) == sorted(delivery_ids)
    for delivery_id in delivery_ids:
        assert _delivery(delivery_id).status == "delivered"


@requires_postgres
def test_no_row_lock_is_held_during_channel_work() -> None:
    _, _, job_id = _delivery_job()
    observed: list[bool] = []

    def try_lock(_: DeliveryRequest) -> None:
        with session_scope() as session:
            try:
                session.execute(
                    sa.select(OutboxJob.id)
                    .where(OutboxJob.id == job_id)
                    .with_for_update(nowait=True)
                )
                observed.append(True)
            except OperationalError:
                observed.append(False)
            session.rollback()

    _worker(_Adapter(on_call=try_lock)).run_once()
    assert observed == [True]


@requires_postgres
def test_expired_lease_is_reclaimed_and_the_old_owner_cannot_overwrite_it() -> None:
    _, delivery_id, job_id = _delivery_job()
    worker_a = _worker(_Adapter(), worker_id="worker-a")
    worker_b = _worker(_Adapter(ChannelResult.delivered("from-b")), worker_id="worker-b")

    (stale,) = worker_a.claim()
    _expire_lease(job_id)
    (reclaimed,) = worker_b.claim()
    assert reclaimed.id == job_id and reclaimed.attempts == 2

    # Worker A comes back after losing its lease: nothing it writes lands.
    report = worker_a.process(stale)
    assert report.result == LEASE_LOST_RESULT
    with session_scope() as session:
        assert finalize_job(session, stale, status="completed") is None
        session.rollback()
    job = _job(job_id)
    assert (job.status, job.locked_by, job.attempts) == ("processing", "worker-b", 2)
    assert _delivery(delivery_id).status != "delivered"

    assert worker_b.process(reclaimed).result == "completed"
    job = _job(job_id)
    assert (job.status, job.locked_by, job.locked_until) == ("completed", None, None)
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.provider_message_id) == ("delivered", "from-b")


@requires_postgres
def test_live_lease_is_not_reclaimed() -> None:
    _delivery_job()
    worker_a, worker_b = _worker(), _worker()
    assert len(worker_a.claim()) == 1
    assert worker_b.claim() == []


@requires_postgres
def test_exhausted_expired_lease_ends_dead_and_closes_the_delivery() -> None:
    _, delivery_id, job_id = _delivery_job()
    adapter = _Adapter()
    worker = _worker(adapter, max_attempts=1)
    (crashed,) = worker.claim()
    # The worker "dies" right after starting the attempt (Delivery left in
    # processing), as the handler's first fenced step leaves it.
    JobLease(crashed, get_session_factory()).run_fenced(
        lambda s: s.execute(
            sa.update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .values(
                status="processing",
                attempts=1,
                first_attempt_at=sa.func.now(),
                last_attempt_at=sa.func.now(),
            )
        )
    )
    _expire_lease(job_id)

    (report,) = worker.run_once()
    assert (report.result, report.error_code) == ("dead", LEASE_EXPIRED_ERROR_CODE)
    assert adapter.calls == []
    job = _job(job_id)
    assert (job.status, job.last_error_code, job.locked_by) == ("dead", "lease_expired", None)
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.next_retry_at) == ("failed", None)
    assert delivery.last_error_code == LEASE_EXPIRED_ERROR_CODE


# --- Delivery lifecycle --------------------------------------------------------------


@requires_postgres
def test_success_delivers_and_completes_the_job() -> None:
    _, delivery_id, job_id = _delivery_job()
    adapter = _Adapter(ChannelResult.delivered("msg-42"))
    (report,) = _worker(adapter).run_once()

    assert report.result == "completed"
    (request,) = adapter.calls
    assert (request.delivery_id, request.channel, request.attempt) == (delivery_id, "email", 1)
    job = _job(job_id)
    assert (job.status, job.attempts, job.locked_by, job.locked_until) == (
        "completed",
        1,
        None,
        None,
    )
    assert job.finished_at is not None and job.last_error_code is None
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.provider_message_id, delivery.attempts) == (
        "delivered",
        "msg-42",
        1,
    )
    assert delivery.delivered_at is not None and delivery.first_attempt_at is not None


@requires_postgres
def test_retryable_failure_schedules_a_bounded_backoff() -> None:
    _, delivery_id, job_id = _delivery_job()
    before = _db_now()
    adapter = _Adapter(ChannelResult.retryable("smtp_timeout", "Connection timed out"))
    (report,) = _worker(adapter, retry_base=timedelta(seconds=30)).run_once()

    assert report.result == "pending"
    job = _job(job_id)
    assert (job.status, job.attempts, job.locked_by, job.finished_at) == ("pending", 1, None, None)
    assert job.last_error_code == "smtp_timeout"
    assert job.next_attempt_at >= before + timedelta(seconds=30)
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.attempts) == ("failed", 1)
    assert delivery.next_retry_at == job.next_attempt_at
    assert (delivery.last_error_code, delivery.last_error_message) == (
        "smtp_timeout",
        "Connection timed out",
    )
    # Not due yet: nothing to claim.
    assert _worker(adapter).run_once() == []


@requires_postgres
def test_attempts_are_bounded_then_terminal() -> None:
    _, delivery_id, job_id = _delivery_job()
    adapter = _Adapter(ChannelResult.retryable("smtp_timeout"))
    worker = _worker(adapter, max_attempts=2)

    assert [r.result for r in worker.run_once()] == ["pending"]
    _make_due(job_id)
    assert [r.result for r in worker.run_once()] == ["dead"]
    _make_due(job_id)
    assert worker.run_once() == []

    assert len(adapter.calls) == 2
    job = _job(job_id)
    assert (job.status, job.attempts, job.last_error_code) == ("dead", 2, "smtp_timeout")
    assert job.finished_at is not None
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.attempts, delivery.next_retry_at) == ("failed", 2, None)
    assert delivery.delivered_at is None


@requires_postgres
def test_retry_after_failure_can_still_succeed() -> None:
    _, delivery_id, job_id = _delivery_job()
    adapter = _Adapter(ChannelResult.retryable("smtp_timeout"), ChannelResult.delivered("m-2"))
    worker = _worker(adapter)
    worker.run_once()
    _make_due(job_id)
    assert [r.result for r in worker.run_once()] == ["completed"]
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.attempts, delivery.next_retry_at) == ("delivered", 2, None)
    assert [c.attempt for c in adapter.calls] == [1, 2]


@requires_postgres
def test_permanent_failure_is_terminal_at_once() -> None:
    _, delivery_id, job_id = _delivery_job()
    adapter = _Adapter(ChannelResult.permanent("recipient_rejected", "Mailbox unavailable"))
    (report,) = _worker(adapter, max_attempts=5).run_once()

    assert report.result == "dead"
    assert _job(job_id).last_error_code == "recipient_rejected"
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.next_retry_at, delivery.last_error_code) == (
        "failed",
        None,
        "recipient_rejected",
    )


@requires_postgres
def test_missing_channel_adapter_is_a_bounded_retryable_failure() -> None:
    _, delivery_id, _ = _delivery_job(channel="telegram")
    (report,) = _worker(_Adapter(), channel="email").run_once()
    assert (report.result, report.error_code) == (
        "pending",
        CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE,
    )
    assert _delivery(delivery_id).last_error_code == CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE


# --- Idempotency -----------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["delivered", "cancelled", "skipped"])
@requires_postgres
def test_terminal_delivery_completes_the_job_without_channel_work(terminal_status: str) -> None:
    _, delivery_id, job_id = _delivery_job()
    with session_scope() as session:
        values: dict[str, Any] = {"status": terminal_status}
        if terminal_status == "delivered":
            values["delivered_at"] = sa.func.now()
        session.execute(
            sa.update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .values(**values)
        )
        session.commit()
    adapter = _Adapter()
    (report,) = _worker(adapter).run_once()

    assert report.result == "completed"
    assert adapter.calls == []
    assert _delivery(delivery_id).status == terminal_status
    assert _job(job_id).status == "completed"


@requires_postgres
def test_reprocessing_a_delivered_job_does_not_deliver_again() -> None:
    _, delivery_id, job_id = _delivery_job()
    adapter = _Adapter(ChannelResult.delivered("first"))
    worker = _worker(adapter)
    worker.run_once()
    # Redelivery of the same job (at-least-once): put it back in the queue.
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob)
            .where(OutboxJob.id == job_id)
            .values(status="pending", finished_at=None, next_attempt_at=sa.func.now())
        )
        session.commit()

    assert [r.result for r in worker.run_once()] == ["completed"]
    assert len(adapter.calls) == 1
    delivery = _delivery(delivery_id)
    assert (delivery.status, delivery.provider_message_id, delivery.attempts) == (
        "delivered",
        "first",
        1,
    )


@requires_postgres
def test_same_delivery_cannot_be_enqueued_twice() -> None:
    _, delivery_id, job_id = _delivery_job()
    with session_scope() as session:
        again, created = enqueue_outbox_job(
            session,
            job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
            payload={"delivery_id": str(delivery_id)},
            deduplication_key=notification_delivery_deduplication_key(delivery_id),
        )
        session.commit()
    assert (again.id, created) == (job_id, False)


# --- Invalid jobs ----------------------------------------------------------------------


@requires_postgres
def test_unknown_job_type_is_terminal_and_does_not_stop_the_worker() -> None:
    unknown_id = _raw_job("test.unknown", {})
    _, delivery_id, _ = _delivery_job()
    reports = {r.job_id: r for r in _worker(_Adapter()).run_once()}

    assert (reports[unknown_id].result, reports[unknown_id].error_code) == (
        "dead",
        UNKNOWN_JOB_TYPE_ERROR_CODE,
    )
    unknown = _job(unknown_id)
    assert (unknown.status, unknown.last_error_code, unknown.attempts) == (
        "dead",
        UNKNOWN_JOB_TYPE_ERROR_CODE,
        1,
    )
    assert _delivery(delivery_id).status == "delivered"


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        ({}, INVALID_PAYLOAD_ERROR_CODE),
        ({"delivery_id": "not-a-uuid"}, INVALID_PAYLOAD_ERROR_CODE),
        ({"delivery_id": str(uuid.uuid4())}, DELIVERY_NOT_FOUND_ERROR_CODE),
    ],
)
@requires_postgres
def test_unprocessable_delivery_job_is_terminal(payload: dict[str, Any], error_code: str) -> None:
    job_id = _raw_job(NOTIFICATION_DELIVERY_JOB_TYPE, payload)
    (report,) = _worker(_Adapter()).run_once()
    assert (report.result, report.error_code) == ("dead", error_code)
    assert _job(job_id).status == "dead"


@requires_postgres
def test_unexpected_handler_error_is_a_bounded_retry() -> None:
    job_id = _raw_job("test.broken", {})

    def broken(lease: JobLease) -> HandlerResult:
        raise ValueError("boom")

    worker = OutboxWorker(
        session_factory=get_session_factory(),
        handlers={"test.broken": broken},
        config=_config(max_attempts=2),
    )
    assert [r.result for r in worker.run_once()] == ["pending"]
    _make_due(job_id)
    assert [r.result for r in worker.run_once()] == ["dead"]
    job = _job(job_id)
    assert (job.last_error_code, job.last_error_message) == (HANDLER_ERROR_CODE, "ValueError")


# --- Secrets ---------------------------------------------------------------------------


@requires_postgres
def test_exception_text_never_reaches_the_database_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, delivery_id, job_id = _delivery_job()
    secret = "smtp_password=hunter2-token"
    caplog.set_level(logging.DEBUG)
    _worker(_Raising(RuntimeError(secret))).run_once()

    job, delivery = _job(job_id), _delivery(delivery_id)
    assert (job.last_error_code, job.last_error_message) == (ADAPTER_ERROR_CODE, "RuntimeError")
    assert (delivery.last_error_code, delivery.last_error_message) == (
        ADAPTER_ERROR_CODE,
        "RuntimeError",
    )
    assert "hunter2" not in caplog.text
    assert str(delivery_id) not in caplog.text  # the payload is never logged


# --- Graceful shutdown ---------------------------------------------------------------


@requires_postgres
def test_shutdown_finishes_the_current_job_and_releases_the_rest() -> None:
    first = _delivery_job()
    second = _delivery_job()
    stop = threading.Event()
    adapter = _Adapter(on_call=lambda _request: stop.set())
    reports = _worker(adapter, batch_size=2).run_once(stop)

    assert [r.result for r in reports] == ["completed", "released"]
    assert len(adapter.calls) == 1
    done_id = adapter.calls[0].delivery_id
    released_job = next(j for j in (first, second) if j[1] != done_id)[2]
    job = _job(released_job)
    assert (job.status, job.attempts, job.locked_by, job.locked_until) == ("pending", 0, None, None)
    # The released job is immediately claimable by the next worker.
    assert [r.result for r in _worker(_Adapter()).run_once()] == ["completed"]


@requires_postgres
def test_stopped_worker_claims_nothing() -> None:
    _, _, job_id = _delivery_job()
    stop = threading.Event()
    stop.set()
    assert _worker(_Adapter()).run_once(stop) == []
    assert _job(job_id).status == "pending"


@requires_postgres
def test_run_loop_returns_when_stopped() -> None:
    _, delivery_id, _ = _delivery_job()
    stop = threading.Event()
    adapter = _Adapter(on_call=lambda _request: stop.set())
    worker = _worker(adapter, poll_interval=timedelta(seconds=60))
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert _delivery(delivery_id).status == "delivered"


# --- Transaction boundary / production wiring --------------------------------------


@requires_postgres
def test_delivery_failure_never_touches_business_or_notification_state() -> None:
    club_id, delivery_id, _ = _delivery_job()
    _worker(_Adapter(ChannelResult.permanent("rejected"))).run_once()
    with session_scope() as session:
        assert session.get(Club, club_id) is not None
        notification_status = session.execute(
            sa.text(
                "SELECT n.status FROM notifications n JOIN notification_deliveries d "
                "ON d.notification_id = n.id WHERE d.id = :id"
            ),
            {"id": delivery_id},
        ).scalar_one()
    assert notification_status == "pending"


@requires_postgres
def test_production_worker_registers_no_channel_adapter() -> None:
    _, delivery_id, _ = _delivery_job()
    (report,) = build_worker(_config()).run_once()
    assert (report.result, report.error_code) == (
        "pending",
        CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE,
    )
    assert _delivery(delivery_id).status == "failed"


@requires_postgres
def test_worker_process_starts_and_stops_gracefully_on_sigterm(database_url: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "OUTBOX_WORKER_POLL_INTERVAL_SECONDS": "60",
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "app.cli.run_outbox_worker"],
        cwd=Path(API_ROOT),
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stderr is not None
        started = process.stderr.readline()
        assert "outbox worker started" in started, started
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0
    assert "outbox worker stopped" in stderr
