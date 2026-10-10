"""Request/response models for /api/v1/me/notification-preferences
(Issue #336, ADR-0049 §2.1). Personal Telegram messages only: no model
names a destination, a chat id or another User."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, StrictBool


class EventPreferenceOut(BaseModel):
    event_type: str
    # Mandatory types (event.cancelled / event.rescheduled) ignore the
    # personal opt-out; they are listed read-only with `enabled = true`.
    mandatory: bool
    enabled: bool


class NotificationPreferencesOut(BaseModel):
    channel: str
    # The personal master switch; unset means OFF.
    personal_enabled: bool
    # Personal delivery needs a verified linked Telegram account.
    telegram_linked: bool
    events: list[EventPreferenceOut]


class NotificationPreferencesUpdate(BaseModel):
    """A partial update: an omitted field leaves the stored value
    unchanged. `events` may name only optional, non-blocked types."""

    model_config = ConfigDict(extra="forbid")

    personal_enabled: Optional[StrictBool] = None
    events: Optional[dict[str, StrictBool]] = None
