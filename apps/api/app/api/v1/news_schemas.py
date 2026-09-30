"""Request/response models for /api/v1/news (TH-0120 / Issue #227;
docs/04-ux/news.md §6). Single resources are returned directly and the
list uses the canonical `{items, pagination}` envelope (ADR-0014).
"""

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NewsStatusLiteral = Literal["draft", "published", "archived"]
NewsCreateStatusLiteral = Literal["draft", "published"]
NewsAudienceLiteral = Literal["club", "groups"]


class NewsCreateRequest(BaseModel):
    """`status` is the management form's publication status: `draft`
    (default) or `published`. `archived` is reachable only through
    `POST /news/{id}/archive`. No `club_id`: News always belongs to the
    installation's Club."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=255)
    body: str
    status: NewsCreateStatusLiteral = "draft"
    audience_type: NewsAudienceLiteral
    group_ids: list[UUID] = Field(default_factory=list)
    event_date: Optional[date] = None
    location: Optional[str] = Field(default=None, max_length=255)
    event_id: Optional[UUID] = None


class NewsUpdateRequest(BaseModel):
    """PATCH: content, audience and Event link. No `status` — lifecycle
    changes only through the publish/archive actions."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = None
    audience_type: Optional[NewsAudienceLiteral] = None
    group_ids: Optional[list[UUID]] = None
    event_date: Optional[date] = None
    location: Optional[str] = Field(default=None, max_length=255)
    event_id: Optional[UUID] = None


class NewsLinkedEventOut(BaseModel):
    """The linked Event, exposed only when the reader may themselves read
    that Event under the existing Event authorization — so News never
    becomes a side channel into an Event the reader cannot open."""

    id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    status: str


class NewsOut(BaseModel):
    id: UUID
    title: str
    body: str
    status: NewsStatusLiteral
    published_at: Optional[datetime]
    archived_at: Optional[datetime]
    event_date: Optional[date]
    location: Optional[str]
    audience_type: NewsAudienceLiteral
    # Administrator only (management/edit); `null` for every other reader:
    # audience configuration is a management concern and the frontend never
    # derives visibility from it.
    group_ids: Optional[list[UUID]]
    # Version of the current image (the image URL is
    # `/api/v1/news/{id}/image?v={image_file_id}`); `null` = no image.
    image_file_id: Optional[UUID]
    linked_event: Optional[NewsLinkedEventOut]
    created_by: UUID
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime
