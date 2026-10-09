"""Unit tests for the Email channel's SMTP configuration, message
construction, transport and provider error classification (Issue #327) —
no database. STARTTLS/SSL are verified against injected smtplib doubles;
plain SMTP, AUTH, reply codes and timeouts run a real `smtplib`
conversation against the local fake server in tests/smtp_server.py.
"""

import email
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Any

import pytest

from app.core.config import ConfigurationError, SmtpSettings
from app.notifications.email_adapter import EmailContent, build_message, parse_email_address
from app.notifications.smtp import (
    SMTP_AUTHENTICATION_FAILED,
    SMTP_CONFIGURATION_INVALID,
    SMTP_CONNECTION_FAILED,
    SMTP_REJECTED,
    SMTP_TIMEOUT,
    SMTP_TLS_FAILED,
    SMTP_TRANSIENT_FAILURE,
    SmtplibTransport,
    classify_smtp_error,
)
from tests.smtp_server import FakeSmtpConfig, FakeSmtpServer

SECRET = "s3cr3t-smtp-pa55word"
_SMTP_ENV = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_TIMEOUT_SECONDS",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_SENDER_EMAIL",
    "SMTP_SENDER_NAME",
)


def _settings(**overrides: Any) -> SmtpSettings:
    fields: dict[str, Any] = {
        "host": "smtp.test",
        "port": 587,
        "security": "starttls",
        "timeout_seconds": 5.0,
        "sender_email": "noreply@club.test",
        "sender_name": "TourCRM Club",
        "username": "mailer",
        "password": SECRET,
    }
    fields.update(overrides)
    return SmtpSettings(**fields)


# --- Configuration (validation of the settings built from Settings, ADR-0048) ----------


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": " "},
        {"port": 0},
        {"port": 70000},
        {"security": "tls13"},
        {"timeout_seconds": 0},
        {"sender_email": "not-an-address"},
        {"sender_email": ""},
        {"password": None},
        {"username": None},
    ],
)
def test_invalid_configuration_is_rejected_without_echoing_the_password(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        _settings(**overrides)
    assert SECRET not in str(exc_info.value)


def test_smtp_environment_is_not_a_configuration_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0048 §2.6: no env fallback — app.core.config no longer offers an
    environment reader for SMTP at all."""
    for name in _SMTP_ENV:
        monkeypatch.setenv(name, "smtp.test")
    import app.core.config as config

    assert not hasattr(config, "get_smtp_settings")


def test_password_is_never_in_repr() -> None:
    settings = _settings()
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)


# --- Destination / message ------------------------------------------------------------


@pytest.mark.parametrize("value", ["anna@club.test", "  Anna.B+tag@mail.club.test "])
def test_valid_email_destination(value: str) -> None:
    address = parse_email_address(value)
    assert address is not None and address.addr_spec == value.strip()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "anna",
        "anna@",
        "@club.test",
        "anna@localhost",
        "anna smith@club.test",
        "anna@club.test\r\nBcc: x@evil.test",
        "Anna <anna@club.test>",
        "a@club.test,b@club.test",
    ],
)
def test_malformed_email_destination(value: Any) -> None:
    assert parse_email_address(value) is None


def test_message_carries_sender_recipient_subject_and_text_body() -> None:
    message = build_message(
        _settings(),
        EmailContent("anna@club.test", "Поход в субботу", "Сбор в 8:00 у клуба."),
    )
    parsed = email.message_from_bytes(message.as_bytes(), policy=email.policy.default)
    assert parsed["From"] == "TourCRM Club <noreply@club.test>"
    assert parsed["To"] == "anna@club.test"
    assert parsed["Subject"] == "Поход в субботу"
    assert parsed["Date"] and parsed["Message-ID"].endswith("@club.test>")
    assert parsed.get_content_type() == "text/plain"
    assert not parsed.is_multipart()
    assert parsed.get_content().strip() == "Сбор в 8:00 у клуба."


def test_message_without_template_subject_has_no_subject_header() -> None:
    message = build_message(_settings(sender_name=None), EmailContent("a@club.test", None, "Body"))
    assert message["Subject"] is None
    assert message["From"] == "noreply@club.test"


def test_subject_with_a_line_break_cannot_become_a_header() -> None:
    with pytest.raises(ValueError):
        build_message(_settings(), EmailContent("a@club.test", "Hi\nBcc: x@evil.test", "Body"))


# --- Transport: STARTTLS / SSL / none against smtplib doubles -------------------------------


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.calls: list[tuple[str, Any]] = []
        _FakeClient.instances.append(self)

    def starttls(self, *, context: ssl.SSLContext) -> None:
        self.calls.append(("starttls", context))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", (user, password)))

    def send_message(self, message: EmailMessage, *, from_addr: str, to_addrs: list[str]) -> None:
        self.calls.append(("send_message", (from_addr, to_addrs)))

    def quit(self) -> None:
        self.calls.append(("quit", None))

    def close(self) -> None:
        self.calls.append(("close", None))


def _transport(settings: SmtpSettings) -> tuple[SmtplibTransport, list[str], ssl.SSLContext]:
    _FakeClient.instances = []
    used: list[str] = []
    context = ssl.create_default_context()

    def plain(*args: Any, **kwargs: Any) -> Any:
        used.append("plain")
        return _FakeClient(*args, **kwargs)

    def implicit_tls(*args: Any, **kwargs: Any) -> Any:
        used.append("ssl")
        return _FakeClient(*args, **kwargs)

    transport = SmtplibTransport(
        settings,
        smtp_factory=plain,
        smtp_ssl_factory=implicit_tls,
        ssl_context_factory=lambda: context,
    )
    return transport, used, context


def _send(transport: SmtplibTransport) -> None:
    transport.send(EmailMessage(), sender="noreply@club.test", recipient="anna@club.test")


def test_starttls_upgrades_then_authenticates_then_sends() -> None:
    transport, used, context = _transport(_settings(security="starttls"))
    _send(transport)
    (client,) = _FakeClient.instances
    assert used == ["plain"]
    assert (client.host, client.port, client.kwargs) == ("smtp.test", 587, {"timeout": 5.0})
    assert [name for name, _ in client.calls] == ["starttls", "login", "send_message", "quit"]
    assert client.calls[0][1] is context
    assert client.calls[1][1] == ("mailer", SECRET)
    assert client.calls[2][1] == ("noreply@club.test", ["anna@club.test"])


def test_ssl_uses_implicit_tls_with_a_verified_context() -> None:
    transport, used, context = _transport(_settings(security="ssl", port=465))
    _send(transport)
    (client,) = _FakeClient.instances
    assert used == ["ssl"]
    assert client.kwargs == {"timeout": 5.0, "context": context}
    assert [name for name, _ in client.calls] == ["login", "send_message", "quit"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_no_security_and_no_credentials_neither_upgrades_nor_authenticates() -> None:
    transport, used, _ = _transport(
        _settings(security="none", port=25, username=None, password=None)
    )
    _send(transport)
    (client,) = _FakeClient.instances
    assert used == ["plain"]
    assert [name for name, _ in client.calls] == ["send_message", "quit"]


# --- Error classification ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind", "code", "smtp_code"),
    [
        (
            smtplib.SMTPRecipientsRefused({"a@x.test": (450, b"busy")}),
            "retryable",
            SMTP_TRANSIENT_FAILURE,
            450,
        ),
        (
            smtplib.SMTPRecipientsRefused({"a@x.test": (550, b"no user")}),
            "permanent",
            SMTP_REJECTED,
            550,
        ),
        (smtplib.SMTPDataError(451, b"try later"), "retryable", SMTP_TRANSIENT_FAILURE, 451),
        (smtplib.SMTPDataError(554, b"rejected"), "permanent", SMTP_REJECTED, 554),
        (smtplib.SMTPSenderRefused(553, b"bad sender", "x"), "permanent", SMTP_REJECTED, 553),
        (smtplib.SMTPConnectError(421, b"too busy"), "retryable", SMTP_TRANSIENT_FAILURE, 421),
        (
            smtplib.SMTPAuthenticationError(535, SECRET.encode()),
            "permanent",
            SMTP_AUTHENTICATION_FAILED,
            535,
        ),
        (
            smtplib.SMTPAuthenticationError(454, b"temporary"),
            "retryable",
            SMTP_TRANSIENT_FAILURE,
            454,
        ),
        (
            smtplib.SMTPNotSupportedError("STARTTLS extension not supported"),
            "permanent",
            SMTP_CONFIGURATION_INVALID,
            None,
        ),
        (smtplib.SMTPServerDisconnected("lost"), "retryable", SMTP_CONNECTION_FAILED, None),
        (socket.timeout("timed out"), "retryable", SMTP_TIMEOUT, None),
        (TimeoutError(), "retryable", SMTP_TIMEOUT, None),
        (ConnectionRefusedError(111, "refused"), "retryable", SMTP_CONNECTION_FAILED, None),
        (ConnectionResetError(104, "reset"), "retryable", SMTP_CONNECTION_FAILED, None),
        (
            socket.gaierror(socket.EAI_AGAIN, "temporary failure"),
            "retryable",
            SMTP_CONNECTION_FAILED,
            None,
        ),
        (
            socket.gaierror(socket.EAI_NONAME, "unknown host"),
            "permanent",
            SMTP_CONFIGURATION_INVALID,
            None,
        ),
        (
            ssl.SSLCertVerificationError("certificate verify failed"),
            "permanent",
            SMTP_TLS_FAILED,
            None,
        ),
        (ssl.SSLError("handshake interrupted"), "retryable", SMTP_TLS_FAILED, None),
    ],
)
def test_provider_errors_are_classified_and_sanitized(
    exc: BaseException, kind: str, code: str, smtp_code: Any
) -> None:
    failure = classify_smtp_error(exc)
    assert (failure.kind, failure.code, failure.smtp_code) == (kind, code, smtp_code)
    assert failure.message == (None if smtp_code is None else f"SMTP {smtp_code}")
    assert SECRET not in repr(failure)


# --- Real smtplib conversation against the local fake server ------------------------------


def _local(server: FakeSmtpServer, **overrides: Any) -> SmtpSettings:
    return _settings(host="127.0.0.1", port=server.port, security="none", **overrides)


def _message() -> EmailMessage:
    return build_message(_settings(), EmailContent("anna@club.test", "Тема", "Текст письма"))


def test_real_smtp_conversation_with_authentication() -> None:
    config = FakeSmtpConfig(username="mailer", password=SECRET)
    with FakeSmtpServer(config) as server:
        SmtplibTransport(_local(server)).send(
            _message(), sender="noreply@club.test", recipient="anna@club.test"
        )
    (received,) = config.received
    assert (received.mail_from, received.rcpt_to) == ("noreply@club.test", ["anna@club.test"])
    parsed = email.message_from_bytes(received.data, policy=email.policy.default)
    assert parsed["Subject"] == "Тема"
    assert parsed.get_content().strip() == "Текст письма"
    assert config.auth_attempts == [("mailer", SECRET)]


def test_real_authentication_failure_is_permanent_and_never_echoes_the_reply() -> None:
    config = FakeSmtpConfig(username="mailer", password="other")
    with FakeSmtpServer(config) as server:
        with pytest.raises(smtplib.SMTPAuthenticationError) as exc_info:
            SmtplibTransport(_local(server)).send(
                _message(), sender="noreply@club.test", recipient="anna@club.test"
            )
    failure = classify_smtp_error(exc_info.value)
    assert (failure.kind, failure.code, failure.message) == (
        "permanent",
        SMTP_AUTHENTICATION_FAILED,
        "SMTP 535",
    )
    assert config.received == []


@pytest.mark.parametrize(
    ("reply", "kind", "code"),
    [
        ("450 4.2.1 Mailbox busy", "retryable", SMTP_TRANSIENT_FAILURE),
        ("550 5.1.1 No such user", "permanent", SMTP_REJECTED),
    ],
)
def test_real_recipient_rejection_is_classified(reply: str, kind: str, code: str) -> None:
    config = FakeSmtpConfig(rcpt_reply=reply)
    with FakeSmtpServer(config) as server:
        with pytest.raises(smtplib.SMTPRecipientsRefused) as exc_info:
            SmtplibTransport(_local(server, username=None, password=None)).send(
                _message(), sender="noreply@club.test", recipient="anna@club.test"
            )
    failure = classify_smtp_error(exc_info.value)
    assert (failure.kind, failure.code) == (kind, code)


def test_real_timeout_is_retryable() -> None:
    config = FakeSmtpConfig(silent=True)
    with FakeSmtpServer(config) as server:
        with pytest.raises(OSError) as exc_info:
            SmtplibTransport(_local(server, timeout_seconds=0.5)).send(
                _message(), sender="noreply@club.test", recipient="anna@club.test"
            )
        assert server.silent_connected.is_set()
    failure = classify_smtp_error(exc_info.value)
    assert (failure.kind, failure.code) == ("retryable", SMTP_TIMEOUT)


def test_real_connection_failure_is_retryable() -> None:
    with FakeSmtpServer(FakeSmtpConfig()) as server:
        port = server.port
    # The server is closed: nothing listens on the port any more.
    with pytest.raises(OSError) as exc_info:
        SmtplibTransport(_settings(host="127.0.0.1", port=port, security="none")).send(
            _message(), sender="noreply@club.test", recipient="anna@club.test"
        )
    failure = classify_smtp_error(exc_info.value)
    assert (failure.kind, failure.code) == ("retryable", SMTP_CONNECTION_FAILED)
