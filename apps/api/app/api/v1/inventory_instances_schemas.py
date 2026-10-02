"""Request/response models for the Inventory instance endpoints
(Slice 2, Issue #230; docs/04-domain/inventory.md §7, §13, §16).

No request can set `state`, `storage_location_id` (except as the target of
an operation), `item_id` after creation or `inventory_number`: those change
only through movements, or never (Q2, Q10). Requests forbid unknown fields,
so sending one of them is a 422.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.inventory import MANUFACTURER_CODE_MAX_LENGTH, TEXT_MAX_LENGTH

InstanceStateLiteral = Literal["available", "issued", "in_repair", "written_off"]
MovementTypeLiteral = Literal[
    "receipt",
    "transfer",
    "issue",
    "return",
    "write_off",
    "adjustment",
    "writeoff_reversal",
    "repair_start",
    "repair_end",
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryInstanceCreateRequest(_Request):
    """The `receipt` of a new instance (Q7)."""

    item_id: UUID
    storage_location_id: UUID
    # Receipt cost in RUB kopecks (§11 п.6); optional.
    unit_cost_minor: Optional[int] = Field(default=None, ge=0)
    manufacturer_barcode: Optional[str] = Field(
        default=None, max_length=MANUFACTURER_CODE_MAX_LENGTH
    )
    manufacturer_serial_number: Optional[str] = Field(
        default=None, max_length=MANUFACTURER_CODE_MAX_LENGTH
    )
    description: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryInstanceUpdateRequest(_Request):
    """PATCH: only these three fields (§7.5); `null` or blank clears one."""

    manufacturer_barcode: Optional[str] = Field(
        default=None, max_length=MANUFACTURER_CODE_MAX_LENGTH
    )
    manufacturer_serial_number: Optional[str] = Field(
        default=None, max_length=MANUFACTURER_CODE_MAX_LENGTH
    )
    description: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryInstanceTransferRequest(_Request):
    to_location_id: UUID
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryInstanceWriteOffRequest(_Request):
    """`comment` is the mandatory write-off reason (Q8)."""

    comment: str = Field(max_length=TEXT_MAX_LENGTH)


class InventoryMovementReverseRequest(_Request):
    """Required only when the original location of the write-off is
    archived; rejected when it is still active (GAP-2)."""

    storage_location_id: Optional[UUID] = None


class InventoryInstanceOut(BaseModel):
    id: UUID
    item_id: UUID
    inventory_number: str
    manufacturer_barcode: Optional[str]
    manufacturer_serial_number: Optional[str]
    description: Optional[str]
    state: InstanceStateLiteral
    storage_location_id: Optional[UUID]
    created_by: UUID
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class InventoryMovementOut(BaseModel):
    id: UUID
    item_id: UUID
    instance_id: Optional[UUID]
    movement_type: MovementTypeLiteral
    from_location_id: Optional[UUID]
    to_location_id: Optional[UUID]
    # Quantity movements only (Slice 3); `null` for instance movements.
    quantity: Optional[int]
    unit_cost_minor: Optional[int]
    comment: Optional[str]
    reverses_movement_id: Optional[UUID]
    # Slice 4: the issue line of an `issue`/`return` (and of a lost
    # instance's `write_off`); `null` otherwise.
    issue_line_id: Optional[UUID]
    created_by: UUID
    created_at: datetime
