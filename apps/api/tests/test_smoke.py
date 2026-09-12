"""Minimal foundation smoke checks for Issue #4 (full test framework is Issue #7)."""

from fastapi.testclient import TestClient

from app.api.v1.router import router as v1_router
from app.main import app


def test_app_imports_without_error() -> None:
    assert app.title == "TourCRM API"


def test_app_starts_and_stops() -> None:
    with TestClient(app) as client:
        assert client is not None


def test_v1_router_uses_versioned_prefix() -> None:
    # app.main imports and includes this router at module load time, so a
    # successful import above already proves the wiring works; this checks
    # the versioned API boundary itself matches docs/03-architecture/
    # application-architecture.md (§9-10).
    assert v1_router.prefix == "/api/v1"
