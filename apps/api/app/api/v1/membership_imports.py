"""Participant import job API — /api/v1/memberships/imports (TH-0118.1 /
Issue #185).

Canonical sources: docs/05-api/people-api.md §22,
docs/05-api/endpoint-inventory.md §4.1,
docs/02-requirements/roles-and-permissions.md §4 (`membership.import`).

    POST /memberships/imports                      create job (multipart)
    GET  /memberships/imports/{import_id}          status + statistics
    GET  /memberships/imports/{import_id}/errors   paginated errors

Authorization: `POST` has no object yet, so — like `POST /memberships` —
it uses the generic 403 AuthorizationDenied contract: `membership.import`
with `all` scope in the installation's sole Club (the Club the new job is
bound to; the client never supplies `club_id`). Both `GET` endpoints apply
the ImportJob object policy (app.imports.authorization) with
existence-hiding, mirroring app.api.v1.memberships/app.api.v1.persons: a
job that does not exist and a job the requester may not access receive an
identical 404.

Routers stay thin: lifecycle, storage and query logic live in
app.imports.service/app.imports.queries. Creating a job never parses,
validates or applies the file, and is not audit-required (people-api.md
§22).
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.membership_imports_schemas import (
    ImportJobCreatedOut,
    ImportJobErrorOut,
    ImportJobOut,
    ImportJobStatisticsOut,
)
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.imports import ImportJob, ImportJobError
from app.db.session import get_db
from app.imports import queries as import_queries
from app.imports.authorization import (
    PERMISSION_CODE,
    can_access_import_job,
    resolve_sole_club_id,
)
from app.imports.service import UnsupportedImportFormatError, create_import_job
from app.storage.file_storage import FileStorage
from app.storage.local import get_file_storage

router = APIRouter(prefix="/memberships/imports", tags=["membership-imports"])

_NOT_FOUND_DETAIL = "Import job not found"


def _get_authorized_import_job_or_404(
    db: Session, *, import_id: uuid.UUID, user_id: uuid.UUID
) -> ImportJob:
    job = import_queries.get_import_job(db, import_job_id=import_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    if not can_access_import_job(db, job=job, user_id=user_id):
        # Deliberately identical to "does not exist" above — see module
        # docstring.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return job


def _import_job_out(job: ImportJob, *, error_count: int) -> ImportJobOut:
    return ImportJobOut(
        import_id=job.id,
        club_id=job.club_id,
        created_by_user_id=job.created_by_user_id,
        status=job.status,
        format=job.source_format,
        statistics=ImportJobStatisticsOut(
            total_records=job.total_records,
            valid_records=job.valid_records,
            invalid_records=job.invalid_records,
            created_records=job.created_records,
            updated_records=job.updated_records,
            skipped_records=job.skipped_records,
            error_count=error_count,
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _import_job_error_out(error: ImportJobError) -> ImportJobErrorOut:
    return ImportJobErrorOut(
        id=error.id,
        row_number=error.row_number,
        field=error.field,
        code=error.code,
        message=error.message,
        created_at=error.created_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ImportJobCreatedOut)
def create_membership_import(
    file: UploadFile = File(...),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
    _csrf: None = Depends(require_csrf_token),
) -> ImportJobCreatedOut:
    """Create an import job in `uploaded` from a CSV/XLSX file. Stores the
    source file and its reference only — no parsing, validation or apply.
    """
    club_id = resolve_sole_club_id(db)
    Authorizer(session=db, user_id=principal.user_id, permission_code=PERMISSION_CODE).check(
        ResourceContext(club_id=club_id)
    )

    original_name = (file.filename or "").strip()
    if len(original_name) > 255:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "filename_too_long", "File name is too long"
        )
    mime_type = (file.content_type or "application/octet-stream").strip()
    if len(mime_type) > 255:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "mime_type_too_long", "MIME type is too long"
        )

    try:
        job = create_import_job(
            db,
            storage,
            club_id=club_id,
            content=file.file.read(),
            original_name=original_name,
            mime_type=mime_type,
            actor_user_id=principal.user_id,
        )
    except UnsupportedImportFormatError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unsupported_import_format",
            "Import file must be CSV or XLSX",
        ) from exc
    return ImportJobCreatedOut(
        import_id=job.id,
        status=job.status,
        format=job.source_format,
        created_at=job.created_at,
    )


@router.get("/{import_id}", response_model=ImportJobOut)
def get_membership_import(
    import_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> ImportJobOut:
    job = _get_authorized_import_job_or_404(db, import_id=import_id, user_id=principal.user_id)
    error_count = import_queries.count_import_job_errors(db, import_job_id=job.id)
    return _import_job_out(job, error_count=error_count)


@router.get("/{import_id}/errors", response_model=CollectionResponse[ImportJobErrorOut])
def list_membership_import_errors(
    import_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[ImportJobErrorOut]:
    job = _get_authorized_import_job_or_404(db, import_id=import_id, user_id=principal.user_id)
    rows, total = import_queries.list_import_job_errors_page(
        db, import_job_id=job.id, page=page, page_size=page_size
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_import_job_error_out(error) for error in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )
