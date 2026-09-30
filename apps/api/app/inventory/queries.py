"""Inventory Foundation read queries (docs/04-domain/inventory.md).

Every query is scoped to the installation's Club; system units, which
belong to no Club, are part of every Club's unit reference. A record of
another Club is answered exactly like a nonexistent one.
"""

import uuid
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.inventory import (
    InventoryCategory,
    InventoryInstance,
    InventoryItem,
    InventoryMovement,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.inventory.vocabulary import INSTANCE_WRITTEN_OFF

InventoryRecord = TypeVar(
    "InventoryRecord", InventoryCategory, InventoryUnit, InventoryStorageLocation, InventoryItem
)


def _club_condition(model: Any, club_id: uuid.UUID) -> sa.ColumnElement[bool]:
    if model is InventoryUnit:
        return sa.or_(InventoryUnit.is_system.is_(True), InventoryUnit.club_id == club_id)
    return model.club_id == club_id


def get_record(
    session: Session,
    model: type[InventoryRecord],
    *,
    record_id: uuid.UUID,
    club_id: uuid.UUID,
    for_update: bool = False,
) -> InventoryRecord | None:
    stmt = sa.select(model).where(model.id == record_id, _club_condition(model, club_id))
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def list_records(
    session: Session,
    model: type[InventoryRecord],
    *,
    club_id: uuid.UUID,
    status: str,
    page: int,
    page_size: int,
) -> tuple[list[InventoryRecord], int]:
    """`status` is `active`, `archived` or `all`; ordered by name."""
    conditions = [_club_condition(model, club_id)]
    if status != "all":
        conditions.append(model.status == status)
    total = session.execute(
        sa.select(sa.func.count()).select_from(model).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(model)
            .where(*conditions)
            .order_by(sa.func.lower(model.name), model.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def get_instance(
    session: Session, *, instance_id: uuid.UUID, club_id: uuid.UUID, for_update: bool = False
) -> InventoryInstance | None:
    """The instance if its item belongs to `club_id`."""
    stmt = (
        sa.select(InventoryInstance)
        .join(InventoryItem, InventoryItem.id == InventoryInstance.item_id)
        .where(InventoryInstance.id == instance_id, InventoryItem.club_id == club_id)
    )
    if for_update:
        stmt = stmt.with_for_update(of=InventoryInstance)
    return session.execute(stmt).scalar_one_or_none()


def list_instances(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID | None,
    state: str | None,
    storage_location_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> tuple[list[InventoryInstance], int]:
    """Without a `state` filter every instance except `written_off` (GAP-4);
    ordered by inventory number."""
    conditions: list[sa.ColumnElement[bool]] = [InventoryItem.club_id == club_id]
    if state is None:
        conditions.append(InventoryInstance.state != INSTANCE_WRITTEN_OFF)
    else:
        conditions.append(InventoryInstance.state == state)
    if item_id is not None:
        conditions.append(InventoryInstance.item_id == item_id)
    if storage_location_id is not None:
        conditions.append(InventoryInstance.storage_location_id == storage_location_id)
    joined = sa.select(InventoryInstance).join(
        InventoryItem, InventoryItem.id == InventoryInstance.item_id
    )
    total = session.execute(
        sa.select(sa.func.count()).select_from(joined.where(*conditions).subquery())
    ).scalar_one()
    rows = (
        session.execute(
            joined.where(*conditions)
            .order_by(
                sa.func.length(InventoryInstance.inventory_number),
                InventoryInstance.inventory_number,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_instance_movements(session: Session, *, instance_id: uuid.UUID) -> list[InventoryMovement]:
    """Chronological history of one instance (§13)."""
    return list(
        session.execute(
            sa.select(InventoryMovement)
            .where(InventoryMovement.instance_id == instance_id)
            .order_by(InventoryMovement.created_at, InventoryMovement.id)
        )
        .scalars()
        .all()
    )


__all__ = [
    "get_record",
    "list_records",
    "get_instance",
    "list_instances",
    "list_instance_movements",
]
