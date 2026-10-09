"""Test helpers for Administrator Notification Settings (Issue #333): a
fixed TEST-ONLY key ring and direct writers for the settings rows, so
adapter/poller/API tests configure SMTP/Telegram the way Settings stores
them (PostgreSQL, encrypted secrets) — there is no environment source.

Shared by tests/unit and tests/integration, like tests/telegram_fakes.py.
"""

from typing import Optional

from app.db.notification_settings import (
    IntegrationSecret,
    NotificationEmailSettings,
    NotificationGlobalPolicy,
    NotificationTelegramSettings,
)
from app.db.session import session_scope
from app.notification_settings.crypto import KeyRing, encrypt_secret
from app.notification_settings.vocabulary import (
    SECRET_SMTP_PASSWORD,
    SECRET_TELEGRAM_BOT_TOKEN,
    SINGLETON_ID,
)

# Obviously fake, test-only 32-byte keys (bytes 0..31 and 32..63).
TEST_KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
TEST_KEY_B = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="
TEST_KEY_RING = f"testa:{TEST_KEY_A}"


def store_secret(name: str, value: str, key_ring: Optional[str] = None) -> None:
    ring = KeyRing.parse(key_ring) if key_ring is not None else KeyRing.from_env()
    token = encrypt_secret(ring, name, value)
    with session_scope() as session:
        row = session.get(IntegrationSecret, name)
        if row is None:
            session.add(IntegrationSecret(name=name, ciphertext=token))
        else:
            row.ciphertext = token
        session.commit()


def store_policy(*, email: bool = True, telegram: bool = True) -> None:
    with session_scope() as session:
        row = session.get(NotificationGlobalPolicy, SINGLETON_ID)
        if row is None:
            session.add(
                NotificationGlobalPolicy(
                    id=SINGLETON_ID, email_enabled=email, telegram_enabled=telegram
                )
            )
        else:
            row.email_enabled, row.telegram_enabled = email, telegram
        session.commit()


def store_email_settings(
    *,
    host: Optional[str] = "smtp.test",
    port: Optional[int] = 587,
    security: str = "starttls",
    username: Optional[str] = "mailer",
    password: Optional[str] = None,
    sender_email: Optional[str] = "noreply@club.test",
    sender_name: Optional[str] = "TourCRM Club",
) -> None:
    with session_scope() as session:
        row = session.get(NotificationEmailSettings, SINGLETON_ID)
        if row is None:
            row = NotificationEmailSettings(id=SINGLETON_ID)
            session.add(row)
        row.smtp_host, row.smtp_port, row.smtp_security = host, port, security
        row.smtp_username, row.sender_email, row.sender_name = username, sender_email, sender_name
        session.commit()
    if password is not None:
        store_secret(SECRET_SMTP_PASSWORD, password)


def store_telegram_settings(*, token: Optional[str], username: Optional[str] = None) -> None:
    with session_scope() as session:
        row = session.get(NotificationTelegramSettings, SINGLETON_ID)
        if row is None:
            row = NotificationTelegramSettings(id=SINGLETON_ID)
            session.add(row)
        row.bot_username = username
        session.commit()
    if token is not None:
        store_secret(SECRET_TELEGRAM_BOT_TOKEN, token)
