"""Request/response models for /api/v1/inventory (TH-0121 / Issue #230;
docs/04-domain/inventory.md). Single resources are returned directly and
lists use the canonical `{items, pagination}` envelope (ADR-0014).

No request carries `club_id` or `status`: records always belong to the
installation's Club, and archiving happens only through the `/archive`
actions. No nomenclature schema carries a quantity/stock/state/location
field — the stock is derived from movements (inventory.md §13).
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.inventory import NAME_MAX_LENGTH, UNIT_NAME_MAX_LENGTH

InventoryStatusLiteral = Literal["active", "archived"]
InventoryStatusFilterLiteral = Literal["active", "archived", "all"]
AccountingModeLiteral = Literal["quantity", "instance"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- categories ------------------------------------------------------------------


class InventoryCategoryCreateRequest(_Request):
    name: str = Field(max_length=NAME_MAX_LENGTH)


class InventoryCategoryUpdateRequest(_Request):
    name: str = Field(max_length=NAME_MAX_LENGTH)


class InventoryCategoryOut(BaseModel):
    id: UUID
    name: str
    status: InventoryStatusLiteral
    archived_at: Optional[datetime]
    created_by: UUID
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


# --- units -----------------------------------------------------------------------


class InventoryUnitCreateRequest(_Request):
    name: str = Field(max_length=UNIT_NAME_MAX_LENGTH)


class InventoryUnitUpdateRequest(_Request):
    name: str = Field(max_length=UNIT_NAME_MAX_LENGTH)


class InventoryUnitOut(BaseModel):
    id: UUID
    name: str
    # System units (`шт`, `м`, `комплект`, `пара`) are read-only (G11).
    is_system: bool
    status: InventoryStatusLiteral
    archived_at: Optional[datetime]
    created_by: Optional[UUID]
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


# --- storage locations -------------------------------------------------------------


class InventoryStorageLocationCreateRequest(_Request):
    name: str = Field(max_length=NAME_MAX_LENGTH)
    # `null`/omitted = a root location.
    parent_id: Optional[UUID] = None


class InventoryStorageLocationUpdateRequest(_Request):
    """PATCH: rename and/or re-parent; `parent_id: null` makes the location
    a root."""

    name: Optional[str] = Field(default=None, max_length=NAME_MAX_LENGTH)
    parent_id: Optional[UUID] = None


class InventoryStorageLocationOut(BaseModel):
    id: UUID
    parent_id: Optional[UUID]
    name: str
    status: InventoryStatusLiteral
    archived_at: Optional[datetime]
    created_by: UUID
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


# --- nomenclature -------------------------------------------------------------------


class InventoryItemCreateRequest(_Request):
    name: str = Field(max_length=NAME_MAX_LENGTH)
    category_id: UUID
    unit_id: UUID
    accounting_mode: AccountingModeLiteral
    # Current item cost in RUB kopecks (inventory.md §11); optional.
    current_cost_minor: Optional[int] = Field(default=None, ge=0)


class InventoryItemUpdateRequest(_Request):
    """PATCH. `current_cost_minor: null` clears the cost; the other fields
    cannot be null."""

    name: Optional[str] = Field(default=None, max_length=NAME_MAX_LENGTH)
    category_id: Optional[UUID] = None
    unit_id: Optional[UUID] = None
    accounting_mode: Optional[AccountingModeLiteral] = None
    current_cost_minor: Optional[int] = Field(default=None, ge=0)


class InventoryItemOut(BaseModel):
    id: UUID
    name: str
    category_id: UUID
    unit_id: UUID
    accounting_mode: AccountingModeLiteral
    current_cost_minor: Optional[int]
    status: InventoryStatusLiteral
    archived_at: Optional[datetime]
    created_by: UUID
    updated_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime
