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

Security emails (Issue #342, PO decisions of 2026-10-12): a Notification
whose `event_type` has a registered `DeliverySecretResolver` (the
password-reset code) differs in exactly two ways:

- destination: the User's `login_identifier` even when
  `email_verified_at IS NULL` — the account's own login address is where
  its recovery code goes; it is still never `Person.email`;
- content: the template must contain the placeholder `{{code}}` exactly
  once in its body (and nowhere in its subject); the resolver returns the
  one-time secret, decrypted in this process's memory only, and it is
  substituted there. Nothing else is rendered. The resolver decides
  whether the secret may still be sent (a consumed, revoked or expired
  challenge is a permanent failure) — the adapter never reads auth tables.

The secret exists only in the `EmailContent` handed to the transport (its
body is excluded from `repr`) and is never logged, persisted or put into a
ChannelResult.

At-least-once (ADR-0046 §5.5): a timeout after the message data was sent
may mean the server accepted it; it is still reported as retryable, so the
same Delivery can be sent again. The returned `provider_message_id` is the
message's own Message-ID.
"""

import smtplib
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional, Protocol

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

# The only placeholder a security email template may (and must) contain.
SECRET_PLACEHOLDER = "{{code}}"


class DeliverySecretResolver(Protocol):
    """Returns the one-time secret a security email of `notification`
    must carry, or a ChannelResult failure (safe code only) when it can no
    longer be sent. Runs inside the adapter's short read-only session."""

    def resolve(self, session: Session, notification: Notification) -> "str | ChannelResult": ...


@dataclass(frozen=True)
class EmailContent:
    recipient: str
    subject: Optional[str]
    # May carry a one-time secret (security emails): never in a repr.
    body: str = field(repr=False)


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


def _load_content(
    session: Session,
    request: DeliveryRequest,
    secret_resolvers: Mapping[str, DeliverySecretResolver],
) -> EmailContent | ChannelResult:
    """Returns the content to send, or the failure to report."""
    notification = session.get(Notification, request.notification_id)
    resolver = (
        secret_resolvers.get(notification.event_type) if notification is not None else None
    )

    user = session.get(User, request.destination_id)
    if user is None:
        return ChannelResult.permanent(DESTINATION_NOT_FOUND)
    if user.email_verified_at is None and resolver is None:
        return ChannelResult.permanent(DESTINATION_UNVERIFIED)
    address = parse_email_address(user.login_identifier)
    if address is None:
        return ChannelResult.permanent(DESTINATION_INVALID)

    template = (
        session.get(NotificationTemplate, notification.template_id)
        if notification is not None and notification.template_id is not None
        else None
    )
    if template is None or template.channel != CHANNEL_EMAIL:
        return ChannelResult.permanent(TEMPLATE_UNAVAILABLE)
    body = template.body_template
    if resolver is not None:
        assert notification is not None
        if body.count(SECRET_PLACEHOLDER) != 1 or SECRET_PLACEHOLDER in (
            template.subject_template or ""
        ):
            return ChannelResult.permanent(TEMPLATE_UNAVAILABLE)
        secret = resolver.resolve(session, notification)
        if isinstance(secret, ChannelResult):
            return secret
        body = body.replace(SECRET_PLACEHOLDER, secret)
    return EmailContent(
        recipient=address.addr_spec,
        subject=template.subject_template,
        body=body,
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
        secret_resolvers: Optional[Mapping[str, DeliverySecretResolver]] = None,
    ) -> None:
        """`secret_resolvers` maps a security email's `event_type` to the
        resolver of its one-time secret (module docstring)."""
        self._settings = settings
        self._transport = transport
        self._session_factory = session_factory
        self._secret_resolvers = dict(secret_resolvers or {})

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        if request.channel != CHANNEL_EMAIL or request.destination_type != DESTINATION_USER:
            return ChannelResult.permanent(DESTINATION_UNSUPPORTED)

        with self._session_factory() as session:
            loaded = _load_content(session, request, self._secret_resolvers)
            session.rollback()
        if isinstance(loaded, ChannelResult):
            return loaded
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
    "SECRET_PLACEHOLDER",
    "DeliverySecretResolver",
    "EmailContent",
    "parse_email_address",
    "build_message",
    "EmailChannelAdapter",
]
