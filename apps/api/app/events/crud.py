"""Event CRUD and lifecycle service functions (Issue #40).

Canonical sources: docs/03-architecture/adr/ADR-0018-event-lifecycle.md
(status graph, cancellation-requires-reason), docs/03-architecture/adr/
ADR-0019-event-field-model.md (field list), docs/02-requirements/
business-rules.md §10.

Validation is delegated entirely to app.events.lifecycle — never
reimplemented here (that module already encodes the exact ADR-0018
transition graph and ADR-0019 field-level rules). This module performs
no authorization: the caller (the API router) must resolve a
ResourceContext and call Authorizer.check() before invoking any function
here, exactly as app.events.service and app.groups.service already keep
Club-ownership validation and authorization as two separate layers.

Row locking for concurrent mutations of the *same* Event (two overlapping
status transitions, or a transition racing an update) is the router's
responsibility: it must load the Event with `SELECT ... FOR UPDATE`
before calling update_event/transition_event_status/archive_event, so
these functions can assume the row is already exclusively locked for the
duration of the caller's transaction.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.events import Event
from app.events.lifecycle import (
    validate_coordinates,
    validate_event_type,
    validate_status_transition,
    validate_time_range,
)

# ADR-0018: every Event starts as `draft`; no document describes a
# "create directly as published" path, and `status` is never
# client-controlled at creation.
INITIAL_EVENT_STATUS = "draft"

# ADR-0018 / roles-and-permissions.md §12: archiving is reachable only via
# the dedicated archive endpoint (`event.manage`), never through the
# general status-transition endpoint.
ARCHIVED_STATUS = "archived"

# The PATCH-writable Event fields (Issue #40): `status`/`cancellation_reason`
# only change through transition_event_status()/archive_event(); `id`,
# `club_id`, `created_by`/`created_at`/`updated_at` are immutable via PATCH;
# `updated_by` is always server-set from the authenticated principal.
UPDATABLE_EVENT_FIELDS = frozenset(
    {
        "event_type",
        "title",
        "description",
        "start_at",
        "end_at",
        "timezone",
        "location_type",
        "location_name",
        "location_address",
        "location_latitude",
        "location_longitude",
    }
)


class EventTransitionNotAllowedError(Exception):
    """Raised when a caller attempts to reach `archived` through
    transition_event_status() instead of the dedicated archive_event().
    """


def create_event(
    session: Session,
    *,
    club_id: uuid.UUID,
    event_type: str,
    title: str,
    description: Optional[str],
    start_at: datetime,
    end_at: datetime,
    timezone: str,
    location_type: Optional[str],
    location_name: Optional[str],
    location_address: Optional[str],
    location_latitude: Optional[float],
    location_longitude: Optional[float],
    created_by: uuid.UUID,
) -> Event:
    """Create a new Event, always starting in `draft` with no
    cancellation reason, `created_by == updated_by == created_by`.
    """
    validate_event_type(event_type)
    validate_time_range(start_at, end_at)
    validate_coordinates(location_latitude, location_longitude)

    event = Event(
        club_id=club_id,
        event_type=event_type,
        title=title,
        description=description,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
        location_type=location_type,
        location_name=location_name,
        location_address=location_address,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        status=INITIAL_EVENT_STATUS,
        cancellation_reason=None,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(event)
    session.commit()
    return event


def update_event(
    session: Session,
    *,
    event: Event,
    updated_by: uuid.UUID,
    **fields,
) -> Event:
    """Apply a partial update (PATCH) of the client-writable Event
    fields. `status`/`cancellation_reason` are never accepted here — see
    transition_event_status()/archive_event() for lifecycle changes.
    """
    unknown_fields = set(fields) - UPDATABLE_EVENT_FIELDS
    if unknown_fields:
        raise ValueError(f"Fields not updatable via update_event: {sorted(unknown_fields)}")

    if "event_type" in fields:
        validate_event_type(fields["event_type"])

    if "start_at" in fields or "end_at" in fields:
        start_at = fields.get("start_at", event.start_at)
        end_at = fields.get("end_at", event.end_at)
        validate_time_range(start_at, end_at)

    if "location_latitude" in fields or "location_longitude" in fields:
        latitude = fields.get("location_latitude", event.location_latitude)
        longitude = fields.get("location_longitude", event.location_longitude)
        validate_coordinates(latitude, longitude)

    for field_name, value in fields.items():
        setattr(event, field_name, value)
    event.updated_by = updated_by
    session.commit()
    return event


def transition_event_status(
    session: Session,
    *,
    event: Event,
    new_status: str,
    cancellation_reason: Optional[str],
    updated_by: uuid.UUID,
) -> Event:
    """Move `event` to `new_status` per ADR-0018's exact transition
    graph. Raises EventTransitionNotAllowedError (persisting nothing) for
    a request targeting `archived` — that transition is reachable only
    through archive_event(), which alone requires `event.manage`.
    """
    if new_status == ARCHIVED_STATUS:
        raise EventTransitionNotAllowedError(
            "Archiving must go through the dedicated archive endpoint (event.manage)"
        )
    validate_status_transition(event.status, new_status, cancellation_reason=cancellation_reason)
    event.status = new_status
    event.cancellation_reason = cancellation_reason if new_status == "cancelled" else None
    event.updated_by = updated_by
    session.commit()
    return event


def archive_event(session: Session, *, event: Event, updated_by: uuid.UUID) -> Event:
    """Move `event` to `archived`. Only reachable from `completed` or
    `cancelled` (ADR-0018); the caller must have already checked
    `event.manage`, since this permission is the only one archiving
    requires (roles-and-permissions.md §12) — no separate `event.archive`
    permission exists.
    """
    validate_status_transition(event.status, ARCHIVED_STATUS)
    event.status = ARCHIVED_STATUS
    event.updated_by = updated_by
    session.commit()
    return event


__all__ = [
    "INITIAL_EVENT_STATUS",
    "ARCHIVED_STATUS",
    "UPDATABLE_EVENT_FIELDS",
    "EventTransitionNotAllowedError",
    "create_event",
    "update_event",
    "transition_event_status",
    "archive_event",
]
