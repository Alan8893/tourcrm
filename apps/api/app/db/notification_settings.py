"""Administrator Notification Settings persistence (Issue #333, ADR-0048).

- `notification_global_policy` — the installation-wide Global Admin Policy,
  one switch per MVP channel. Singleton (`id = 1`); when the row does not
  exist every channel is OFF (fail closed, ADR-0048 §2.8). There is no
  club-level policy.
- `notification_email_settings` / `notification_telegram_settings` —
  non-secret integration configuration (SMTP connection and sender, bot
  username). Singletons; NULL fields are "not configured".
- `integration_secrets` — the UI-managed secrets (SMTP password, Telegram
  bot token), one row per secret identifier, holding only the AES-256-GCM
  token of app.notification_settings.crypto. The encryption keys are never
  stored in the database. This table is the only place a secret exists;
  no API, audit row, outbox payload or log ever carries its value.
- `notification_test_send_attempts` — one row per administrator test-send
  attempt, the persistent basis of the 5-per-10-minutes rate limit
  (ADR-0048 §2.10). Holds no address, chat id or message.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.notification_settings.vocabulary import (
    CANONICAL_SECRET_NAMES,
    CANONICAL_SMTP_SECURITY_MODES,
    CANONICAL_TEST_DESTINATION_KINDS,
    CANONICAL_TEST_OUTCOMES,
    SINGLETON_ID,
)
from app.notifications.vocabulary import CANONICAL_NOTIFICATION_CHANNELS


def _in(values: frozenset[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(values))


def _timestamp(onupdate: bool = False) -> Mapped[datetime]:
    if onupdate:
        return mapped_column(
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        )
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated_by() -> Mapped[Optional[uuid.UUID]]:
    return mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class NotificationGlobalPolicy(Base):
    __tablename__ = "notification_global_policy"

    id: Mapped[int] = mapped_column(
        sa.SmallInteger, primary_key=True, autoincrement=False, default=SINGLETON_ID
    )
    email_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    telegram_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp(onupdate=True)
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _updated_by()

    __table_args__ = (
        sa.CheckConstraint(f"id = {SINGLETON_ID}", name="ck_notification_global_policy_singleton"),
    )


class NotificationEmailSettings(Base):
    __tablename__ = "notification_email_settings"

    id: Mapped[int] = mapped_column(
        sa.SmallInteger, primary_key=True, autoincrement=False, default=SINGLETON_ID
    )
    smtp_host: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    # NULL = the default port of `smtp_security`.
    smtp_port: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    smtp_security: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="starttls", server_default="starttls"
    )
    smtp_username: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    sender_email: Mapped[Optional[str]] = mapped_column(sa.String(320), nullable=True)
    sender_name: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp(onupdate=True)
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _updated_by()

    __table_args__ = (
        sa.CheckConstraint(
            f"id = {SINGLETON_ID}", name="ck_notification_email_settings_singleton"
        ),
        sa.CheckConstraint(
            f"smtp_security IN ({_in(CANONICAL_SMTP_SECURITY_MODES)})",
            name="ck_notification_email_settings_security_valid",
        ),
        sa.CheckConstraint(
            "smtp_port IS NULL OR smtp_port BETWEEN 1 AND 65535",
            name="ck_notification_email_settings_port_range",
        ),
        sa.CheckConstraint(
            "smtp_host IS NULL OR btrim(smtp_host) <> ''",
            name="ck_notification_email_settings_host_not_blank",
        ),
    )


class NotificationTelegramSettings(Base):
    __tablename__ = "notification_telegram_settings"

    id: Mapped[int] = mapped_column(
        sa.SmallInteger, primary_key=True, autoincrement=False, default=SINGLETON_ID
    )
    bot_username: Mapped[Optional[str]] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp(onupdate=True)
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _updated_by()

    __table_args__ = (
        sa.CheckConstraint(
            f"id = {SINGLETON_ID}", name="ck_notification_telegram_settings_singleton"
        ),
        sa.CheckConstraint(
            "bot_username IS NULL OR btrim(bot_username) <> ''",
            name="ck_notification_telegram_settings_username_not_blank",
        ),
    )


class IntegrationSecret(Base):
    """One encrypted UI-managed secret. `ciphertext` is the
    app.notification_settings.crypto token (`v1.<key_id>.<nonce>.<ct>`)."""

    __tablename__ = "integration_secrets"

    name: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp(onupdate=True)
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _updated_by()

    __table_args__ = (
        sa.CheckConstraint(
            f"name IN ({_in(CANONICAL_SECRET_NAMES)})", name="ck_integration_secrets_name_valid"
        ),
        sa.CheckConstraint(
            "ciphertext LIKE 'v1.%'", name="ck_integration_secrets_ciphertext_versioned"
        ),
    )

    def __repr__(self) -> str:
        return f"IntegrationSecret(name={self.name!r})"


class NotificationTestSendAttempt(Base):
    __tablename__ = "notification_test_send_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    destination_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    created_at: Mapped[datetime] = _timestamp()
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # NULL while the provider call is in progress.
    outcome: Mapped[Optional[str]] = mapped_column(sa.String(16), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            f"channel IN ({_in(CANONICAL_NOTIFICATION_CHANNELS)})",
            name="ck_notification_test_send_attempts_channel_valid",
        ),
        sa.CheckConstraint(
            f"destination_kind IN ({_in(CANONICAL_TEST_DESTINATION_KINDS)})",
            name="ck_notification_test_send_attempts_destination_kind_valid",
        ),
        sa.CheckConstraint(
            f"outcome IS NULL OR outcome IN ({_in(CANONICAL_TEST_OUTCOMES)})",
            name="ck_notification_test_send_attempts_outcome_valid",
        ),
        sa.CheckConstraint(
            "(outcome IS NULL) = (completed_at IS NULL)",
            name="ck_notification_test_send_attempts_completed_iff_outcome",
        ),
        sa.Index(
            "ix_notification_test_send_attempts_actor_created",
            "actor_user_id",
            "created_at",
        ),
    )


__all__ = [
    "NotificationGlobalPolicy",
    "NotificationEmailSettings",
    "NotificationTelegramSettings",
    "IntegrationSecret",
    "NotificationTestSendAttempt",
]
