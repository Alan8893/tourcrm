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
    InventoryItem,
    InventoryStorageLocation,
    InventoryUnit,
)

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


__all__ = ["get_record", "list_records"]
