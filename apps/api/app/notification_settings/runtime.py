"""Effective runtime configuration of the Email and Telegram channels
(Issue #333, ADR-0048 §2.6/§2.9, ADR-0047 §6).

The only source is PostgreSQL (the singleton settings rows and
`integration_secrets`); there is no environment fallback. Every consumer —
the API, the outbox worker's channel adapters, the Telegram poller and the
test send — calls these functions for each use, inside a short session it
closes before any network I/O, so a saved change applies without a restart.

Each load returns a state from app.notification_settings.vocabulary:

- `not_configured` — nothing usable is stored;
- `incomplete` — required fields are missing (Email: host or sender; SMTP
  username without password or the reverse);
- `secret_unavailable` — a stored secret cannot be decrypted: the key ring
  is missing/malformed, its key id is unknown, the key is wrong or the
  token was tampered with (fail closed, never plaintext);
- `invalid` — the stored values fail the settings validation;
- `configured` — `settings` is the ready-to-use SmtpSettings/
  TelegramSettings (which hold the decrypted secret only in memory).
"""

from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import (
    ConfigurationError,
    SmtpSettings,
    TelegramSettings,
    get_telegram_api_base_url,
)
from app.db.notification_settings import (
    IntegrationSecret,
    NotificationEmailSettings,
    NotificationGlobalPolicy,
    NotificationTelegramSettings,
)
from app.notification_settings.crypto import (
    KeyRing,
    KeyRingUnavailable,
    SecretDecryptionError,
    decrypt_secret,
)
from app.notification_settings.vocabulary import (
    CONFIG_CONFIGURED,
    CONFIG_INCOMPLETE,
    CONFIG_INVALID,
    CONFIG_NOT_CONFIGURED,
    CONFIG_SECRET_UNAVAILABLE,
    ENCRYPTION_AVAILABLE,
    ENCRYPTION_INVALID,
    ENCRYPTION_MISSING,
    SECRET_SMTP_PASSWORD,
    SECRET_TELEGRAM_BOT_TOKEN,
    SINGLETON_ID,
    SMTP_DEFAULT_PORTS,
    SMTP_TIMEOUT_SECONDS,
)
from app.notifications.vocabulary import CHANNEL_EMAIL, CHANNEL_TELEGRAM


class SecretUnavailable(Exception):
    """A stored secret exists but cannot be decrypted. `code` is safe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EmailRuntime:
    state: str
    settings: Optional[SmtpSettings] = None


@dataclass(frozen=True)
class TelegramRuntime:
    state: str
    settings: Optional[TelegramSettings] = None
    bot_username: Optional[str] = None


def encryption_status() -> str:
    try:
        KeyRing.from_env()
    except KeyRingUnavailable as exc:
        return ENCRYPTION_MISSING if exc.reason == "missing" else ENCRYPTION_INVALID
    return ENCRYPTION_AVAILABLE


def secret_configured(session: Session, name: str) -> bool:
    return session.get(IntegrationSecret, name) is not None


def read_secret(session: Session, name: str) -> Optional[str]:
    """The decrypted secret, None when not stored. Raises SecretUnavailable
    (fail closed) when it is stored but cannot be decrypted."""
    row = session.get(IntegrationSecret, name)
    if row is None:
        return None
    try:
        key_ring = KeyRing.from_env()
    except KeyRingUnavailable:
        raise SecretUnavailable("settings_encryption_unavailable") from None
    try:
        return decrypt_secret(key_ring, name, row.ciphertext)
    except SecretDecryptionError as exc:
        code = exc.code
    raise SecretUnavailable(code)


def global_channel_enabled(session: Session, channel: str) -> bool:
    """Global Admin Policy for `channel`; no saved policy = OFF."""
    policy = session.get(NotificationGlobalPolicy, SINGLETON_ID)
    if policy is None:
        return False
    if channel == CHANNEL_EMAIL:
        return policy.email_enabled
    if channel == CHANNEL_TELEGRAM:
        return policy.telegram_enabled
    return False


def load_email_runtime(session: Session) -> EmailRuntime:
    row = session.get(NotificationEmailSettings, SINGLETON_ID)
    password_stored = secret_configured(session, SECRET_SMTP_PASSWORD)
    if row is None or (
        row.smtp_host is None
        and row.sender_email is None
        and row.smtp_username is None
        and not password_stored
    ):
        return EmailRuntime(CONFIG_NOT_CONFIGURED)
    if row.smtp_host is None or row.sender_email is None:
        return EmailRuntime(CONFIG_INCOMPLETE)
    if (row.smtp_username is None) != (not password_stored):
        return EmailRuntime(CONFIG_INCOMPLETE)
    try:
        password = read_secret(session, SECRET_SMTP_PASSWORD)
    except SecretUnavailable:
        return EmailRuntime(CONFIG_SECRET_UNAVAILABLE)
    try:
        settings = SmtpSettings(
            host=row.smtp_host,
            port=row.smtp_port or SMTP_DEFAULT_PORTS.get(row.smtp_security, 0),
            security=row.smtp_security,
            timeout_seconds=SMTP_TIMEOUT_SECONDS,
            sender_email=row.sender_email,
            sender_name=row.sender_name,
            username=row.smtp_username,
            password=password,
        )
    except ConfigurationError:
        return EmailRuntime(CONFIG_INVALID)
    return EmailRuntime(CONFIG_CONFIGURED, settings)


def load_telegram_bot_username(session: Session) -> Optional[str]:
    return session.execute(
        sa.select(NotificationTelegramSettings.bot_username).where(
            NotificationTelegramSettings.id == SINGLETON_ID
        )
    ).scalar_one_or_none()


def load_telegram_runtime(session: Session) -> TelegramRuntime:
    username = load_telegram_bot_username(session)
    try:
        token = read_secret(session, SECRET_TELEGRAM_BOT_TOKEN)
    except SecretUnavailable:
        return TelegramRuntime(CONFIG_SECRET_UNAVAILABLE, bot_username=username)
    if token is None:
        return TelegramRuntime(CONFIG_NOT_CONFIGURED, bot_username=username)
    try:
        settings = TelegramSettings(
            bot_token=token, bot_username=username, api_base_url=get_telegram_api_base_url()
        )
    except ConfigurationError:
        return TelegramRuntime(CONFIG_INVALID, bot_username=username)
    return TelegramRuntime(CONFIG_CONFIGURED, settings, username)


__all__ = [
    "SecretUnavailable",
    "EmailRuntime",
    "TelegramRuntime",
    "encryption_status",
    "secret_configured",
    "read_secret",
    "global_channel_enabled",
    "load_email_runtime",
    "load_telegram_bot_username",
    "load_telegram_runtime",
]
