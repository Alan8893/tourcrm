"""Inventory Slice 2 API — instances (Issue #230; docs/04-domain/inventory.md
§7, §13, §16).

    GET    /inventory/instances                          list (item_id, state, storage_location_id)
    POST   /inventory/instances                          receipt of a new instance
    GET    /inventory/instances/{instance_id}            detail
    PATCH  /inventory/instances/{instance_id}            manufacturer codes / description
    POST   /inventory/instances/{instance_id}/transfer
    POST   /inventory/instances/{instance_id}/repair-start
    POST   /inventory/instances/{instance_id}/repair-end
    POST   /inventory/instances/{instance_id}/write-off
    GET    /inventory/instances/{instance_id}/movements  chronological history
    POST   /inventory/movements/{movement_id}/reverse    write-off reversal

No DELETE: instances are never removed; `written_off` is terminal (Q8).
State and storage location change only through the movement endpoints.

Authorization (inventory.md §3): every endpoint requires the Administrator
(app.inventory.authorization), checked before the target is loaded, so a
non-Administrator gets the same 403 whether or not the id exists. An
instance/movement of another Club is a 404.
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
from app.api.v1.inventory_instances_schemas import (
    InstanceStateLiteral,
    InventoryInstanceCreateRequest,
    InventoryInstanceOut,
    InventoryInstanceTransferRequest,
    InventoryInstanceUpdateRequest,
    InventoryInstanceWriteOffRequest,
    InventoryMovementOut,
    InventoryMovementReverseRequest,
)
from app.db.inventory import InventoryInstance
from app.db.session import get_db
from app.inventory import instances as instance_service
from app.inventory.authorization import require_inventory_administrator
from app.inventory.queries import get_instance, list_instance_movements, list_instances

logger = logging.getLogger("tourcrm.api.inventory.instances")

router = APIRouter(prefix="/inventory", tags=["inventory"])

_DOMAIN_ERRORS = (*INVENTORY_DOMAIN_ERRORS,)


def _not_found(label: str) -> APIError:
    return APIError(status.HTTP_404_NOT_FOUND, "not_found", f"{label} not found")


def _instance_or_404(
    db: Session, *, instance_id: uuid.UUID, club_id: uuid.UUID, for_update: bool = False
) -> InventoryInstance:
    instance = get_instance(db, instance_id=instance_id, club_id=club_id, for_update=for_update)
    if instance is None:
        raise _not_found("Inventory instance")
    return instance


def _out(instance: InventoryInstance) -> InventoryInstanceOut:
    return InventoryInstanceOut.model_validate(instance, from_attributes=True)


@router.get("/instances", response_model=CollectionResponse[InventoryInstanceOut])
def list_inventory_instances(
    item_id: Optional[uuid.UUID] = Query(default=None),
    state: Optional[InstanceStateLiteral] = Query(default=None),
    storage_location_id: Optional[uuid.UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryInstanceOut]:
    """Without `state`, every instance except `written_off` (GAP-4)."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    rows, total = list_instances(
        db,
        club_id=club_id,
        item_id=item_id,
        state=state,
        storage_location_id=storage_location_id,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_out(row) for row in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post("/instances", status_code=status.HTTP_201_CREATED, response_model=InventoryInstanceOut)
def receive_inventory_instance(
    payload: InventoryInstanceCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        instance = instance_service.receive_instance(
            db,
            club_id=club_id,
            item_id=payload.item_id,
            storage_location_id=payload.storage_location_id,
            unit_cost_minor=payload.unit_cost_minor,
            manufacturer_barcode=payload.manufacturer_barcode,
            manufacturer_serial_number=payload.manufacturer_serial_number,
            description=payload.description,
            created_by=principal.user_id,
        )
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.receipt.success id=%s", instance.id)
    return _out(instance)


@router.get("/instances/{instance_id}", response_model=InventoryInstanceOut)
def get_inventory_instance(
    instance_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _out(_instance_or_404(db, instance_id=instance_id, club_id=club_id))


@router.patch("/instances/{instance_id}", response_model=InventoryInstanceOut)
def update_inventory_instance(
    instance_id: uuid.UUID,
    payload: InventoryInstanceUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    instance = _instance_or_404(db, instance_id=instance_id, club_id=club_id, for_update=True)
    try:
        instance = instance_service.update_instance(
            db,
            instance=instance,
            fields=payload.model_dump(exclude_unset=True),
            updated_by=principal.user_id,
        )
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.update.success id=%s", instance.id)
    return _out(instance)


@router.post("/instances/{instance_id}/transfer", response_model=InventoryInstanceOut)
def transfer_inventory_instance(
    instance_id: uuid.UUID,
    payload: InventoryInstanceTransferRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    instance = _instance_or_404(db, instance_id=instance_id, club_id=club_id, for_update=True)
    try:
        instance = instance_service.transfer_instance(
            db,
            instance=instance,
            to_location_id=payload.to_location_id,
            comment=payload.comment,
            created_by=principal.user_id,
        )
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.transfer.success id=%s", instance.id)
    return _out(instance)


@router.post("/instances/{instance_id}/repair-start", response_model=InventoryInstanceOut)
def start_inventory_instance_repair(
    instance_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    instance = _instance_or_404(db, instance_id=instance_id, club_id=club_id, for_update=True)
    try:
        instance = instance_service.start_repair(
            db, instance=instance, created_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.repair_start.success id=%s", instance.id)
    return _out(instance)


@router.post("/instances/{instance_id}/repair-end", response_model=InventoryInstanceOut)
def end_inventory_instance_repair(
    instance_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    instance = _instance_or_404(db, instance_id=instance_id, club_id=club_id, for_update=True)
    try:
        instance = instance_service.end_repair(db, instance=instance, created_by=principal.user_id)
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.repair_end.success id=%s", instance.id)
    return _out(instance)


@router.post("/instances/{instance_id}/write-off", response_model=InventoryInstanceOut)
def write_off_inventory_instance(
    instance_id: uuid.UUID,
    payload: InventoryInstanceWriteOffRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    instance = _instance_or_404(db, instance_id=instance_id, club_id=club_id, for_update=True)
    try:
        instance = instance_service.write_off_instance(
            db, instance=instance, comment=payload.comment, created_by=principal.user_id
        )
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.write_off.success id=%s", instance.id)
    return _out(instance)


@router.get("/instances/{instance_id}/movements", response_model=list[InventoryMovementOut])
def list_inventory_instance_movements(
    instance_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> list[InventoryMovementOut]:
    """Chronological, immutable history (§13)."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    instance = _instance_or_404(db, instance_id=instance_id, club_id=club_id)
    return [
        InventoryMovementOut.model_validate(movement, from_attributes=True)
        for movement in list_instance_movements(db, instance_id=instance.id)
    ]


@router.post("/movements/{movement_id}/reverse", response_model=InventoryInstanceOut)
def reverse_inventory_write_off(
    movement_id: uuid.UUID,
    payload: Optional[InventoryMovementReverseRequest] = Body(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryInstanceOut:
    """Reverse an instance write-off (E, GAP-2); returns the restored
    instance."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        instance = instance_service.reverse_write_off(
            db,
            club_id=club_id,
            movement_id=movement_id,
            storage_location_id=payload.storage_location_id if payload is not None else None,
            created_by=principal.user_id,
        )
    except instance_service.InventoryMovementNotFoundError as exc:
        raise _not_found("Inventory movement") from exc
    except _DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.instance.writeoff_reversal.success id=%s", instance.id)
    return _out(instance)
