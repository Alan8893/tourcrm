"""Request/response models for /api/v1/trips (Issue #245).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Only the canonical fields of this slice: the Trip carries no
tourism attribute yet (all deferred by the Issue #245 PO decision) and
TripParticipant exposes the confirmed `actual_participation` fact plus
its identity — never a registration status (that stays on
EventParticipation).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, StrictBool


class TripCreateRequest(BaseModel):
    event_id: UUID


class TripOut(BaseModel):
    event_id: UUID
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
