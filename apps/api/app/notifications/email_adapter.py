"""The Email channel adapter (Issue #327, ADR-0045 §2.6).

Implements the existing `ChannelAdapter` boundary
(app.notifications.delivery) for the `email` channel; the worker's
`NotificationDeliveryHandler` calls it with no database transaction held
and records the result together with the outbox job. The adapter never
creates a Notification, Delivery or outbox job and has no retry loop —
retry count, backoff and the terminal transition belong to the worker.

Per call:

1. one short read-only session, closed before any SMTP I/O:
   - destination (PO decision G1): the recipient User's
     `login_identifier`, and only when `email_verified_at IS NOT NULL`;
     otherwise a permanent `destination_unverified`. `Person.email` is
     never used and there is no fallback between the two;
   - content (PO decision G2): the Notification's template as stored —
     `subject_template` -> Subject (omitted when the template has none),
     `body_template` -> the text/plain body. No rendering, no HTML. A
     missing template or one of another channel is a permanent
     `template_unavailable`;
2. build an RFC 5322 message (From = configured sender, Date, Message-ID);
3. send through the injected `SmtpTransport`; provider exceptions are
   classified by app.notifications.smtp and reported only as a stable code
   plus the numeric SMTP reply code.

At-least-once (ADR-0046 §5.5): a timeout after the message data was sent
may mean the server accepted it; it is still reported as retryable, so the
same Delivery can be sent again. The returned `provider_message_id` is the
message's own Message-ID.
"""

import smtplib
from dataclasses import dataclass
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import SmtpSettings
from app.db.identity import User
from app.db.notifications import Notification, NotificationTemplate
from app.notifications.delivery import ChannelResult, DeliveryRequest
from app.notifications.smtp import SmtpTransport, classify_smtp_error
from app.notifications.vocabulary import CHANNEL_EMAIL, DESTINATION_USER

DESTINATION_UNSUPPORTED = "destination_unsupported"
DESTINATION_NOT_FOUND = "destination_not_found"
DESTINATION_UNVERIFIED = "destination_unverified"
DESTINATION_INVALID = "destination_invalid"
TEMPLATE_UNAVAILABLE = "template_unavailable"


@dataclass(frozen=True)
class EmailContent:
    recipient: str
    subject: Optional[str]
    body: str


def parse_email_address(value: Optional[str]) -> Optional[Address]:
    """A single plain addr-spec, or None when `value` is not one (no
    display name, no header-injection characters)."""
    if value is None:
        return None
    candidate = value.strip()
    if not candidate or any(ch in candidate for ch in "\r\n<>,;\"") or " " in candidate:
        return None
    try:
        address = Address(addr_spec=candidate)
    except (ValueError, IndexError, HeaderParseError):
        return None
    if not address.username or "." not in address.domain:
        return None
    return address


def _load_content(session: Session, request: DeliveryRequest) -> EmailContent | str:
    """Returns the content to send, or a permanent error code."""
    user = session.get(User, request.destination_id)
    if user is None:
        return DESTINATION_NOT_FOUND
    if user.email_verified_at is None:
        return DESTINATION_UNVERIFIED
    address = parse_email_address(user.login_identifier)
    if address is None:
        return DESTINATION_INVALID

    notification = session.get(Notification, request.notification_id)
    template = (
        session.get(NotificationTemplate, notification.template_id)
        if notification is not None and notification.template_id is not None
        else None
    )
    if template is None or template.channel != CHANNEL_EMAIL:
        return TEMPLATE_UNAVAILABLE
    return EmailContent(
        recipient=address.addr_spec,
        subject=template.subject_template,
        body=template.body_template,
    )


def build_message(settings: SmtpSettings, content: EmailContent) -> EmailMessage:
    sender = Address(display_name=settings.sender_name or "", addr_spec=settings.sender_email)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = Address(addr_spec=content.recipient)
    if content.subject:
        message["Subject"] = content.subject
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=sender.domain)
    message.set_content(content.body, subtype="plain", charset="utf-8")
    return message


class EmailChannelAdapter:
    """ChannelAdapter for `email`. `session_factory` is used only for the
    short read before sending; `transport` performs the SMTP I/O."""

    def __init__(
        self,
        *,
        settings: SmtpSettings,
        transport: SmtpTransport,
        session_factory: sessionmaker,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._session_factory = session_factory

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        if request.channel != CHANNEL_EMAIL or request.destination_type != DESTINATION_USER:
            return ChannelResult.permanent(DESTINATION_UNSUPPORTED)

        with self._session_factory() as session:
            loaded = _load_content(session, request)
            session.rollback()
        if isinstance(loaded, str):
            return ChannelResult.permanent(loaded)
        return self.send(loaded)

    def send(self, content: EmailContent) -> ChannelResult:
        """Build and send one message. No database access: callers (deliver,
        the administrator test send) hold no transaction during this call."""
        try:
            message = build_message(self._settings, content)
        except ValueError:
            # The stored subject cannot be a header (e.g. a line break).
            return ChannelResult.permanent(TEMPLATE_UNAVAILABLE)
        try:
            self._transport.send(
                message, sender=self._settings.sender_email, recipient=content.recipient
            )
        except (smtplib.SMTPException, OSError) as exc:
            failure = classify_smtp_error(exc)
            if failure.kind == "permanent":
                return ChannelResult.permanent(failure.code, failure.message)
            return ChannelResult.retryable(failure.code, failure.message)
        return ChannelResult.delivered(str(message["Message-ID"]))


__all__ = [
    "DESTINATION_UNSUPPORTED",
    "DESTINATION_NOT_FOUND",
    "DESTINATION_UNVERIFIED",
    "DESTINATION_INVALID",
    "TEMPLATE_UNAVAILABLE",
    "EmailContent",
    "parse_email_address",
    "build_message",
    "EmailChannelAdapter",
]
