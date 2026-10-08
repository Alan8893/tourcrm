"""Notification persistence contracts for the Notification Engine
(Issue #318; the Engine and its policy resolution are #319).

Transaction semantics mirror app.audit.service and app.outbox.service:
nothing here commits or rolls back. The Engine runs

    ... business mutation ...
    notification, created = create_notification(session, ...)
    if created:
        delivery, _ = add_delivery(session, notification=notification, ...)
        enqueue_outbox_job(session, job_type=..., payload={...})
    session.commit()

so the business mutation, the Notification (+ Deliveries) and the outbox
record commit — or roll back — together (ADR-0045 §2.3). Delivery
outcomes are written later by the worker in its own transactions, so a
failed delivery never touches the business transaction.

Idempotency uses `INSERT ... ON CONFLICT DO NOTHING` on the UNIQUE
constraints: a duplicate is reported as `created=False` with the existing
row, and the caller's transaction stays usable (a raised constraint
violation would abort the whole PostgreSQL transaction, business mutation
included). Which fields form an `idempotency_key` is fixed by each
business event's specification gate (ADR-0045 §5), never here.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.notifications import Notification, NotificationDelivery
from app.notifications.vocabulary import (
    CANONICAL_DESTINATION_TYPES,
    CANONICAL_NOTIFICATION_CHANNELS,
    CHANNEL_TELEGRAM,
    DESTINATION_TELEGRAM_DESTINATION,
)


class NotificationPersistenceError(ValueError):
    """Base class for this module's typed input-validation failures."""


class InvalidNotificationChannelError(NotificationPersistenceError):
    def __init__(self, channel: str) -> None:
        super().__init__(f"{channel!r} is not a canonical notification channel")
        self.channel = channel


class InvalidDeliveryDestinationError(NotificationPersistenceError):
    """Unknown `destination_type`, or a Telegram destination on a
    non-Telegram channel."""


class BlankNotificationFieldError(NotificationPersistenceError):
    def __init__(self, field: str) -> None:
        super().__init__(f"{field} must not be blank")
        self.field = field


def _require_not_blank(**fields: str) -> None:
    for name, value in fields.items():
        if not value.strip():
            raise BlankNotificationFieldError(name)


def get_notification_by_idempotency_key(
    session: Session, idempotency_key: str
) -> Optional[Notification]:
    return session.execute(
        select(Notification).where(Notification.idempotency_key == idempotency_key)
    ).scalar_one_or_none()


def create_notification(
    session: Session,
    *,
    idempotency_key: str,
    event_type: str,
    subject_type: str,
    recipient_user_id: uuid.UUID,
    subject_id: Optional[uuid.UUID] = None,
    club_id: Optional[uuid.UUID] = None,
    template_id: Optional[uuid.UUID] = None,
    priority: int = 0,
    scheduled_at: Optional[datetime] = None,
) -> tuple[Notification, bool]:
    """Create one logical Notification (initial status `pending`) in the caller's open
    transaction, idempotently on `idempotency_key`.

    Returns `(notification, created)`; when a Notification with the same
    key already exists nothing is written and it is returned with
    `created=False`.
    """
    _require_not_blank(
        idempotency_key=idempotency_key, event_type=event_type, subject_type=subject_type
    )

    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "recipient_user_id": recipient_user_id,
        "club_id": club_id,
        "template_id": template_id,
        "priority": priority,
        "scheduled_at": scheduled_at,
    }
    # Pending ORM objects (the business mutation, the recipient...) must
    # reach the database before this Core-level INSERT references them.
    session.flush()
    inserted_id = session.execute(
        insert(Notification)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Notification.id)
    ).scalar_one_or_none()

    if inserted_id is not None:
        notification = session.get(Notification, inserted_id)
        assert notification is not None
        return notification, True

    existing = get_notification_by_idempotency_key(session, idempotency_key)
    assert existing is not None
    return existing, False


def add_delivery(
    session: Session,
    *,
    notification: Notification,
    channel: str,
    destination_type: str,
    destination_id: uuid.UUID,
) -> tuple[NotificationDelivery, bool]:
    """Create one `pending` channel Delivery of `notification` in the
    caller's open transaction, idempotently on
    (notification, channel, destination_type, destination_id).

    Returns `(delivery, created)`.
    """
    if channel not in CANONICAL_NOTIFICATION_CHANNELS:
        raise InvalidNotificationChannelError(channel)
    if destination_type not in CANONICAL_DESTINATION_TYPES:
        raise InvalidDeliveryDestinationError(
            f"{destination_type!r} is not a canonical destination type"
        )
    if destination_type == DESTINATION_TELEGRAM_DESTINATION and channel != CHANNEL_TELEGRAM:
        raise InvalidDeliveryDestinationError(
            "a telegram_destination can only be used by the telegram channel"
        )

    identity = {
        "notification_id": notification.id,
        "channel": channel,
        "destination_type": destination_type,
        "destination_id": destination_id,
    }
    session.flush()
    inserted_id = session.execute(
        insert(NotificationDelivery)
        .values(id=uuid.uuid4(), **identity)
        .on_conflict_do_nothing(index_elements=list(identity))
        .returning(NotificationDelivery.id)
    ).scalar_one_or_none()

    if inserted_id is not None:
        delivery = session.get(NotificationDelivery, inserted_id)
        assert delivery is not None
        return delivery, True

    existing = session.execute(
        select(NotificationDelivery).filter_by(**identity)
    ).scalar_one()
    return existing, False


def list_deliveries(session: Session, notification_id: uuid.UUID) -> list[NotificationDelivery]:
    return list(
        session.execute(
            select(NotificationDelivery)
            .where(NotificationDelivery.notification_id == notification_id)
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        ).scalars()
    )


__all__ = [
    "NotificationPersistenceError",
    "InvalidNotificationChannelError",
    "InvalidDeliveryDestinationError",
    "BlankNotificationFieldError",
    "get_notification_by_idempotency_key",
    "create_notification",
    "add_delivery",
    "list_deliveries",
]
