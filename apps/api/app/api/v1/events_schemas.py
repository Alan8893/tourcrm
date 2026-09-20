"""Request/response models for /api/v1/events (Issue #40), the calendar
projection (Issue #82 / TH-0080), conflict detection (Issue #91 /
TH-0085), and Attendance (Issue #94 / TH-0087).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Field list matches ADR-0019 field-for-field: no separate
`location` field, no invented extra field.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas import Pagination


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
    # TH-0108 / ADR-0037 §1-§2, events-api.md §6: 0 groups means this Event
    # is club-wide; 1+ means it targets exactly those Groups. Both fields
    # default to empty — omitting them entirely creates a club-wide Event
    # with no assigned instructor, same as before this Issue.
    group_ids: list[UUID] = Field(default_factory=list)
    instructor_ids: list[UUID] = Field(default_factory=list)


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
    # TH-0108: absent (the default) leaves current targeting/assignments
    # untouched, matching every other field's `exclude_unset` PATCH
    # semantics here; an explicit list (including `[]`) replaces the
    # currently active set — see app.events.crud.update_event_with_targeting.
    group_ids: Optional[list[UUID]] = None
    instructor_ids: Optional[list[UUID]] = None


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
    # TH-0108 / ADR-0037 §1-§2: the Event's currently active target Groups
    # (EventGroupTarget) and responsible instructors/Users
    # (EventStaffAssignment) — bare UUIDs only, so the frontend can
    # reconstruct current targeting against the Group list / User
    # Directory it already has, without this endpoint exposing any extra
    # User field.
    group_ids: list[UUID]
    instructor_ids: list[UUID]


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


class ConflictObjectRefOut(BaseModel):
    """One side of a derived conflict (events-api.md §28): the concrete
    Event/EventOccurrence object, identified by its own stable opaque
    ID — no second identity. `series_id`/`series_version` are populated
    only for `object_type="occurrence"`, reusing the same governing-
    series exposure already public on `CalendarItemOut`."""

    object_type: Literal["event", "occurrence"]
    object_id: UUID
    series_id: Optional[UUID]
    series_version: Optional[int]


class ConflictOut(BaseModel):
    """One `GET /events/conflicts` row — a derived relationship between
    two concrete objects, never a persisted entity (ADR-0031 §10). `id`
    is deterministically derived from the unordered pair of object
    identities plus `domain`; the same underlying conflict always
    produces the same `id`."""

    id: str
    first_object: ConflictObjectRefOut
    second_object: ConflictObjectRefOut
    domain: Literal["instructor", "group", "participant"]
    overlap_start_at: datetime
    overlap_end_at: datetime


# --- Attendance (Issue #94 / TH-0087, ADR-0032) -----------------------------


class AttendanceMarkRequest(BaseModel):
    """PUT .../attendance/{person_id}. Field-invariant validation
    (`present` <=> no `absence_reason`/`comment`, closed vocabularies) is
    deliberately not duplicated here — it is enforced once, by
    app.events.attendance.validate_attendance_fields, exactly like
    EventCreateRequest defers its own cross-field/vocabulary checks to
    the domain layer rather than the schema layer."""

    status: str
    absence_reason: Optional[str] = None
    comment: Optional[str] = None


class AttendanceBulkItemRequest(BaseModel):
    person_id: UUID
    status: str
    absence_reason: Optional[str] = None
    comment: Optional[str] = None


class AttendanceBulkMarkRequest(BaseModel):
    items: list[AttendanceBulkItemRequest] = Field(min_length=1)


class AttendanceCorrectionRequest(BaseModel):
    status: str
    absence_reason: Optional[str] = None
    comment: Optional[str] = None
    reason: str = Field(min_length=1)


class AttendancePersonOut(BaseModel):
    """A minimal Person projection (ADR-0032 §9's "person identity/
    projection") — deliberately as narrow as `PersonOut`'s own withheld-
    PII precedent (no phone/email/address/photo)."""

    id: UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]


class AttendanceMarkOut(BaseModel):
    """Response for the single-mark PUT and each item of the bulk PUT."""

    person_id: UUID
    status: Literal["present", "absent"]
    absence_reason: Optional[str]
    comment: Optional[str]
    created_at: datetime
    updated_at: datetime


class AttendanceBulkMarkOut(BaseModel):
    items: list[AttendanceMarkOut]


class AttendanceCorrectionOut(BaseModel):
    """ADR-0032 §9: "previous status, new status, mandatory reason,
    actor and timestamp"."""

    person_id: UUID
    previous_status: Optional[Literal["present", "absent"]]
    new_status: Literal["present", "absent"]
    absence_reason: Optional[str]
    comment: Optional[str]
    reason: str
    actor_user_id: UUID
    corrected_at: datetime


class AttendanceEntryOut(BaseModel):
    """One `GET .../attendance` row. `status=None` means "unmarked" — it
    is never treated as `absent` (ADR-0032 §9)."""

    person: AttendancePersonOut
    status: Optional[Literal["present", "absent"]]
    absence_reason: Optional[str]
    comment: Optional[str]


class AttendanceSummaryOut(BaseModel):
    """Derived, not persisted (ADR-0032 §9)."""

    total: int
    marked: int
    present: int
    absent: int
    unmarked: int


class AttendanceListOut(BaseModel):
    items: list[AttendanceEntryOut]
    pagination: Pagination
    summary: AttendanceSummaryOut
