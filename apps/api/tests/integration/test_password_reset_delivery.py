"""Issue #342: password-reset code delivery by email through the
Notification Center and the outbox, against real PostgreSQL.

Request -> challenge + encrypted delivery copy + Notification + Delivery +
outbox job (one transaction) -> worker -> NotificationDeliveryHandler ->
EmailChannelAdapter (decrypts in memory) -> SMTP transport double ->
recording transaction (clears the copy).

The raw code is only ever read back from the message the transport
received — exactly what the user gets — and then used against the real
confirm endpoint/service.
"""

import email
import logging
import re
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Optional

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.authentication import service as auth_service
from app.authentication.password_reset_delivery import (
    AUTH_DELIVERY_KEY_RING_ENV,
    CHALLENGE_NOT_DELIVERABLE,
    DELIVERY_ENCRYPTION_UNAVAILABLE,
    PASSWORD_RESET_EVENT_TYPE,
    PASSWORD_RESET_TEMPLATE_CODE,
    PasswordResetCodeDelivery,
)
from app.authentication.passwords import hash_password, verify_password
from app.cli.run_outbox_worker import build_worker
from app.core.config import SmtpSettings
from app.db.authentication import AuthenticatedSession, PasswordResetChallenge
from app.db.identity import Person, User
from app.db.notifications import (
    CommunicationChannelPreference,
    CommunicationPreference,
    Notification,
    NotificationDelivery,
    NotificationRule,
)
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.main import app
from app.notification_settings.crypto import KeyRing, SecretDecryptionError, decrypt_secret
from app.notification_settings.policy import GlobalAdminPolicy
from app.notifications import security_email
from app.notifications.delivery import NotificationDeliveryHandler
from app.notifications.email_adapter import EmailChannelAdapter
from app.notifications.vocabulary import NOTIFICATION_DELIVERY_JOB_TYPE
from app.outbox.worker import OutboxWorker, WorkerConfig
from tests.notification_settings_helpers import (
    TEST_KEY_B,
    TEST_KEY_RING,
    store_email_settings,
    store_policy,
)
from tests.smtp_server import FakeSmtpConfig, FakeSmtpServer

from .conftest import requires_postgres

PASSWORD = "currentpassword1"
NEW_PASSWORD = "brandnewpassword1"
# TEST-ONLY delivery key ring, deliberately different from the settings one.
DELIVERY_KEY_RING = f"auth1:{TEST_KEY_B}"
_CODE_LINE = re.compile(r"Одноразовый код: (\S+)")


@pytest.fixture(autouse=True)
def _delivery_key_ring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_DELIVERY_KEY_RING_ENV, DELIVERY_KEY_RING)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class _Transport:
    """SmtpTransport double. Records every message it was handed — also
    those of failed attempts (`attempted`) — and raises `raises` for the
    first `fail_times` calls, optionally AFTER recording the message as
    accepted (the ambiguous "server took it, then the connection broke")."""

    def __init__(
        self,
        raises: Optional[BaseException] = None,
        fail_times: int = 0,
        accepted_before_failure: bool = False,
    ) -> None:
        self.attempted: list[EmailMessage] = []
        self.sent: list[tuple[EmailMessage, str, str]] = []
        self.raises = raises
        self.fail_times = fail_times
        self.accepted_before_failure = accepted_before_failure

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> None:
        self.attempted.append(message)
        if self.raises is not None and self.fail_times > 0:
            self.fail_times -= 1
            if self.accepted_before_failure:
                self.sent.append((message, sender, recipient))
            raise self.raises
        self.sent.append((message, sender, recipient))


def _smtp_settings() -> SmtpSettings:
    return SmtpSettings(
        host="smtp.test",
        port=587,
        security="starttls",
        timeout_seconds=5.0,
        sender_email="noreply@club.test",
        sender_name="TourCRM",
        username=None,
        password=None,
    )


def _worker(transport: Any, *, max_attempts: int = 5) -> OutboxWorker:
    """The production handler wiring (resolver + cleanup + Global policy)
    with a recording transport instead of the settings-driven SMTP."""
    factory = get_session_factory()
    codes = PasswordResetCodeDelivery()
    adapter = EmailChannelAdapter(
        settings=_smtp_settings(),
        transport=transport,
        session_factory=factory,
        secret_resolvers={PASSWORD_RESET_EVENT_TYPE: codes},
    )
    handler = NotificationDeliveryHandler(
        {"email": adapter}, admin_policy=GlobalAdminPolicy(), secret_cleanup=codes
    )
    return OutboxWorker(
        session_factory=factory,
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: handler},
        config=WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}", max_attempts=max_attempts),
    )


