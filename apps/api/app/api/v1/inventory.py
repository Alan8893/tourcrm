"""Inventory Foundation API — /api/v1/inventory (TH-0121 / Issue #230).

Canonical source: docs/04-domain/inventory.md; API conventions from
docs/05-api/api-conventions.md / ADR-0014 (envelopes) and the News router
(action endpoints, CSRF on every state change).

    GET/POST   /inventory/categories
    GET/PATCH  /inventory/categories/{category_id}
    POST       /inventory/categories/{category_id}/archive
    GET/POST   /inventory/units
    GET/PATCH  /inventory/units/{unit_id}
    POST       /inventory/units/{unit_id}/archive
    GET/POST   /inventory/storage-locations
    GET/PATCH  /inventory/storage-locations/{location_id}
    POST       /inventory/storage-locations/{location_id}/archive
    GET/POST   /inventory/items
    GET/PATCH  /inventory/items/{item_id}
    POST       /inventory/items/{item_id}/archive

There is deliberately no DELETE anywhere: records are never physically
removed, "delete" is the irreversible archive action (inventory.md §17).
No movement endpoint exists: the Foundation only fixes the movement
journal model; movement workflows are later slices.

Authorization (inventory.md §3): every endpoint — reads included — requires
the Administrator (app.inventory.authorization), checked before the target
record is loaded, so a non-Administrator gets the same 403 whether or not
the id exists. A record of another Club is a 404.
"""

import logging
import uuid
from typing import Any, NoReturn, TypeVar

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.inventory_schemas import (
    InventoryCategoryCreateRequest,
    InventoryCategoryOut,
    InventoryCategoryUpdateRequest,
    InventoryItemCreateRequest,
    InventoryItemOut,
    InventoryItemUpdateRequest,
    InventoryStatusFilterLiteral,
    InventoryStorageLocationCreateRequest,
    InventoryStorageLocationOut,
    InventoryStorageLocationUpdateRequest,
    InventoryUnitCreateRequest,
    InventoryUnitOut,
    InventoryUnitUpdateRequest,
)
from app.db.inventory import (
    InventoryCategory,
    InventoryItem,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.db.session import get_db
from app.inventory import service as inventory_service
from app.inventory.authorization import require_inventory_administrator
from app.inventory.lifecycle import (
    AccountingModeLockedError,
    ArchivedReferenceError,
    InvalidInventoryDataError,
    InvalidLocationParentError,
    InventoryDomainError,
    InventoryRecordArchivedError,
    LocationHasActiveChildrenError,
    SystemUnitImmutableError,
    UnitLockedError,
)
from app.inventory.queries import InventoryRecord, get_record, list_records

logger = logging.getLogger("tourcrm.api.inventory")

router = APIRouter(prefix="/inventory", tags=["inventory"])

OutT = TypeVar("OutT", bound=BaseModel)

_DOMAIN_ERRORS = (
    InventoryDomainError,
    inventory_service.InventoryReferenceNotFoundError,
    inventory_service.InventoryNameConflictError,
)


def _raise_for_domain_error(exc: Exception) -> NoReturn:
    conflict = status.HTTP_409_CONFLICT
    unprocessable = status.HTTP_422_UNPROCESSABLE_ENTITY
    if isinstance(exc, InventoryRecordArchivedError):
        raise APIError(conflict, "inventory_record_archived", str(exc)) from exc
    if isinstance(exc, SystemUnitImmutableError):
        raise APIError(conflict, "system_unit_immutable", str(exc)) from exc
    if isinstance(exc, AccountingModeLockedError):
        raise APIError(conflict, "accounting_mode_locked", str(exc)) from exc
    if isinstance(exc, UnitLockedError):
        raise APIError(conflict, "unit_locked", str(exc)) from exc
    if isinstance(exc, LocationHasActiveChildrenError):
        raise APIError(conflict, "location_has_active_children", str(exc)) from exc
    if isinstance(exc, inventory_service.InventoryNameConflictError):
        raise APIError(conflict, "name_conflict", str(exc)) from exc
    if isinstance(exc, ArchivedReferenceError):
        raise APIError(unprocessable, "archived_reference", str(exc)) from exc
    if isinstance(exc, inventory_service.InventoryReferenceNotFoundError):
        raise APIError(unprocessable, "invalid_reference", str(exc)) from exc
    if isinstance(exc, InvalidLocationParentError):
        raise APIError(unprocessable, "invalid_parent", str(exc)) from exc
    if isinstance(exc, InvalidInventoryDataError):
        raise APIError(unprocessable, "invalid_inventory_data", str(exc)) from exc
    raise exc


def _not_found(label: str) -> APIError:
    return APIError(status.HTTP_404_NOT_FOUND, "not_found", f"{label} not found")


def _managed(
    db: Session,
    model: type[InventoryRecord],
    *,
    record_id: uuid.UUID,
    club_id: uuid.UUID,
    label: str,
) -> InventoryRecord:
    record = get_record(db, model, record_id=record_id, club_id=club_id, for_update=True)
    if record is None:
        raise _not_found(label)
    return record


def _read(
    db: Session,
    model: type[InventoryRecord],
    *,
    record_id: uuid.UUID,
    club_id: uuid.UUID,
    label: str,
) -> InventoryRecord:
    record = get_record(db, model, record_id=record_id, club_id=club_id)
    if record is None:
        raise _not_found(label)
    return record


def _collection(
    db: Session,
    model: type[InventoryRecord],
    out: type[OutT],
    *,
    club_id: uuid.UUID,
    status_filter: str,
    page: int,
    page_size: int,
) -> CollectionResponse[OutT]:
    rows, total = list_records(
        db, model, club_id=club_id, status=status_filter, page=page, page_size=page_size
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[out.model_validate(row, from_attributes=True) for row in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


def _reject_nulls(fields: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        if name in fields and fields[name] is None:
            raise APIError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_inventory_data",
                f"{name} cannot be null",
            )


_PAGE = Query(default=1, ge=1)
_PAGE_SIZE = Query(default=50, ge=1, le=200)
# Default `active`: the selection view. `archived`/`all` show history
# (archived records stay readable, G12/G13).
_STATUS = Query(default="active", alias="status")


# --- categories ------------------------------------------------------------------

_CATEGORY = "Inventory category"


def _category_out(category: InventoryCategory) -> InventoryCategoryOut:
    return InventoryCategoryOut.model_validate(category, from_attributes=True)


@router.get("/categories", response_model=CollectionResponse[InventoryCategoryOut])
def list_categories(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    status_: InventoryStatusFilterLiteral = _STATUS,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryCategoryOut]:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _collection(
        db,
        InventoryCategory,
        InventoryCategoryOut,
        club_id=club_id,
        status_filter=status_,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/categories", status_code=status.HTTP_201_CREATED, response_model=InventoryCategoryOut
)
def create_category(
    payload: InventoryCategoryCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryCategoryOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        category = inventory_service.create_category(
            db, club_id=club_id, name=payload.name, created_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.category.create.success id=%s", category.id)
    return _category_out(category)


@router.get("/categories/{category_id}", response_model=InventoryCategoryOut)
def get_category(
    category_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> InventoryCategoryOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _category_out(
        _read(db, InventoryCategory, record_id=category_id, club_id=club_id, label=_CATEGORY)
    )


@router.patch("/categories/{category_id}", response_model=InventoryCategoryOut)
def update_category(
    category_id: uuid.UUID,
    payload: InventoryCategoryUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryCategoryOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    category = _managed(
        db, InventoryCategory, record_id=category_id, club_id=club_id, label=_CATEGORY
    )
    try:
        category = inventory_service.update_category(
            db, category=category, name=payload.name, updated_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.category.update.success id=%s", category.id)
    return _category_out(category)


@router.post("/categories/{category_id}/archive", response_model=InventoryCategoryOut)
def archive_category(
    category_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryCategoryOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    category = _managed(
        db, InventoryCategory, record_id=category_id, club_id=club_id, label=_CATEGORY
    )
    try:
        category = inventory_service.archive_category(
            db, category=category, updated_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.category.archive.success id=%s", category.id)
    return _category_out(category)


# --- units -----------------------------------------------------------------------

_UNIT = "Inventory unit"


def _unit_out(unit: InventoryUnit) -> InventoryUnitOut:
    return InventoryUnitOut.model_validate(unit, from_attributes=True)


@router.get("/units", response_model=CollectionResponse[InventoryUnitOut])
def list_units(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    status_: InventoryStatusFilterLiteral = _STATUS,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryUnitOut]:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _collection(
        db,
        InventoryUnit,
        InventoryUnitOut,
        club_id=club_id,
        status_filter=status_,
        page=page,
        page_size=page_size,
    )


@router.post("/units", status_code=status.HTTP_201_CREATED, response_model=InventoryUnitOut)
def create_unit(
    payload: InventoryUnitCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryUnitOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        unit = inventory_service.create_unit(
            db, club_id=club_id, name=payload.name, created_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.unit.create.success id=%s", unit.id)
    return _unit_out(unit)


@router.get("/units/{unit_id}", response_model=InventoryUnitOut)
def get_unit(
    unit_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> InventoryUnitOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _unit_out(_read(db, InventoryUnit, record_id=unit_id, club_id=club_id, label=_UNIT))


@router.patch("/units/{unit_id}", response_model=InventoryUnitOut)
def update_unit(
    unit_id: uuid.UUID,
    payload: InventoryUnitUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryUnitOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    unit = _managed(db, InventoryUnit, record_id=unit_id, club_id=club_id, label=_UNIT)
    try:
        unit = inventory_service.update_unit(
            db, unit=unit, name=payload.name, updated_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.unit.update.success id=%s", unit.id)
    return _unit_out(unit)


@router.post("/units/{unit_id}/archive", response_model=InventoryUnitOut)
def archive_unit(
    unit_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryUnitOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    unit = _managed(db, InventoryUnit, record_id=unit_id, club_id=club_id, label=_UNIT)
    try:
        unit = inventory_service.archive_unit(db, unit=unit, updated_by=principal.user_id)
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.unit.archive.success id=%s", unit.id)
    return _unit_out(unit)


# --- storage locations -------------------------------------------------------------

_LOCATION = "Inventory storage location"


def _location_out(location: InventoryStorageLocation) -> InventoryStorageLocationOut:
    return InventoryStorageLocationOut.model_validate(location, from_attributes=True)


@router.get("/storage-locations", response_model=CollectionResponse[InventoryStorageLocationOut])
def list_storage_locations(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    status_: InventoryStatusFilterLiteral = _STATUS,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryStorageLocationOut]:
    """Flat list; the tree is expressed by `parent_id`."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _collection(
        db,
        InventoryStorageLocation,
        InventoryStorageLocationOut,
        club_id=club_id,
        status_filter=status_,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/storage-locations",
    status_code=status.HTTP_201_CREATED,
    response_model=InventoryStorageLocationOut,
)
def create_storage_location(
    payload: InventoryStorageLocationCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryStorageLocationOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        location = inventory_service.create_location(
            db,
            club_id=club_id,
            name=payload.name,
            parent_id=payload.parent_id,
            created_by=principal.user_id,
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.location.create.success id=%s", location.id)
    return _location_out(location)


@router.get("/storage-locations/{location_id}", response_model=InventoryStorageLocationOut)
def get_storage_location(
    location_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> InventoryStorageLocationOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _location_out(
        _read(db, InventoryStorageLocation, record_id=location_id, club_id=club_id, label=_LOCATION)
    )


@router.patch("/storage-locations/{location_id}", response_model=InventoryStorageLocationOut)
def update_storage_location(
    location_id: uuid.UUID,
    payload: InventoryStorageLocationUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryStorageLocationOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    location = _managed(
        db, InventoryStorageLocation, record_id=location_id, club_id=club_id, label=_LOCATION
    )
    fields = payload.model_dump(exclude_unset=True)
    _reject_nulls(fields, ("name",))
    try:
        location = inventory_service.update_location(
            db, location=location, fields=fields, updated_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.location.update.success id=%s", location.id)
    return _location_out(location)


@router.post("/storage-locations/{location_id}/archive", response_model=InventoryStorageLocationOut)
def archive_storage_location(
    location_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryStorageLocationOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    location = _managed(
        db, InventoryStorageLocation, record_id=location_id, club_id=club_id, label=_LOCATION
    )
    try:
        location = inventory_service.archive_location(
            db, location=location, updated_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.location.archive.success id=%s", location.id)
    return _location_out(location)


# --- nomenclature -------------------------------------------------------------------

_ITEM = "Inventory item"
_NON_NULLABLE_ITEM_FIELDS = ("name", "category_id", "unit_id", "accounting_mode")


def _item_out(item: InventoryItem) -> InventoryItemOut:
    return InventoryItemOut.model_validate(item, from_attributes=True)


@router.get("/items", response_model=CollectionResponse[InventoryItemOut])
def list_items(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    status_: InventoryStatusFilterLiteral = _STATUS,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryItemOut]:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _collection(
        db,
        InventoryItem,
        InventoryItemOut,
        club_id=club_id,
        status_filter=status_,
        page=page,
        page_size=page_size,
    )


@router.post("/items", status_code=status.HTTP_201_CREATED, response_model=InventoryItemOut)
def create_item(
    payload: InventoryItemCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryItemOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        item = inventory_service.create_item(
            db,
            club_id=club_id,
            name=payload.name,
            category_id=payload.category_id,
            unit_id=payload.unit_id,
            accounting_mode=payload.accounting_mode,
            current_cost_minor=payload.current_cost_minor,
            created_by=principal.user_id,
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.item.create.success id=%s", item.id)
    return _item_out(item)


@router.get("/items/{item_id}", response_model=InventoryItemOut)
def get_item(
    item_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> InventoryItemOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _item_out(_read(db, InventoryItem, record_id=item_id, club_id=club_id, label=_ITEM))


@router.patch("/items/{item_id}", response_model=InventoryItemOut)
def update_item(
    item_id: uuid.UUID,
    payload: InventoryItemUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryItemOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    item = _managed(db, InventoryItem, record_id=item_id, club_id=club_id, label=_ITEM)
    fields = payload.model_dump(exclude_unset=True)
    _reject_nulls(fields, _NON_NULLABLE_ITEM_FIELDS)
    try:
        item = inventory_service.update_item(
            db, item=item, fields=fields, updated_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.item.update.success id=%s", item.id)
    return _item_out(item)


@router.post("/items/{item_id}/archive", response_model=InventoryItemOut)
def archive_item(
    item_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryItemOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    item = _managed(db, InventoryItem, record_id=item_id, club_id=club_id, label=_ITEM)
    try:
        item = inventory_service.archive_item(db, item=item, updated_by=principal.user_id)
    except _DOMAIN_ERRORS as exc:
        _raise_for_domain_error(exc)
    logger.info("inventory.item.archive.success id=%s", item.id)
    return _item_out(item)
