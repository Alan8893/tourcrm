"""Integration tests for the Telegram channel adapter on the real worker
path (Issue #329, ADR-0047 §5): Worker -> NotificationDeliveryHandler ->
TelegramChannelAdapter -> Bot API transport -> Delivery finalization,
against real PostgreSQL. The Bot API side is the scripted in-memory
transport or the local fake HTTP server — never real Telegram.
"""

import json
import logging
import uuid
from typing import Any, Optional

import pytest
import sqlalchemy as sa

from app.cli.run_outbox_worker import build_worker
from app.core.config import TelegramSettings
from app.db.identity import Club, Person, User
from app.db.notifications import (
    Notification,
    NotificationDelivery,
    NotificationTemplate,
    TelegramDestination,
)
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.db.telegram import TelegramIdentity
from app.notifications.delivery import NotificationDeliveryHandler
from app.notifications.repository import add_delivery, create_notification
from app.notifications.telegram_adapter import (
    DESTINATION_DISABLED,
    DESTINATION_NOT_FOUND,
    DESTINATION_UNLINKED,
    MESSAGE_TOO_LONG,
    TEMPLATE_UNAVAILABLE,
    TelegramChannelAdapter,
)
from app.notifications.vocabulary import (
    NOTIFICATION_DELIVERY_JOB_TYPE,
    notification_delivery_deduplication_key,
)
from app.outbox.service import enqueue_outbox_job
from app.outbox.worker import OutboxWorker, WorkerConfig
from app.telegram import bot_api
from app.telegram.bot_api import BotApiResponse, TelegramTransportError
from tests.telegram_fakes import (
    BOT_TOKEN,
    FakeBotApiServer,
    FakeBotApiServerConfig,
    ScriptedTransport,
    ServerReply,
    api_error,
    client_for,
    ok,
)

from .conftest import requires_postgres

ANNA_TG = 7_000_000_001
GROUP_CHAT = -1001234567890
BODY = "Сбор в 8:00 у клуба. <b>не HTML</b> *не Markdown*"


def _worker(transport: ScriptedTransport, *, max_attempts: int = 5) -> OutboxWorker:
    factory = get_session_factory()
    adapter = TelegramChannelAdapter(client=client_for(transport), session_factory=factory)
    return OutboxWorker(
        session_factory=factory,
        handlers={
            NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler({"telegram": adapter})
        },
        config=WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}", max_attempts=max_attempts),
    )


def _sent_ok(message_id: int = 555) -> ScriptedTransport:
    return ScriptedTransport(defaults={"sendMessage": ok({"message_id": message_id})})


