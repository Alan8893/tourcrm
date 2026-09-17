"""Request/response models for /api/v1/persons (Issue #62, ADR-0035).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper). `PersonOut` includes `phone`/`email`/`address`/`photo_file_id`:
ADR-0025 §8's baseline withholding of these fields is superseded by
ADR-0035 §3/§4 — contact fields and `photo_file_id` are ordinary Person
fields governed by the same `person.read` + scope + object-relationship
policy as the rest of the record, with no separate permission. The
authorization layer (app.people.authorization / the persons router)
still decides whether a given requester may see the record at all;
this schema only decides what is included once that access is granted.
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
    photo_file_id: Optional[UUID] = None


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
    photo_file_id: Optional[UUID] = None


class PersonOut(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]
    birth_date: Optional[date]
    phone: Optional[str]
    email: Optional[str]
    address: Optional[str]
    photo_file_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime
