"""Runtime application of Administrator Notification Settings (Issue #333,
ADR-0048 §2.6/§2.8/§2.9) against PostgreSQL: the outbox worker reads the
current SMTP/Telegram configuration for every delivery attempt (a saved
change applies without a new worker), Global OFF blocks already queued
deliveries, undecryptable secrets fail closed without any provider call,
the key-rotation CLI, and the migration round trip."""

import smtplib
import subprocess
import sys
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.cli.run_outbox_worker import build_worker
from app.core.config import SmtpSettings
from app.db.identity import Club, Person, User
from app.db.notification_settings import IntegrationSecret
from app.db.notifications import NotificationDelivery, NotificationTemplate
from app.db.outbox import OutboxJob
from app.db.session import get_engine, session_scope
from app.notification_settings.crypto import KeyRing, decrypt_secret, token_key_id
from app.notifications.repository import add_delivery, create_notification
from app.notifications.vocabulary import (
    NOTIFICATION_DELIVERY_JOB_TYPE,
    notification_delivery_deduplication_key,
)
from app.outbox.service import enqueue_outbox_job
from app.outbox.worker import WorkerConfig
from tests.notification_settings_helpers import (
    TEST_KEY_A,
    TEST_KEY_B,
    store_email_settings,
    store_policy,
    store_secret,
    store_telegram_settings,
)
from tests.telegram_fakes import BOT_TOKEN, ScriptedTransport, client_for, ok

from ._schema_reset import API_ROOT, run_alembic
from .conftest import requires_postgres

SMTP_PASSWORD = "Sm7p-Pa55w0rd-UNIQUE-4711"
NEW_SMTP_PASSWORD = "R0tated-Pa55w0rd-UNIQUE-0815"
TELEGRAM_CHAT = 7_100_000_001


class _RecordingTransport:
    """SmtpTransport double that records the settings each send used."""

    instances: list["_RecordingTransport"] = []

    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings
        self.sent: list[str] = []
        _RecordingTransport.instances.append(self)

    def send(self, message: Any, *, sender: str, recipient: str) -> None:
        self.sent.append(recipient)


@pytest.fixture(autouse=True)
def _reset_transports() -> None:
    _RecordingTransport.instances = []


def _delivery(channel: str = "email", telegram_chat: int = TELEGRAM_CHAT) -> dict[str, uuid.UUID]:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test",
            status="active",
            email_verified_at=sa.func.now(),
        )
        session.add_all([club, user])
        session.flush()
        if channel == "telegram":
            from app.db.telegram import TelegramIdentity

            session.add(
                TelegramIdentity(user_id=user.id, telegram_user_id=telegram_chat, status="active")
            )
        template = NotificationTemplate(
            code=f"tpl-{uuid.uuid4().hex[:8]}", channel=channel, locale="ru",
            subject_template="Тема", body_template="Текст",
        )
        session.add(template)
        session.flush()
        notification, _ = create_notification(
            session,
            idempotency_key=f"test:{uuid.uuid4()}",
            event_type="test.event",
            subject_type="test_subject",
            recipient_user_id=user.id,
            club_id=club.id,
            template_id=template.id,
        )
        delivery, _ = add_delivery(
            session, notification=notification, channel=channel,
            destination_type="user", destination_id=user.id,
        )
        job, _ = enqueue_outbox_job(
            session,
            job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
            payload={"delivery_id": str(delivery.id)},
            deduplication_key=notification_delivery_deduplication_key(delivery.id),
        )
        session.commit()
        return {"delivery": delivery.id, "job": job.id}


def _db_now() -> Any:
    with session_scope() as session:
        return session.execute(sa.select(sa.func.now())).scalar_one()


def _recipient(ids: dict[str, uuid.UUID]) -> str:
    with session_scope() as session:
        delivery = session.get(NotificationDelivery, ids["delivery"])
        assert delivery is not None
        user = session.get(User, delivery.destination_id)
        assert user is not None and user.login_identifier is not None
        return user.login_identifier


def _row(model: Any, row_id: uuid.UUID) -> Any:
    with session_scope() as session:
        return session.get(model, row_id)


