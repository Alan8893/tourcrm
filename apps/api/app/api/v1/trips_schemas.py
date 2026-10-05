"""Request/response models for /api/v1/trips (Issue #245).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Only the canonical fields implemented so far: the Trip carries
the optional TourismType reference (Issue #264) and the optional
structured Official Difficulty (Issue #268), and TripParticipant
exposes the confirmed `actual_participation` fact plus its identity —
never a registration status (that stays on EventParticipation).
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool

OfficialDifficultyMode = Literal["NONE", "DEGREE", "CATEGORY", "WEEKEND"]
OfficialDifficultyValue = Literal["I", "II", "III", "IV", "V", "VI"]


class OfficialDifficultyIn(BaseModel):
    """Issue #268 (trips-and-tourist-profile.md §4): one structured
    classification. `value` only for DEGREE (I–III) and CATEGORY (I–VI);
    `source` required for DEGREE/CATEGORY/WEEKEND, optional for NONE. The
    combination is validated by app.trips.official_difficulty."""

    model_config = ConfigDict(extra="forbid")

    mode: OfficialDifficultyMode
    value: Optional[OfficialDifficultyValue] = None
    source: Optional[str] = None


class OfficialDifficultyOut(BaseModel):
    mode: OfficialDifficultyMode
    value: Optional[OfficialDifficultyValue]
    source: Optional[str]


class TripCreateRequest(BaseModel):
    event_id: UUID
    # Issue #264: optional TourismType catalog reference (active entry only).
    tourism_type_id: Optional[UUID] = None
    # Issue #268: optional Official Difficulty (Administrator only).
    official_difficulty: Optional[OfficialDifficultyIn] = None


class TripUpdateRequest(BaseModel):
    """Ordinary Trip editing (Issues #264, #268). Only fields present in
    the body are changed; `tourism_type_id: null` clears the TourismType,
    `official_difficulty: null` clears the Official Difficulty."""

    tourism_type_id: Optional[UUID] = None
    official_difficulty: Optional[OfficialDifficultyIn] = None


class TripOut(BaseModel):
    event_id: UUID
    tourism_type_id: Optional[UUID]
    official_difficulty: Optional[OfficialDifficultyOut]
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
