"""The Telegram channel adapter (Issue #329, ADR-0047 §5, ADR-0045 §2.7).

Implements the existing `ChannelAdapter` boundary
(app.notifications.delivery) for the `telegram` channel; the worker's
`NotificationDeliveryHandler` calls it with no database transaction held
and records the result together with the outbox job. No retry loop here —
retry count, backoff and the terminal transition belong to the worker.

Per call:

1. one short read-only session, closed before any Bot API I/O:
   - destination `user`: the recipient User's **active** linked Telegram
     identity (app.db.telegram.TelegramIdentity); its private chat id is
     the Telegram user id. No active identity -> permanent
     `destination_unlinked`;
   - destination `telegram_destination`: the `telegram_destinations` row —
     `chat_id` plus optional `message_thread_id` (topic routing). Missing
     -> permanent `destination_not_found`; `enabled = false` -> permanent
     `destination_disabled`;
   - content: the Notification's template as stored, which must be a
     `telegram` template; its `body_template` is sent as plain text (no
     parse_mode, no rendering). Missing/other channel -> permanent
     `template_unavailable`; longer than Telegram's 4096-character limit
     -> permanent `message_too_long`;
2. `sendMessage` through app.telegram.bot_api; its failures are already
   stable safe codes (see that module), mapped to retryable/permanent.
   The rate-limit `retry_after` is reported only as the safe message
   `retry_after=<n>s`; the worker's own backoff policy is unchanged.

At-least-once (ADR-0047 §5): a timeout after Telegram accepted the
message is still retryable and may produce a duplicate. The returned
`provider_message_id` is the Telegram message id (never a chat id).
"""

from dataclasses import dataclass
import re
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.db.notifications import Notification, NotificationTemplate, TelegramDestination
from app.db.telegram import TelegramIdentity
from app.notifications.delivery import ChannelResult, DeliveryRequest
from app.notifications.vocabulary import (
    CHANNEL_TELEGRAM,
    DESTINATION_TELEGRAM_DESTINATION,
    DESTINATION_USER,
)
from app.telegram.bot_api import BotApiClient, TelegramApiError
from app.telegram.vocabulary import IDENTITY_ACTIVE

DESTINATION_UNSUPPORTED = "destination_unsupported"
DESTINATION_NOT_FOUND = "destination_not_found"
DESTINATION_UNLINKED = "destination_unlinked"
DESTINATION_DISABLED = "destination_disabled"
TEMPLATE_UNAVAILABLE = "template_unavailable"
MESSAGE_TOO_LONG = "message_too_long"

# Bot API sendMessage: 1-4096 characters of text after entity parsing.
TELEGRAM_MESSAGE_MAX_LENGTH = 4096


@dataclass(frozen=True)
class TelegramContent:
    chat_id: int
    message_thread_id: Optional[int]
    text: str


def _resolve_chat(
    session: Session, request: DeliveryRequest
) -> tuple[int, Optional[int]] | str:
    if request.destination_type == DESTINATION_USER:
        telegram_user_id = session.execute(
            sa.select(TelegramIdentity.telegram_user_id).where(
                TelegramIdentity.user_id == request.destination_id,
                TelegramIdentity.status == IDENTITY_ACTIVE,
            )
        ).scalar_one_or_none()
        if telegram_user_id is None:
            return DESTINATION_UNLINKED
        return telegram_user_id, None
    destination = session.get(TelegramDestination, request.destination_id)
    if destination is None:
        return DESTINATION_NOT_FOUND
    if not destination.enabled:
        return DESTINATION_DISABLED
    return destination.chat_id, destination.message_thread_id


def _load_content(session: Session, request: DeliveryRequest) -> TelegramContent | str:
    """Returns the content to send, or a permanent error code."""
    chat = _resolve_chat(session, request)
    if isinstance(chat, str):
        return chat
    notification = session.get(Notification, request.notification_id)
    template = (
        session.get(NotificationTemplate, notification.template_id)
        if notification is not None and notification.template_id is not None
        else None
    )
    if template is None or template.channel != CHANNEL_TELEGRAM:
        return TEMPLATE_UNAVAILABLE
    context = notification.render_context if notification is not None else {}
    text = re.sub(
        r"{{([a-z_]+)}}",
        lambda match: str(context.get(match.group(1), "")),
        template.body_template,
    )
    if "{{" in text or "}}" in text:
        return TEMPLATE_UNAVAILABLE
    if len(text) > TELEGRAM_MESSAGE_MAX_LENGTH:
        return MESSAGE_TOO_LONG
    chat_id, message_thread_id = chat
    return TelegramContent(
        chat_id=chat_id, message_thread_id=message_thread_id, text=text
    )


class TelegramChannelAdapter:
    """ChannelAdapter for `telegram`. `session_factory` is used only for
    the short read before sending; `client` performs the Bot API I/O."""

    def __init__(self, *, client: BotApiClient, session_factory: sessionmaker) -> None:
        self._client = client
        self._session_factory = session_factory

    def __repr__(self) -> str:
        return "TelegramChannelAdapter()"

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        if request.channel != CHANNEL_TELEGRAM or request.destination_type not in (
            DESTINATION_USER,
            DESTINATION_TELEGRAM_DESTINATION,
        ):
            return ChannelResult.permanent(DESTINATION_UNSUPPORTED)

        with self._session_factory() as session:
            loaded = _load_content(session, request)
            session.rollback()
        if isinstance(loaded, str):
            return ChannelResult.permanent(loaded)
        return self.send(loaded)

    def send(self, content: TelegramContent) -> ChannelResult:
        """Send one plain-text message. No database access: callers (deliver,
        the administrator test send) hold no transaction during this call."""
        try:
            message_id = self._client.send_message(
                chat_id=content.chat_id,
                text=content.text,
                message_thread_id=content.message_thread_id,
            )
        except TelegramApiError as exc:
            if exc.retryable:
                return ChannelResult.retryable(exc.code, exc.safe_message)
            return ChannelResult.permanent(exc.code, exc.safe_message)
        return ChannelResult.delivered(str(message_id))


__all__ = [
    "DESTINATION_UNSUPPORTED",
    "DESTINATION_NOT_FOUND",
    "DESTINATION_UNLINKED",
    "DESTINATION_DISABLED",
    "TEMPLATE_UNAVAILABLE",
    "MESSAGE_TOO_LONG",
    "TELEGRAM_MESSAGE_MAX_LENGTH",
    "TelegramContent",
    "TelegramChannelAdapter",
]