def _worker(**kwargs: Any):  # type: ignore[no-untyped-def]
    return build_worker(
        WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"),
        smtp_transport_factory=_RecordingTransport,  # type: ignore[arg-type]
        **kwargs,
    )


# --- Worker: dynamic configuration ----------------------------------------------------------


@requires_postgres
def test_worker_uses_the_current_settings_for_every_delivery() -> None:
    store_policy(email=True)
    store_email_settings(host="smtp-one.test", password=SMTP_PASSWORD)
    worker = _worker()
    first = _delivery()
    (report,) = worker.run_once()
    assert report.result == "completed"

    # The administrator changes host and password; the SAME worker applies it.
    store_email_settings(host="smtp-two.test", password=NEW_SMTP_PASSWORD)
    second = _delivery()
    (report,) = worker.run_once()
    assert report.result == "completed"
    used = [(t.settings.host, t.settings.password) for t in _RecordingTransport.instances]
    assert used == [("smtp-one.test", SMTP_PASSWORD), ("smtp-two.test", NEW_SMTP_PASSWORD)]
    assert _row(NotificationDelivery, first["delivery"]).status == "delivered"
    assert _row(NotificationDelivery, second["delivery"]).status == "delivered"


def _make_due(job_id: uuid.UUID) -> None:
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob)
            .where(OutboxJob.id == job_id)
            .values(next_attempt_at=sa.func.now() - timedelta(seconds=1))
        )
        session.commit()


def _counts() -> tuple[int, int]:
    with session_scope() as session:
        return (
            session.execute(sa.select(sa.func.count()).select_from(NotificationDelivery))
            .scalar_one(),
            session.execute(sa.select(sa.func.count()).select_from(OutboxJob)).scalar_one(),
        )


class _FailingTransport(_RecordingTransport):
    def send(self, message: Any, *, sender: str, recipient: str) -> None:
        raise smtplib.SMTPServerDisconnected("connection lost")


@requires_postgres
def test_global_off_pauses_without_provider_call_attempt_or_terminal_state() -> None:
    store_policy(email=True, telegram=True)
    store_email_settings(password=SMTP_PASSWORD)
    ids = _delivery()
    store_policy(email=False, telegram=True)
    worker = _worker()
    before = _db_now()
    (report,) = worker.run_once()
    assert (report.result, report.error_code) == ("deferred", "channel_disabled_by_policy")
    assert _RecordingTransport.instances == []
    job = _row(OutboxJob, ids["job"])
    assert (job.status, job.attempts, job.locked_by, job.finished_at) == (
        "pending",
        0,
        None,
        None,
    )
    # Re-checked after the pause interval, not immediately: no busy loop.
    assert job.next_attempt_at >= before + worker.config.pause_recheck
    assert worker.run_once() == []
    delivery = _row(NotificationDelivery, ids["delivery"])
    assert (delivery.status, delivery.attempts, delivery.last_error_code) == ("pending", 0, None)
    assert delivery.first_attempt_at is None


@requires_postgres
def test_paused_delivery_survives_many_rechecks_then_resumes_when_switched_on() -> None:
    """Full path: Global OFF -> deferred without attempts -> Global ON ->
    the same Delivery and job are processed by the ordinary path."""
    store_email_settings(password=SMTP_PASSWORD)
    store_policy(email=False, telegram=False)
    ids = _delivery()
    worker = _worker()
    # More re-checks than max_attempts (5): the budget is never consumed.
    for _ in range(worker.config.max_attempts + 3):
        (report,) = worker.run_once()
        assert report.result == "deferred"
        _make_due(ids["job"])
    assert _row(OutboxJob, ids["job"]).attempts == 0
    assert _row(NotificationDelivery, ids["delivery"]).attempts == 0

    store_policy(email=True, telegram=False)
    (report,) = worker.run_once()
    assert report.result == "completed"
    assert [t.sent for t in _RecordingTransport.instances] == [[_recipient(ids)]]
    job, delivery = _row(OutboxJob, ids["job"]), _row(NotificationDelivery, ids["delivery"])
    assert (job.status, job.attempts) == ("completed", 1)
    assert (delivery.status, delivery.attempts) == ("delivered", 1)
    # No duplicate Delivery or outbox job, and nothing left to process.
    assert _counts() == (1, 1)
    _make_due(ids["job"])
    assert worker.run_once() == []


@requires_postgres
def test_provider_errors_still_consume_attempts_and_a_retrying_delivery_pauses() -> None:
    store_policy(email=True)
    store_email_settings(password=SMTP_PASSWORD)
    ids = _delivery()
    failing = build_worker(
        WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"),
        smtp_transport_factory=_FailingTransport,  # type: ignore[arg-type]
    )
    (report,) = failing.run_once()
    assert (report.result, report.error_code) == ("pending", "smtp_connection_failed")
    job, delivery = _row(OutboxJob, ids["job"]), _row(NotificationDelivery, ids["delivery"])
    assert (job.attempts, delivery.attempts, delivery.status) == (1, 1, "failed")
    assert delivery.next_retry_at is not None

    # Switched OFF while the retry waits: the retry is paused, not counted.
    store_policy(email=False)
    _make_due(ids["job"])
    (report,) = failing.run_once()
    assert report.result == "deferred"
    job, delivery = _row(OutboxJob, ids["job"]), _row(NotificationDelivery, ids["delivery"])
    assert (job.attempts, delivery.attempts) == (1, 1)
    assert (delivery.status, delivery.last_error_code) == ("failed", "smtp_connection_failed")
    # The Delivery's scheduled retry follows the job's new eligibility.
    assert delivery.next_retry_at == job.next_attempt_at

    store_policy(email=True)
    _make_due(ids["job"])
    (report,) = _worker().run_once()
    assert report.result == "completed"
    assert _row(OutboxJob, ids["job"]).attempts == 2
    assert _row(NotificationDelivery, ids["delivery"]).attempts == 2


@requires_postgres
def test_switching_off_during_a_send_does_not_cancel_it_but_pauses_the_next() -> None:
    store_policy(email=True)
    store_email_settings(password=SMTP_PASSWORD)
    first, second = _delivery(), _delivery()

    class _SwitchOffMidSend(_RecordingTransport):
        def send(self, message: Any, *, sender: str, recipient: str) -> None:
            store_policy(email=False)
            super().send(message, sender=sender, recipient=recipient)

    worker = build_worker(
        WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}", batch_size=2),
        smtp_transport_factory=_SwitchOffMidSend,  # type: ignore[arg-type]
    )
    reports = worker.run_once()
    assert [report.result for report in reports] == ["completed", "deferred"]
    delivered = [first, second][0 if reports[0].job_id == first["job"] else 1]
    paused = second if delivered is first else first
    assert _row(NotificationDelivery, delivered["delivery"]).status == "delivered"
    assert _row(NotificationDelivery, paused["delivery"]).attempts == 0


@requires_postgres
def test_concurrent_workers_pause_and_resume_every_delivery_exactly_once() -> None:
    import threading

    store_email_settings(password=SMTP_PASSWORD)
    store_policy(email=False)
    ids = [_delivery() for _ in range(6)]
    workers = [
        build_worker(
            WorkerConfig(worker_id=f"w-{index}-{uuid.uuid4().hex[:4]}", batch_size=2),
            smtp_transport_factory=_RecordingTransport,  # type: ignore[arg-type]
        )
        for index in range(3)
    ]

    def run_all() -> list[Any]:
        results: list[Any] = []
        barrier = threading.Barrier(len(workers))

        def run(worker: Any) -> None:
            barrier.wait()
            results.extend(worker.run_once())

        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results

    paused = run_all()
    assert sorted(report.result for report in paused) == ["deferred"] * 6
    assert len({report.job_id for report in paused}) == 6
    assert _RecordingTransport.instances == []
    assert all(_row(OutboxJob, item["job"]).attempts == 0 for item in ids)

    store_policy(email=True)
    for item in ids:
        _make_due(item["job"])
    resumed = run_all()
    assert sorted(report.result for report in resumed) == ["completed"] * 6
    assert len({report.job_id for report in resumed}) == 6
    assert sum(len(t.sent) for t in _RecordingTransport.instances) == 6
    assert all(
        _row(NotificationDelivery, item["delivery"]).status == "delivered" for item in ids
    )
    assert _counts() == (6, 6)


@requires_postgres
@pytest.mark.parametrize("key_ring", [None, f"other:{TEST_KEY_B}"])
def test_undecryptable_secret_fails_closed_without_provider_call(
    monkeypatch: pytest.MonkeyPatch, key_ring: str | None
) -> None:
    store_policy(email=True)
    store_email_settings(password=SMTP_PASSWORD)
    if key_ring is None:
        monkeypatch.delenv("SETTINGS_ENCRYPTION_KEYS")
    else:
        monkeypatch.setenv("SETTINGS_ENCRYPTION_KEYS", key_ring)
    ids = _delivery()
    (report,) = _worker().run_once()
    assert (report.result, report.error_code) == ("pending", "channel_adapter_unavailable")
    assert _RecordingTransport.instances == []
    delivery = _row(NotificationDelivery, ids["delivery"])
    assert delivery.last_error_message == "configuration=secret_unavailable"
    job = _row(OutboxJob, ids["job"])
    for text in (str(job.payload), str(job.last_error_message), str(delivery.last_error_message)):
        assert SMTP_PASSWORD not in text


@requires_postgres
def test_telegram_worker_picks_up_a_replaced_token_without_restart() -> None:
    store_policy(email=False, telegram=True)
    store_telegram_settings(token=BOT_TOKEN)
    replacement = "222222222:CCReplacementFakeTokenForTestsOnly_2222"
    transports = {
        BOT_TOKEN: ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})}),
        replacement: ScriptedTransport(defaults={"sendMessage": ok({"message_id": 2})}),
    }
    worker = _worker(
        telegram_client_factory=lambda settings: client_for(transports[settings.bot_token])
    )
    _delivery("telegram")
    worker.run_once()
    store_telegram_settings(token=replacement)
    _delivery("telegram", telegram_chat=TELEGRAM_CHAT + 1)
    worker.run_once()
    assert len(transports[BOT_TOKEN].calls_to("sendMessage")) == 1
    assert len(transports[replacement].calls_to("sendMessage")) == 1
    assert transports[replacement].calls[0].params["chat_id"] == TELEGRAM_CHAT + 1


# --- Key rotation CLI -----------------------------------------------------------------------


