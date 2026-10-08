"""Notification Center persistence (Issue #318, ADR-0045 §3).

Canonical sources: docs/03-architecture/adr/ADR-0045-notification-center-
and-communication-architecture.md §2/§3, docs/03-architecture/
database-schema.md §18, docs/04-modules/notifications-and-communications.md
§4 (Delivery statuses).

- `notification_templates`, `notification_rules`,
  `communication_preferences` — configuration the Notification Engine
  (#319) resolves in the ADR-0045 §2.4 order (Admin Policy -> Rule -> User
  Preference -> Delivery). No resolution logic lives here.
- `notifications` — the logical message to ONE recipient. Its
  `idempotency_key` is globally UNIQUE, so the same logical Notification
  can never exist twice; the key's composition is fixed by each business
  event's specification gate (ADR-0045 §5), not here.
- `notification_deliveries` — channel-specific delivery state; a
  Notification has zero or more Deliveries, at most one per
  (channel, destination). A Delivery stores an internal destination
  reference, never a raw email address or Telegram chat id.
- `telegram_destinations` — group/topic routing (ADR-0045 §2.7): the
  routing identity is `chat_id` + optional `message_thread_id`; the topic
  display name is presentation metadata only.

No column here holds a provider credential, bot token or raw
verification/reset/linking token (ADR-0045 §4); provider configuration
and secrets are a separate integration concern.

Shape mirrors the rest of app.db: plain FK columns, no ORM
`relationship()`, RESTRICT foreign keys (delivery history is never
cascaded away), closed vocabularies enforced by CHECK constraints.
"""

import uuid
from datetime import datetime, time
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.events.lifecycle import validate_timezone
from app.notifications.vocabulary import (
    CANONICAL_DELIVERY_STATUSES,
    CANONICAL_DESTINATION_TYPES,
    CANONICAL_NOTIFICATION_CHANNELS,
    CANONICAL_NOTIFICATION_STATUSES,
    CHANNEL_TELEGRAM,
    DELIVERY_DELIVERED,
    DELIVERY_PENDING,
    DESTINATION_TELEGRAM_DESTINATION,
    NOTIFICATION_PENDING,
)


def _in(values: frozenset[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(values))


_CHANNEL_VALUES = _in(CANONICAL_NOTIFICATION_CHANNELS)


def _created_at() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class NotificationTemplate(Base):
    """A channel/locale-specific message template (ADR-0045 §3)."""

    __tablename__ = "notification_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    locale: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    subject_template: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    body_template: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1, server_default=sa.text("1")
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        sa.UniqueConstraint("code", name="uq_notification_templates_code"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNEL_VALUES})", name="ck_notification_templates_channel_valid"
        ),
        sa.CheckConstraint("btrim(code) <> ''", name="ck_notification_templates_code_not_blank"),
        sa.CheckConstraint(
            "btrim(locale) <> ''", name="ck_notification_templates_locale_not_blank"
        ),
        sa.CheckConstraint(
            "btrim(body_template) <> ''", name="ck_notification_templates_body_not_blank"
        ),
        sa.CheckConstraint("version >= 1", name="ck_notification_templates_version_positive"),
    )


class NotificationRule(Base):
    """Administrative rule for one event type / channel / recipient scope
    (ADR-0045 §2.4). `club_id` NULL is the installation-wide rule.
    `scheduling` holds the rule's timing parameters, whose exact shape is
    fixed per event by its specification gate (ADR-0045 §5)."""

    __tablename__ = "notification_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    recipient_scope: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    scheduling: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        # One rule per (scope, event, channel, audience): policy resolution
        # must never see two competing rules. NULLS NOT DISTINCT so the
        # installation-wide (club_id NULL) rule is unique too.
        sa.UniqueConstraint(
            "club_id",
            "event_type",
            "channel",
            "recipient_scope",
            name="uq_notification_rules_club_event_channel_scope",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNEL_VALUES})", name="ck_notification_rules_channel_valid"
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> ''", name="ck_notification_rules_event_type_not_blank"
        ),
        sa.CheckConstraint(
            "btrim(recipient_scope) <> ''",
            name="ck_notification_rules_recipient_scope_not_blank",
        ),
        sa.CheckConstraint(
            "scheduling IS NULL OR jsonb_typeof(scheduling) = 'object'",
            name="ck_notification_rules_scheduling_is_object",
        ),
    )


class Notification(Base):
    """The logical message addressed to one recipient (ADR-0045 §2.1).
    Not a provider delivery attempt — see NotificationDelivery."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    subject_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    subject_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("notification_templates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Relative priority (larger = more urgent); its use is the Notification
    # Engine's/worker's concern.
    priority: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        default=NOTIFICATION_PENDING,
        server_default=NOTIFICATION_PENDING,
    )
    idempotency_key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        sa.UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_NOTIFICATION_STATUSES)})",
            name="ck_notifications_status_valid",
        ),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> ''", name="ck_notifications_idempotency_key_not_blank"
        ),
        sa.CheckConstraint("btrim(event_type) <> ''", name="ck_notifications_event_type_not_blank"),
        sa.CheckConstraint(
            "btrim(subject_type) <> ''", name="ck_notifications_subject_type_not_blank"
        ),
        # The recipient's own notification list, newest first.
        sa.Index(
            "ix_notifications_recipient_user_id_created_at", "recipient_user_id", "created_at"
        ),
        # Club-scoped administrative delivery journal.
        sa.Index("ix_notifications_club_id_created_at", "club_id", "created_at"),
    )


class NotificationDelivery(Base):
    """One channel-specific delivery of a Notification (ADR-0045 §2.2,
    §2.9). Owns attempts, provider message id and the last SAFE error —
    never a credential or a message body."""

    __tablename__ = "notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("notifications.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    destination_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # Internal id of the `destination_type` entity (polymorphic: no FK).
    destination_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=DELIVERY_PENDING, server_default=DELIVERY_PENDING
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    first_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # ADR-0045 §2.9: "first/last attempt timestamps".
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    provider_message_id: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(sa.String(1024), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        # Stable delivery identity per Notification (ADR-0046 §5.5): a
        # re-processed Notification cannot fan out twice to one destination.
        sa.UniqueConstraint(
            "notification_id",
            "channel",
            "destination_type",
            "destination_id",
            name="uq_notification_deliveries_notification_channel_destination",
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNEL_VALUES})", name="ck_notification_deliveries_channel_valid"
        ),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_DELIVERY_STATUSES)})",
            name="ck_notification_deliveries_status_valid",
        ),
        sa.CheckConstraint(
            f"destination_type IN ({_in(CANONICAL_DESTINATION_TYPES)})",
            name="ck_notification_deliveries_destination_type_valid",
        ),
        sa.CheckConstraint(
            f"destination_type <> '{DESTINATION_TELEGRAM_DESTINATION}' "
            f"OR channel = '{CHANNEL_TELEGRAM}'",
            name="ck_notification_deliveries_telegram_destination_channel",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_notification_deliveries_attempts_non_negative"
        ),
        sa.CheckConstraint(
            "(attempts = 0) = (first_attempt_at IS NULL)",
            name="ck_notification_deliveries_first_attempt_iff_attempted",
        ),
        sa.CheckConstraint(
            "(first_attempt_at IS NULL) = (last_attempt_at IS NULL)",
            name="ck_notification_deliveries_attempt_timestamps_together",
        ),
        sa.CheckConstraint(
            "last_attempt_at IS NULL OR last_attempt_at >= first_attempt_at",
            name="ck_notification_deliveries_attempt_timestamps_ordered",
        ),
        sa.CheckConstraint(
            f"(status = '{DELIVERY_DELIVERED}') = (delivered_at IS NOT NULL)",
            name="ck_notification_deliveries_delivered_at_iff_delivered",
        ),
    )


class CommunicationPreference(Base):
    """A user's own channel/notification-type preference (ADR-0045 §2.8).
    Subordinate to effective administrative policy — it can opt out, never
    enable what Admin Policy disables (resolution is #319).

    Quiet hours are optional; when present all three fields are set and
    `quiet_hours_start`/`quiet_hours_end` are local times in
    `quiet_hours_timezone` (a window may cross midnight)."""

    __tablename__ = "communication_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    notification_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    quiet_hours_start: Mapped[Optional[time]] = mapped_column(sa.Time, nullable=True)
    quiet_hours_end: Mapped[Optional[time]] = mapped_column(sa.Time, nullable=True)
    quiet_hours_timezone: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        sa.UniqueConstraint(
            "user_id",
            "channel",
            "notification_type",
            name="uq_communication_preferences_user_channel_type",
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNEL_VALUES})", name="ck_communication_preferences_channel_valid"
        ),
        sa.CheckConstraint(
            "btrim(notification_type) <> ''",
            name="ck_communication_preferences_notification_type_not_blank",
        ),
        sa.CheckConstraint(
            "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL) "
            "AND (quiet_hours_start IS NULL) = (quiet_hours_timezone IS NULL)",
            name="ck_communication_preferences_quiet_hours_complete",
        ),
        sa.CheckConstraint(
            "quiet_hours_start IS NULL OR quiet_hours_start <> quiet_hours_end",
            name="ck_communication_preferences_quiet_hours_non_empty",
        ),
    )

    @validates("quiet_hours_timezone")
    def _validate_quiet_hours_timezone(self, key: str, value: Optional[str]) -> Optional[str]:
        # IANA validity cannot be a CHECK constraint (same reasoning as
        # app.db.events.Event.timezone).
        if value is not None:
            validate_timezone(value)
        return value


class TelegramDestination(Base):
    """Telegram group/topic routing destination (ADR-0045 §2.7).

    Routing identity is (`chat_id`, `message_thread_id`): `chat_id` is the
    Telegram chat/group id (negative for groups, beyond 32 bits — BIGINT);
    `message_thread_id` identifies a forum Topic and is NULL for the chat
    itself. `topic_name` is presentation metadata only and never used for
    routing. `notification_scope` is the destination's notification
    scope/configuration, interpreted by the Notification Engine. No bot
    token or other credential is stored here."""

    __tablename__ = "telegram_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    chat_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    message_thread_id: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    topic_name: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    notification_scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        # One destination per routing identity; NULLS NOT DISTINCT so the
        # chat itself (no topic) is unique too.
        sa.UniqueConstraint(
            "chat_id",
            "message_thread_id",
            name="uq_telegram_destinations_chat_id_message_thread_id",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_telegram_destinations_name_not_blank"),
        sa.CheckConstraint("chat_id <> 0", name="ck_telegram_destinations_chat_id_non_zero"),
        sa.CheckConstraint(
            "message_thread_id IS NULL OR message_thread_id > 0",
            name="ck_telegram_destinations_message_thread_id_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(notification_scope) = 'object'",
            name="ck_telegram_destinations_notification_scope_is_object",
        ),
    )


__all__ = [
    "NotificationTemplate",
    "NotificationRule",
    "Notification",
    "NotificationDelivery",
    "CommunicationPreference",
    "TelegramDestination",
]
