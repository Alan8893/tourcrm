"""Request/response models for /api/v1/memberships/exports (TH-0118.4 /
Issue #218; docs/05-api/participant-export-api.md).

The request never carries a `person_ids` list or any other dataset source —
unknown keys are rejected (`extra="forbid"`), so the dataset is always
resolved server-side from the context (§3).
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.schemas import Pagination


class ParticipantExportSelection(BaseModel):
    """The dataset selection shared by the export and its report preview —
    context, filters and allowlisted fields. One shape, so the preview can
    never be asked for anything the export would not produce."""

    model_config = ConfigDict(extra="forbid")

    context: Literal["club", "group", "event", "group_event"]
    # Required only for the matching contexts; rejected elsewhere (422).
    group_id: UUID | None = None
    event_id: UUID | None = None
    # ClubMembership.status for `club`/`event`, GroupMembership
    # .membership_status for `group`/`group_event`; defaults to `active`.
    membership_status: str | None = Field(default=None, min_length=1, max_length=32)
    # EventParticipation.registration_status; `event`/`group_event` only;
    # omitted → every status. Closed canonical vocabulary published by
    # `GET /memberships/exports/filters` (app.exports.filters).
    participation_status: str | None = Field(default=None, min_length=1, max_length=32)
    # Validated against the canonical allowlist by the backend; the frontend
    # is never the source of truth for which fields exist.
    fields: list[Annotated[str, StringConstraints(max_length=64)]] = Field(max_length=64)


class ParticipantExportRequest(ParticipantExportSelection):
    """`POST /memberships/exports` — the whole dataset in one format."""

    format: Literal["xlsx", "pdf", "print"]


class ParticipantExportPreviewRequest(ParticipantExportSelection):
    """`POST /memberships/exports/preview` — one page of the same dataset
    (Issue #299 «Участники мероприятий» report preview). Same page bounds
    as every paginated v1 collection."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class ExportFieldOut(BaseModel):
    field_code: str
    label: str
    contexts: list[str]


class ExportFieldsOut(BaseModel):
    """`GET /memberships/exports/fields` — the canonical allowlist."""

    items: list[ExportFieldOut]


class ExportFilterOptionOut(BaseModel):
    value: str
    label: str


class ExportFiltersOut(BaseModel):
    """`GET /memberships/exports/filters` — backend-authoritative values
    (and display labels) for export filters whose vocabulary the frontend
    must not hardcode."""

    participation_status: list[ExportFilterOptionOut]


class ExportColumnOut(BaseModel):
    field_code: str
    label: str


class ParticipantExportPreviewOut(BaseModel):
    """`POST /memberships/exports/preview` — one page of the canonical
    export dataset. `columns` are the requested allowlisted fields in
    request order; every row of `items` holds one text cell per column,
    formatted exactly as the PDF/print representations show it (empty
    string for no value). `pagination.total` counts the whole dataset."""

    title: str
    columns: list[ExportColumnOut]
    items: list[list[str]]
    pagination: Pagination