def _telegram_delivery(
    *,
    destination: str = "user",
    linked: bool = True,
    identity_status: str = "active",
    thread_id: Optional[int] = None,
    destination_enabled: bool = True,
    destination_exists: bool = True,
    template_channel: Optional[str] = "telegram",
    body: str = BODY,
) -> dict[str, Any]:
    """Notification + telegram Delivery + outbox job, committed together
    the way the Notification Engine leaves them."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person, login_identifier=f"{uuid.uuid4().hex[:8]}@c.test", status="active"
        )
        session.add_all([club, user])
        session.flush()
        if linked:
            session.add(
                TelegramIdentity(
                    user_id=user.id,
                    telegram_user_id=ANNA_TG,
                    status=identity_status,
                    ended_at=None if identity_status == "active" else sa.func.now(),
                )
            )
        destination_id = user.id
        if destination == "telegram_destination":
            if destination_exists:
                row = TelegramDestination(
                    club_id=club.id,
                    name="Club group",
                    chat_id=GROUP_CHAT,
                    message_thread_id=thread_id,
                    topic_name="Походы" if thread_id else None,
                    enabled=destination_enabled,
                )
                session.add(row)
                session.flush()
                destination_id = row.id
            else:
                destination_id = uuid.uuid4()
        template_id = None
        if template_channel is not None:
            template = NotificationTemplate(
                code=f"tpl-{uuid.uuid4().hex[:8]}",
                channel=template_channel,
                locale="ru",
                body_template=body,
            )
            session.add(template)
            session.flush()
            template_id = template.id
        notification, _ = create_notification(
            session,
            idempotency_key=f"test:{uuid.uuid4()}",
            event_type="test.event",
            subject_type="test_subject",
            recipient_user_id=user.id,
            club_id=club.id,
            template_id=template_id,
        )
        delivery, _ = add_delivery(
            session,
            notification=notification,
            channel="telegram",
            destination_type=destination,
            destination_id=destination_id,
        )
        job, _ = enqueue_outbox_job(
            session,
            job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
            payload={"delivery_id": str(delivery.id)},
            deduplication_key=notification_delivery_deduplication_key(delivery.id),
        )
        session.commit()
        return {"notification": notification.id, "delivery": delivery.id, "job": job.id}


def _delivery(delivery_id: uuid.UUID) -> NotificationDelivery:
    with session_scope() as session:
        row = session.get(NotificationDelivery, delivery_id)
        assert row is not None
        return row


def _job(job_id: uuid.UUID) -> OutboxJob:
    with session_scope() as session:
        row = session.get(OutboxJob, job_id)
        assert row is not None
        return row


# --- Routing --------------------------------------------------------------------------------


@requires_postgres
def test_private_user_receives_the_template_body_as_plain_text() -> None:
    ids = _telegram_delivery()
    transport = _sent_ok(555)
    (report,) = _worker(transport).run_once()

    assert report.result == "completed"
    (call,) = transport.calls_to("sendMessage")
    assert call.params == {"chat_id": ANNA_TG, "text": BODY}
    delivery = _delivery(ids["delivery"])
    assert (delivery.status, delivery.provider_message_id) == ("delivered", "555")
    assert _job(ids["job"]).status == "completed"


@requires_postgres
def test_group_destination_is_sent_to_its_chat() -> None:
    _telegram_delivery(destination="telegram_destination")
    transport = _sent_ok()
    (report,) = _worker(transport).run_once()
    assert report.result == "completed"
    (call,) = transport.calls_to("sendMessage")
    assert call.params == {"chat_id": GROUP_CHAT, "text": BODY}


@requires_postgres
def test_topic_destination_routes_by_message_thread_id() -> None:
    _telegram_delivery(destination="telegram_destination", thread_id=42)
    transport = _sent_ok()
    _worker(transport).run_once()
    (call,) = transport.calls_to("sendMessage")
    assert call.params == {"chat_id": GROUP_CHAT, "text": BODY, "message_thread_id": 42}


# --- Destination / content failures (permanent, no Bot API call) -----------------------------


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"linked": False}, DESTINATION_UNLINKED),
        ({"identity_status": "unlinked"}, DESTINATION_UNLINKED),
        ({"identity_status": "replaced"}, DESTINATION_UNLINKED),
        ({"destination": "telegram_destination", "destination_enabled": False},
         DESTINATION_DISABLED),
        ({"destination": "telegram_destination", "destination_exists": False},
         DESTINATION_NOT_FOUND),
        ({"template_channel": "email"}, TEMPLATE_UNAVAILABLE),
        ({"template_channel": None}, TEMPLATE_UNAVAILABLE),
        ({"body": "я" * 4097}, MESSAGE_TOO_LONG),
    ],
)
@requires_postgres
def test_unresolvable_destination_or_content_is_terminal(kwargs: dict, code: str) -> None:
    ids = _telegram_delivery(**kwargs)
    transport = _sent_ok()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", code)
    assert transport.calls_to("sendMessage") == []
    delivery = _delivery(ids["delivery"])
    assert (delivery.status, delivery.next_retry_at, delivery.last_error_code) == (
        "failed",
        None,
        code,
    )


@requires_postgres
def test_message_of_exactly_the_limit_is_sent() -> None:
    _telegram_delivery(body="я" * 4096)
    (report,) = _worker(_sent_ok()).run_once()
    assert report.result == "completed"


# --- Provider failures --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "code", "message"),
    [
        (TelegramTransportError("timeout"), bot_api.TELEGRAM_TIMEOUT, None),
        (TelegramTransportError("network"), bot_api.TELEGRAM_NETWORK_ERROR, None),
        (api_error(429, "Too Many Requests: retry after 30", {"retry_after": 30}),
         bot_api.TELEGRAM_RATE_LIMITED, "retry_after=30s"),
        (api_error(500, "Internal Server Error"), bot_api.TELEGRAM_SERVER_ERROR, None),
        (BotApiResponse(200, None), bot_api.TELEGRAM_MALFORMED_RESPONSE, None),
        (ok({"no": "message id"}), bot_api.TELEGRAM_MALFORMED_RESPONSE, None),
    ],
)
@requires_postgres
def test_transient_failures_are_retried_by_the_worker(
    outcome: Any, code: str, message: Optional[str]
) -> None:
    ids = _telegram_delivery()
    transport = ScriptedTransport().script("sendMessage", outcome)
    (report,) = _worker(transport, max_attempts=3).run_once()
    assert (report.result, report.error_code) == ("pending", code)
    delivery = _delivery(ids["delivery"])
    assert (delivery.status, delivery.last_error_code, delivery.last_error_message) == (
        "failed",
        code,
        message,
    )
    assert delivery.next_retry_at is not None
    job = _job(ids["job"])
    assert (job.status, job.attempts) == ("pending", 1)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (api_error(403, "Forbidden: bot was blocked by the user"), bot_api.TELEGRAM_BOT_BLOCKED),
        (api_error(403, "Forbidden: bot was kicked from the supergroup chat"),
         bot_api.TELEGRAM_CHAT_FORBIDDEN),
        (api_error(400, "Bad Request: chat not found"), bot_api.TELEGRAM_CHAT_NOT_FOUND),
        (api_error(400, "Bad Request: message thread not found"),
         bot_api.TELEGRAM_TOPIC_UNAVAILABLE),
        (api_error(401, "Unauthorized"), bot_api.TELEGRAM_CONFIGURATION_INVALID),
        (api_error(404, "Not Found"), bot_api.TELEGRAM_CONFIGURATION_INVALID),
    ],
)
@requires_postgres
def test_permanent_provider_failures_are_terminal(response: BotApiResponse, code: str) -> None:
    ids = _telegram_delivery(destination="telegram_destination", thread_id=3)
    transport = ScriptedTransport().script("sendMessage", response)
    (report,) = _worker(transport, max_attempts=5).run_once()
    assert (report.result, report.error_code) == ("dead", code)
    delivery = _delivery(ids["delivery"])
    assert (delivery.status, delivery.next_retry_at, delivery.last_error_code) == (
        "failed",
        None,
        code,
    )
    assert delivery.last_error_message is None


@requires_postgres
def test_retry_then_success_delivers() -> None:
    ids = _telegram_delivery()
    transport = ScriptedTransport().script(
        "sendMessage", TelegramTransportError("timeout"), ok({"message_id": 9})
    )
    worker = _worker(transport)
    (first,) = worker.run_once()
    assert first.result == "pending"
    with session_scope() as session:
        session.execute(
            sa.update(OutboxJob)
            .where(OutboxJob.id == ids["job"])
            .values(next_attempt_at=sa.func.now())
        )
        session.commit()
    (second,) = worker.run_once()
    assert second.result == "completed"
    assert _delivery(ids["delivery"]).provider_message_id == "9"


@requires_postgres
def test_no_database_transaction_is_held_during_the_bot_api_call() -> None:
    ids = _telegram_delivery()
    observed: dict[str, Any] = {}

    def probe(params: Any) -> BotApiResponse:
        with session_scope() as session:
            observed["open_transactions"] = session.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                    "AND state IN ('idle in transaction', 'idle in transaction (aborted)')"
                )
            ).scalar_one()
            for table, row_id in (
                ("outbox_jobs", ids["job"]),
                ("notification_deliveries", ids["delivery"]),
            ):
                session.execute(
                    sa.text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": row_id},
                )
            session.rollback()
        return ok({"message_id": 1})

    (report,) = _worker(ScriptedTransport().script("sendMessage", probe)).run_once()
    assert report.result == "completed"
    assert observed["open_transactions"] == 0


# --- Production wiring and secrets ----------------------------------------------------------


@requires_postgres
def test_production_worker_sends_through_the_real_http_transport() -> None:
    ids = _telegram_delivery(destination="telegram_destination", thread_id=11)
    config = FakeBotApiServerConfig(
        by_method={"sendMessage": ServerReply(body=b'{"ok": true, "result": {"message_id": 77}}')}
    )
    with FakeBotApiServer(config) as server:
        settings = TelegramSettings(bot_token=BOT_TOKEN, api_base_url=server.base_url)
        worker = build_worker(
            WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"), telegram_settings=settings
        )
        (report,) = worker.run_once()
    assert report.result == "completed"
    ((path, params),) = config.received
    assert path == f"/bot{BOT_TOKEN}/sendMessage"
    assert params == {"chat_id": GROUP_CHAT, "text": BODY, "message_thread_id": 11}
    assert _delivery(ids["delivery"]).provider_message_id == "77"


@requires_postgres
def test_worker_without_telegram_configuration_keeps_the_unavailable_behaviour() -> None:
    _telegram_delivery()
    worker = build_worker(WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"))
    (report,) = worker.run_once()
    assert (report.result, report.error_code) == ("pending", "channel_adapter_unavailable")


@requires_postgres
def test_token_and_message_body_never_reach_state_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    ids = _telegram_delivery()
    # A provider answer that echoes the message and the token back.
    echo = json.dumps(
        {
            "ok": False,
            "error_code": 400,
            "description": f"Bad Request: can't parse {BODY} for bot{BOT_TOKEN}",
        }
    ).encode()
    config = FakeBotApiServerConfig(by_method={"sendMessage": ServerReply(status=400, body=echo)})
    with FakeBotApiServer(config) as server:
        settings = TelegramSettings(bot_token=BOT_TOKEN, api_base_url=server.base_url)
        worker = build_worker(
            WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"), telegram_settings=settings
        )
        (report,) = worker.run_once()
    assert (report.result, report.error_code) == ("dead", bot_api.TELEGRAM_REQUEST_REJECTED)

    with session_scope() as session:
        persisted = " ".join(
            str(value)
            for table in ("notification_deliveries", "outbox_jobs")
            for row in session.execute(sa.text(f"SELECT * FROM {table}")).all()
            for value in row
        )
        notification = session.get(Notification, ids["notification"])
        assert notification is not None
    for secret in (BOT_TOKEN, BOT_TOKEN.split(":")[1], "can't parse", BODY):
        assert secret not in persisted
        assert secret not in caplog.text
