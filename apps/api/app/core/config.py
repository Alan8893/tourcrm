"""Environment-driven application configuration.

No default carries a real credential. `DATABASE_URL` must be supplied by the
environment (locally via a gitignored `.env`, in CI/production via secret
management) — see `apps/api/.env.example`.
"""

import os
import re
import urllib.parse
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


# Issue #327, ADR-0045 §2.6 / ADR-0048: SMTP is integration configuration
# managed in Settings → Notifications and built per use by
# app.notification_settings.runtime — never read from the environment.
# `password` is a secret (decrypted only in memory), excluded from repr so it
# can never reach a log line or traceback through this object.
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
            raise ConfigurationError("SMTP host must not be blank")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("SMTP port must be between 1 and 65535")
        if self.security not in SMTP_SECURITY_MODES:
            raise ConfigurationError("SMTP security must be one of: starttls, ssl, none")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("SMTP timeout must be positive")
        if (self.username is None) != (self.password is None):
            raise ConfigurationError("SMTP username and password must be set together")
        try:
            sender = Address(addr_spec=self.sender_email)
        except (ValueError, IndexError, HeaderParseError):
            raise ConfigurationError("SMTP sender email is not a valid email address") from None
        if not sender.username or not sender.domain:
            raise ConfigurationError("SMTP sender email is not a valid email address")


def _optional_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    return value if value is not None and value.strip() else None


# Issue #329, ADR-0047 §6 / ADR-0048: the Telegram bot token and username are
# managed in Settings → Notifications (app.notification_settings) — never
# read from the environment. `bot_token` is a secret (decrypted only in
# memory), excluded from repr and never echoed by a ConfigurationError. The
# bot username is public (it is part of every deep link).
TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,128}")
# Telegram bot usernames: 5-32 characters, letters/digits/underscore,
# starting with a letter and ending in "bot" (case-insensitive).
_TELEGRAM_BOT_USERNAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]")
_DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
# The bot token travels in the path of every Bot API URL, so the endpoint is
# an allowlist, not free configuration: HTTPS only to the official host on
# the default port, plain HTTP only to a loopback address (local fake
# servers in tests/development).
_TELEGRAM_API_HOST = "api.telegram.org"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TELEGRAM_API_BASE_URL_ERROR = (
    "TELEGRAM_API_BASE_URL must be https://api.telegram.org "
    "(or http://<loopback>:<port> for a local fake server)"
)


def validate_telegram_api_base_url(value: str) -> str:
    """Return the normalized Bot API base URL (scheme://host[:port], no
    trailing slash), rebuilt from parsed components, or raise
    ConfigurationError. Accepted: `https://api.telegram.org` (optionally
    `:443` or a trailing `/`), and `http://` to `localhost`, `127.0.0.1` or
    `[::1]` with an explicit port. Rejected: any other host or scheme,
    userinfo, a path, query or fragment, other ports, and characters that
    parsers may silently strip (whitespace, control characters, a backslash)."""
    if not value or any(ch.isspace() or not ch.isprintable() or ch == "\\" for ch in value):
        raise ConfigurationError(_TELEGRAM_API_BASE_URL_ERROR)
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise ConfigurationError(_TELEGRAM_API_BASE_URL_ERROR) from None
    host = parts.hostname
    if (
        host is None
        or "@" in parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
    ):
        raise ConfigurationError(_TELEGRAM_API_BASE_URL_ERROR)
    if parts.scheme == "https" and host == _TELEGRAM_API_HOST and port in (None, 443):
        return f"https://{_TELEGRAM_API_HOST}"
    if parts.scheme == "http" and host in _LOOPBACK_HOSTS and port is not None and port > 0:
        rendered_host = f"[{host}]" if ":" in host else host
        return f"http://{rendered_host}:{port}"
    raise ConfigurationError(_TELEGRAM_API_BASE_URL_ERROR)


def validate_telegram_bot_username(value: str) -> str:
    username = value.strip().removeprefix("@")
    if not _TELEGRAM_BOT_USERNAME_PATTERN.fullmatch(username):
        raise ConfigurationError("Telegram bot username is not valid")
    return username


@dataclass(frozen=True)
class TelegramSettings:
    """Bot API access for the outbox worker's Telegram adapter and the
    poller. `api_base_url` is restricted by validate_telegram_api_base_url:
    production leaves it at the official default; a loopback HTTP URL exists
    only for local fake servers."""

    bot_token: str = field(repr=False)
    bot_username: Optional[str] = None
    api_base_url: str = _DEFAULT_TELEGRAM_API_BASE_URL
    # Connect/read timeout of one ordinary Bot API request (sendMessage, getMe).
    request_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(self.bot_token):
            raise ConfigurationError("Telegram bot token has an invalid format")
        if self.bot_username is not None:
            object.__setattr__(
                self, "bot_username", validate_telegram_bot_username(self.bot_username)
            )
        object.__setattr__(
            self, "api_base_url", validate_telegram_api_base_url(self.api_base_url)
        )
        if not 0 < self.request_timeout_seconds <= 120:
            raise ConfigurationError("Telegram request timeout must be in (0, 120]")


def get_telegram_api_base_url() -> str:
    """The Bot API endpoint: the official one, or the allowlisted loopback
    override `TELEGRAM_API_BASE_URL` used by local fake servers in tests and
    development. This is a transport override only — the bot token and
    username are never read from the environment (ADR-0048 §2.6)."""
    return validate_telegram_api_base_url(
        _optional_env("TELEGRAM_API_BASE_URL") or _DEFAULT_TELEGRAM_API_BASE_URL
    )
