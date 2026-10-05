"""Request/response models for /api/v1/tourism-types (Issue #264).

Single resources are returned directly, collections in the canonical
`{items, pagination}` envelope (ADR-0014).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TourismTypeCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class TourismTypeUpdateRequest(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class TourismTypeOut(BaseModel):
    id: UUID
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime
