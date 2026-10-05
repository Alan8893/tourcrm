"""Country and Region catalog API (Issue #271).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §9
(Issue #258, PR #270).

    GET/POST   /countries
    GET/PATCH  /countries/{country_id}
    POST       /countries/{country_id}/activate
    POST       /countries/{country_id}/deactivate

    GET/POST   /regions
    GET/PATCH  /regions/{region_id}
    POST       /regions/{region_id}/activate
    POST       /regions/{region_id}/deactivate

There is deliberately no DELETE: entries are never physically removed —
the lifecycle is deactivate/reactivate, and deactivation never changes
Trips that already reference the entry. Once a Trip has used an entry,
its semantic fields are immutable by ordinary editing (§9): Country
`code`/`name` (409 `country_in_use`), Region `code`/`name`/`country_id`
(409 `region_in_use`); `active` and provenance stay editable.

Authorization (app.trips.geography_authorization), checked before any
record is loaded: reads require a `trip.read` grant; every mutation
requires the Administrator's `trip.manage` with `all` scope. No
Geography-specific permission exists. Mutations require the CSRF token.
"""

import logging
import uuid
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.geography_schemas import (
    CountryCreateRequest,
    CountryOut,
    CountryUpdateRequest,
    RegionCreateRequest,
    RegionOut,
    RegionUpdateRequest,
)
from app.db.session import get_db
from app.db.trips import Country, Region
from app.trips import geography as geography_service
from app.trips.geography_authorization import (
    require_geography_manager,
    require_geography_reader,
)

logger = logging.getLogger("tourcrm.api.geography")

countries_router = APIRouter(prefix="/countries", tags=["geography"])
regions_router = APIRouter(prefix="/regions", tags=["geography"])

_PROVENANCE_FIELDS = ("source_type", "source_reference")


def _country_out(row: Country) -> CountryOut:
    return CountryOut.model_validate(row, from_attributes=True)


def _region_out(row: Region) -> RegionOut:
    return RegionOut.model_validate(row, from_attributes=True)


def _provenance_changes(payload: BaseModel) -> dict[str, Any]:
    """Only provenance fields present in the body change; `null` clears."""
    return {
        field: getattr(payload, field)
        for field in _PROVENANCE_FIELDS
        if field in payload.model_fields_set
    }


def _raise(exc: geography_service.GeographyError) -> NoReturn:
    if isinstance(
        exc, (geography_service.CountryNotFoundError, geography_service.RegionNotFoundError)
    ):
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", str(exc)) from exc
    if isinstance(exc, geography_service.CountryCodeConflictError):
        raise APIError(status.HTTP_409_CONFLICT, "country_code_conflict", str(exc)) from exc
    if isinstance(exc, geography_service.RegionCodeConflictError):
        raise APIError(status.HTTP_409_CONFLICT, "region_code_conflict", str(exc)) from exc
    if isinstance(exc, geography_service.CountryInUseError):
        raise APIError(status.HTTP_409_CONFLICT, "country_in_use", str(exc)) from exc
    if isinstance(exc, geography_service.RegionInUseError):
        raise APIError(status.HTTP_409_CONFLICT, "region_in_use", str(exc)) from exc
    raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_geography", str(exc)) from exc


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total else 0


# --- Country -------------------------------------------------------------------------


@countries_router.get("", response_model=CollectionResponse[CountryOut])
def list_countries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    active: bool | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[CountryOut]:
    require_geography_reader(db, user_id=principal.user_id)
    rows, total = geography_service.list_countries(
        db, page=page, page_size=page_size, active=active
    )
    return CollectionResponse(
        items=[_country_out(row) for row in rows],
        pagination=Pagination(
            page=page, page_size=page_size, total=total, pages=_pages(total, page_size)
        ),
    )


@countries_router.post("", status_code=status.HTTP_201_CREATED, response_model=CountryOut)
def create_country(
    payload: CountryCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> CountryOut:
    require_geography_manager(db, user_id=principal.user_id)
    try:
        row = geography_service.create_country(
            db,
            code=payload.code,
            name=payload.name,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
        )
    except geography_service.GeographyError as exc:
        _raise(exc)
    logger.info("countries.created id=%s user_id=%s", row.id, principal.user_id)
    return _country_out(row)


@countries_router.get("/{country_id}", response_model=CountryOut)
def get_country(
    country_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CountryOut:
    require_geography_reader(db, user_id=principal.user_id)
    try:
        return _country_out(geography_service.get_country(db, country_id))
    except geography_service.GeographyError as exc:
        _raise(exc)


@countries_router.patch("/{country_id}", response_model=CountryOut)
def update_country(
    country_id: uuid.UUID,
    payload: CountryUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> CountryOut:
    require_geography_manager(db, user_id=principal.user_id)
    try:
        row = geography_service.update_country(
            db,
            country_id=country_id,
            code=payload.code,
            name=payload.name,
            **_provenance_changes(payload),
        )
    except geography_service.GeographyError as exc:
        _raise(exc)
    return _country_out(row)


def _set_country_active(
    db: Session, principal: CurrentPrincipal, country_id: uuid.UUID, active: bool
) -> CountryOut:
    require_geography_manager(db, user_id=principal.user_id)
    try:
        row = geography_service.set_country_active(db, country_id=country_id, active=active)
    except geography_service.GeographyError as exc:
        _raise(exc)
    logger.info("countries.active id=%s active=%s user_id=%s", row.id, active, principal.user_id)
    return _country_out(row)


@countries_router.post("/{country_id}/activate", response_model=CountryOut)
def activate_country(
    country_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> CountryOut:
    return _set_country_active(db, principal, country_id, True)


@countries_router.post("/{country_id}/deactivate", response_model=CountryOut)
def deactivate_country(
    country_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> CountryOut:
    return _set_country_active(db, principal, country_id, False)


# --- Region --------------------------------------------------------------------------


@regions_router.get("", response_model=CollectionResponse[RegionOut])
def list_regions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    country_id: uuid.UUID | None = Query(default=None),
    active: bool | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[RegionOut]:
    require_geography_reader(db, user_id=principal.user_id)
    rows, total = geography_service.list_regions(
        db, page=page, page_size=page_size, country_id=country_id, active=active
    )
    return CollectionResponse(
        items=[_region_out(row) for row in rows],
        pagination=Pagination(
            page=page, page_size=page_size, total=total, pages=_pages(total, page_size)
        ),
    )


@regions_router.post("", status_code=status.HTTP_201_CREATED, response_model=RegionOut)
def create_region(
    payload: RegionCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RegionOut:
    require_geography_manager(db, user_id=principal.user_id)
    try:
        row = geography_service.create_region(
            db,
            country_id=payload.country_id,
            code=payload.code,
            name=payload.name,
            semantic_type=payload.semantic_type,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
        )
    except geography_service.CountryNotFoundError as exc:
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "country_not_found", str(exc)) from exc
    except geography_service.GeographyError as exc:
        _raise(exc)
    logger.info("regions.created id=%s user_id=%s", row.id, principal.user_id)
    return _region_out(row)


@regions_router.get("/{region_id}", response_model=RegionOut)
def get_region(
    region_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> RegionOut:
    require_geography_reader(db, user_id=principal.user_id)
    try:
        return _region_out(geography_service.get_region(db, region_id))
    except geography_service.GeographyError as exc:
        _raise(exc)


@regions_router.patch("/{region_id}", response_model=RegionOut)
def update_region(
    region_id: uuid.UUID,
    payload: RegionUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RegionOut:
    require_geography_manager(db, user_id=principal.user_id)
    try:
        # The Region is loaded (and locked) first: a missing Region is 404,
        # a missing target Country 422.
        row = geography_service.update_region(
            db,
            region_id=region_id,
            country_id=payload.country_id,
            code=payload.code,
            name=payload.name,
            **_provenance_changes(payload),
        )
    except geography_service.CountryNotFoundError as exc:
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "country_not_found", str(exc)) from exc
    except geography_service.GeographyError as exc:
        _raise(exc)
    return _region_out(row)


def _set_region_active(
    db: Session, principal: CurrentPrincipal, region_id: uuid.UUID, active: bool
) -> RegionOut:
    require_geography_manager(db, user_id=principal.user_id)
    try:
        row = geography_service.set_region_active(db, region_id=region_id, active=active)
    except geography_service.GeographyError as exc:
        _raise(exc)
    logger.info("regions.active id=%s active=%s user_id=%s", row.id, active, principal.user_id)
    return _region_out(row)


@regions_router.post("/{region_id}/activate", response_model=RegionOut)
def activate_region(
    region_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RegionOut:
    return _set_region_active(db, principal, region_id, True)


@regions_router.post("/{region_id}/deactivate", response_model=RegionOut)
def deactivate_region(
    region_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RegionOut:
    return _set_region_active(db, principal, region_id, False)
