"""TourismType catalog API — /api/v1/tourism-types (Issue #264).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §3
(Issue #256).

    GET/POST   /tourism-types
    GET/PATCH  /tourism-types/{tourism_type_id}
    POST       /tourism-types/{tourism_type_id}/activate
    POST       /tourism-types/{tourism_type_id}/deactivate

There is deliberately no DELETE: catalog entries are never physically
removed — the lifecycle is deactivate/reactivate, and deactivation never
changes Trips that already reference the entry.

Authorization (app.trips.tourism_type_authorization), checked before any
record is loaded: reads require a `trip.read` grant; every mutation
requires the Administrator's `trip.manage` with `all` scope. No
TourismType-specific permission exists. Mutations require the CSRF
token.
"""

import logging
import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.tourism_types_schemas import (
    TourismTypeCreateRequest,
    TourismTypeOut,
    TourismTypeUpdateRequest,
)
from app.db.session import get_db
from app.db.trips import TourismType
from app.trips import tourism_types as tourism_type_service
from app.trips.tourism_type_authorization import (
    require_tourism_type_manager,
    require_tourism_type_reader,
)

logger = logging.getLogger("tourcrm.api.tourism_types")

router = APIRouter(prefix="/tourism-types", tags=["tourism-types"])


def _out(row: TourismType) -> TourismTypeOut:
    return TourismTypeOut.model_validate(row, from_attributes=True)


def _raise(exc: tourism_type_service.TourismTypeError) -> NoReturn:
    if isinstance(exc, tourism_type_service.TourismTypeNotFoundError):
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Tourism type not found") from exc
    if isinstance(exc, tourism_type_service.TourismTypeCodeConflictError):
        raise APIError(status.HTTP_409_CONFLICT, "tourism_type_code_conflict", str(exc)) from exc
    raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_tourism_type", str(exc)) from exc


@router.get("", response_model=CollectionResponse[TourismTypeOut])
def list_tourism_types(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    active: bool | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[TourismTypeOut]:
    require_tourism_type_reader(db, user_id=principal.user_id)
    rows, total = tourism_type_service.list_tourism_types(
        db, page=page, page_size=page_size, active=active
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_out(row) for row in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TourismTypeOut)
def create_tourism_type(
    payload: TourismTypeCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TourismTypeOut:
    require_tourism_type_manager(db, user_id=principal.user_id)
    try:
        row = tourism_type_service.create_tourism_type(db, code=payload.code, name=payload.name)
    except tourism_type_service.TourismTypeError as exc:
        _raise(exc)
    logger.info("tourism_types.created id=%s user_id=%s", row.id, principal.user_id)
    return _out(row)


@router.get("/{tourism_type_id}", response_model=TourismTypeOut)
def get_tourism_type(
    tourism_type_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> TourismTypeOut:
    require_tourism_type_reader(db, user_id=principal.user_id)
    try:
        return _out(tourism_type_service.get_tourism_type(db, tourism_type_id))
    except tourism_type_service.TourismTypeError as exc:
        _raise(exc)


@router.patch("/{tourism_type_id}", response_model=TourismTypeOut)
def update_tourism_type(
    tourism_type_id: uuid.UUID,
    payload: TourismTypeUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TourismTypeOut:
    require_tourism_type_manager(db, user_id=principal.user_id)
    try:
        row = tourism_type_service.update_tourism_type(
            db, tourism_type_id=tourism_type_id, code=payload.code, name=payload.name
        )
    except tourism_type_service.TourismTypeError as exc:
        _raise(exc)
    return _out(row)


def _set_active(
    db: Session, principal: CurrentPrincipal, tourism_type_id: uuid.UUID, active: bool
) -> TourismTypeOut:
    require_tourism_type_manager(db, user_id=principal.user_id)
    try:
        row = tourism_type_service.set_tourism_type_active(
            db, tourism_type_id=tourism_type_id, active=active
        )
    except tourism_type_service.TourismTypeError as exc:
        _raise(exc)
    logger.info(
        "tourism_types.active id=%s active=%s user_id=%s", row.id, active, principal.user_id
    )
    return _out(row)


@router.post("/{tourism_type_id}/activate", response_model=TourismTypeOut)
def activate_tourism_type(
    tourism_type_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TourismTypeOut:
    return _set_active(db, principal, tourism_type_id, True)


@router.post("/{tourism_type_id}/deactivate", response_model=TourismTypeOut)
def deactivate_tourism_type(
    tourism_type_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TourismTypeOut:
    return _set_active(db, principal, tourism_type_id, False)
