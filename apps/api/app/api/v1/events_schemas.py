"""Request/response models for /api/v1/events (Issue #40).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Field list matches ADR-0019 field-for-field: no separate
`location` field, no invented extra field.
"""

from datetime import datetime
from typing import Optional
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
