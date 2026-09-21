"""Response model for /api/v1/persons/{person_id}/documents (Issue #160).

Field list matches the canonical Document persistence model field-for-
field (app/db/documents.py), mirroring MembershipOut's own convention —
never `storage_key` or any filesystem/storage detail: those belong to the
separate `File` row this schema does not expose at all (ADR-0040 §3).
`file_id` is safe to expose — it is `File.id`, an opaque database key,
never the storage_key/path (ADR-0040 §3, Issue #160 §2/§4/§5).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: UUID
    person_id: UUID
    document_group_id: UUID
    version_number: int
    document_type: str
    status: str
    issued_at: Optional[datetime]
    expires_at: Optional[datetime]
    file_id: UUID
    uploaded_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime
