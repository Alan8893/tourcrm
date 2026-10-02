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
    InventoryIssue,
    InventoryIssueLine,
    InventoryItem,
    InventoryItemStock,
    InventoryMovement,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.inventory.vocabulary import ACCOUNTING_MODE_QUANTITY, INSTANCE_WRITTEN_OFF

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


def list_stock(
    session: Session,
    *,
    club_id: uuid.UUID,
    item_id: uuid.UUID | None,
    storage_location_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> tuple[list[InventoryItemStock], int]:
    """Non-zero stock rows of quantity items (Slice 3), ordered by item and
    location."""
    conditions: list[sa.ColumnElement[bool]] = [
        InventoryItem.club_id == club_id,
        InventoryItem.accounting_mode == ACCOUNTING_MODE_QUANTITY,
        InventoryItemStock.quantity > 0,
    ]
    if item_id is not None:
        conditions.append(InventoryItemStock.item_id == item_id)
    if storage_location_id is not None:
        conditions.append(InventoryItemStock.storage_location_id == storage_location_id)
    joined = (
        sa.select(InventoryItemStock)
        .join(InventoryItem, InventoryItem.id == InventoryItemStock.item_id)
        .where(*conditions)
    )
    total = session.execute(sa.select(sa.func.count()).select_from(joined.subquery())).scalar_one()
    rows = (
        session.execute(
            joined.order_by(InventoryItemStock.item_id, InventoryItemStock.storage_location_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_item_movements(
    session: Session,
    *,
    item_id: uuid.UUID,
    storage_location_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> tuple[list[InventoryMovement], int]:
    """Chronological history of one item (§13); with `storage_location_id`
    only the movements from or to that location."""
    conditions: list[sa.ColumnElement[bool]] = [InventoryMovement.item_id == item_id]
    if storage_location_id is not None:
        conditions.append(
            sa.or_(
                InventoryMovement.from_location_id == storage_location_id,
                InventoryMovement.to_location_id == storage_location_id,
            )
        )
    total = session.execute(
        sa.select(sa.func.count()).select_from(InventoryMovement).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(InventoryMovement)
            .where(*conditions)
            .order_by(InventoryMovement.created_at, InventoryMovement.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def get_issue(
    session: Session, *, issue_id: uuid.UUID, club_id: uuid.UUID
) -> InventoryIssue | None:
    return session.execute(
        sa.select(InventoryIssue).where(
            InventoryIssue.id == issue_id, InventoryIssue.club_id == club_id
        )
    ).scalar_one_or_none()


def list_issues(
    session: Session,
    *,
    club_id: uuid.UUID,
    status: str | None,
    recipient_type: str | None,
    recipient_id: uuid.UUID | None,
    event_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> tuple[list[InventoryIssue], int]:
    """Issue documents of the Club (Slice 4), newest first."""
    conditions: list[sa.ColumnElement[bool]] = [InventoryIssue.club_id == club_id]
    if status is not None:
        conditions.append(InventoryIssue.status == status)
    if recipient_type is not None:
        conditions.append(InventoryIssue.recipient_type == recipient_type)
    if recipient_id is not None:
        conditions.append(
            sa.or_(
                InventoryIssue.recipient_person_id == recipient_id,
                InventoryIssue.recipient_user_id == recipient_id,
                InventoryIssue.recipient_group_id == recipient_id,
            )
        )
    if event_id is not None:
        conditions.append(InventoryIssue.event_id == event_id)
    total = session.execute(
        sa.select(sa.func.count()).select_from(InventoryIssue).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(InventoryIssue)
            .where(*conditions)
            .order_by(InventoryIssue.created_at.desc(), InventoryIssue.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_issue_lines(session: Session, *, issue_id: uuid.UUID) -> list[InventoryIssueLine]:
    """Lines of one issue in the order they were added."""
    return list(
        session.execute(
            sa.select(InventoryIssueLine)
            .where(InventoryIssueLine.issue_id == issue_id)
            .order_by(InventoryIssueLine.created_at, InventoryIssueLine.id)
        )
        .scalars()
        .all()
    )


def list_issue_movements(
    session: Session, *, issue_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[InventoryMovement], int]:
    """Chronological movements of one issue's lines: its issues, returns
    and the write-offs of lost instances."""
    condition = InventoryMovement.issue_line_id.in_(
        sa.select(InventoryIssueLine.id).where(InventoryIssueLine.issue_id == issue_id)
    )
    total = session.execute(
        sa.select(sa.func.count()).select_from(InventoryMovement).where(condition)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(InventoryMovement)
            .where(condition)
            .order_by(InventoryMovement.created_at, InventoryMovement.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


__all__ = [
    "get_issue",
    "list_issues",
    "list_issue_lines",
    "list_issue_movements",
    "list_stock",
    "list_item_movements",
    "get_record",
    "list_records",
    "get_instance",
    "list_instances",
    "list_instance_movements",
]
