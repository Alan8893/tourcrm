"""Security emails of the Notification Center (Issue #342, ADR-0045 §2.10,
auth-api.md §13, PO decisions of 2026-10-12).

A security email (today only the password-reset code) is a personal email
that an authentication flow must be able to send regardless of notification
rules and of the recipient's notification preferences — a user cannot opt
out of their own recovery code. It still goes through the same
Notification -> Delivery -> `notification.delivery` outbox job -> worker ->
Email Adapter path as every other notification; only the Engine's rule,
preference, audience and reachability resolution is not applied.

The Global Admin Policy stays absolute: with email switched OFF nothing is
created here, and a Delivery already queued is paused by the worker
(app.notifications.delivery) like any other.

`render_context` stays empty: the Notification names its subject
(`subject_type`/`subject_id`) and the one-time secret is obtained only at
send time by the Email Adapter's DeliverySecretResolver. Nothing here
commits; the caller commits together with its own mutation.
"""

import uuid
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.notifications import NotificationTemplate
from app.notifications.ports import AdminPolicy
from app.notifications.repository import add_delivery, create_notification
from app.notifications.vocabulary import (
    CHANNEL_EMAIL,
    DESTINATION_USER,
    NOTIFICATION_DELIVERY_JOB_TYPE,
    notification_delivery_deduplication_key,
)
from app.outbox.service import enqueue_outbox_job

SecurityEmailOutcome = Literal["queued", "channel_disabled", "template_unavailable"]


@dataclass(frozen=True)
class SecurityEmailResult:
    outcome: SecurityEmailOutcome
    delivery_id: Optional[uuid.UUID] = None


def security_email_available(
    session: Session, *, template_code: str, admin_policy: AdminPolicy
) -> SecurityEmailResult:
    """Whether `queue_security_email` would queue now (no write)."""
    if not admin_policy.global_channel_enabled(session, channel=CHANNEL_EMAIL):
        return SecurityEmailResult("channel_disabled")
    if _template_id(session, template_code) is None:
        return SecurityEmailResult("template_unavailable")
    return SecurityEmailResult("queued")


def _template_id(session: Session, template_code: str) -> Optional[uuid.UUID]:
    return session.execute(
        select(NotificationTemplate.id).where(
            NotificationTemplate.code == template_code,
            NotificationTemplate.channel == CHANNEL_EMAIL,
            NotificationTemplate.is_active.is_(True),
        )
    ).scalar_one_or_none()


def queue_security_email(
    session: Session,
    *,
    event_type: str,
    subject_type: str,
    subject_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
    template_code: str,
    admin_policy: AdminPolicy,
) -> SecurityEmailResult:
    """Create the Notification, its one email Delivery and its outbox job in
    the caller's open transaction. Idempotent per (event_type, subject_id).
    Writes nothing when the Global email switch is OFF or the template is
    missing/inactive."""
    if not admin_policy.global_channel_enabled(session, channel=CHANNEL_EMAIL):
        return SecurityEmailResult("channel_disabled")
    template_id = _template_id(session, template_code)
    if template_id is None:
        return SecurityEmailResult("template_unavailable")

    notification, _ = create_notification(
        session,
        idempotency_key=f"{event_type}:{subject_id}",
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        recipient_user_id=recipient_user_id,
        template_id=template_id,
    )
    delivery, _ = add_delivery(
        session,
        notification=notification,
        channel=CHANNEL_EMAIL,
        destination_type=DESTINATION_USER,
        destination_id=recipient_user_id,
    )
    enqueue_outbox_job(
        session,
        job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
        payload={"delivery_id": str(delivery.id)},
        deduplication_key=notification_delivery_deduplication_key(delivery.id),
    )
    return SecurityEmailResult("queued", delivery.id)


__all__ = [
    "SecurityEmailOutcome",
    "SecurityEmailResult",
    "security_email_available",
    "queue_security_email",
]
