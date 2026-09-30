"""Share locks on inventory reference rows, used by every stock-changing
operation (instances — Slice 2, quantities — Slice 3).

An operation that receives into, moves within or writes off from a
storage location, or that works on an item, holds `SELECT ... FOR SHARE`
on those rows until commit. Archiving an item or a location holds its row
lock (`FOR UPDATE`, taken by the router), so the archive preconditions in
app.inventory.service can never race a concurrent operation.
"""

import uuid
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.inventory import InventoryItem, InventoryStorageLocation
from app.inventory.lifecycle import ensure_selectable
from app.inventory.service import KIND_ITEM, KIND_LOCATION, InventoryReferenceNotFoundError


def share_locked_item(session: Session, *, club_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
    item = session.execute(
        sa.select(InventoryItem)
        .where(InventoryItem.id == item_id, InventoryItem.club_id == club_id)
        .with_for_update(read=True)
    ).scalar_one_or_none()
    if item is None:
        raise InventoryReferenceNotFoundError(KIND_ITEM, item_id)
    return item


def share_locked_location(
    session: Session, *, club_id: uuid.UUID, location_id: uuid.UUID
) -> InventoryStorageLocation:
    location = session.execute(
        sa.select(InventoryStorageLocation)
        .where(
            InventoryStorageLocation.id == location_id,
            InventoryStorageLocation.club_id == club_id,
        )
        .with_for_update(read=True)
    ).scalar_one_or_none()
    if location is None:
        raise InventoryReferenceNotFoundError(KIND_LOCATION, location_id)
    return location


def share_locked_locations(
    session: Session, *, club_id: uuid.UUID, location_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, InventoryStorageLocation]:
    """Share-locks several locations of the Club in one deterministic order
    (`None` and duplicates dropped, ids sorted), so operations touching the
    same locations never wait on each other in opposite order. Ids that are
    not locations of the Club are absent from the result; the caller
    decides how to report them."""
    locked: dict[uuid.UUID, InventoryStorageLocation] = {}
    for location_id in sorted({i for i in location_ids if i is not None}, key=str):
        location = session.execute(
            sa.select(InventoryStorageLocation)
            .where(
                InventoryStorageLocation.id == location_id,
                InventoryStorageLocation.club_id == club_id,
            )
            .with_for_update(read=True)
        ).scalar_one_or_none()
        if location is not None:
            locked[location_id] = location
    return locked


def selectable_location(
    session: Session, *, club_id: uuid.UUID, location_id: uuid.UUID
) -> InventoryStorageLocation:
    """An active location of the Club, share-locked."""
    location = share_locked_location(session, club_id=club_id, location_id=location_id)
    ensure_selectable(location.status, kind=KIND_LOCATION)
    return location


__all__ = [
    "share_locked_item",
    "share_locked_location",
    "share_locked_locations",
    "selectable_location",
]