def _run_cli(key_ring: str | None) -> subprocess.CompletedProcess:
    import os

    env = {key: value for key, value in os.environ.items() if key != "SETTINGS_ENCRYPTION_KEYS"}
    if key_ring is not None:
        env["SETTINGS_ENCRYPTION_KEYS"] = key_ring
    return subprocess.run(
        [sys.executable, "-m", "app.cli.reencrypt_settings_secrets"],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _tokens() -> dict[str, str]:
    with session_scope() as session:
        rows = session.execute(sa.select(IntegrationSecret.name, IntegrationSecret.ciphertext))
        return {name: ciphertext for name, ciphertext in rows}


@requires_postgres
def test_rotation_reencrypts_with_the_primary_key_and_prints_no_secrets() -> None:
    old_ring = f"old:{TEST_KEY_A}"
    store_secret("smtp_password", SMTP_PASSWORD, key_ring=old_ring)
    store_secret("telegram_bot_token", BOT_TOKEN, key_ring=old_ring)
    before = _tokens()

    rotated = f"new:{TEST_KEY_B},old:{TEST_KEY_A}"
    result = _run_cli(rotated)
    assert result.returncode == 0, result.stderr
    assert "primary_key_id=new reencrypted=2 already_current=0" in result.stdout
    after = _tokens()
    assert all(token_key_id(token) == "new" for token in after.values())
    assert after != before
    # Readable with the new key alone: the old key can now be removed.
    new_only = KeyRing.parse(f"new:{TEST_KEY_B}")
    assert decrypt_secret(new_only, "smtp_password", after["smtp_password"]) == SMTP_PASSWORD
    assert decrypt_secret(new_only, "telegram_bot_token", after["telegram_bot_token"]) == BOT_TOKEN
    for text in (result.stdout, result.stderr):
        for secret in (SMTP_PASSWORD, BOT_TOKEN, TEST_KEY_A, TEST_KEY_B, *before.values(),
                       *after.values()):
            assert secret not in text

    # Idempotent.
    again = _run_cli(rotated)
    assert "reencrypted=0 already_current=2" in again.stdout


@requires_postgres
def test_rotation_aborts_without_changes_when_a_value_cannot_be_decrypted() -> None:
    store_secret("smtp_password", SMTP_PASSWORD, key_ring=f"old:{TEST_KEY_A}")
    store_secret("telegram_bot_token", BOT_TOKEN, key_ring=f"lost:{TEST_KEY_B}")
    before = _tokens()
    result = _run_cli(f"new:{TEST_KEY_B},old:{TEST_KEY_A}")
    assert result.returncode == 1
    assert "secret_key_unknown" in result.stderr and "nothing was changed" in result.stderr
    assert _tokens() == before


@requires_postgres
@pytest.mark.parametrize("key_ring", [None, "broken"])
def test_rotation_without_a_valid_key_ring_exits_2(key_ring: str | None) -> None:
    result = _run_cli(key_ring)
    assert result.returncode == 2
    assert "SETTINGS_ENCRYPTION_KEYS" in result.stderr


# --- Migration ------------------------------------------------------------------------------

_MIGRATION_PARENT = "162f2e413172"
_NEW_TABLES = {
    "notification_global_policy",
    "notification_email_settings",
    "notification_telegram_settings",
    "integration_secrets",
    "notification_test_send_attempts",
}


@requires_postgres
def test_migration_round_trip(database_url: str) -> None:
    try:
        downgrade = run_alembic("downgrade", _MIGRATION_PARENT, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        assert not _NEW_TABLES & set(sa.inspect(get_engine()).get_table_names())
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert _NEW_TABLES <= set(sa.inspect(get_engine()).get_table_names())
    # No plaintext secret column anywhere: only the encrypted token.
    columns = {
        column["name"]
        for table in _NEW_TABLES
        for column in sa.inspect(get_engine()).get_columns(table)
    }
    assert not {name for name in columns if "password" in name or "token" in name}
    assert "ciphertext" in columns


# --- Concurrent first saves -----------------------------------------------------------------


@requires_postgres
def test_concurrent_first_saves_of_policy_settings_and_secret_do_not_conflict() -> None:
    import threading

    from app.db.notification_settings import NotificationGlobalPolicy
    from app.notification_settings import service

    with session_scope() as session:
        person = Person(last_name="Adminova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(person=person, login_identifier=f"a-{uuid.uuid4().hex[:8]}@c.test",
                    status="active")
        session.add(user)
        session.commit()
        actor = user.id
    barrier = threading.Barrier(6)
    errors: list[BaseException] = []

    def work(index: int) -> None:
        barrier.wait()
        try:
            with session_scope() as session:
                service.update_policy(
                    session, actor_user_id=actor, email_enabled=True, telegram_enabled=False
                )
                service.update_telegram_settings(
                    session, actor_user_id=actor, bot_username=f"club{index}_bot"
                )
                service.set_secret(
                    session, actor_user_id=actor, name="smtp_password", value=f"pw-{index}"
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    with session_scope() as session:
        assert session.execute(
            sa.select(sa.func.count()).select_from(NotificationGlobalPolicy)
        ).scalar_one() == 1
        assert session.execute(
            sa.select(sa.func.count()).select_from(IntegrationSecret)
        ).scalar_one() == 1
        first_saves = session.execute(
            sa.text(
                "SELECT count(*) FROM audit_logs WHERE action = 'notification_policy.updated'"
            )
        ).scalar_one()
    # Exactly one call created the policy; the others found it saved.
    assert first_saves == 1