def _user(login: str = "anna@club.test", *, verified: bool = False) -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Ivanova", first_name=f"A-{uuid.uuid4().hex[:6]}")
        user = User(
            person=person,
            login_identifier=login,
            password_hash=hash_password(PASSWORD),
            status="active",
            email_verified_at=datetime(2026, 9, 1, tzinfo=timezone.utc) if verified else None,
        )
        session.add(user)
        session.commit()
        return user.id


def _request(identifier: str = "anna@club.test") -> str:
    with session_scope() as session:
        return auth_service.request_password_reset_email(
            session, identifier, admin_policy=GlobalAdminPolicy()
        )


def _code(message: EmailMessage) -> str:
    parsed = email.message_from_bytes(message.as_bytes(), policy=email.policy.default)
    match = _CODE_LINE.search(parsed.get_content())
    assert match is not None
    return match.group(1)


def _challenges(user_id: uuid.UUID) -> list[PasswordResetChallenge]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(PasswordResetChallenge)
                .where(PasswordResetChallenge.user_id == user_id)
                .order_by(PasswordResetChallenge.created_at)
            ).scalars()
        )


def _count(model: Any) -> int:
    with session_scope() as session:
        return session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def _make_jobs_due() -> None:
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob)
            .where(OutboxJob.status == "pending")
            .values(next_attempt_at=sa.func.now() - timedelta(seconds=1))
        )
        session.commit()


def _confirm(raw_token: str, password: str = NEW_PASSWORD, confirm: Optional[str] = None) -> None:
    with session_scope() as session:
        auth_service.confirm_password_reset(
            session,
            raw_token=raw_token,
            new_password=password,
            new_password_confirmation=password if confirm is None else confirm,
        )


def _assert_code_nowhere_in_database(raw_code: str) -> None:
    """Scan every column of every table (as text) for the raw code."""
    with session_scope() as session:
        tables = session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
            )
        ).scalars()
        # Table names only: a failure message never shows the code itself.
        hits = [
            table
            for table in list(tables)
            if session.execute(
                sa.text(f'SELECT count(*) FROM "{table}" t WHERE position(:code in t::text) > 0'),
                {"code": raw_code},
            ).scalar_one()
        ]
    assert hits == []


# --- 1. Identical public response -------------------------------------------------------


@requires_postgres
def test_known_and_unknown_identifier_get_the_identical_public_response(
    client: TestClient,
) -> None:
    store_policy(email=True)
    _user()
    known = client.post(
        "/api/v1/auth/password-reset/request", json={"identifier": "anna@club.test"}
    )
    unknown = client.post(
        "/api/v1/auth/password-reset/request", json={"identifier": "nobody@club.test"}
    )
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert known.json() == {"status": "ok"}
    assert _count(NotificationDelivery) == 1  # only the known account got a delivery


@requires_postgres
def test_a_failure_while_queueing_keeps_the_public_response_and_logs_only_a_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store_policy(email=True)
    user_id = _user()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("queue down: anna@club.test secret-detail")

    monkeypatch.setattr(auth_service, "queue_security_email", boom)
    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/api/v1/auth/password-reset/request", json={"identifier": "anna@club.test"}
        )
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    assert "outcome=error error_type=RuntimeError" in caplog.text
    assert "secret-detail" not in caplog.text and "anna@club.test" not in caplog.text
    # 2. transactional: nothing of the failed request survived.
    assert _challenges(user_id) == []
    assert _count(Notification) == 0 and _count(OutboxJob) == 0


# --- 2. Challenge and outbox job are written together -------------------------------------


