"""Test-only harness for the Issue #6 API foundation.

`probe_app` reuses the SAME production error handlers, request-id middleware
and schemas as the shipped app (app.api.errors / app.api.request_context /
app.api.schemas) so these tests exercise real production code paths. Its
routes (`/probe/...`) are deliberately not part of the shipped `app.main`
app or its OpenAPI: Issue #6 explicitly forbids adding fictitious domain
endpoints to the real API just to have something to document/test against.
`real_client` wraps the actual shipped application for tests that must
observe the real app (unknown-endpoint handling, OpenAPI contents, the
literal `/api/v1` boundary).
"""

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.errors import register_exception_handlers
from app.api.request_context import RequestIDMiddleware
from app.api.schemas import CollectionResponse, Pagination
from app.main import app as real_app

probe_router = APIRouter()


@probe_router.get("/probe/{item_id}")
def get_probe_item(item_id: str) -> dict:
    return {"id": item_id, "name": "example"}


@probe_router.get("/probe-collection")
def list_probe_items() -> CollectionResponse[dict]:
    return CollectionResponse(
        items=[{"id": "1"}, {"id": "2"}],
        pagination=Pagination(page=1, page_size=50, total=2, pages=1),
    )


@probe_router.get("/probe-validate")
def validate_probe(count: int) -> dict:
    return {"count": count}


@probe_router.get("/probe-error/{status_code}")
def raise_http_error(status_code: int) -> None:
    raise HTTPException(status_code=status_code, detail="probe http error")


@probe_router.get("/probe-crash")
def crash_probe() -> None:
    raise RuntimeError("boom: unexpected probe failure with secret=hunter2 at /var/lib/tourcrm")


def build_probe_app() -> FastAPI:
    app = FastAPI(title="TourCRM API Foundation Probe")
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(probe_router)
    return app


@pytest.fixture
def probe_client() -> TestClient:
    return TestClient(build_probe_app(), raise_server_exceptions=False)


@pytest.fixture
def real_client() -> TestClient:
    return TestClient(real_app, raise_server_exceptions=False)
