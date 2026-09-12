"""Readiness against a real, disposable PostgreSQL (Issue #10).

The failure path (DB unavailable) is covered in tests/api/test_health.py by
monkeypatching check_connection — it needs no real database and stays fast.
This file covers exactly the one scenario that genuinely requires a live
PostgreSQL: readiness succeeding against it. Uses the same
skip-if-no-database contract as the rest of this suite (see conftest.py).
"""

from fastapi.testclient import TestClient

from app.main import app

from .conftest import requires_postgres


@requires_postgres
def test_readiness_returns_200_against_a_real_database(database_url: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@requires_postgres
def test_readiness_works_on_a_freshly_reset_schema_before_any_migration(
    database_url: str,
) -> None:
    # The autouse _clean_database fixture (conftest.py) has already dropped
    # and recreated the public schema for this test, with no Alembic
    # migration applied yet — readiness must not depend on any table
    # existing, only on the ability to connect.
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