@requires_postgres
def test_request_writes_challenge_copy_notification_delivery_and_job_together() -> None:
    store_policy(email=True)
    user_id = _user()
    assert _request() == "queued"

    (challenge,) = _challenges(user_id)
    assert challenge.delivery_secret_ciphertext is not None
    with session_scope() as session:
        notification = session.execute(sa.select(Notification)).scalar_one()
        delivery = session.execute(sa.select(NotificationDelivery)).scalar_one()
        job = session.execute(sa.select(OutboxJob)).scalar_one()
        assert (notification.event_type, notification.subject_id) == (
            PASSWORD_RESET_EVENT_TYPE,
            challenge.id,
        )
        assert notification.recipient_user_id == user_id
        assert notification.render_context == {}
        assert (delivery.channel, delivery.destination_id) == ("email", user_id)
        assert job.payload == {"delivery_id": str(delivery.id)}


@requires_postgres
def test_an_enqueue_failure_rolls_back_the_challenge_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_policy(email=True)
    user_id = _user()

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("enqueue failed")

    monkeypatch.setattr(security_email, "enqueue_outbox_job", fail)
    with pytest.raises(RuntimeError):
        _request()
    assert _challenges(user_id) == []
    assert _count(Notification) == 0


# --- 3. Unverified login email, end to end -----------------------------------------------


@requires_postgres
def test_unverified_account_receives_the_code_and_can_set_a_new_password(
    client: TestClient,
) -> None:
    store_policy(email=True)
    user_id = _user(verified=False)
    client.post("/api/v1/auth/password-reset/request", json={"identifier": "ANNA@club.test"})
    transport = _Transport()
    (report,) = _worker(transport).run_once()

    assert report.result == "completed"
    ((message, _sender, recipient),) = transport.sent
    assert recipient == "anna@club.test"
    assert message["Subject"] == "TourCRM: код для установки пароля"
    code = _code(message)
    assert "{{" not in message.get_content()

    (challenge,) = _challenges(user_id)
    assert challenge.delivery_secret_ciphertext is None  # cleared on success

    # The code is not a password: /auth/login refuses it.
    login_with_code = client.post(
        "/api/v1/auth/login", json={"identifier": "anna@club.test", "password": code}
    )
    assert login_with_code.status_code == 401

    confirm = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": code, "new_password": NEW_PASSWORD, "new_password_confirm": NEW_PASSWORD},
    )
    assert confirm.status_code == 200, confirm.text
    reused = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": code,
            "new_password": "another1password",
            "new_password_confirm": "another1password",
        },
    )
    assert reused.json()["error"]["code"] == "invalid_or_expired_token"
    login = client.post(
        "/api/v1/auth/login", json={"identifier": "anna@club.test", "password": NEW_PASSWORD}
    )
    assert login.status_code == 200, login.text


@requires_postgres
def test_full_path_through_the_production_worker_and_a_real_smtp_conversation() -> None:
    store_policy(email=True)
    user_id = _user()
    assert _request() == "queued"
    config = FakeSmtpConfig()
    with FakeSmtpServer(config) as server:
        store_email_settings(host="127.0.0.1", port=server.port, security="none", username=None)
        (report,) = build_worker(WorkerConfig(worker_id="w-prod")).run_once()
    assert report.result == "completed"
    (received,) = config.received
    assert received.rcpt_to == ["anna@club.test"]
    code = _code(email.message_from_bytes(received.data, policy=email.policy.default))
    _confirm(code)
    assert _challenges(user_id)[0].consumed_at is not None


# --- 4. Global email OFF is absolute -----------------------------------------------------


@requires_postgres
def test_global_email_off_issues_nothing_and_keeps_an_admin_code_valid() -> None:
    store_policy(email=False)
    user_id = _user()
    with session_scope() as session:
        admin_code = auth_service.request_password_reset(session, "anna@club.test")
    assert admin_code is not None

    assert _request() == "channel_disabled"
    assert len(_challenges(user_id)) == 1
    assert _count(Notification) == 0 and _count(OutboxJob) == 0
    _confirm(admin_code)  # the administrator's code was not superseded


@requires_postgres
def test_a_queued_code_is_not_sent_while_email_is_switched_off() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    store_policy(email=False)
    transport = _Transport()
    (report,) = _worker(transport).run_once()

    assert report.result == "deferred"
    assert transport.attempted == []
    assert _challenges(user_id)[0].delivery_secret_ciphertext is not None  # kept for later

    store_policy(email=True)
    _make_jobs_due()
    (report,) = _worker(transport).run_once()
    assert report.result == "completed" and len(transport.sent) == 1


# --- 5. Rules and preferences do not apply -----------------------------------------------


