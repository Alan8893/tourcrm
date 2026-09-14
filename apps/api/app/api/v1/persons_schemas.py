"""Request/response models for /api/v1/persons (Issue #62).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). `PersonOut` deliberately omits `phone`/`email`/`address` and
`photo_file_id` (ADR-0025 §8 — withheld from the baseline API response
pending a dedicated permission/scope policy; no file domain exists yet
for `photo_file_id`) even though `PersonCreateRequest`/`PersonUpdateRequest`
accept the first three as input — the caller who submits a value already
knows it, so there is no disclosure concern on write, only on read.
"""

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PersonCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    middle_name: Optional[str] = Field(default=None, max_length=255)
    birth_date: Optional[date] = None
    phone: Optional[str] = Field(default=None, max_length=32)
    email: Optional[str] = Field(default=None, max_length=255)
    address: Optional[str] = None


class PersonUpdateRequest(BaseModel):
    """PATCH: only fields actually present in the request body are
    applied (`exclude_unset=True`, mirroring app.api.v1.events'
    EventUpdateRequest) — an omitted field leaves the current value
    untouched.
    """

    first_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    middle_name: Optional[str] = Field(default=None, max_length=255)
    birth_date: Optional[date] = None
    phone: Optional[str] = Field(default=None, max_length=32)
    email: Optional[str] = Field(default=None, max_length=255)
    address: Optional[str] = None


class PersonOut(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]
    birth_date: Optional[date]
    created_at: datetime
    updated_at: datetime
