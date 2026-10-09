"""Business-event notification integration for Event lifecycle changes.

Issue #336 / ADR-0045 / docs/04-modules/notification-event-catalog.md.
The caller owns the business transaction; this module never commits.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authorization.service import can
from app.db.events import Event, EventParticipation
from app.db.identity import Person, User
from app.db.notifications import NotificationGlobalPolicy, NotificationRule
from app.events.authorization import build_event_resource_context
from app.notifications.engine import NotificationRequest, Recipient, plan_notifications
from app.notifications.ports import AdminPolicy
from app.notifications.vocabulary import CHANNEL_TELEGRAM
from app.notification_settings.vocabulary import SINGLETON_ID


@dataclass(frozen=True)
class _GlobalOnlyAdminPolicy:
    """Resolve the persisted global policy; absent row is fail-closed."""

    def global_channel_enabled(self, session: Session, *, channel: str) -> bool:
        row = session.get(NotificationGlobalPolicy, SINGLETON_ID)
        return bool(row and channel == CHANNEL_TELEGRAM and row.telegram_enabled)

    def club_channel_enabled(
        self, session: Session, *, club_id: uuid.UUID, channel: str
    ) -> bool:
        # ADR-0048 v1 has no club-level channel policy.
        return self.global_channel_enabled(session, channel=channel)


@dataclass(frozen=True)
class _EventPreferencePolicy:
    event_type: str
    mandatory: bool

    def allows(self, *, channel: str, stored_enabled: bool | None) -> bool:
        if channel != CHANNEL_TELEGRAM:
            return False
        if self.mandatory:
            return True
        # Explicit opt-in only; missing preferences fail closed.
        return stored_enabled is True


def _recipients(session: Session, event: Event) -> list[Recipient]:
    rows = session.execute(
        sa.select(User.id)
        .join(Person, Person.id == User.person_id)
        .join(EventParticipation, EventParticipation.person_id == Person.id)
        .where(
            EventParticipation.event_id == event.id,
            EventParticipation.registration_status == "registered",
            User.status == "active",
        )
        .order_by(User.id)
    ).scalars().all()
    return [
        Recipient(
            user_id=user_id,
            idempotency_key=f"{event.id}:{event.status}:{event.updated_at.isoformat()}:{user_id}",
        )
        for user_id in rows
    ]


def plan_event_lifecycle_notification(
    session: Session,
    *,
    event: Event,
    event_type: str,
    mandatory: bool,
) -> None:
    """Plan a Telegram message for registered participants in the open transaction.

    Rules/templates are deliberately required to exist in the DB; no rule
    or missing template means no delivery, as specified by the Engine.
    """
    recipients = _recipients(session, event)
    if not recipients:
        return

    def context_for(db: Session, user_id: uuid.UUID):
        return build_event_resource_context(
            db, event=event, user_id=user_id, permission_code="event.read"
        )

    from app.notifications.ports import PermissionRecipientAccess

    plan_notifications(
        session,
        NotificationRequest(
            event_type=event_type,
            subject_type="event",
            recipient_scope="registered_participants",
            recipients=recipients,
            channel_templates={"telegram": f"{event_type}.telegram.ru"},
            subject_id=event.id,
            club_id=event.club_id,
            priority=100 if mandatory else 0,
        ),
        admin_policy=_GlobalOnlyAdminPolicy(),
        recipient_access=PermissionRecipientAccess(
            permission_code="event.read", context_for=context_for
        ),
        preference_policy=_EventPreferencePolicy(
            event_type=event_type, mandatory=mandatory
        ),
    )
