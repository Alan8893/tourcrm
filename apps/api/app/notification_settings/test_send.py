"""Administrator test send (Issue #333, ADR-0048 §2.4/§2.10).

Synchronous, through the existing channel adapters' `send` step
(EmailChannelAdapter / TelegramChannelAdapter) — no second sending
mechanism, and no business Notification, Delivery or outbox job. The
message is a fixed text that identifies itself as a test.

Destinations:

- Email: any syntactically valid address entered by the administrator;
- Telegram: the administrator's own active linked Telegram account, or an
  existing enabled `telegram_destination` (group/topic). A client-supplied
  chat id is never accepted.

Flow (no database transaction is open during the provider call):

1. transaction 1 — lock the administrator's User row, enforce the rate
   limit (at most TEST_SEND_RATE_LIMIT_COUNT attempts per actor in any
   rolling TEST_SEND_RATE_LIMIT_WINDOW, counted in
   `notification_test_send_attempts`, so it holds across processes and
   restarts; successful and failed attempts both count), resolve the
   destination and the current channel configuration, record the attempt,
   commit. A destination/configuration failure is recorded as a failed
   attempt and audited without calling the provider.
2. no transaction — `adapter.send(content)`; provider failures are the
   adapters' stable safe codes.
3. transaction 2 — record the attempt's outcome and audit it
   (`notification_test_send.attempted`, outcome success/failure, channel,
   destination kind, error code only).

The Global Admin Policy is not consulted: a test checks the configuration,
typically before the channel is switched on.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.core.config import SmtpSettings, TelegramSettings
from app.db.identity import User
from app.db.notification_settings import NotificationTestSendAttempt
from app.db.notifications import TelegramDestination
from app.db.session import get_session_factory
from app.db.telegram import TelegramIdentity
from app.notification_settings.runtime import load_email_runtime, load_telegram_runtime
from app.notification_settings.vocabulary import (
    CONFIG_CONFIGURED,
    TEST_DESTINATION_EMAIL,
    TEST_DESTINATION_TELEGRAM_DESTINATION,
    TEST_DESTINATION_TELEGRAM_SELF,
    TEST_OUTCOME_DELIVERED,
    TEST_OUTCOME_FAILED,
    TEST_SEND_RATE_LIMIT_COUNT,
    TEST_SEND_RATE_LIMIT_WINDOW,
)
from app.notifications.configured_adapters import BotApiClientFactory, SmtpTransportFactory
from app.notifications.delivery import ADAPTER_ERROR_CODE, ChannelResult
from app.notifications.email_adapter import EmailChannelAdapter, EmailContent, parse_email_address
from app.notifications.smtp import SmtplibTransport
from app.notifications.telegram_adapter import (
    DESTINATION_DISABLED,
    DESTINATION_NOT_FOUND,
    TelegramChannelAdapter,
    TelegramContent,
)
from app.notifications.vocabulary import CHANNEL_EMAIL, CHANNEL_TELEGRAM
from app.telegram.bot_api import build_bot_api_client
from app.telegram.vocabulary import IDENTITY_ACTIVE

TEST_EMAIL_SUBJECT = "TourCRM: тестовое сообщение"
TEST_EMAIL_BODY = (
    "Это тестовое сообщение TourCRM.\n\n"
    "Оно отправлено администратором из раздела «Настройки → Уведомления», чтобы "
    "проверить настройки Email. Сообщение не связано ни с каким событием и не "
    "требует действий."
)
TEST_TELEGRAM_TEXT = (
    "[ТЕСТ] TourCRM: тестовое сообщение для проверки настроек Telegram. "
    "Оно не связано ни с каким событием и не требует действий."
)

DESTINATION_INVALID = "destination_invalid"
TELEGRAM_ACCOUNT_NOT_LINKED = "telegram_account_not_linked"
RATE_LIMITED = "rate_limited"


def configuration_error_code(state: str) -> str:
    """`channel_not_configured`, `channel_incomplete`, `channel_invalid`,
    `channel_secret_unavailable`."""
    return f"channel_{state}"


class TestSendRateLimited(Exception):
    """The administrator exceeded the test-send rate limit."""


@dataclass(frozen=True)
class TestSendRequest:
    channel: str
    destination_kind: str
    email: Optional[str] = None
    telegram_destination_id: Optional[uuid.UUID] = None


@dataclass(frozen=True)
class TestSendResult:
    channel: str
    destination_kind: str
    status: str
    error_code: Optional[str] = None

    @property
    def is_test(self) -> bool:
        return True


@dataclass(frozen=True)
class _Prepared:
    email: Optional[tuple[SmtpSettings, EmailContent]] = None
    telegram: Optional[tuple[TelegramSettings, TelegramContent]] = None


def _audit(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    request: TestSendRequest,
    error_code: Optional[str],
    request_id: Optional[str],
) -> None:
    record_audit_event(
        session,
        action="notification_test_send.attempted",
        actor_type="user",
        actor_user_id=actor_user_id,
        outcome="success" if error_code is None else "failure",
        request_id=request_id,
        details={
            "channel": request.channel,
            "destination_kind": request.destination_kind,
            "error_code": error_code,
        },
    )


def _prepare(
    session: Session, *, actor_user_id: uuid.UUID, request: TestSendRequest
) -> _Prepared | str:
    """The configuration and content to send, or a safe error code."""
    if request.channel == CHANNEL_EMAIL and request.destination_kind == TEST_DESTINATION_EMAIL:
        address = parse_email_address(request.email)
        if address is None:
            return DESTINATION_INVALID
        email_runtime = load_email_runtime(session)
        if email_runtime.state != CONFIG_CONFIGURED or email_runtime.settings is None:
            return configuration_error_code(email_runtime.state)
        return _Prepared(
            email=(
                email_runtime.settings,
                EmailContent(
                    recipient=address.addr_spec, subject=TEST_EMAIL_SUBJECT, body=TEST_EMAIL_BODY
                ),
            )
        )
    if request.channel != CHANNEL_TELEGRAM:
        return DESTINATION_INVALID
    if request.destination_kind == TEST_DESTINATION_TELEGRAM_SELF:
        chat_id = session.execute(
            sa.select(TelegramIdentity.telegram_user_id).where(
                TelegramIdentity.user_id == actor_user_id,
                TelegramIdentity.status == IDENTITY_ACTIVE,
            )
        ).scalar_one_or_none()
        if chat_id is None:
            return TELEGRAM_ACCOUNT_NOT_LINKED
        thread_id: Optional[int] = None
    elif request.destination_kind == TEST_DESTINATION_TELEGRAM_DESTINATION:
        if request.telegram_destination_id is None:
            return DESTINATION_INVALID
        destination = session.get(TelegramDestination, request.telegram_destination_id)
        if destination is None:
            return DESTINATION_NOT_FOUND
        if not destination.enabled:
            return DESTINATION_DISABLED
        chat_id, thread_id = destination.chat_id, destination.message_thread_id
    else:
        return DESTINATION_INVALID
    telegram_runtime = load_telegram_runtime(session)
    if telegram_runtime.state != CONFIG_CONFIGURED or telegram_runtime.settings is None:
        return configuration_error_code(telegram_runtime.state)
    return _Prepared(
        telegram=(
            telegram_runtime.settings,
            TelegramContent(chat_id=chat_id, message_thread_id=thread_id, text=TEST_TELEGRAM_TEXT),
        )
    )


def _send(
    prepared: _Prepared,
    *,
    smtp_transport_factory: SmtpTransportFactory,
    telegram_client_factory: BotApiClientFactory,
) -> ChannelResult:
    """Provider I/O only — no session. Content stays in memory."""
    try:
        if prepared.email is not None:
            settings, content = prepared.email
            # `send` never opens a session; the factory serves `deliver` only.
            email_adapter = EmailChannelAdapter(
                settings=settings,
                transport=smtp_transport_factory(settings),
                session_factory=get_session_factory(),
            )
            return email_adapter.send(content)
        assert prepared.telegram is not None
        telegram_settings, telegram_content = prepared.telegram
        telegram_adapter = TelegramChannelAdapter(
            client=telegram_client_factory(telegram_settings),
            session_factory=get_session_factory(),
        )
        return telegram_adapter.send(telegram_content)
    except Exception as exc:  # noqa: BLE001 - recorded as a failed attempt
        # Only the type: exception text may carry provider details.
        return ChannelResult.retryable(ADAPTER_ERROR_CODE, type(exc).__name__)


def send_test_message(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    request: TestSendRequest,
    request_id: Optional[str] = None,
    smtp_transport_factory: SmtpTransportFactory = SmtplibTransport,
    telegram_client_factory: BotApiClientFactory = build_bot_api_client,
) -> TestSendResult:
    """Run one test send (module docstring). Commits its own transactions;
    the caller must not hold uncommitted work in `session`. Raises
    TestSendRateLimited after auditing the refused attempt."""
    session.execute(sa.select(User.id).where(User.id == actor_user_id).with_for_update())
    recent = session.execute(
        sa.select(sa.func.count())
        .select_from(NotificationTestSendAttempt)
        .where(
            NotificationTestSendAttempt.actor_user_id == actor_user_id,
            NotificationTestSendAttempt.created_at
            > sa.func.now() - TEST_SEND_RATE_LIMIT_WINDOW,
        )
    ).scalar_one()
    if recent >= TEST_SEND_RATE_LIMIT_COUNT:
        _audit(
            session,
            actor_user_id=actor_user_id,
            request=request,
            error_code=RATE_LIMITED,
            request_id=request_id,
        )
        session.commit()
        raise TestSendRateLimited()

    prepared = _prepare(session, actor_user_id=actor_user_id, request=request)
    attempt = NotificationTestSendAttempt(
        actor_user_id=actor_user_id,
        channel=request.channel,
        destination_kind=request.destination_kind,
    )
    if isinstance(prepared, str):
        attempt.outcome = TEST_OUTCOME_FAILED
        attempt.error_code = prepared
        attempt.completed_at = session.execute(sa.select(sa.func.now())).scalar_one()
        session.add(attempt)
        session.flush()
        _audit(
            session,
            actor_user_id=actor_user_id,
            request=request,
            error_code=prepared,
            request_id=request_id,
        )
        session.commit()
        return TestSendResult(
            request.channel, request.destination_kind, TEST_OUTCOME_FAILED, prepared
        )
    session.add(attempt)
    session.flush()
    attempt_id = attempt.id
    session.commit()

    result = _send(
        prepared,
        smtp_transport_factory=smtp_transport_factory,
        telegram_client_factory=telegram_client_factory,
    )
    error_code = None if result.kind == "delivered" else (result.error_code or ADAPTER_ERROR_CODE)
    session.execute(
        sa.update(NotificationTestSendAttempt)
        .where(NotificationTestSendAttempt.id == attempt_id)
        .values(
            outcome=TEST_OUTCOME_DELIVERED if error_code is None else TEST_OUTCOME_FAILED,
            error_code=error_code,
            completed_at=sa.func.now(),
        )
    )
    _audit(
        session,
        actor_user_id=actor_user_id,
        request=request,
        error_code=error_code,
        request_id=request_id,
    )
    session.commit()
    return TestSendResult(
        request.channel,
        request.destination_kind,
        TEST_OUTCOME_DELIVERED if error_code is None else TEST_OUTCOME_FAILED,
        error_code,
    )


__all__ = [
    "TEST_EMAIL_SUBJECT",
    "TEST_EMAIL_BODY",
    "TEST_TELEGRAM_TEXT",
    "DESTINATION_INVALID",
    "TELEGRAM_ACCOUNT_NOT_LINKED",
    "RATE_LIMITED",
    "configuration_error_code",
    "TestSendRateLimited",
    "TestSendRequest",
    "TestSendResult",
    "send_test_message",
]
