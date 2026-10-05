"""Request/response models for /api/v1/trips (Issue #245).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Only the canonical fields implemented so far: the Trip carries
the optional TourismType reference (Issue #264), the optional
structured Official Difficulty (Issue #268) and the optional Geography
— Country and Region references (Issue #271) — and the Duration
Classification (Issue #274), and TripParticipant
exposes the confirmed `actual_participation` fact plus its identity —
never a registration status (that stays on EventParticipation).
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool

OfficialDifficultyMode = Literal["NONE", "DEGREE", "CATEGORY", "WEEKEND"]
OfficialDifficultyValue = Literal["I", "II", "III", "IV", "V", "VI"]
# Issue #274 (trips-and-tourist-profile.md §10).
DurationClassification = Literal["ONE_DAY", "MULTI_DAY", "UNCLASSIFIED"]


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
    # Issue #271: optional Geography (active entries; Region of the Country).
    country_id: Optional[UUID] = None
    region_id: Optional[UUID] = None
    # Issue #274: Duration Classification (Administrator only); omitted =
    # `UNCLASSIFIED`.
    duration_classification: DurationClassification = "UNCLASSIFIED"


class TripUpdateRequest(BaseModel):
    """Ordinary Trip editing (Issues #264, #268, #271). Only fields present
    in the body are changed; `null` clears the TourismType, the Official
    Difficulty, the Country or the Region. The resulting Region must
    belong to the resulting Country. `duration_classification` is never
    null — `UNCLASSIFIED` unsets it."""

    tourism_type_id: Optional[UUID] = None
    official_difficulty: Optional[OfficialDifficultyIn] = None
    country_id: Optional[UUID] = None
    region_id: Optional[UUID] = None
    duration_classification: DurationClassification = "UNCLASSIFIED"


class TripOut(BaseModel):
    event_id: UUID
    tourism_type_id: Optional[UUID]
    official_difficulty: Optional[OfficialDifficultyOut]
    country_id: Optional[UUID]
    region_id: Optional[UUID]
    duration_classification: DurationClassification
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
