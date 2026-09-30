"""Participant Export API — /api/v1/memberships/exports (TH-0118.4 /
Issue #218; docs/05-api/participant-export-api.md, PO decisions GAP-1..7
recorded on Issue #218).

    GET  /memberships/exports/fields   canonical export-field allowlist
    POST /memberships/exports          synchronous export (xlsx | pdf | print)

Administrator-only. Every format goes through the same single
`app.exports.service.build_participant_export` call — one authorization
policy and one canonical dataset; the format only selects the renderer
(§7, §9). Status codes: 401 unauthenticated, 403 not an Administrator of
the current Club or missing an existing `all`-scope read grant for the
exported data, 422 invalid context/filter combination or unknown/
unavailable/empty fields, 404 Group/Event nonexistent or of another Club
(identical, existence-hiding).

Not audit-required: no canonical audit action exists for participant
export (GAP-7) — an operational log line is written instead, carrying no
personal data. The export performs reads only.
"""

import logging
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
from app.api.v1.membership_exports_schemas import (
    ExportFieldOut,
    ExportFieldsOut,
    ParticipantExportRequest,
)
from app.db.session import get_db
from app.exports import rendering
from app.exports import service as export_service
from app.exports.authorization import require_club_administrator
from app.exports.fields import EXPORT_CONTEXTS, EXPORT_FIELDS
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
    try:
        dataset = export_service.build_participant_export(
            db,
            user_id=principal.user_id,
            request=export_service.ParticipantExportRequest(
                context=payload.context,
                fields=tuple(payload.fields),
                group_id=payload.group_id,
                event_id=payload.event_id,
                membership_status=payload.membership_status,
                participation_status=payload.participation_status,
            ),
        )
    except export_service.ExportRequestError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, exc.message, details=exc.details
        ) from exc
    except export_service.ExportTargetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL[exc.target]
        ) from exc

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