@requires_postgres
def test_disabled_rules_and_opted_out_preferences_do_not_block_the_code() -> None:
    store_policy(email=True)
    user_id = _user()
    with session_scope() as session:
        session.add_all(
            [
                NotificationRule(
                    event_type=PASSWORD_RESET_EVENT_TYPE,
                    channel="email",
                    recipient_scope="user",
                    is_enabled=False,
                ),
                CommunicationChannelPreference(
                    user_id=user_id, channel="email", destination_type="user", enabled=False
                ),
                CommunicationPreference(
                    user_id=user_id,
                    channel="email",
                    destination_type="user",
                    notification_type=PASSWORD_RESET_EVENT_TYPE,
                    enabled=False,
                ),
            ]
        )
        session.commit()
    assert _request() == "queued"
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert report.result == "completed" and len(transport.sent) == 1


# --- 6. The raw code is stored and logged nowhere -----------------------------------------


@requires_postgres
def test_raw_code_is_absent_from_database_payloads_audit_and_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    store_policy(email=True)
    user_id = _user()
    transport = _Transport(raises=socket.timeout("timed out"), fail_times=1)
    with caplog.at_level(logging.DEBUG):
        client.post("/api/v1/auth/password-reset/request", json={"identifier": "anna@club.test"})
        _worker(transport).run_once()  # a failed attempt: error paths too
        code = _code(transport.attempted[0])
        _assert_code_nowhere_in_database(code)  # while the copy is pending
        _make_jobs_due()
        _worker(transport).run_once()
    leaked_to_logs = code in caplog.text
    assert not leaked_to_logs, "the raw code was written to a log"
    _assert_code_nowhere_in_database(code)

    (challenge,) = _challenges(user_id)
    assert challenge.delivery_secret_ciphertext is None
    with session_scope() as session:
        delivery = session.execute(sa.select(NotificationDelivery)).scalar_one()
        assert delivery.status == "delivered"


@requires_postgres
def test_the_stored_copy_is_bound_to_its_own_key_ring_and_challenge() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    (challenge,) = _challenges(user_id)
    token = challenge.delivery_secret_ciphertext
    assert token is not None and token.startswith("v1.auth1.")
    # The settings key ring cannot read it, nor can another challenge's name.
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(
            KeyRing.parse(TEST_KEY_RING), f"password_reset_challenge:{challenge.id}", token
        )
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(
            KeyRing.parse(DELIVERY_KEY_RING),
            f"password_reset_challenge:{uuid.uuid4()}",
            token,
            aad_prefix=b"tourcrm.auth_delivery_secret.v1:",
        )


# --- 7-9. Retries resend the same code -----------------------------------------------------


@requires_postgres
def test_a_retry_after_a_transient_failure_sends_the_same_code() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    transport = _Transport(raises=socket.timeout("timed out"), fail_times=1)
    (first,) = _worker(transport).run_once()
    assert first.result == "pending"
    assert _challenges(user_id)[0].delivery_secret_ciphertext is not None

    _make_jobs_due()
    (second,) = _worker(transport).run_once()
    assert second.result == "completed"
    same_code = _code(transport.attempted[0]) == _code(transport.attempted[1])
    assert same_code, "the retry sent a different code"
    assert len(_challenges(user_id)) == 1  # no new challenge was issued


@requires_postgres
def test_a_worker_crash_mid_send_is_recovered_by_another_worker_with_the_same_code() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    crashed = _worker(_Transport())
    assert len(crashed.claim()) == 1  # leased, then the process "dies"
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob).values(locked_until=sa.func.now() - timedelta(seconds=1))
        )
        session.commit()

    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert report.result == "completed"
    _confirm(_code(transport.sent[0][0]))
    assert len(_challenges(user_id)) == 1


@requires_postgres
def test_smtp_timeout_after_acceptance_resends_the_same_still_valid_code() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    transport = _Transport(
        raises=socket.timeout("timed out"), fail_times=1, accepted_before_failure=True
    )
    (first,) = _worker(transport).run_once()
    assert first.result == "pending"
    _make_jobs_due()
    _worker(transport).run_once()

    first_message, second_message = (sent[0] for sent in transport.sent)
    same_code = _code(first_message) == _code(second_message)
    assert same_code, "the retry sent a different code"
    # The code from the email the user may already hold still works.
    _confirm(_code(first_message))
    assert len(_challenges(user_id)) == 1


