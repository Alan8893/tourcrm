"""Test send (Issue #333, ADR-0048 §2.4/§2.10) over HTTP and the service:
Email to an entered address through the real SMTP transport (local fake
server), Telegram to the administrator's own linked account or an enabled
destination through the real Bot API transport (local fake server); the
message and the result are marked as tests; no Notification, Delivery or
outbox job is created; no transaction is held during the provider call;
safe provider errors; persistent rate limit (5 per 10 minutes, success and
failure counted, across threads and independent processes); audit
without secrets, addresses or message content."""

import json
import logging
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path
from typing import Any, Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.notification_settings import NotificationTestSendAttempt
from app.db.notifications import Notification, NotificationDelivery, TelegramDestination
from app.db.outbox import OutboxJob
from app.db.session import session_scope
from app.db.telegram import TelegramIdentity
from app.main import app
from app.notification_settings import test_send as test_send_service
from app.notification_settings.test_send import (
    TEST_EMAIL_SUBJECT,
    TEST_TELEGRAM_TEXT,
    send_test_message,
)
from tests.notification_settings_helpers import (
    store_email_settings,
    store_policy,
    store_telegram_settings,
)
from tests.smtp_server import FakeSmtpConfig, FakeSmtpServer
from tests.telegram_fakes import (
    BOT_TOKEN,
    FakeBotApiServer,
    FakeBotApiServerConfig,
    ServerReply,
)

from ._schema_reset import API_ROOT
from .conftest import requires_postgres

URL = "/api/v1/settings/notifications/test-send"
SMTP_PASSWORD = "Sm7p-Pa55w0rd-UNIQUE-4711"
ADMIN_TG = 9_000_000_001
GROUP_CHAT = -1009876543210


def _admin() -> uuid.UUID:
    """An administrator of the installation's single Club (created once)."""
    with session_scope() as session:
        club = session.execute(sa.select(Club)).scalar_one_or_none()
        if club is None:
            club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Adminova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person, login_identifier=f"a-{uuid.uuid4().hex[:8]}@club.test", status="active"
        )
        session.add_all([club, user])
        session.flush()
        role = session.execute(sa.select(Role).where(Role.code == "admin")).scalar_one()
        session.add(
            UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all", club_id=club.id)
        )
        session.commit()
        return user.id


@pytest.fixture
def client() -> Iterator[TestClient]:
    test_client = TestClient(app, raise_server_exceptions=True)
    test_client.cookies.set("csrf_token", "test-csrf-token")
    yield test_client
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture
def admin(client: TestClient) -> uuid.UUID:
    user_id = _admin()
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )
    return user_id


def _post(client: TestClient, body: dict):
    return client.post(URL, json=body, headers={"X-CSRF-Token": "test-csrf-token"})


EMAIL_BODY = {"channel": "email", "destination_kind": "email_address", "email": "x@club.test"}


def _counts() -> tuple[int, int, int]:
    with session_scope() as session:
        return tuple(  # type: ignore[return-value]
            session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()
            for model in (Notification, NotificationDelivery, OutboxJob)
        )


def _attempts(actor: uuid.UUID) -> list[NotificationTestSendAttempt]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(NotificationTestSendAttempt)
                .where(NotificationTestSendAttempt.actor_user_id == actor)
                .order_by(NotificationTestSendAttempt.created_at)
            ).scalars()
        )


def _audits() -> list[AuditLog]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(AuditLog)
                .where(AuditLog.action == "notification_test_send.attempted")
                .order_by(AuditLog.occurred_at)
            ).scalars()
        )


# --- Email --------------------------------------------------------------------------------


