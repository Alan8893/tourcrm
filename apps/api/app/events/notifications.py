"""Event business notifications: `event.cancelled` and `event.rescheduled`
(Issue #336 PR-1; catalog §3/§3.3, ADR-0049).

Called by app.events.crud **inside** the Event mutation's own transaction,
before it commits (ADR-0049 §2.6): the Event change, its linked
EventOccurrence sync, the Notifications, Deliveries and outbox jobs commit
or roll back together. Nothing here commits, sends or isolates the Engine
in a savepoint; any error propagates and the caller rolls back. Planning
goes only through app.notifications.business.plan_catalog_notification.

Scope (PO decision 3): only `Event`. EventOccurrence / EventSeries changes
never reach this module.

Audience (catalog §3.1): the Users whose Person holds a `registered`
EventParticipation of the Event at the moment of the change — a
registration cancelled earlier is not included — whose User account is
`active` (the same precondition the Telegram linking flow and sign-in use).
A Person without a User account gets nothing; guardians/parents are never
added. Each recipient must still pass the existing `event.read` object
policy for the Event (RecipientAccess). Both types may also be published to
administrator-configured group/topic routes (PO decision 7).

Facts (ADR-0049 §2.9):

- `event.cancelled`: a cancellation is terminal (ADR-0018), so the Event id
  is the fact id;
- `event.rescheduled`: a published Event's `start_at`, `end_at` or
  `timezone` actually changed: the old values are captured before the
  update, the new ones re-read from the database after it is flushed, so
  re-sending the same instant with another UTC offset is no change. The
  fact id is a UUIDv5 of the Event id, the old and new schedule and the
  transaction's start time (`now()`), so it is stable
  for the whole transaction — planning the same fact twice in it cannot
  duplicate — and a later reschedule back to earlier values is a new
  fact. A repeated update with unchanged values detects no change and so
  creates no fact.

Dates are rendered in the Event's own time zone; a reschedule shows the
old schedule in the old time zone and the new one in the new time zone.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.events import Event, EventParticipation
from app.db.identity import User
from app.events.authorization import build_event_resource_context
from app.notifications.business import plan_catalog_notification
from app.notifications.catalog import EVENT_CANCELLED, EVENT_RESCHEDULED
from app.notifications.ports import PermissionRecipientAccess

_REGISTERED_STATUS = "registered"
_ACTIVE_USER_STATUS = "active"
_PUBLISHED_STATUS = "published"
_EVENT_READ_PERMISSION = "event.read"
_SUBJECT_TYPE = "event"

# Namespace of the deterministic reschedule fact ids (ADR-0049 §2.9).
_RESCHEDULE_FACT_NAMESPACE = uuid.UUID("5d3f0f3e-6f0c-4f5a-9a2e-0b6c1d2e3f40")


@dataclass(frozen=True)
class EventSchedule:
    """The schedule fields whose change is a reschedule (PO decision 1)."""

    start_at: datetime
    end_at: datetime
    timezone: str


def schedule_of(event: Event) -> EventSchedule:
    return EventSchedule(start_at=event.start_at, end_at=event.end_at, timezone=event.timezone)


def format_event_schedule(schedule: EventSchedule) -> str:
    """`12.10.2026 08:00–18:00 (Europe/Moscow)`, or
    `12.10.2026 08:00 – 13.10.2026 18:00 (Europe/Moscow)` across days, in
    the schedule's own time zone."""
    zone = ZoneInfo(schedule.timezone)
    start = schedule.start_at.astimezone(zone)
    end = schedule.end_at.astimezone(zone)
    if start.date() == end.date():
        span = f"{start:%d.%m.%Y %H:%M}–{end:%H:%M}"
    else:
        span = f"{start:%d.%m.%Y %H:%M} – {end:%d.%m.%Y %H:%M}"
    return f"{span} ({schedule.timezone})"


