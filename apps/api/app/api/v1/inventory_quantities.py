"""Inventory Slice 3 API — quantity stock and movements (Issue #230;
docs/04-domain/inventory.md §6, §13, §16, §17).

    GET  /inventory/stock                          stock rows (filters: item_id, location)
    GET  /inventory/items/{item_id}/stock          stock of one quantity item
    GET  /inventory/items/{item_id}/movements      chronological history of one item
    POST /inventory/items/{item_id}/receipts       receipt
    POST /inventory/items/{item_id}/transfers      transfer between locations
    POST /inventory/items/{item_id}/write-offs     write-off (reason required)
    POST /inventory/items/{item_id}/write-offs/{movement_id}/reverse
                                                   full write-off reversal

Stock is never set directly: it changes only through these movements.
The instance write-off reversal (`POST /inventory/movements/{id}/reverse`,
Slice 2) is unchanged.

Authorization (inventory.md §3): every endpoint requires the Administrator
(app.inventory.authorization), checked before the item is loaded, so a
non-Administrator gets the same 403 whether or not the id exists. An item
or movement of another Club is a 404.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.inventory_errors import INVENTORY_DOMAIN_ERRORS, raise_inventory_domain_error
from app.api.v1.inventory_instances_schemas import InventoryMovementOut
from app.api.v1.inventory_quantities_schemas import (
    InventoryReceiptRequest,
    InventoryStockOut,
    InventoryTransferRequest,
    InventoryWriteOffRequest,
    InventoryWriteOffReverseRequest,
)
from app.db.inventory import InventoryItem, InventoryMovement
from app.db.session import get_db
from app.inventory import quantities as quantity_service
from app.inventory.authorization import require_inventory_administrator
from app.inventory.lifecycle import ensure_quantity_mode
from app.inventory.queries import get_record, list_item_movements, list_stock

logger = logging.getLogger("tourcrm.api.inventory.quantities")

router = APIRouter(prefix="/inventory", tags=["inventory"])

_PAGE = Query(default=1, ge=1)
_PAGE_SIZE = Query(default=50, ge=1, le=200)


def _not_found(label: str) -> APIError:
    return APIError(status.HTTP_404_NOT_FOUND, "not_found", f"{label} not found")


def _item_or_404(db: Session, *, item_id: uuid.UUID, club_id: uuid.UUID) -> InventoryItem:
    item = get_record(db, InventoryItem, record_id=item_id, club_id=club_id)
    if item is None:
        raise _not_found("Inventory item")
    return item


def _pagination(page: int, page_size: int, total: int) -> Pagination:
    pages = (total + page_size - 1) // page_size if total else 0
    return Pagination(page=page, page_size=page_size, total=total, pages=pages)


def _movement_out(movement: InventoryMovement) -> InventoryMovementOut:
    return InventoryMovementOut.model_validate(movement, from_attributes=True)


@router.get("/stock", response_model=CollectionResponse[InventoryStockOut])
def list_inventory_stock(
    item_id: Optional[uuid.UUID] = Query(default=None),
    storage_location_id: Optional[uuid.UUID] = Query(default=None),
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryStockOut]:
    """Non-zero stock of quantity items per storage location."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    rows, total = list_stock(
        db,
        club_id=club_id,
        item_id=item_id,
        storage_location_id=storage_location_id,
        page=page,
        page_size=page_size,
    )
    return CollectionResponse(
        items=[InventoryStockOut.model_validate(row, from_attributes=True) for row in rows],
        pagination=_pagination(page, page_size, total),
    )


