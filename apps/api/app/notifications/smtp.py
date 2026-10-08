"""SMTP transport and provider error classification for the Email channel
adapter (Issue #327, ADR-0045 §2.6).

The only module that talks SMTP. `smtplib`/`ssl`/`socket` types and
exceptions never leave it: `classify_smtp_error` turns them into a stable,
safe `SmtpFailure` (retryable or permanent, an error code and at most the
numeric SMTP reply code) — never the server's reply text, the exception
message or any credential.

One connection per message: connect (implicit TLS for `ssl`), STARTTLS
for `starttls`, AUTH when a username is configured, send, QUIT. The
connect/command timeout is `SMTP_TIMEOUT_SECONDS`. TLS uses the default
verified context (certificate and hostname checks on).
"""

import smtplib
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal, Optional, Protocol

from app.core.config import SmtpSettings

SMTP_TIMEOUT = "smtp_timeout"
SMTP_CONNECTION_FAILED = "smtp_connection_failed"
SMTP_TRANSIENT_FAILURE = "smtp_transient_failure"
SMTP_REJECTED = "smtp_rejected"
SMTP_AUTHENTICATION_FAILED = "smtp_authentication_failed"
SMTP_TLS_FAILED = "smtp_tls_failed"
SMTP_CONFIGURATION_INVALID = "smtp_configuration_invalid"


class SmtpTransport(Protocol):
    """Sends one prepared message. Raises the underlying smtplib/socket/ssl
    exception on failure; the adapter classifies it."""

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> None: ...


SmtpFactory = Callable[..., smtplib.SMTP]


class SmtplibTransport:
    """The production SmtpTransport over the standard library."""

    def __init__(
        self,
        settings: SmtpSettings,
        *,
        smtp_factory: SmtpFactory = smtplib.SMTP,
        smtp_ssl_factory: SmtpFactory = smtplib.SMTP_SSL,
        ssl_context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
    ) -> None:
        self._settings = settings
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory
        self._ssl_context_factory = ssl_context_factory

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> None:
        settings = self._settings
        if settings.security == "ssl":
            client = self._smtp_ssl_factory(
                settings.host,
                settings.port,
                timeout=settings.timeout_seconds,
                context=self._ssl_context_factory(),
            )
        else:
            client = self._smtp_factory(
                settings.host, settings.port, timeout=settings.timeout_seconds
            )
        try:
            if settings.security == "starttls":
                client.starttls(context=self._ssl_context_factory())
            if settings.username is not None and settings.password is not None:
                client.login(settings.username, settings.password)
            client.send_message(message, from_addr=sender, to_addrs=[recipient])
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                client.close()


@dataclass(frozen=True)
class SmtpFailure:
    kind: Literal["retryable", "permanent"]
    code: str
    # The numeric SMTP reply code when the server answered; safe to keep.
    smtp_code: Optional[int] = None

    @property
    def message(self) -> Optional[str]:
        return None if self.smtp_code is None else f"SMTP {self.smtp_code}"


def _by_reply_code(smtp_code: int, permanent_code: str) -> SmtpFailure:
    if 400 <= smtp_code < 500:
        return SmtpFailure("retryable", SMTP_TRANSIENT_FAILURE, smtp_code)
    return SmtpFailure("permanent", permanent_code, smtp_code)


def classify_smtp_error(exc: BaseException) -> SmtpFailure:
    """Retryable: timeouts, connection/network failures, temporary DNS
    failure, SMTP 4xx. Permanent: SMTP 5xx, authentication rejection,
    TLS verification failure, unsupported STARTTLS/AUTH, unknown host."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        if 400 <= exc.smtp_code < 500:
            return SmtpFailure("retryable", SMTP_TRANSIENT_FAILURE, exc.smtp_code)
        return SmtpFailure("permanent", SMTP_AUTHENTICATION_FAILED, exc.smtp_code)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        return _by_reply_code(max(codes) if codes else 550, SMTP_REJECTED)
    if isinstance(exc, smtplib.SMTPResponseException):
        return _by_reply_code(exc.smtp_code, SMTP_REJECTED)
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return SmtpFailure("permanent", SMTP_CONFIGURATION_INVALID)
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        # smtplib reports a read timeout as a disconnect; keep it a timeout.
        cause = exc.__cause__ or exc.__context__
        if isinstance(cause, (socket.timeout, TimeoutError)):
            return SmtpFailure("retryable", SMTP_TIMEOUT)
        return SmtpFailure("retryable", SMTP_CONNECTION_FAILED)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return SmtpFailure("retryable", SMTP_TIMEOUT)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return SmtpFailure("permanent", SMTP_TLS_FAILED)
    if isinstance(exc, ssl.SSLError):
        return SmtpFailure("retryable", SMTP_TLS_FAILED)
    if isinstance(exc, socket.gaierror):
        if exc.errno == socket.EAI_AGAIN:
            return SmtpFailure("retryable", SMTP_CONNECTION_FAILED)
        return SmtpFailure("permanent", SMTP_CONFIGURATION_INVALID)
    if isinstance(exc, (OSError, smtplib.SMTPException)):
        return SmtpFailure("retryable", SMTP_CONNECTION_FAILED)
    return SmtpFailure("retryable", SMTP_CONNECTION_FAILED)


__all__ = [
    "SMTP_TIMEOUT",
    "SMTP_CONNECTION_FAILED",
    "SMTP_TRANSIENT_FAILURE",
    "SMTP_REJECTED",
    "SMTP_AUTHENTICATION_FAILED",
    "SMTP_TLS_FAILED",
    "SMTP_CONFIGURATION_INVALID",
    "SmtpTransport",
    "SmtplibTransport",
    "SmtpFailure",
    "classify_smtp_error",
]
