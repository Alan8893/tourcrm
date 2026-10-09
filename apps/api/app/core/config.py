"""Environment-driven application configuration.

No default carries a real credential. `DATABASE_URL` must be supplied by the
environment (locally via a gitignored `.env`, in CI/production via secret
management) — see `apps/api/.env.example`.
"""

import os
import re
from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.headerregistry import Address
from typing import Optional


class ConfigurationError(RuntimeError):
    """Raised when required environment configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    # auth-api.md §19 / ADR-0009: session/CSRF cookies need `Secure` in an
    # Internet deployment, but a plain-HTTP LAN/local-dev deployment (also
    # explicitly anticipated by §19) cannot set it. No specific deployment
    # profile is canonical, so this is deployment configuration (default
    # "secure", matching the documented default expectation), not a
    # hardcoded assumption baked into the cookie-issuing code itself.
    cookie_secure: bool = True
    # TH-0117.2 / Issue #158, ADR-0040 §3: root directory for the local
    # FileStorage adapter (app.storage.local.LocalFileStorage). Relative to
    # the process's working directory unless given as an absolute path.
    # Not a secret (unlike database_url), so — like cookie_secure — it gets
    # a safe non-production default rather than a hard ConfigurationError
    # when unset, so existing callers of get_settings() are unaffected.
    file_storage_root: str = "var/file-storage"


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ConfigurationError(
            "DATABASE_URL environment variable is not set. "
            "See apps/api/.env.example for the expected format."
        )
    cookie_secure = os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("false", "0", "no")
    file_storage_root = os.getenv("FILE_STORAGE_ROOT", "var/file-storage")
    return Settings(
        database_url=database_url,
        cookie_secure=cookie_secure,
        file_storage_root=file_storage_root,
    )


# Issue #327, ADR-0045 §2.6: SMTP is integration/deployment configuration,
# never an ordinary feature setting. `password` is a secret: supplied only
# through the environment/secret management, excluded from repr so it can
# never reach a log line or traceback through this object.
SMTP_SECURITY_MODES: frozenset[str] = frozenset({"starttls", "ssl", "none"})


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    timeout_seconds: float
    sender_email: str
    sender_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ConfigurationError("SMTP_HOST must not be blank")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("SMTP_PORT must be between 1 and 65535")
        if self.security not in SMTP_SECURITY_MODES:
            raise ConfigurationError("SMTP_SECURITY must be one of: starttls, ssl, none")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("SMTP_TIMEOUT_SECONDS must be positive")
        if (self.username is None) != (self.password is None):
            raise ConfigurationError("SMTP_USERNAME and SMTP_PASSWORD must be set together")
        try:
            sender = Address(addr_spec=self.sender_email)
        except (ValueError, IndexError, HeaderParseError):
            raise ConfigurationError("SMTP_SENDER_EMAIL is not a valid email address") from None
        if not sender.username or not sender.domain:
            raise ConfigurationError("SMTP_SENDER_EMAIL is not a valid email address")


_SMTP_DEFAULT_PORTS = {"starttls": 587, "ssl": 465, "none": 25}


def _optional_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    return value if value is not None and value.strip() else None


def get_smtp_settings() -> Optional[SmtpSettings]:
    """SMTP settings from the environment, or None when SMTP_HOST is unset
    (Email delivery not configured). Raises ConfigurationError for a
    present but invalid configuration — never echoing the password."""
    host = _optional_env("SMTP_HOST")
    if host is None:
        return None
    security = (os.getenv("SMTP_SECURITY") or "starttls").strip().lower()
    port_raw = _optional_env("SMTP_PORT")
    timeout_raw = _optional_env("SMTP_TIMEOUT_SECONDS")
    try:
        port = int(port_raw) if port_raw is not None else _SMTP_DEFAULT_PORTS.get(security, 0)
    except ValueError:
        raise ConfigurationError("SMTP_PORT must be an integer") from None
    try:
        timeout_seconds = float(timeout_raw) if timeout_raw is not None else 30.0
    except ValueError:
        raise ConfigurationError("SMTP_TIMEOUT_SECONDS must be a number") from None
    sender_email = _optional_env("SMTP_SENDER_EMAIL")
    if sender_email is None:
        raise ConfigurationError("SMTP_SENDER_EMAIL is required when SMTP_HOST is set")
    return SmtpSettings(
        host=host.strip(),
        port=port,
        security=security,
        timeout_seconds=timeout_seconds,
        sender_email=sender_email.strip(),
        sender_name=_optional_env("SMTP_SENDER_NAME"),
        username=_optional_env("SMTP_USERNAME"),
        password=os.getenv("SMTP_PASSWORD") or None,
    )


# Issue #329, ADR-0047 §6: Telegram is integration/deployment configuration,
# never an ordinary feature setting. `bot_token` is a secret: supplied only
# through the environment/secret management, excluded from repr, and never
# echoed by a ConfigurationError. The bot username is public (it is part of
# every deep link) and is all the API process needs.
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,128}")
# Telegram bot usernames: 5-32 characters, letters/digits/underscore,
# starting with a letter and ending in "bot" (case-insensitive).
_TELEGRAM_BOT_USERNAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]")
_DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"


def validate_telegram_bot_username(value: str) -> str:
    username = value.strip().removeprefix("@")
    if not _TELEGRAM_BOT_USERNAME_PATTERN.fullmatch(username):
        raise ConfigurationError("TELEGRAM_BOT_USERNAME is not a valid Telegram bot username")
    return username


@dataclass(frozen=True)
class TelegramSettings:
    """Bot API access for the outbox worker's Telegram adapter and the
    poller. `api_base_url` exists for tests against a local fake server;
    production leaves it at the default."""

    bot_token: str = field(repr=False)
    bot_username: Optional[str] = None
    api_base_url: str = _DEFAULT_TELEGRAM_API_BASE_URL
    # Connect/read timeout of one ordinary Bot API request (sendMessage, getMe).
    request_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not _TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(self.bot_token):
            raise ConfigurationError("TELEGRAM_BOT_TOKEN has an invalid format")
        if self.bot_username is not None:
            object.__setattr__(
                self, "bot_username", validate_telegram_bot_username(self.bot_username)
            )
        # The token travels in the URL path: plain HTTP only to a loopback
        # test server, never over a network.
        if not (
            self.api_base_url.startswith("https://")
            or self.api_base_url.startswith(("http://127.0.0.1:", "http://localhost:"))
        ):
            raise ConfigurationError(
                "TELEGRAM_API_BASE_URL must be an https URL (http only for localhost)"
            )
        if not 0 < self.request_timeout_seconds <= 120:
            raise ConfigurationError("TELEGRAM_REQUEST_TIMEOUT_SECONDS must be in (0, 120]")


def get_telegram_settings() -> Optional[TelegramSettings]:
    """Telegram settings from the environment, or None when
    TELEGRAM_BOT_TOKEN is unset (Telegram delivery/polling not
    configured). Raises ConfigurationError for a present but invalid
    configuration — never echoing the token."""
    token = _optional_env("TELEGRAM_BOT_TOKEN")
    if token is None:
        return None
    timeout_raw = _optional_env("TELEGRAM_REQUEST_TIMEOUT_SECONDS")
    try:
        timeout_seconds = float(timeout_raw) if timeout_raw is not None else 10.0
    except ValueError:
        raise ConfigurationError("TELEGRAM_REQUEST_TIMEOUT_SECONDS must be a number") from None
    return TelegramSettings(
        bot_token=token.strip(),
        bot_username=_optional_env("TELEGRAM_BOT_USERNAME"),
        api_base_url=(
            _optional_env("TELEGRAM_API_BASE_URL") or _DEFAULT_TELEGRAM_API_BASE_URL
        ).rstrip("/"),
        request_timeout_seconds=timeout_seconds,
    )


def get_telegram_bot_username() -> Optional[str]:
    """The public bot username the API needs to build a linking deep link,
    or None when TELEGRAM_BOT_USERNAME is unset (linking unavailable). The
    API process never needs TELEGRAM_BOT_TOKEN."""
    username = _optional_env("TELEGRAM_BOT_USERNAME")
    return None if username is None else validate_telegram_bot_username(username)