@router.get("/items/{item_id}/stock", response_model=CollectionResponse[InventoryStockOut])
def get_inventory_item_stock(
    item_id: uuid.UUID,
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryStockOut]:
    """Non-zero stock of one quantity item per storage location."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    item = _item_or_404(db, item_id=item_id, club_id=club_id)
    try:
        ensure_quantity_mode(item.accounting_mode)
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    rows, total = list_stock(
        db,
        club_id=club_id,
        item_id=item.id,
        storage_location_id=None,
        page=page,
        page_size=page_size,
    )
    return CollectionResponse(
        items=[InventoryStockOut.model_validate(row, from_attributes=True) for row in rows],
        pagination=_pagination(page, page_size, total),
    )


@router.get("/items/{item_id}/movements", response_model=CollectionResponse[InventoryMovementOut])
def list_inventory_item_movements(
    item_id: uuid.UUID,
    storage_location_id: Optional[uuid.UUID] = Query(default=None),
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryMovementOut]:
    """Chronological, immutable history of the item (§13)."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    item = _item_or_404(db, item_id=item_id, club_id=club_id)
    rows, total = list_item_movements(
        db,
        item_id=item.id,
        storage_location_id=storage_location_id,
        page=page,
        page_size=page_size,
    )
    return CollectionResponse(
        items=[_movement_out(row) for row in rows],
        pagination=_pagination(page, page_size, total),
    )


@router.post(
    "/items/{item_id}/receipts",
    status_code=status.HTTP_201_CREATED,
    response_model=InventoryMovementOut,
)
def receive_inventory_quantity(
    item_id: uuid.UUID,
    payload: InventoryReceiptRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryMovementOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _item_or_404(db, item_id=item_id, club_id=club_id)
    try:
        movement = quantity_service.receive(
            db,
            club_id=club_id,
            item_id=item_id,
            storage_location_id=payload.storage_location_id,
            quantity=payload.quantity,
            unit_cost_minor=payload.unit_cost_minor,
            comment=payload.comment,
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.quantity.receipt.success movement_id=%s", movement.id)
    return _movement_out(movement)


@router.post(
    "/items/{item_id}/transfers",
    status_code=status.HTTP_201_CREATED,
    response_model=InventoryMovementOut,
)
def transfer_inventory_quantity(
    item_id: uuid.UUID,
    payload: InventoryTransferRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryMovementOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _item_or_404(db, item_id=item_id, club_id=club_id)
    try:
        movement = quantity_service.transfer(
            db,
            club_id=club_id,
            item_id=item_id,
            from_location_id=payload.from_location_id,
            to_location_id=payload.to_location_id,
            quantity=payload.quantity,
            comment=payload.comment,
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.quantity.transfer.success movement_id=%s", movement.id)
    return _movement_out(movement)


@router.post(
    "/items/{item_id}/write-offs",
    status_code=status.HTTP_201_CREATED,
    response_model=InventoryMovementOut,
)
def write_off_inventory_quantity(
    item_id: uuid.UUID,
    payload: InventoryWriteOffRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryMovementOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _item_or_404(db, item_id=item_id, club_id=club_id)
    try:
        movement = quantity_service.write_off(
            db,
            club_id=club_id,
            item_id=item_id,
            storage_location_id=payload.storage_location_id,
            quantity=payload.quantity,
            comment=payload.comment,
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.quantity.write_off.success movement_id=%s", movement.id)
    return _movement_out(movement)


@router.post(
    "/items/{item_id}/write-offs/{movement_id}/reverse",
    status_code=status.HTTP_201_CREATED,
    response_model=InventoryMovementOut,
)
def reverse_inventory_quantity_write_off(
    item_id: uuid.UUID,
    movement_id: uuid.UUID,
    payload: Optional[InventoryWriteOffReverseRequest] = Body(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryMovementOut:
    """Full reversal of a quantity write-off (§16, E); returns the
    `writeoff_reversal` movement."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _item_or_404(db, item_id=item_id, club_id=club_id)
    try:
        movement = quantity_service.reverse_write_off(
            db,
            club_id=club_id,
            item_id=item_id,
            movement_id=movement_id,
            storage_location_id=payload.storage_location_id if payload is not None else None,
            created_by=principal.user_id,
        )
    except quantity_service.InventoryWriteOffNotFoundError as exc:
        raise _not_found("Inventory movement") from exc
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.quantity.writeoff_reversal.success movement_id=%s", movement.id)
    return _movement_out(movement)
