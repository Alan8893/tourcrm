"""Canonical response envelopes per ADR-0014 (API response envelope).

Single-resource responses return the resource directly (no wrapper) and are
therefore not modeled here. Collections use `Pagination`/a generic items+
pagination shape; no `data`/`meta` envelope is used anywhere in API v1.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class CollectionResponse(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    pagination: Pagination
