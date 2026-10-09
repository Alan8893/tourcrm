"""The Notification Engine application boundary (Issue #319, ADR-0045
§2.4/§2.10).

Business modules hand a business event to `plan_notifications` and never
call a provider. The Engine resolves, in ADR-0045 §2.4 order:

    Global Admin Policy -> Club Admin Policy
      -> Notification Rule (installation-wide, then club-specific)
      -> Audience (RecipientAccess) -> User Preference
      -> Template -> Notification -> Delivery -> outbox job

Every narrower level can only restrict a broader one (restrict-only):

- Admin Policy: Global OFF is absolute; Club OFF cannot be overridden by a
  rule or a preference. No connected Admin Policy source
  (`admin_policy=None`) fails closed.
- Notification Rule: the installation-wide rule (`club_id` NULL) and the
  club's own rule for (event_type, channel, recipient_scope) both apply;
  every applicable rule must be enabled, so a club rule can never re-enable
  what the installation-wide rule disables. No applicable rule means no
  Delivery — there is no implicit "allowed" default.
- User Preference: the stored (channel, `event_type`) preference is handed
  to the event's PreferencePolicy (its specification gate); the Engine has
  no preference default of its own.

A Notification is created only together with at least one eligible
Delivery (ADR-0045 §2.10): zero eligible Deliveries means no Notification,
no Delivery and no outbox job. Each Delivery gets exactly one
`notification.delivery` outbox job.

Who the candidate recipients are, which channels/templates an event uses,
its `recipient_scope`, timing and idempotency key are the business event's
specification gate (ADR-0045 §5) and arrive as input — the Engine defines
none of them.

Transaction semantics mirror app.notifications.repository: nothing here
commits or rolls back; the caller commits the business mutation, the
Notifications, Deliveries and outbox jobs together. Every check runs
before the first write, so a typed error leaves the caller's transaction
untouched.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.notifications import (
    CommunicationPreference,
    Notification,
    NotificationDelivery,
    NotificationRule,
    NotificationTemplate,
)
from app.notifications.ports import AdminPolicy, PreferencePolicy, RecipientAccess
from app.notifications.repository import (
    InvalidNotificationChannelError,
    add_delivery,
    create_notification,
    get_notification_by_idempotency_key,
    list_deliveries,
)
from app.notifications.vocabulary import (
    CANONICAL_NOTIFICATION_CHANNELS,
    DESTINATION_USER,
    NOTIFICATION_DELIVERY_JOB_TYPE,
    notification_delivery_deduplication_key,
)
from app.outbox.service import enqueue_outbox_job

# Why a channel or recipient produced no Delivery.
EXCLUDED_ADMIN_POLICY_UNAVAILABLE = "admin_policy_unavailable"
EXCLUDED_GLOBAL_POLICY_DISABLED = "global_policy_disabled"
EXCLUDED_CLUB_POLICY_DISABLED = "club_policy_disabled"
EXCLUDED_NO_APPLICABLE_RULE = "no_applicable_rule"
EXCLUDED_RULE_DISABLED = "rule_disabled"
EXCLUDED_TEMPLATE_UNAVAILABLE = "template_unavailable"
EXCLUDED_RECIPIENT_NOT_AUTHORIZED = "recipient_not_authorized"
EXCLUDED_PREFERENCE_DISABLED = "preference_disabled"
# Recipient-level: every candidate channel was excluded at event level
# (see NotificationPlanOutcome.excluded_channels for each channel's reason).
EXCLUDED_NO_ELIGIBLE_CHANNEL = "no_eligible_channel"


class NotificationEngineError(ValueError):
    """Base class for the Engine's typed input/contract failures. Raised
    before anything is written."""


class InvalidNotificationRequestError(NotificationEngineError):
    """The request is malformed (blank fields, duplicate recipients or
    idempotency keys, no channel)."""


class IdempotencyKeyConflictError(NotificationEngineError):
    """An existing Notification with this idempotency key belongs to a
    different recipient or event — the key does not identify this
    business event/recipient."""


class MultiChannelTemplateNotRepresentableError(NotificationEngineError):
    """More than one channel is eligible for one recipient, but a
    Notification stores a single `template_id` and the existing template
    model is channel-specific, so the per-channel templates cannot be
    persisted (recorded as a GAP of #319; the model is not extended here)."""


@dataclass(frozen=True)
class Recipient:
    """A candidate recipient and the caller's canonical idempotency key for
    this business event + recipient."""

    user_id: uuid.UUID
    idempotency_key: str


@dataclass(frozen=True)
class NotificationRequest:
    """One business event handed to the Engine.

    `channel_templates` maps each candidate channel to the code of its
    template (both from the event's specification gate).
    """

    event_type: str
    subject_type: str
    recipient_scope: str
    recipients: Sequence[Recipient]
    channel_templates: Mapping[str, str]
    render_context: Mapping[str, str] = field(default_factory=dict)
    subject_id: Optional[uuid.UUID] = None
    club_id: Optional[uuid.UUID] = None
    scheduled_at: Optional[datetime] = None
    priority: int = 0


@dataclass(frozen=True)
class RecipientOutcome:
    user_id: uuid.UUID
    notification: Optional[Notification]
    deliveries: tuple[NotificationDelivery, ...]
    # True only when this call created the Notification.
    created: bool
    # Recipient-level reason when no Notification exists.
    excluded_reason: Optional[str] = None
    # Channels that passed the event-level policy but not this recipient's.
    excluded_channels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationPlanOutcome:
    # Event-level reason per excluded channel.
    excluded_channels: Mapping[str, str]
    recipients: tuple[RecipientOutcome, ...]


@dataclass(frozen=True)
class _RecipientPlan:
    recipient: Recipient
    channels: tuple[str, ...]
    excluded_channels: Mapping[str, str]


def _validate(request: NotificationRequest) -> None:
    for name in ("event_type", "subject_type", "recipient_scope"):
        if not getattr(request, name).strip():
            raise InvalidNotificationRequestError(f"{name} must not be blank")
    if not request.channel_templates:
        raise InvalidNotificationRequestError("at least one candidate channel is required")
    for channel, code in request.channel_templates.items():
        if channel not in CANONICAL_NOTIFICATION_CHANNELS:
            raise InvalidNotificationChannelError(channel)
        if not code.strip():
            raise InvalidNotificationRequestError(f"template code for {channel!r} is blank")
    user_ids = [r.user_id for r in request.recipients]
    keys = [r.idempotency_key for r in request.recipients]
    if len(set(user_ids)) != len(user_ids):
        raise InvalidNotificationRequestError("a recipient is listed more than once")
    if len(set(keys)) != len(keys):
        raise InvalidNotificationRequestError("idempotency keys must be unique per recipient")
    if any(not key.strip() for key in keys):
        raise InvalidNotificationRequestError("idempotency_key must not be blank")


def _rule_exclusion(
    session: Session, request: NotificationRequest, channel: str
) -> Optional[str]:
    """Installation-wide rule -> club-specific rule, restrict-only."""
    club_condition: sa.ColumnElement[bool] = NotificationRule.club_id.is_(None)
    if request.club_id is not None:
        club_condition = sa.or_(club_condition, NotificationRule.club_id == request.club_id)
    rules = session.execute(
        select(NotificationRule).where(
            NotificationRule.event_type == request.event_type,
            NotificationRule.channel == channel,
            NotificationRule.recipient_scope == request.recipient_scope,
            club_condition,
        )
    ).scalars().all()
    if not rules:
        return EXCLUDED_NO_APPLICABLE_RULE
    if not all(rule.is_enabled for rule in rules):
        return EXCLUDED_RULE_DISABLED
    return None


def _resolve_template(
    session: Session, *, code: str, channel: str
) -> Optional[NotificationTemplate]:
    template = session.execute(
        select(NotificationTemplate).where(NotificationTemplate.code == code)
    ).scalar_one_or_none()
    if template is None or not template.is_active or template.channel != channel:
        return None
    return template


def _event_level_channels(
    session: Session, request: NotificationRequest, admin_policy: AdminPolicy
) -> tuple[dict[str, NotificationTemplate], dict[str, str]]:
    """Admin Policy -> Notification Rule -> Template, per channel, in a
    deterministic channel order."""
    eligible: dict[str, NotificationTemplate] = {}
    excluded: dict[str, str] = {}
    for channel in sorted(request.channel_templates):
        if not admin_policy.global_channel_enabled(session, channel=channel):
            excluded[channel] = EXCLUDED_GLOBAL_POLICY_DISABLED
            continue
        if request.club_id is not None and not admin_policy.club_channel_enabled(
            session, club_id=request.club_id, channel=channel
        ):
            excluded[channel] = EXCLUDED_CLUB_POLICY_DISABLED
            continue
        rule_exclusion = _rule_exclusion(session, request, channel)
        if rule_exclusion is not None:
            excluded[channel] = rule_exclusion
            continue
        template = _resolve_template(
            session, code=request.channel_templates[channel], channel=channel
        )
        if template is None:
            excluded[channel] = EXCLUDED_TEMPLATE_UNAVAILABLE
            continue
        eligible[channel] = template
    return eligible, excluded


def _stored_preference(
    session: Session, *, user_id: uuid.UUID, channel: str, event_type: str
) -> Optional[bool]:
    # ADR-0045 §2.8: a preference's notification type is the event_type.
    return session.execute(
        select(CommunicationPreference.enabled).where(
            CommunicationPreference.user_id == user_id,
            CommunicationPreference.channel == channel,
            CommunicationPreference.notification_type == event_type,
        )
    ).scalar_one_or_none()


def _existing_outcome(
    session: Session, request: NotificationRequest, recipient: Recipient
) -> Optional[RecipientOutcome]:
    existing = get_notification_by_idempotency_key(session, recipient.idempotency_key)
    if existing is None:
        return None
    if existing.recipient_user_id != recipient.user_id or existing.event_type != request.event_type:
        raise IdempotencyKeyConflictError(
            "the idempotency key already identifies another recipient or event"
        )
    return RecipientOutcome(
        user_id=recipient.user_id,
        notification=existing,
        deliveries=tuple(list_deliveries(session, existing.id)),
        created=False,
    )


def _excluded(recipient: Recipient, reason: str) -> RecipientOutcome:
    return RecipientOutcome(
        user_id=recipient.user_id,
        notification=None,
        deliveries=(),
        created=False,
        excluded_reason=reason,
    )


def _write(
    session: Session,
    request: NotificationRequest,
    plan: _RecipientPlan,
    templates: Mapping[str, NotificationTemplate],
) -> RecipientOutcome:
    (channel,) = plan.channels
    notification, created = create_notification(
        session,
        idempotency_key=plan.recipient.idempotency_key,
        event_type=request.event_type,
        subject_type=request.subject_type,
        subject_id=request.subject_id,
        recipient_user_id=plan.recipient.user_id,
        club_id=request.club_id,
        template_id=templates[channel].id,
        render_context=dict(request.render_context),
        priority=request.priority,
        scheduled_at=request.scheduled_at,
    )
    if not created:
        # A concurrent invocation committed this Notification first; its
        # transaction already wrote the Deliveries and outbox jobs.
        return RecipientOutcome(
            user_id=plan.recipient.user_id,
            notification=notification,
            deliveries=tuple(list_deliveries(session, notification.id)),
            created=False,
        )

    deliveries = []
    for delivery_channel in plan.channels:
        delivery, _ = add_delivery(
            session,
            notification=notification,
            channel=delivery_channel,
            destination_type=DESTINATION_USER,
            destination_id=plan.recipient.user_id,
        )
        enqueue_outbox_job(
            session,
            job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
            payload={"delivery_id": str(delivery.id)},
            deduplication_key=notification_delivery_deduplication_key(delivery.id),
            available_at=request.scheduled_at,
        )
        deliveries.append(delivery)
    return RecipientOutcome(
        user_id=plan.recipient.user_id,
        notification=notification,
        deliveries=tuple(deliveries),
        created=True,
        excluded_channels=plan.excluded_channels,
    )


def plan_notifications(
    session: Session,
    request: NotificationRequest,
    *,
    admin_policy: Optional[AdminPolicy],
    recipient_access: RecipientAccess,
    preference_policy: PreferencePolicy,
) -> NotificationPlanOutcome:
    """Plan and persist the Notifications/Deliveries/outbox jobs of one
    business event inside the caller's open transaction (never commits).

    Idempotent per recipient: when a Notification with the recipient's
    idempotency key already exists it is returned unchanged
    (`created=False`) and nothing is written for that recipient.

    Raises NotificationEngineError subclasses (or
    InvalidNotificationChannelError) before any write.
    """
    _validate(request)

    outcomes: dict[uuid.UUID, RecipientOutcome] = {}
    pending: list[Recipient] = []
    for recipient in request.recipients:
        existing = _existing_outcome(session, request, recipient)
        if existing is not None:
            outcomes[recipient.user_id] = existing
        else:
            pending.append(recipient)

    if admin_policy is None:
        for recipient in pending:
            outcomes[recipient.user_id] = _excluded(recipient, EXCLUDED_ADMIN_POLICY_UNAVAILABLE)
        excluded_all = {c: EXCLUDED_ADMIN_POLICY_UNAVAILABLE for c in request.channel_templates}
        return _result(request, outcomes, excluded_all)

    templates, event_excluded = _event_level_channels(session, request, admin_policy)

    plans: list[_RecipientPlan] = []
    for recipient in pending:
        if not templates:
            outcomes[recipient.user_id] = _excluded(recipient, EXCLUDED_NO_ELIGIBLE_CHANNEL)
            continue
        if not recipient_access.allows(session, user_id=recipient.user_id):
            outcomes[recipient.user_id] = _excluded(recipient, EXCLUDED_RECIPIENT_NOT_AUTHORIZED)
            continue
        channels: list[str] = []
        recipient_excluded: dict[str, str] = {}
        for channel in templates:
            stored = _stored_preference(
                session,
                user_id=recipient.user_id,
                channel=channel,
                event_type=request.event_type,
            )
            if preference_policy.allows(channel=channel, stored_enabled=stored):
                channels.append(channel)
            else:
                recipient_excluded[channel] = EXCLUDED_PREFERENCE_DISABLED
        if not channels:
            outcomes[recipient.user_id] = _excluded(recipient, EXCLUDED_PREFERENCE_DISABLED)
            continue
        if len(channels) > 1:
            raise MultiChannelTemplateNotRepresentableError(
                f"channels {channels} are all eligible for one recipient"
            )
        plans.append(_RecipientPlan(recipient, tuple(channels), recipient_excluded))

    for plan in plans:
        outcomes[plan.recipient.user_id] = _write(session, request, plan, templates)

    return _result(request, outcomes, event_excluded)


def _result(
    request: NotificationRequest,
    outcomes: Mapping[uuid.UUID, RecipientOutcome],
    excluded_channels: Mapping[str, str],
) -> NotificationPlanOutcome:
    return NotificationPlanOutcome(
        excluded_channels=dict(excluded_channels),
        recipients=tuple(outcomes[r.user_id] for r in request.recipients),
    )


__all__ = [
    "EXCLUDED_ADMIN_POLICY_UNAVAILABLE",
    "EXCLUDED_GLOBAL_POLICY_DISABLED",
    "EXCLUDED_CLUB_POLICY_DISABLED",
    "EXCLUDED_NO_APPLICABLE_RULE",
    "EXCLUDED_RULE_DISABLED",
    "EXCLUDED_TEMPLATE_UNAVAILABLE",
    "EXCLUDED_RECIPIENT_NOT_AUTHORIZED",
    "EXCLUDED_PREFERENCE_DISABLED",
    "EXCLUDED_NO_ELIGIBLE_CHANNEL",
    "NotificationEngineError",
    "InvalidNotificationRequestError",
    "IdempotencyKeyConflictError",
    "MultiChannelTemplateNotRepresentableError",
    "Recipient",
    "NotificationRequest",
    "RecipientOutcome",
    "NotificationPlanOutcome",
    "plan_notifications",
]
