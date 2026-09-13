"""Request/response models for /api/v1/memberships (Issue #62).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). Field list matches the canonical ClubMembership persistence
model field-for-field (app/db/identity.py).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class MembershipCreateRequest(BaseModel):
    person_id: UUID
    club_id: UUID
    membership_type: str
    status: str
    joined_at: datetime


class MembershipUpdateRequest(BaseModel):
    """PATCH: only `membership_type` — `status`/`joined_at`/`left_at` are
    lifecycle fields, changed only via the dedicated status endpoint.
    """

    membership_type: str


class MembershipStatusTransitionRequest(BaseModel):
    status: str
    reason: Optional[str] = None


class MembershipOut(BaseModel):
    id: UUID
    club_id: UUID
    person_id: UUID
    membership_type: str
    status: str
    joined_at: datetime
    left_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
