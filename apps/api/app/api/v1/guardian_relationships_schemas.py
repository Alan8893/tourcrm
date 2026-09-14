"""Request/response models for /api/v1/guardian-relationships and
/api/v1/me/children (Issue #64).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). `GuardianRelationshipOut` matches the canonical persistence
model field-for-field (app/db/identity.py) and Issue #64 §9's response
schema exactly, except `status` reflects
app.people.guardian_lifecycle.effective_status()'s read-time derivation
rather than the raw stored column (see app.api.v1.guardian_relationships'
`_guardian_relationship_out`).
"""

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class GuardianRelationshipCreateRequest(BaseModel):
    guardian_person_id: UUID
    relationship_type: str
    is_primary_contact: bool = False
    # Issue #64 §8/§16: creation must reject any status other than
    # `active` — there is no `pending` status for this entity (ADR-0023).
    # A Literal type rejects anything else at the schema layer, the same
    # way FastAPI/Pydantic already reject any other required-field
    # violation, without needing a custom domain error for this check.
    status: Literal["active"] = "active"


class GuardianRelationshipUpdateRequest(BaseModel):
    """PATCH: only `relationship_type`/`is_primary_contact` — no `status`
    field exists on this schema at all, so a client cannot smuggle a
    status change through PATCH regardless of payload content (Issue #64
    §8/§13).
    """

    relationship_type: Optional[str] = None
    is_primary_contact: Optional[bool] = None


class GuardianRelationshipOut(BaseModel):
    id: UUID
    guardian_person_id: UUID
    child_person_id: UUID
    relationship_type: str
    status: str
    is_primary_contact: bool
    valid_from: datetime
    valid_to: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ChildOut(BaseModel):
    """`GET /me/children` projection (Issue #64 §9 GAP-B, resolved by the
    PO as exactly these four fields). Deliberately excludes
    `phone`/`email`/`address` and every other Person field, matching
    Issue #62 §9's sensitive-field withholding policy.
    """

    id: UUID
    full_name: str
    birth_date: Optional[date]
    photo_file_id: Optional[UUID]