@requires_postgres
def test_email_test_is_sent_and_marked_without_business_notifications(
    client: TestClient, admin: uuid.UUID
) -> None:
    config = FakeSmtpConfig(username="mailer", password=SMTP_PASSWORD)
    with FakeSmtpServer(config) as server:
        store_email_settings(host="127.0.0.1", port=server.port, security="none",
                             password=SMTP_PASSWORD)
        response = _post(client, {**EMAIL_BODY, "email": "Tester@Club.Test"})
    assert response.status_code == 200
    assert response.json() == {
        "test": True,
        "channel": "email",
        "destination_kind": "email_address",
        "status": "delivered",
        "error_code": None,
    }
    (received,) = config.received
    assert received.rcpt_to == ["Tester@Club.Test"]
    message = message_from_bytes(received.data, policy=default_policy)
    assert message["Subject"] == TEST_EMAIL_SUBJECT
    assert "тестовое сообщение" in message.get_content().lower()
    # No business Notification / Delivery / outbox job.
    assert _counts() == (0, 0, 0)
    (attempt,) = _attempts(admin)
    assert (attempt.outcome, attempt.error_code) == ("delivered", None)
    (audit,) = _audits()
    assert (audit.outcome, audit.actor_user_id) == ("success", admin)
    assert audit.details == {"channel": "email", "destination_kind": "email_address",
                             "error_code": None}


@requires_postgres
def test_test_send_ignores_the_global_policy(client: TestClient, admin: uuid.UUID) -> None:
    store_policy(email=False, telegram=False)
    with FakeSmtpServer(FakeSmtpConfig()) as server:
        store_email_settings(host="127.0.0.1", port=server.port, security="none", username=None)
        assert _post(client, EMAIL_BODY).json()["status"] == "delivered"


@requires_postgres
def test_provider_failure_is_a_safe_code_without_credentials(
    client: TestClient, admin: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    config = FakeSmtpConfig(username="mailer", password="server-expects-other")
    with FakeSmtpServer(config) as server:
        store_email_settings(host="127.0.0.1", port=server.port, security="none",
                             password=SMTP_PASSWORD)
        response = _post(client, EMAIL_BODY)
    body = response.json()
    assert (response.status_code, body["status"], body["error_code"]) == (
        200,
        "failed",
        "smtp_authentication_failed",
    )
    (audit,) = _audits()
    assert audit.outcome == "failure"
    assert audit.details["error_code"] == "smtp_authentication_failed"
    with session_scope() as session:
        dumped = " ".join(
            str(value)
            for table in ("audit_logs", "notification_test_send_attempts")
            for record in session.execute(sa.text(f"SELECT * FROM {table}")).all()
            for value in record
        )
    for secret in (SMTP_PASSWORD, "x@club.test", "mailer"):
        assert secret not in dumped
        assert secret not in response.text
    assert SMTP_PASSWORD not in caplog.text


@requires_postgres
def test_unconfigured_channel_is_a_failed_attempt_without_provider_call(
    client: TestClient, admin: uuid.UUID
) -> None:
    body = _post(client, EMAIL_BODY).json()
    assert (body["status"], body["error_code"]) == ("failed", "channel_not_configured")
    (attempt,) = _attempts(admin)
    assert (attempt.outcome, attempt.error_code) == ("failed", "channel_not_configured")
    assert _audits()[0].outcome == "failure"


@requires_postgres
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({**EMAIL_BODY, "email": "not-an-address"}, "invalid_email"),
        ({**EMAIL_BODY, "email": None}, "invalid_email"),
        ({"channel": "email", "destination_kind": "own_telegram_account"},
         "invalid_test_destination"),
        ({"channel": "telegram", "destination_kind": "email_address", "email": "x@club.test"},
         "invalid_test_destination"),
        ({"channel": "telegram", "destination_kind": "telegram_destination"},
         "invalid_test_destination"),
        ({"channel": "telegram", "destination_kind": "own_telegram_account",
          "chat_id": 12345}, "validation_error"),
        ({"channel": "max", "destination_kind": "email_address"}, "validation_error"),
    ],
)
def test_invalid_requests_are_rejected_and_not_counted(
    client: TestClient, admin: uuid.UUID, body: dict, code: str
) -> None:
    response = _post(client, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert _attempts(admin) == []


# --- Telegram -----------------------------------------------------------------------------


def _telegram_server() -> tuple[FakeBotApiServerConfig, Any]:
    config = FakeBotApiServerConfig(
        by_method={"sendMessage": ServerReply(body=b'{"ok": true, "result": {"message_id": 5}}')}
    )
    return config, FakeBotApiServer(config)


@requires_postgres
def test_telegram_test_to_the_admins_own_linked_account(
    client: TestClient, admin: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"channel": "telegram", "destination_kind": "own_telegram_account"}
    store_telegram_settings(token=BOT_TOKEN)
    assert _post(client, body).json()["error_code"] == "telegram_account_not_linked"
    with session_scope() as session:
        session.add(TelegramIdentity(user_id=admin, telegram_user_id=ADMIN_TG, status="active"))
        session.commit()
    config, server = _telegram_server()
    with server:
        monkeypatch.setenv("TELEGRAM_API_BASE_URL", server.base_url)
        response = _post(client, body)
    assert response.json()["status"] == "delivered"
    ((path, params),) = config.received
    assert path == f"/bot{BOT_TOKEN}/sendMessage"
    assert params == {"chat_id": ADMIN_TG, "text": TEST_TELEGRAM_TEXT}
    assert TEST_TELEGRAM_TEXT.startswith("[ТЕСТ]")
    assert BOT_TOKEN not in response.text
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_telegram_test_to_an_enabled_destination_and_topic(
    client: TestClient, admin: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_telegram_settings(token=BOT_TOKEN)
    with session_scope() as session:
        enabled = TelegramDestination(name="Клуб", chat_id=GROUP_CHAT, message_thread_id=7,
                                      topic_name="Походы")
        disabled = TelegramDestination(name="Old", chat_id=GROUP_CHAT - 1, enabled=False)
        session.add_all([enabled, disabled])
        session.commit()
        enabled_id, disabled_id = enabled.id, disabled.id
    options = client.get("/api/v1/settings/notifications/telegram-destinations").json()["items"]
    assert options == [{"id": str(enabled_id), "name": "Клуб", "topic_name": "Походы"}]

    config, server = _telegram_server()
    with server:
        monkeypatch.setenv("TELEGRAM_API_BASE_URL", server.base_url)
        delivered = _post(client, {"channel": "telegram",
                                   "destination_kind": "telegram_destination",
                                   "telegram_destination_id": str(enabled_id)})
        disabled_result = _post(client, {"channel": "telegram",
                                         "destination_kind": "telegram_destination",
                                         "telegram_destination_id": str(disabled_id)})
        missing = _post(client, {"channel": "telegram",
                                 "destination_kind": "telegram_destination",
                                 "telegram_destination_id": str(uuid.uuid4())})
    assert delivered.json()["status"] == "delivered"
    ((_, params),) = config.received
    assert params == {"chat_id": GROUP_CHAT, "text": TEST_TELEGRAM_TEXT, "message_thread_id": 7}
    assert disabled_result.json()["error_code"] == "destination_disabled"
    assert missing.json()["error_code"] == "destination_not_found"


@requires_postgres
def test_telegram_provider_error_is_safe(
    client: TestClient, admin: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_telegram_settings(token=BOT_TOKEN)
    with session_scope() as session:
        session.add(TelegramIdentity(user_id=admin, telegram_user_id=ADMIN_TG, status="active"))
        session.commit()
    echo = json.dumps({"ok": False, "error_code": 403,
                       "description": f"Forbidden: bot was blocked by the user bot{BOT_TOKEN}"})
    config = FakeBotApiServerConfig(
        by_method={"sendMessage": ServerReply(status=403, body=echo.encode())}
    )
    with FakeBotApiServer(config) as server:
        monkeypatch.setenv("TELEGRAM_API_BASE_URL", server.base_url)
        response = _post(
            client, {"channel": "telegram", "destination_kind": "own_telegram_account"}
        )
    assert response.json()["error_code"] == "telegram_bot_blocked"
    assert BOT_TOKEN not in response.text and "blocked by the user" not in response.text
    assert BOT_TOKEN not in str(_audits()[0].details)


# --- Rate limit -----------------------------------------------------------------------------


@requires_postgres
def test_rate_limit_counts_success_and_failure_and_is_audited(
    client: TestClient, admin: uuid.UUID
) -> None:
    with FakeSmtpServer(FakeSmtpConfig()) as server:
        store_email_settings(host="127.0.0.1", port=server.port, security="none", username=None)
        statuses = [_post(client, EMAIL_BODY).json()["status"] for _ in range(3)]
    # The server is gone now: the next attempts fail and still count.
    statuses += [_post(client, EMAIL_BODY).json()["status"] for _ in range(2)]
    assert statuses == ["delivered"] * 3 + ["failed"] * 2
    limited = _post(client, EMAIL_BODY)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "too_many_requests"
    assert len(_attempts(admin)) == 5
    assert _audits()[-1].details["error_code"] == "rate_limited"

    # The window is rolling: attempts older than 10 minutes no longer count.
    with session_scope() as session:
        session.execute(
            sa.update(NotificationTestSendAttempt).values(
                created_at=sa.func.now() - timedelta(minutes=11)
            )
        )
        session.commit()
    assert _post(client, EMAIL_BODY).status_code == 200


@requires_postgres
def test_rate_limit_is_per_administrator(client: TestClient, admin: uuid.UUID) -> None:
    for _ in range(5):
        _post(client, EMAIL_BODY)
    assert _post(client, EMAIL_BODY).status_code == 429
    other = _admin()
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=other, session_id=uuid.uuid4()
    )
    assert _post(client, EMAIL_BODY).status_code == 200


@requires_postgres
def test_rate_limit_holds_under_concurrency() -> None:
    actor = _admin()
    results: list[str] = []
    barrier = threading.Barrier(8)

    def attempt() -> None:
        barrier.wait()
        with session_scope() as session:
            try:
                send_test_message(
                    session,
                    actor_user_id=actor,
                    request=test_send_service.TestSendRequest(
                        "email", "email_address", email="x@club.test"
                    ),
                )
                results.append("attempted")
            except test_send_service.TestSendRateLimited:
                results.append("limited")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == ["attempted"] * 5 + ["limited"] * 3
    assert len(_attempts(actor)) == 5


_PROCESS_SCRIPT = """
import sys, uuid
from app.db.session import session_scope
from app.notification_settings.test_send import (
    TestSendRateLimited, TestSendRequest, send_test_message,
)
actor = uuid.UUID(sys.argv[1])
outcomes = []
for _ in range(int(sys.argv[2])):
    with session_scope() as session:
        try:
            send_test_message(
                session, actor_user_id=actor,
                request=TestSendRequest("email", "email_address", email="x@club.test"),
            )
            outcomes.append("attempted")
        except TestSendRateLimited:
            outcomes.append("limited")
print(",".join(outcomes))
"""


@requires_postgres
def test_rate_limit_holds_across_independent_processes_and_restarts(
    database_url: str, tmp_path: Path
) -> None:
    actor = _admin()
    script = tmp_path / "attempts.py"
    script.write_text(_PROCESS_SCRIPT)

    def run(count: int) -> list[str]:
        import os

        result = subprocess.run(
            [sys.executable, str(script), str(actor), str(count)],
            cwd=API_ROOT,
            env={**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": str(API_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip().split(",")

    # Each process is a fresh interpreter: nothing is counted in memory.
    assert run(3) == ["attempted"] * 3
    assert run(3) == ["attempted", "attempted", "limited"]
    assert run(1) == ["limited"]
    assert len(_attempts(actor)) == 5


# --- Transaction boundary -------------------------------------------------------------------


@requires_postgres
def test_no_transaction_is_held_during_the_provider_call() -> None:
    actor = _admin()
    store_email_settings(username=None)
    observed: dict[str, Any] = {}

    class _ProbeTransport:
        def __init__(self, settings: Any) -> None:
            pass

        def send(self, message: Any, *, sender: str, recipient: str) -> None:
            with session_scope() as session:
                observed["idle_in_transaction"] = session.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                        "AND state IN ('idle in transaction', 'idle in transaction (aborted)')"
                    )
                ).scalar_one()
                # The actor's row lock (rate limit) is not held either.
                session.execute(
                    sa.text("SELECT 1 FROM users WHERE id = :id FOR UPDATE NOWAIT"), {"id": actor}
                )
                observed["pending_attempts"] = session.execute(
                    sa.text(
                        "SELECT count(*) FROM notification_test_send_attempts "
                        "WHERE outcome IS NULL"
                    )
                ).scalar_one()
                session.rollback()

    with session_scope() as session:
        result = send_test_message(
            session,
            actor_user_id=actor,
            request=test_send_service.TestSendRequest(
                "email", "email_address", email="x@club.test"
            ),
            smtp_transport_factory=_ProbeTransport,  # type: ignore[arg-type]
        )
    assert result.status == "delivered"
    assert observed == {"idle_in_transaction": 0, "pending_attempts": 1}
