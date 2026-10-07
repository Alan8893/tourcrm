"""Participant Export API — /api/v1/memberships/exports (TH-0118.4 /
Issue #218; docs/05-api/participant-export-api.md, PO decisions GAP-1..7
recorded on Issue #218).

    GET  /memberships/exports/fields   canonical export-field allowlist
    GET  /memberships/exports/filters  canonical filter values (participation_status)
    POST /memberships/exports          synchronous export (xlsx | pdf | print)
    POST /memberships/exports/preview  one page of the same dataset (Issue #299)

Administrator-only. Every format and the report preview go through the
same single `app.exports.service.build_participant_export` call — one
authorization policy and one canonical dataset; the format only selects the
renderer and the preview only a page of the rows (§7, §9). Status
codes: 401 unauthenticated, 403 not an Administrator of
the current Club or missing an existing `all`-scope read grant for the
exported data, 422 invalid context/filter combination or unknown/
unavailable/empty fields, 404 Group/Event nonexistent or of another Club
(identical, existence-hiding).

Not audit-required: no canonical audit action exists for participant
export (GAP-7) — an operational log line is written instead, carrying no
personal data. The export performs reads only.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.schemas import Pagination
from app.api.v1.membership_exports_schemas import (
    ExportColumnOut,
    ExportFieldOut,
    ExportFieldsOut,
    ExportFilterOptionOut,
    ExportFiltersOut,
    ParticipantExportPreviewOut,
    ParticipantExportPreviewRequest,
    ParticipantExportRequest,
    ParticipantExportSelection,
)
from app.db.session import get_db
from app.exports import rendering
from app.exports import service as export_service
from app.exports.authorization import require_club_administrator
from app.exports.fields import EXPORT_CONTEXTS, EXPORT_FIELDS
from app.exports.filters import PARTICIPATION_STATUS_OPTIONS
from app.imports.authorization import resolve_sole_club_id

router = APIRouter(prefix="/memberships/exports", tags=["membership-exports"])

logger = logging.getLogger("tourcrm.api")

_NOT_FOUND_DETAIL = {"group": "Group not found", "event": "Event not found"}

# Exported lists contain personal data: never cached by browsers/proxies.
_NO_STORE = {"Cache-Control": "no-store"}
# The print page is data rendered as HTML: no script, no external resource.
_PRINT_HEADERS = {
    **_NO_STORE,
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


def _attachment(extension: str, generated_at: datetime) -> str:
    return (
        f'attachment; filename="participants-{generated_at.strftime("%Y%m%d-%H%M")}.{extension}"'
    )


@router.get("/fields", response_model=ExportFieldsOut)
def list_export_fields(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> ExportFieldsOut:
    """The backend-authoritative canonical allowlist for the frontend
    multi-select. Selecting a field here grants nothing: `POST` re-validates
    and re-authorizes every requested field."""
    require_club_administrator(db, user_id=principal.user_id, club_id=resolve_sole_club_id(db))
    return ExportFieldsOut(
        items=[
            ExportFieldOut(
                field_code=field.code,
                label=field.label,
                contexts=[context for context in EXPORT_CONTEXTS if context in field.contexts],
            )
            for field in EXPORT_FIELDS
        ]
    )


@router.get("/filters", response_model=ExportFiltersOut)
def list_export_filters(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> ExportFiltersOut:
    """Backend-authoritative filter vocabularies for the export wizard —
    currently the canonical MVP `participation_status` values with their
    display labels (app.exports.filters). Same Administrator check as
    `/fields`; `POST` re-validates every submitted value."""
    require_club_administrator(db, user_id=principal.user_id, club_id=resolve_sole_club_id(db))
    return ExportFiltersOut(
        participation_status=[
            ExportFilterOptionOut(value=option.value, label=option.label)
            for option in PARTICIPATION_STATUS_OPTIONS
        ]
    )


def _build_dataset(
    db: Session,
    *,
    user_id: uuid.UUID,
    selection: ParticipantExportSelection,
    window: export_service.ExportWindow | None = None,
) -> export_service.ParticipantExportDataset:
    """The one call into the canonical export service, with its errors
    mapped to the documented status codes — shared by the export and the
    preview so neither can authorize, validate or select differently."""
    try:
        return export_service.build_participant_export(
            db,
            user_id=user_id,
            request=export_service.ParticipantExportRequest(
                context=selection.context,
                fields=tuple(selection.fields),
                group_id=selection.group_id,
                event_id=selection.event_id,
                membership_status=selection.membership_status,
                participation_status=selection.participation_status,
            ),
            window=window,
        )
    except export_service.ExportRequestError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, exc.message, details=exc.details
        ) from exc
    except export_service.ExportTargetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL[exc.target]
        ) from exc


@router.post("/preview", response_model=ParticipantExportPreviewOut)
def preview_participant_export(
    payload: ParticipantExportPreviewRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> ParticipantExportPreviewOut:
    """One page of the canonical export dataset for the interactive report
    «Участники мероприятий» (Issue #299). Same request selection, same
    authorization and validation as `POST /memberships/exports`; cells are
    the text the PDF/print representations render."""
    dataset = _build_dataset(
        db,
        user_id=principal.user_id,
        selection=payload,
        window=export_service.ExportWindow(page=payload.page, page_size=payload.page_size),
    )
    pages = (dataset.total + payload.page_size - 1) // payload.page_size if dataset.total else 0
    return ParticipantExportPreviewOut(
        title=dataset.title,
        columns=[
            ExportColumnOut(field_code=column.code, label=column.label)
            for column in dataset.columns
        ],
        items=[[rendering.format_cell(value) for value in row] for row in dataset.rows],
        pagination=Pagination(
            page=payload.page, page_size=payload.page_size, total=dataset.total, pages=pages
        ),
    )


@router.post(
    "",
    response_class=Response,
    responses={
        200: {
            "description": "The export file (xlsx/pdf, as attachment) or print page (html)",
            "content": {
                rendering.XLSX_MEDIA_TYPE: {},
                rendering.PDF_MEDIA_TYPE: {},
                "text/html": {},
            },
        }
    },
)
def export_participants(
    payload: ParticipantExportRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> Response:
    dataset = _build_dataset(db, user_id=principal.user_id, selection=payload)

    logger.info(
        "memberships.export.generated user_id=%s context=%s format=%s fields=%d rows=%d",
        principal.user_id,
        payload.context,
        payload.format,
        len(dataset.columns),
        len(dataset.rows),
    )

    if payload.format == "xlsx":
        return Response(
            content=rendering.render_xlsx(dataset),
            media_type=rendering.XLSX_MEDIA_TYPE,
            headers={
                **_NO_STORE,
                "Content-Disposition": _attachment("xlsx", dataset.generated_at),
            },
        )
    if payload.format == "pdf":
        return Response(
            content=rendering.render_pdf(dataset),
            media_type=rendering.PDF_MEDIA_TYPE,
            headers={**_NO_STORE, "Content-Disposition": _attachment("pdf", dataset.generated_at)},
        )
    return Response(
        content=rendering.render_print_html(dataset),
        media_type=rendering.PRINT_MEDIA_TYPE,
        headers=_PRINT_HEADERS,
    )
