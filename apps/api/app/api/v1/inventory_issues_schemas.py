"""Request/response models for the Inventory issue endpoints (Slice 4,
Issue #236; docs/04-domain/inventory.md §14).

There is no request that deletes a line or lowers an issued quantity:
less property with the recipient is always a return. Requests forbid
unknown fields (422).
"""

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.inventory import TEXT_MAX_LENGTH

RecipientTypeLiteral = Literal["member", "instructor", "group"]
IssueStatusLiteral = Literal["issued", "cancelled"]
AccountingModeLiteral = Literal["quantity", "instance"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryIssueLineRequest(_Request):
    """A quantity for a quantity item, or specific instances for an
    instance item — never both."""

    item_id: UUID
    quantity: Optional[int] = Field(default=None, gt=0)
    instance_ids: Optional[list[UUID]] = Field(default=None, min_length=1)


class InventoryIssueCreateRequest(_Request):
    # Member -> Person id, Instructor -> User id, Group -> Group id.
    recipient_type: RecipientTypeLiteral
    recipient_id: UUID
    event_id: Optional[UUID] = None
    planned_return_date: Optional[date] = None
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)
    lines: list[InventoryIssueLineRequest] = Field(min_length=1)


class InventoryIssueUpdateRequest(_Request):
    """Header only. `recipient_type` and `recipient_id` change together;
    `null` clears `event_id`, `planned_return_date` or `comment`."""

    recipient_type: Optional[RecipientTypeLiteral] = None
    recipient_id: Optional[UUID] = None
    event_id: Optional[UUID] = None
    planned_return_date: Optional[date] = None
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryIssueAddLinesRequest(_Request):
    lines: list[InventoryIssueLineRequest] = Field(min_length=1)


class InventoryQuantityReturnRequest(_Request):
    line_id: UUID
    quantity: int = Field(gt=0)


class InventoryIssueReturnRequest(_Request):
    storage_location_id: UUID
    quantities: list[InventoryQuantityReturnRequest] = Field(default_factory=list)
    instance_ids: list[UUID] = Field(default_factory=list)
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryIssueCancelRequest(_Request):
    storage_location_id: UUID


class InventoryIssueLostRequest(_Request):
    instance_id: UUID
    storage_location_id: UUID
    # Mandatory loss reason — stored as the write-off reason.
    reason: str = Field(max_length=TEXT_MAX_LENGTH)
    # Optional comment — stored on the return half of the operation.
    comment: Optional[str] = Field(default=None, max_length=TEXT_MAX_LENGTH)


class InventoryIssueLineOut(BaseModel):
    id: UUID
    issue_id: UUID
    item_id: UUID
    accounting_mode: AccountingModeLiteral
    # Units for a quantity line; instances for an instance line.
    issued_quantity: int
    returned_quantity: int
    outstanding_quantity: int
    # Instances still issued on the line; empty for a quantity line.
    outstanding_instance_ids: list[UUID]
    created_by: UUID
    created_at: datetime


class InventoryIssueOut(BaseModel):
    id: UUID
    recipient_type: RecipientTypeLiteral
    recipient_id: UUID
    event_id: Optional[UUID]
    planned_return_date: Optional[date]
    comment: Optional[str]
    status: IssueStatusLiteral
    # Derived: something on the issue is still issued.
    has_outstanding: bool
    cancelled_at: Optional[datetime]
    cancelled_by: Optional[UUID]
    created_by: UUID
    created_at: datetime
    updated_by: Optional[UUID]
    updated_at: datetime


class InventoryIssueDetailOut(InventoryIssueOut):
    lines: list[InventoryIssueLineOut]
