"""Pure unit tests for the canonical response schemas (app.api.schemas,
app.api.errors) — pydantic model validation only, no HTTP, no DB.
"""

import pytest
from pydantic import ValidationError

from app.api.errors import ErrorBody, ErrorResponse
from app.api.schemas import CollectionResponse, Pagination


def test_pagination_accepts_valid_values() -> None:
    pagination = Pagination(page=1, page_size=50, total=120, pages=3)
    assert pagination.model_dump() == {
        "page": 1,
        "page_size": 50,
        "total": 120,
        "pages": 3,
    }


def test_pagination_rejects_missing_fields() -> None:
    with pytest.raises(ValidationError):
        Pagination(page=1)


def test_collection_response_shape() -> None:
    collection = CollectionResponse[dict](
        items=[{"id": "a"}, {"id": "b"}],
        pagination=Pagination(page=1, page_size=50, total=2, pages=1),
    )
    dumped = collection.model_dump()
    assert set(dumped.keys()) == {"items", "pagination"}
    assert len(dumped["items"]) == 2


def test_error_response_matches_adr_0014_shape() -> None:
    error = ErrorResponse(
        error=ErrorBody(
            code="not_found", message="Resource was not found", details={}, request_id="rid-1"
        )
    )
    dumped = error.model_dump()
    assert set(dumped.keys()) == {"error"}
    assert set(dumped["error"].keys()) == {"code", "message", "details", "request_id"}