@requires_postgres
def test_retries_exhausted_clear_the_copy() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    transport = _Transport(raises=socket.timeout("timed out"), fail_times=5)
    (report,) = _worker(transport, max_attempts=1).run_once()
    assert report.result == "dead"
    assert _challenges(user_id)[0].delivery_secret_ciphertext is None


@requires_postgres
def test_missing_delivery_key_in_the_worker_is_retried_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    monkeypatch.delenv(AUTH_DELIVERY_KEY_RING_ENV)
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("pending", DELIVERY_ENCRYPTION_UNAVAILABLE)
    assert transport.attempted == []
    assert _challenges(user_id)[0].delivery_secret_ciphertext is not None


@requires_postgres
def test_missing_delivery_key_at_request_time_issues_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_policy(email=True)
    user_id = _user()
    monkeypatch.delenv(AUTH_DELIVERY_KEY_RING_ENV)
    assert _request() == "encryption_unavailable"
    assert _challenges(user_id) == [] and _count(OutboxJob) == 0


# --- 10. Expired, consumed and revoked challenges are not sent -----------------------------


@requires_postgres
def test_an_expired_challenge_is_not_sent_and_its_copy_is_cleared() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    with session_scope() as session:
        session.execute(
            sa.update(PasswordResetChallenge).values(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
            )
        )
        session.commit()
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", CHALLENGE_NOT_DELIVERABLE)
    assert transport.attempted == []
    assert _challenges(user_id)[0].delivery_secret_ciphertext is None


@requires_postgres
def test_a_superseded_challenge_is_not_sent_only_the_latest_one_is() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    _request()
    first, second = _challenges(user_id)
    assert first.revoked_at is not None and first.delivery_secret_ciphertext is None
    transport = _Transport()
    worker = _worker(transport)
    reports = worker.run_once() + worker.run_once()
    assert sorted(r.result for r in reports) == ["completed", "dead"]
    ((message, _, _),) = transport.sent
    _confirm(_code(message))  # the code that went out is the valid one


@requires_postgres
def test_a_consumed_challenge_is_not_sent_again() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    transport = _Transport(
        raises=socket.timeout("timed out"), fail_times=1, accepted_before_failure=True
    )
    _worker(transport).run_once()  # delivered, but reported as a timeout
    _confirm(_code(transport.sent[0][0]))  # the user already used it
    assert _challenges(user_id)[0].delivery_secret_ciphertext is None

    _make_jobs_due()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", CHALLENGE_NOT_DELIVERABLE)
    assert len(transport.attempted) == 1


@requires_postgres
def test_database_forbids_a_copy_on_a_consumed_challenge() -> None:
    store_policy(email=True)
    _user()
    _request()
    with session_scope() as session:
        with pytest.raises(sa.exc.IntegrityError):
            session.execute(sa.update(PasswordResetChallenge).values(consumed_at=sa.func.now()))
            session.flush()


@requires_postgres
def test_expired_code_is_rejected_by_confirm(client: TestClient) -> None:
    store_policy(email=True)
    _user()
    _request()
    transport = _Transport()
    _worker(transport).run_once()
    with session_scope() as session:
        session.execute(
            sa.update(PasswordResetChallenge).values(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
            )
        )
        session.commit()
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": _code(transport.sent[0][0]),
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert response.json()["error"]["code"] == "invalid_or_expired_token"


# --- 11. Atomic consumption -------------------------------------------------------------


