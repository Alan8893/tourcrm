"""Request/response models for /api/v1/events (Issue #40) and the
calendar projection (Issue #82 / TH-0080).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Field list matches ADR-0019 field-for-field: no separate
`location` field, no invented extra field.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EventCreateRequest(BaseModel):
    club_id: UUID
    event_type: str
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    start_at: datetime
    end_at: datetime
    timezone: str
    location_type: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location_latitude: Optional[float] = None
    location_longitude: Optional[float] = None


class EventUpdateRequest(BaseModel):
    """PATCH: only fields actually present in the request body are
    applied (see the router's `exclude_unset=True` usage) — an omitted
    field leaves the current value untouched. `status`/
    `cancellation_reason` are deliberately absent: they only change
    through the dedicated status/archive endpoints.
    """

    event_type: Optional[str] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    timezone: Optional[str] = None
    location_type: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location_latitude: Optional[float] = None
    location_longitude: Optional[float] = None


class EventStatusTransitionRequest(BaseModel):
    status: str
    cancellation_reason: Optional[str] = None


class EventOut(BaseModel):
    id: UUID
    club_id: UUID
    event_type: str
    title: str
    description: Optional[str]
    start_at: datetime
    end_at: datetime
    timezone: str
    location_type: Optional[str]
    location_name: Optional[str]
    location_address: Optional[str]
    location_latitude: Optional[float]
    location_longitude: Optional[float]
    status: str
    cancellation_reason: Optional[str]
    created_by: Optional[UUID]
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class CalendarItemOut(BaseModel):
    """One `GET /events/calendar` row — either an ordinary Event or a
    materialized recurring EventOccurrence (events-api.md §16: "The
    response must identify whether the item is an ordinary Event or
    EventOccurrence"). `id` is the stable, opaque id of that originating
    entity itself — no second calendar-only identity is introduced.

    `series_id`/`series_version` are populated only for `kind="occurrence"`
    and reuse the already-public EventSeries identity fields
    (`app.api.v1.events_series_schemas.EventSeriesOut.id`/`.version`) —
    events-api.md §16: "expose the governing series identifier/version
    where that information is part of the public contract."
    """

    id: UUID
    kind: Literal["event", "occurrence"]
    club_id: UUID
    event_type: str
    title: str
    description: Optional[str]
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    cancellation_reason: Optional[str]
    series_id: Optional[UUID]
    series_version: Optional[int]
