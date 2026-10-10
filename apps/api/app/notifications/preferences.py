"""Personal notification preferences (Issue #336, ADR-0049 §2.1/§2.2).

Two independent stored levels, both for the personal destination
(`destination_type = user`) only:

- the master switch per channel (`communication_channel_preferences`) —
  "personal Telegram messages";
- the per-event preference (`communication_preferences`, the canonical
  `event_type`, ADR-0045 §2.8).

Toggling the master switch never rewrites a per-event row, so turning it
back on restores the user's earlier choices. A group/topic route never
reads either level.

Effective personal policy (the event's PreferencePolicy):

- optional event: master ON **and** event ON; a missing value is OFF;
- mandatory event (`event.cancelled`, `event.rescheduled`): always allowed
  at this level. Global Admin Policy OFF and an administrator-disabled rule
  are checked before this level by the Engine and still block it.

Reachability: a personal Telegram delivery needs the recipient's active,
verified linked identity (ADR-0047 §4); without it no Delivery is planned,
mandatory or not.

Nothing here commits: the API layer owns the transaction.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.notifications import CommunicationChannelPreference, CommunicationPreference
from app.db.telegram import TelegramIdentity
from app.notifications.catalog import (
    CATALOG,
    mandatory_event_types,
    personal_preference_event_types,
)
from app.notifications.vocabulary import CHANNEL_TELEGRAM, DESTINATION_USER
from app.telegram.vocabulary import IDENTITY_ACTIVE


class MandatoryPreferencePolicy:
    """Catalog §5: the personal opt-out does not apply."""

    def allows(
        self, *, channel: str, stored_enabled: Optional[bool], master_enabled: Optional[bool]
    ) -> bool:
        return True


class PersonalOptInPreferencePolicy:
    """ADR-0049 §2.1: master switch and event preference must both be ON;
    a missing value is OFF (fail closed)."""

    def allows(
        self, *, channel: str, stored_enabled: Optional[bool], master_enabled: Optional[bool]
    ) -> bool:
        return master_enabled is True and stored_enabled is True


class TelegramIdentityReachability:
    """Personal Telegram delivery needs an active linked identity. No other
    channel is an active business channel in v1 (catalog §1.2), so any
    other channel is unreachable here (fail closed)."""

    def reachable(self, session: Session, *, user_id: uuid.UUID, channel: str) -> bool:
        if channel != CHANNEL_TELEGRAM:
            return False
        return (
            session.execute(
                sa.select(TelegramIdentity.id).where(
                    TelegramIdentity.user_id == user_id,
                    TelegramIdentity.status == IDENTITY_ACTIVE,
                )
            ).first()
            is not None
        )


class UnknownPreferenceEventTypeError(ValueError):
    """Not a personally switchable catalog key (unknown, mandatory or
    blocked)."""

    def __init__(self, event_type: str) -> None:
        super().__init__(f"{event_type!r} is not a personally switchable notification type")
        self.event_type = event_type


@dataclass(frozen=True)
class EventPreference:
    event_type: str
    mandatory: bool
    # Mandatory types are always effective at the personal level.
    enabled: bool


@dataclass(frozen=True)
class PersonalPreferences:
    channel: str
    personal_enabled: bool
    events: tuple[EventPreference, ...]


def get_personal_preferences(
    session: Session, *, user_id: uuid.UUID, channel: str = CHANNEL_TELEGRAM
) -> PersonalPreferences:
    master = session.execute(
        sa.select(CommunicationChannelPreference.enabled).where(
            CommunicationChannelPreference.user_id == user_id,
            CommunicationChannelPreference.channel == channel,
            CommunicationChannelPreference.destination_type == DESTINATION_USER,
        )
    ).scalar_one_or_none()
    stored = dict(
        session.execute(
            sa.select(CommunicationPreference.notification_type, CommunicationPreference.enabled)
            .where(
                CommunicationPreference.user_id == user_id,
                CommunicationPreference.channel == channel,
                CommunicationPreference.destination_type == DESTINATION_USER,
            )
        ).tuples().all()
    )
    events = [
        EventPreference(event_type=key, mandatory=False, enabled=stored.get(key) is True)
        for key in personal_preference_event_types()
    ] + [
        EventPreference(event_type=key, mandatory=True, enabled=True)
        for key in mandatory_event_types()
    ]
    order = list(CATALOG)
    events.sort(key=lambda item: order.index(item.event_type))
    return PersonalPreferences(
        channel=channel, personal_enabled=master is True, events=tuple(events)
    )


def set_personal_preferences(
    session: Session,
    *,
    user_id: uuid.UUID,
    personal_enabled: Optional[bool] = None,
    events: Optional[Mapping[str, bool]] = None,
    channel: str = CHANNEL_TELEGRAM,
) -> None:
    """Upsert the master switch and/or per-event preferences given; any
    value not given is left as stored. Validates every key before writing,
    so an invalid request changes nothing."""
    switchable = set(personal_preference_event_types())
    for event_type in events or {}:
        if event_type not in switchable:
            raise UnknownPreferenceEventTypeError(event_type)

    session.flush()
    if personal_enabled is not None:
        session.execute(
            insert(CommunicationChannelPreference)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                channel=channel,
                destination_type=DESTINATION_USER,
                enabled=personal_enabled,
            )
            .on_conflict_do_update(
                constraint="uq_communication_channel_preferences_user_channel_destination",
                set_={"enabled": personal_enabled, "updated_at": sa.func.now()},
            )
        )
    for event_type, enabled in (events or {}).items():
        session.execute(
            insert(CommunicationPreference)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                channel=channel,
                destination_type=DESTINATION_USER,
                notification_type=event_type,
                enabled=enabled,
            )
            .on_conflict_do_update(
                constraint="uq_communication_preferences_user_channel_destination_type",
                set_={"enabled": enabled, "updated_at": sa.func.now()},
            )
        )


__all__ = [
    "MandatoryPreferencePolicy",
    "PersonalOptInPreferencePolicy",
    "TelegramIdentityReachability",
    "UnknownPreferenceEventTypeError",
    "EventPreference",
    "PersonalPreferences",
    "get_personal_preferences",
    "set_personal_preferences",
]
