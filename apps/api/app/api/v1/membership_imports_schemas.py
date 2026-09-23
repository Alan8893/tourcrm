"""Request/response models for /api/v1/memberships/imports (TH-0118.1 /
Issue #185; docs/05-api/people-api.md §22).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper); the errors list uses the canonical `CollectionResponse`
envelope. `storage_key`/`source_file_id` are never exposed — there is no
source-file download endpoint in the canonical contract.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ImportJobCreatedOut(BaseModel):
    """`POST /memberships/imports` 201 body — exactly the fields
    people-api.md §22 lists."""

    import_id: UUID
    status: str
    format: str
    created_at: datetime


class ImportJobStatisticsOut(BaseModel):
    """Aggregate statistics. A record counter is `null` until a pipeline
    stage has actually computed it — never a fabricated 0. `error_count`
    is the real number of `error`-severity entries recorded for the job;
    warnings (e.g. `duplicate_exact`) are not counted."""

    total_records: Optional[int]
    valid_records: Optional[int]
    invalid_records: Optional[int]
    created_records: Optional[int]
    updated_records: Optional[int]
    skipped_records: Optional[int]
    error_count: int


class ImportJobOut(BaseModel):
    import_id: UUID
    club_id: UUID
    created_by_user_id: UUID
    status: str
    format: str
    statistics: ImportJobStatisticsOut
    created_at: datetime
    updated_at: datetime


class ImportJobErrorOut(BaseModel):
    """One error or warning. `matched_person_id` is set only for a
    `duplicate_exact` warning against an existing Person — its id only,
    never any of that Person's data."""

    id: UUID
    row_number: Optional[int]
    field: Optional[str]
    code: str
    message: str
    severity: str
    matched_person_id: Optional[UUID]
    created_at: datetime
