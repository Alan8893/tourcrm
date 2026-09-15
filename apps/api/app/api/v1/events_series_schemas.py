"""Request/response schemas for the Event recurrence API (Issue #79).

Canonical source: docs/05-api/event-recurrence-api.md. Field names mirror
the DB column names verbatim, matching app.api.v1.events_schemas's own
convention (no request->DB field remapping).
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RecurrenceRuleInput(BaseModel):
    """Structured recurrence input (ADR-0028 §8) — never raw RRULE text.
    Maps directly onto app.events.rrule.RecurrenceInput.
    """

    frequency: str
    interval: int = 1
    by_day: list[str] = Field(default_factory=list)
    by_month_day: list[int] = Field(default_factory=list)
    by_month: list[int] = Field(default_factory=list)
    count: Optional[int] = None
    until: Optional[datetime] = None


class EventSeriesCreateRequest(BaseModel):
    club_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    event_type: str
    series_start_at: datetime
    series_end_at: Optional[datetime] = None
    occurrence_limit: Optional[int] = None
    duration_minutes: int = Field(gt=0)
    recurrence: RecurrenceRuleInput
    timezone: str


class EventSeriesUpdateRequest(BaseModel):
    """`update_scope="entire_series"` accepts only the non-versioning
    metadata fields (`name`/`description`) — a recurrence-affecting field
    change is never applied in place (ADR-0028: "Series edits cannot
    silently rewrite past events"). `update_scope="entire_series"` with
    the source series_start_at unchanged is the mechanism for adjusting a
    still-open-ended series that has not yet produced a conflicting
    materialized change.

    `update_scope="this_and_following"` carries the full new recurrence
    definition plus the selected boundary `occurrence_id`; the source
    version is the `{series_id}` in the URL itself (this endpoint is
    always called on the specific version the caller believes is
    current — a 409 is returned if it no longer is).

    `this_occurrence` is not a value here: per docs/05-api/
    event-recurrence-api.md, occurrence-level changes go exclusively
    through `POST /events/series/{series_id}/exceptions`.
    """

    update_scope: Literal["this_and_following", "entire_series"]

    # entire_series (metadata only)
    name: Optional[str] = None
    description: Optional[str] = None

    # this_and_following
    occurrence_id: Optional[UUID] = None
    event_type: Optional[str] = None
    series_start_at: Optional[datetime] = None
    series_end_at: Optional[datetime] = None
    occurrence_limit: Optional[int] = None
    duration_minutes: Optional[int] = None
    recurrence: Optional[RecurrenceRuleInput] = None
    timezone: Optional[str] = None


class EventSeriesExceptionRequest(BaseModel):
    """`POST /events/series/{series_id}/exceptions` (ADR-0028 §5) —
    reschedule, cancellation and allow-listed overrides for one concrete
    occurrence, identified by `occurrence_id`.
    """

    occurrence_id: UUID
    exception_type: Literal["rescheduled", "cancelled"]
    effective_start_at: Optional[datetime] = None
    effective_end_at: Optional[datetime] = None
    overrides: Optional[dict] = None
    cancellation_reason: Optional[str] = None


class EventOccurrenceUpdateRequest(BaseModel):
    """`PATCH /events/occurrences/{occurrence_id}` — the direct
    operational lifecycle progression only (`in_progress`/`completed`);
    cancellation/reschedule/override go through the series exceptions
    endpoint (ADR-0028 §5/§7).
    """

    status: Literal["in_progress", "completed"]


class EventSeriesOut(BaseModel):
    id: UUID
    root_series_id: UUID
    version: int
    supersedes_series_id: Optional[UUID]
    club_id: UUID
    name: str
    description: Optional[str]
    event_type: str
    series_start_at: datetime
    series_end_at: Optional[datetime]
    occurrence_limit: Optional[int]
    duration_minutes: int
    recurrence_rule: str
    timezone: str
    status: str
    created_by: Optional[UUID]
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class EventOccurrenceExceptionOut(BaseModel):
    id: UUID
    exception_type: str
    original_start_at: datetime
    effective_start_at: Optional[datetime]
    effective_end_at: Optional[datetime]
    overrides: Optional[dict]
    cancellation_reason: Optional[str]


class EventOccurrenceOut(BaseModel):
    id: UUID
    series_id: UUID
    club_id: UUID
    name: str
    description: Optional[str]
    event_type: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: str
    cancellation_reason: Optional[str]
    exception: Optional[EventOccurrenceExceptionOut] = None


__all__ = [
    "RecurrenceRuleInput",
    "EventSeriesCreateRequest",
    "EventSeriesUpdateRequest",
    "EventSeriesExceptionRequest",
    "EventOccurrenceUpdateRequest",
    "EventSeriesOut",
    "EventOccurrenceExceptionOut",
    "EventOccurrenceOut",
]
