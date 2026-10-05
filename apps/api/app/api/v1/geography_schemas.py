"""Request/response models for /api/v1/countries and /api/v1/regions
(Issue #271).

Single resources are returned directly, collections in the canonical
`{items, pagination}` envelope (ADR-0014). `source_type`/
`source_reference` are the catalog entry's provenance; in an update an
explicit `null` clears them.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

# trips-and-tourist-profile.md §9: the approved Region semantic types.
RegionSemanticType = Literal["administrative_subject"]


class CountryCreateRequest(BaseModel):
    # ISO 3166-1 alpha-2; normalized to upper case.
    code: str = Field(min_length=1, max_length=8)
    name: str = Field(min_length=1, max_length=255)
    source_type: Optional[str] = Field(default=None, max_length=64)
    source_reference: Optional[str] = Field(default=None, max_length=500)


class CountryUpdateRequest(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=8)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    source_type: Optional[str] = Field(default=None, max_length=64)
    source_reference: Optional[str] = Field(default=None, max_length=500)


class CountryOut(BaseModel):
    id: UUID
    code: str
    name: str
    active: bool
    source_type: Optional[str]
    source_reference: Optional[str]
    created_at: datetime
    updated_at: datetime


class RegionCreateRequest(BaseModel):
    country_id: UUID
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    # Set at creation; not editable (absent from RegionUpdateRequest).
    semantic_type: RegionSemanticType
    source_type: Optional[str] = Field(default=None, max_length=64)
    source_reference: Optional[str] = Field(default=None, max_length=500)


class RegionUpdateRequest(BaseModel):
    country_id: Optional[UUID] = None
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    source_type: Optional[str] = Field(default=None, max_length=64)
    source_reference: Optional[str] = Field(default=None, max_length=500)


class RegionOut(BaseModel):
    id: UUID
    country_id: UUID
    code: str
    name: str
    semantic_type: RegionSemanticType
    active: bool
    source_type: Optional[str]
    source_reference: Optional[str]
    created_at: datetime
    updated_at: datetime
