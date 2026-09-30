"""Request/response models for the Inventory quantity endpoints (Slice 3,
Issue #230; docs/04-domain/inventory.md §6, §11, §13, §16).

There is no request that sets a stock quantity: stock changes only through
the movement endpoints. Requests forbid unknown fields (422).
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.inventory import TEXT_MAX_LENGTH


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryReceiptRequest(_Request):
    storage_location_id: UUID
    quantity: int = Field(gt=0)
    # Receipt cost per unit in RUB kopecks (§11 п.6); optional.
    unit_cost_minor: Optional[int] = Field(default=None, ge=0)
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryTransferRequest(_Request):
    from_location_id: UUID
    to_location_id: UUID
    quantity: int = Field(gt=0)
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryWriteOffRequest(_Request):
    storage_location_id: UUID
    quantity: int = Field(gt=0)
    # The mandatory write-off reason.
    comment: str = Field(max_length=TEXT_MAX_LENGTH)


class InventoryWriteOffReverseRequest(_Request):
    """Required only when the original location of the write-off is
    archived; rejected when it is still active."""

    storage_location_id: Optional[UUID] = None


class InventoryStockOut(BaseModel):
    item_id: UUID
    storage_location_id: UUID
    quantity: int