@requires_postgres
def test_two_concurrent_confirmations_consume_the_code_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = _user()
    with session_scope() as session:
        code = auth_service.request_password_reset(session, "anna@club.test")
    assert code is not None

    # Both requests pass the validity check before either consumes.
    barrier = threading.Barrier(2, timeout=20)
    original = auth_service.validate_password_policy

    def synchronized(password: str) -> None:
        barrier.wait()
        original(password)

    monkeypatch.setattr(auth_service, "validate_password_policy", synchronized)
    outcomes: list[str] = []

    def attempt(password: str) -> None:
        try:
            _confirm(code, password)
            outcomes.append("ok:" + password)
        except auth_service.InvalidOrExpiredTokenError:
            outcomes.append("invalid")

    threads = [
        threading.Thread(target=attempt, args=(p,)) for p in ("firstpassword1", "secondpassword2")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(o.split(":")[0] for o in outcomes) == ["invalid", "ok"]
    winner = next(o.split(":")[1] for o in outcomes if o.startswith("ok:"))
    with session_scope() as session:
        audit_completed = session.execute(
            sa.text(
                "SELECT count(*) FROM audit_logs "
                "WHERE action = 'password_reset_challenge.completed'"
            )
        ).scalar_one()
        assert audit_completed == 1
    with session_scope() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert verify_password(winner, user.password_hash or "")


# --- 12-13. Backend confirmation check, session revocation --------------------------------


@requires_postgres
def test_mismatched_confirmation_is_rejected_without_burning_the_code(
    client: TestClient,
) -> None:
    _user()
    with session_scope() as session:
        code = auth_service.request_password_reset(session, "anna@club.test")
    mismatch = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": code, "new_password": NEW_PASSWORD, "new_password_confirm": "different1pw"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "password_confirmation_mismatch"
    missing = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": code, "new_password": NEW_PASSWORD},
    )
    assert missing.status_code == 422
    ok = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": code, "new_password": NEW_PASSWORD, "new_password_confirm": NEW_PASSWORD},
    )
    assert ok.status_code == 200, ok.text


@requires_postgres
def test_successful_reset_revokes_existing_sessions(client: TestClient) -> None:
    user_id = _user()
    login = client.post(
        "/api/v1/auth/login", json={"identifier": "anna@club.test", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    with session_scope() as session:
        code = auth_service.request_password_reset(session, "anna@club.test")
    assert code is not None
    _confirm(code)
    assert client.get("/api/v1/auth/me").status_code == 401
    with session_scope() as session:
        statuses = (
            session.execute(
                sa.select(AuthenticatedSession.status).where(
                    AuthenticatedSession.user_id == user_id
                )
            )
            .scalars()
            .all()
        )
    assert statuses == ["revoked"]


# --- 14. Administrator-issued first-access code is unchanged ------------------------------


@requires_postgres
def test_admin_issued_code_is_returned_for_display_and_never_emailed() -> None:
    store_policy(email=True)
    user_id = _user()
    admin_id = _user("admin@club.test")
    with session_scope() as session:
        code = auth_service.request_password_reset(
            session, "anna@club.test", actor_type="user", actor_user_id=admin_id
        )
    assert code is not None
    (challenge,) = _challenges(user_id)
    assert challenge.delivery_secret_ciphertext is None
    assert _count(Notification) == 0 and _count(OutboxJob) == 0
    _confirm(code)


@requires_postgres
def test_template_is_seeded_by_migration() -> None:
    with session_scope() as session:
        body = session.execute(
            sa.text(
                "SELECT body_template FROM notification_templates "
                "WHERE code = :code AND channel = 'email' AND is_active"
            ),
            {"code": PASSWORD_RESET_TEMPLATE_CODE},
        ).scalar_one()
    assert body.count("{{code}}") == 1


# --- Rendering failures ---------------------------------------------------------------


def _set_template(**values: Any) -> None:
    with session_scope() as session:
        session.execute(
            sa.text(
                "UPDATE notification_templates SET "
                + ", ".join(f"{name} = :{name}" for name in values)
                + " WHERE code = :code"
            ),
            {**values, "code": PASSWORD_RESET_TEMPLATE_CODE},
        )
        session.commit()


@requires_postgres
def test_a_template_without_the_code_placeholder_fails_without_sending() -> None:
    store_policy(email=True)
    user_id = _user()
    _request()
    _set_template(body_template="Код: (нет)")
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", "template_unavailable")
    assert transport.attempted == []
    assert _challenges(user_id)[0].delivery_secret_ciphertext is None


@requires_postgres
def test_the_code_is_never_put_into_a_subject() -> None:
    store_policy(email=True)
    _user()
    _request()
    _set_template(subject_template="Код {{code}}")
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", "template_unavailable")
    assert transport.attempted == []


@requires_postgres
def test_an_inactive_template_issues_nothing() -> None:
    store_policy(email=True)
    user_id = _user()
    _set_template(is_active=False)
    assert _request() == "template_unavailable"
    assert _challenges(user_id) == [] and _count(OutboxJob) == 0
