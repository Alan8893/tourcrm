"""Response models for /api/v1/persons/{person_id}/documents (Issue #160)
and /api/v1/events/{event_id}/document-requirements/{person_id}
(TH-0117.4 / Issue #162).

`DocumentOut`'s field list matches the canonical Document persistence
model field-for-field (app/db/documents.py), mirroring MembershipOut's
own convention — never `storage_key` or any filesystem/storage detail:
those belong to the separate `File` row this schema does not expose at
all (ADR-0040 §3). `file_id` is safe to expose — it is `File.id`, an
opaque database key, never the storage_key/path (ADR-0040 §3, Issue
#160 §2/§4/§5).

`EventDocumentRequirementCheckOut`/`EventDocumentRequirementCheckListOut`
carry only the derived, operational information ADR-0040 §5/events-api.md
§31 define for this check — `document_type`, `required`, and the
`valid`/`missing`/`expired` result — never a `file_id`, `document_id`, or
any other Document/File-internal reference (Issue #162 §6: no unnecessary
metadata beyond what the check itself produces).
"""

from datetime import datetime
from typing import Literal, Optional
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


class EventDocumentRequirementCheckOut(BaseModel):
    document_type: str
    required: bool
    result: Literal["valid", "missing", "expired"]


class EventDocumentRequirementCheckListOut(BaseModel):
    """A single computed resource (ADR-0014: no collection wrapper) — not
    a paginated collection of independently addressable resources."""

    event_id: UUID
    person_id: UUID
    requirements: list[EventDocumentRequirementCheckOut]
