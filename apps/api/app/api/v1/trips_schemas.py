"""Request/response models for /api/v1/trips (Issue #245).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Only the canonical fields implemented so far: the Trip carries
the optional TourismType reference (Issue #264) and
TripParticipant exposes the confirmed `actual_participation` fact plus
its identity — never a registration status (that stays on
EventParticipation).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, StrictBool


class TripCreateRequest(BaseModel):
    event_id: UUID
    # Issue #264: optional TourismType catalog reference (active entry only).
    tourism_type_id: Optional[UUID] = None


class TripUpdateRequest(BaseModel):
    """Ordinary Trip editing (Issue #264). Only fields present in the body
    are changed; `tourism_type_id: null` clears the TourismType."""

    tourism_type_id: Optional[UUID] = None


class TripOut(BaseModel):
    event_id: UUID
    tourism_type_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class TripParticipantRecordRequest(BaseModel):
    actual_participation: StrictBool


class TripParticipantOut(BaseModel):
    event_participation_id: UUID
    event_id: UUID
    person_id: UUID
    actual_participation: bool
    created_at: datetime
    updated_at: datetime
