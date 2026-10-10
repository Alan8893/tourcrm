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
   - content: the Notification's template, which must be a `telegram`
     template, rendered by app.notifications.rendering from the
     Notification's `render_context` snapshot and the template's declared
     variables (app.notifications.catalog) into escaped Telegram HTML
     (`parse_mode=HTML`, ADR-0049 §2.5). Links use the deployment's
     `APP_PUBLIC_BASE_URL`; without it they are omitted. Missing/other
     channel -> permanent `template_unavailable`; a template that cannot
     be rendered (missing required variable, undeclared or malformed
     placeholder) -> permanent `template_render_failed`; visible text
     longer than Telegram's 4096-character limit after rendering ->
     permanent `message_too_long`. A raw `{{placeholder}}` is never sent;
2. `sendMessage` through app.telegram.bot_api; its failures are already
   stable safe codes (see that module), mapped to retryable/permanent.
   The rate-limit `retry_after` is reported only as the safe message
   `retry_after=<n>s`; the worker's own backoff policy is unchanged.

At-least-once (ADR-0047 §5): a timeout after Telegram accepted the
message is still retryable and may produce a duplicate. The returned
`provider_message_id` is the Telegram message id (never a chat id).
"""

from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.db.notifications import Notification, NotificationTemplate, TelegramDestination
from app.db.telegram import TelegramIdentity
from app.notifications.catalog import template_variables
from app.notifications.delivery import ChannelResult, DeliveryRequest
from app.notifications.rendering import (
    MESSAGE_TOO_LONG,
    TELEGRAM_MESSAGE_MAX_LENGTH,
    TELEGRAM_PARSE_MODE_HTML,
    TEMPLATE_RENDER_FAILED,
    TemplateRenderError,
    render_telegram_html,
)
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


@dataclass(frozen=True)
class TelegramContent:
    chat_id: int
    message_thread_id: Optional[int]
    text: str
    # None = plain text (the administrator test send); notifications are HTML.
    parse_mode: Optional[str] = None


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


def _load_content(
    session: Session, request: DeliveryRequest, public_base_url: Optional[str]
) -> TelegramContent | str | ChannelResult:
    """Returns the content to send, or a permanent error code / result."""
    chat = _resolve_chat(session, request)
    if isinstance(chat, str):
        return chat
    notification = session.get(Notification, request.notification_id)
    template = (
        session.get(NotificationTemplate, notification.template_id)
        if notification is not None and notification.template_id is not None
        else None
    )
    if notification is None or template is None or template.channel != CHANNEL_TELEGRAM:
        return TEMPLATE_UNAVAILABLE
    try:
        text = render_telegram_html(
            template.body_template,
            variables=template_variables(template.code),
            context=notification.render_context,
            public_base_url=public_base_url,
        )
    except TemplateRenderError as exc:
        return ChannelResult.permanent(exc.code, exc.safe_message)
    chat_id, message_thread_id = chat
    return TelegramContent(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text=text,
        parse_mode=TELEGRAM_PARSE_MODE_HTML,
    )


class TelegramChannelAdapter:
    """ChannelAdapter for `telegram`. `session_factory` is used only for
    the short read before sending; `client` performs the Bot API I/O;
    `public_base_url` (validated APP_PUBLIC_BASE_URL, or None) builds the
    message links."""

    def __init__(
        self,
        *,
        client: BotApiClient,
        session_factory: sessionmaker,
        public_base_url: Optional[str] = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._public_base_url = public_base_url

    def __repr__(self) -> str:
        return "TelegramChannelAdapter()"

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        if request.channel != CHANNEL_TELEGRAM or request.destination_type not in (
            DESTINATION_USER,
            DESTINATION_TELEGRAM_DESTINATION,
        ):
            return ChannelResult.permanent(DESTINATION_UNSUPPORTED)

        with self._session_factory() as session:
            loaded = _load_content(session, request, self._public_base_url)
            session.rollback()
        if isinstance(loaded, str):
            return ChannelResult.permanent(loaded)
        if isinstance(loaded, ChannelResult):
            return loaded
        return self.send(loaded)

    def send(self, content: TelegramContent) -> ChannelResult:
        """Send one message. No database access: callers (deliver, the
        administrator test send) hold no transaction during this call."""
        try:
            message_id = self._client.send_message(
                chat_id=content.chat_id,
                text=content.text,
                message_thread_id=content.message_thread_id,
                parse_mode=content.parse_mode,
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
    "TEMPLATE_RENDER_FAILED",
    "MESSAGE_TOO_LONG",
    "TELEGRAM_MESSAGE_MAX_LENGTH",
    "TelegramContent",
    "TelegramChannelAdapter",
]
