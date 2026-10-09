"""Audited administrator operations of Settings → Notifications (Issue #333,
ADR-0048 §2.2–§2.11).

Authorization is the caller's (app.api.v1.notification_settings, against
`notification.manage` / `settings.manage`); like app.audit.service nothing
here commits — the caller commits the change together with its audit row.

- Global Admin Policy: per-channel booleans, upserted into the singleton.
- Rules: only existing installation-wide rules (`club_id IS NULL`) can be
  listed and enabled/disabled; none is created or deleted here.
- Integrations: non-secret SMTP/Telegram fields are replaced as a whole;
  saving them never touches a secret.
- Secrets: `set_secret` encrypts a new value with the key ring's primary
  key (refused when the key ring is unavailable — never stored in
  plaintext); `clear_secret` deletes the stored token. The value, the
  ciphertext and the key never reach the audit row, a return value or an
  exception.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.notification_settings import (
    IntegrationSecret,
    NotificationEmailSettings,
    NotificationGlobalPolicy,
    NotificationTelegramSettings,
)
from app.db.notifications import NotificationRule
from app.notification_settings.crypto import KeyRing, KeyRingUnavailable, encrypt_secret
from app.notification_settings.vocabulary import (
    CANONICAL_SECRET_NAMES,
    SECRET_SMTP_PASSWORD,
    SINGLETON_ID,
)
from app.notifications.vocabulary import CHANNEL_EMAIL, CHANNEL_TELEGRAM

EMAIL_FIELDS = (
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "sender_email",
    "sender_name",
)


def _ensure_singleton(session: Session, model: Any, **defaults: Any) -> bool:
    """Create the singleton row if missing (safe under concurrent first
    saves); returns whether this call created it. The caller then locks it."""
    inserted = session.execute(
        pg_insert(model)
        .values(id=SINGLETON_ID, **defaults)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(model.id)
    ).scalar_one_or_none()
    return inserted is not None


def _lock_singleton(session: Session, model: Any) -> Any:
    return session.execute(
        sa.select(model).where(model.id == SINGLETON_ID).with_for_update()
    ).scalar_one()


class EncryptionUnavailable(Exception):
    """The key ring is missing or invalid; secrets cannot be written."""


class RuleNotFound(Exception):
    """No installation-wide rule with this id."""


@dataclass(frozen=True)
class PolicyView:
    email_enabled: bool
    telegram_enabled: bool
    saved: bool


def get_policy(session: Session) -> PolicyView:
    row = session.get(NotificationGlobalPolicy, SINGLETON_ID)
    if row is None:
        return PolicyView(email_enabled=False, telegram_enabled=False, saved=False)
    return PolicyView(row.email_enabled, row.telegram_enabled, saved=True)


def update_policy(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    email_enabled: bool,
    telegram_enabled: bool,
    request_id: Optional[str] = None,
) -> PolicyView:
    created = _ensure_singleton(
        session, NotificationGlobalPolicy, email_enabled=False, telegram_enabled=False
    )
    row = _lock_singleton(session, NotificationGlobalPolicy)
    before = (row.email_enabled, row.telegram_enabled)
    row.email_enabled = email_enabled
    row.telegram_enabled = telegram_enabled
    row.updated_by_user_id = actor_user_id
    session.flush()
    changes: dict[str, Any] = {}
    for channel, old, new in (
        (CHANNEL_EMAIL, before[0], email_enabled),
        (CHANNEL_TELEGRAM, before[1], telegram_enabled),
    ):
        if old != new:
            changes[channel] = {"from": old, "to": new}
    if changes or created:
        record_audit_event(
            session,
            action="notification_policy.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            outcome="success",
            request_id=request_id,
            details={"changes": changes, "first_saved": created},
        )
    return PolicyView(email_enabled, telegram_enabled, saved=True)


def list_installation_rules(session: Session) -> list[NotificationRule]:
    return list(
        session.execute(
            sa.select(NotificationRule)
            .where(NotificationRule.club_id.is_(None))
            .order_by(
                NotificationRule.event_type,
                NotificationRule.channel,
                NotificationRule.recipient_scope,
            )
        ).scalars()
    )


def set_rule_enabled(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    rule_id: uuid.UUID,
    is_enabled: bool,
    request_id: Optional[str] = None,
) -> NotificationRule:
    rule = session.execute(
        sa.select(NotificationRule)
        .where(NotificationRule.id == rule_id, NotificationRule.club_id.is_(None))
        .with_for_update()
    ).scalar_one_or_none()
    if rule is None:
        raise RuleNotFound()
    if rule.is_enabled != is_enabled:
        before = rule.is_enabled
        rule.is_enabled = is_enabled
        session.flush()
        record_audit_event(
            session,
            action="notification_rule.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            outcome="success",
            resource_type="notification_rule",
            resource_id=rule.id,
            request_id=request_id,
            details={"is_enabled": {"from": before, "to": is_enabled}},
        )
    return rule


def get_email_settings(session: Session) -> Optional[NotificationEmailSettings]:
    return session.get(NotificationEmailSettings, SINGLETON_ID)


def get_telegram_settings(session: Session) -> Optional[NotificationTelegramSettings]:
    return session.get(NotificationTelegramSettings, SINGLETON_ID)


def _audit_integration(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    channel: str,
    changed_fields: list[str],
    request_id: Optional[str],
) -> None:
    if not changed_fields:
        return
    record_audit_event(
        session,
        action="notification_integration.updated",
        actor_type="user",
        actor_user_id=actor_user_id,
        outcome="success",
        request_id=request_id,
        details={"channel": channel, "changed_fields": sorted(changed_fields)},
    )


def update_email_settings(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    values: dict[str, Any],
    request_id: Optional[str] = None,
) -> NotificationEmailSettings:
    """Replace every non-secret SMTP field with `values` (validated by the
    caller). The SMTP password is never read or written here."""
    _ensure_singleton(session, NotificationEmailSettings)
    row = _lock_singleton(session, NotificationEmailSettings)
    before: dict[str, Any] = {name: getattr(row, name) for name in EMAIL_FIELDS}
    for name in EMAIL_FIELDS:
        setattr(row, name, values[name])
    row.updated_by_user_id = actor_user_id
    session.flush()
    _audit_integration(
        session,
        actor_user_id=actor_user_id,
        channel=CHANNEL_EMAIL,
        changed_fields=[name for name in EMAIL_FIELDS if before[name] != values[name]],
        request_id=request_id,
    )
    return row


def update_telegram_settings(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    bot_username: Optional[str],
    request_id: Optional[str] = None,
) -> NotificationTelegramSettings:
    _ensure_singleton(session, NotificationTelegramSettings)
    row = _lock_singleton(session, NotificationTelegramSettings)
    before = row.bot_username
    row.bot_username = bot_username
    row.updated_by_user_id = actor_user_id
    session.flush()
    _audit_integration(
        session,
        actor_user_id=actor_user_id,
        channel=CHANNEL_TELEGRAM,
        changed_fields=["bot_username"] if before != bot_username else [],
        request_id=request_id,
    )
    return row


def _channel_of(secret_name: str) -> str:
    return CHANNEL_EMAIL if secret_name == SECRET_SMTP_PASSWORD else CHANNEL_TELEGRAM


def set_secret(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    name: str,
    value: str,
    request_id: Optional[str] = None,
) -> bool:
    """Encrypt and store `value` (set or replace). Returns whether a value
    was replaced. Raises EncryptionUnavailable (nothing stored)."""
    if name not in CANONICAL_SECRET_NAMES:
        raise ValueError("unknown secret")
    try:
        key_ring = KeyRing.from_env()
    except KeyRingUnavailable:
        raise EncryptionUnavailable() from None
    token = encrypt_secret(key_ring, name, value)
    replaced = session.execute(
        sa.select(IntegrationSecret.name).where(IntegrationSecret.name == name).with_for_update()
    ).scalar_one_or_none() is not None
    statement = pg_insert(IntegrationSecret).values(
        name=name, ciphertext=token, updated_by_user_id=actor_user_id
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "ciphertext": statement.excluded.ciphertext,
                "updated_by_user_id": statement.excluded.updated_by_user_id,
                "updated_at": sa.func.now(),
            },
        )
    )
    record_audit_event(
        session,
        action="notification_secret.set",
        actor_type="user",
        actor_user_id=actor_user_id,
        outcome="success",
        request_id=request_id,
        details={"setting": name, "channel": _channel_of(name), "replaced": replaced},
    )
    return replaced


def clear_secret(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    name: str,
    request_id: Optional[str] = None,
) -> bool:
    """Delete the stored secret. Returns whether one existed. Needs no key."""
    if name not in CANONICAL_SECRET_NAMES:
        raise ValueError("unknown secret")
    deleted = session.execute(
        sa.delete(IntegrationSecret).where(IntegrationSecret.name == name)
    ).rowcount
    existed = bool(deleted)
    record_audit_event(
        session,
        action="notification_secret.cleared",
        actor_type="user",
        actor_user_id=actor_user_id,
        outcome="success",
        request_id=request_id,
        details={"setting": name, "channel": _channel_of(name), "existed": existed},
    )
    return existed


__all__ = [
    "EMAIL_FIELDS",
    "EncryptionUnavailable",
    "RuleNotFound",
    "PolicyView",
    "get_policy",
    "update_policy",
    "list_installation_rules",
    "set_rule_enabled",
    "get_email_settings",
    "get_telegram_settings",
    "update_email_settings",
    "update_telegram_settings",
    "set_secret",
    "clear_secret",
]