def registered_user_ids(session: Session, *, event_id: uuid.UUID) -> list[uuid.UUID]:
    """Active Users whose Person is currently `registered` for the Event,
    in a deterministic order. Reads the rows as of this transaction."""
    return list(
        session.execute(
            sa.select(User.id)
            .join(EventParticipation, EventParticipation.person_id == User.person_id)
            .where(
                EventParticipation.event_id == event_id,
                EventParticipation.registration_status == _REGISTERED_STATUS,
                User.status == _ACTIVE_USER_STATUS,
            )
            .order_by(User.id)
        ).scalars()
    )


def _event_read_access(event: Event) -> PermissionRecipientAccess:
    return PermissionRecipientAccess(
        permission_code=_EVENT_READ_PERMISSION,
        context_for=lambda session, user_id: build_event_resource_context(
            session, event=event, user_id=user_id, permission_code=_EVENT_READ_PERMISSION
        ),
    )


def plan_event_cancelled(session: Session, *, event: Event) -> None:
    """Plan `event.cancelled` for an Event just moved to `cancelled` in the
    caller's open transaction."""
    session.flush()
    plan_catalog_notification(
        session,
        event_type=EVENT_CANCELLED,
        fact_id=str(event.id),
        subject_type=_SUBJECT_TYPE,
        subject_id=event.id,
        club_id=event.club_id,
        recipient_user_ids=registered_user_ids(session, event_id=event.id),
        recipient_access=_event_read_access(event),
        render_context={
            "event_title": event.title,
            "event_datetime": format_event_schedule(schedule_of(event)),
            # Empty -> the catalog fallback "не указана" at render time.
            "cancellation_reason": (event.cancellation_reason or "").strip(),
            "event_id": str(event.id),
        },
        publish_to_routes=True,
    )


def _reschedule_fact_id(
    session: Session, event: Event, before: EventSchedule, after: EventSchedule
) -> str:
    # now() is the transaction's start time: stable within it, distinct
    # across the serialized (row-locked) mutations of one Event.
    transaction_started_at = session.execute(sa.select(sa.func.now())).scalar_one()
    material = "|".join(
        [
            str(event.id),
            transaction_started_at.isoformat(),
            before.start_at.isoformat(),
            before.end_at.isoformat(),
            before.timezone,
            after.start_at.isoformat(),
            after.end_at.isoformat(),
            after.timezone,
        ]
    )
    return str(uuid.uuid5(_RESCHEDULE_FACT_NAMESPACE, material))


def plan_event_rescheduled_if_changed(
    session: Session, *, event: Event, before: EventSchedule
) -> Optional[str]:
    """Plan `event.rescheduled` when `event` (already updated in the
    caller's open transaction) is `published` and its schedule differs
    from `before`. Returns the fact id when a reschedule was planned."""
    if event.status != _PUBLISHED_STATUS:
        return None
    # Compare what is actually stored: flush the update and re-read the
    # schedule columns as PostgreSQL normalized them (timestamptz instants),
    # so equal instants written with another offset are not a change.
    session.flush()
    session.refresh(event, attribute_names=["start_at", "end_at", "timezone"])
    after = schedule_of(event)
    if after == before:
        return None
    fact_id = _reschedule_fact_id(session, event, before, after)
    plan_catalog_notification(
        session,
        event_type=EVENT_RESCHEDULED,
        fact_id=fact_id,
        subject_type=_SUBJECT_TYPE,
        subject_id=event.id,
        club_id=event.club_id,
        recipient_user_ids=registered_user_ids(session, event_id=event.id),
        recipient_access=_event_read_access(event),
        render_context={
            "event_title": event.title,
            "old_event_datetime": format_event_schedule(before),
            "new_event_datetime": format_event_schedule(after),
            "event_id": str(event.id),
        },
        publish_to_routes=True,
    )
    return fact_id


__all__ = [
    "EventSchedule",
    "schedule_of",
    "format_event_schedule",
    "registered_user_ids",
    "plan_event_cancelled",
    "plan_event_rescheduled_if_changed",
]
