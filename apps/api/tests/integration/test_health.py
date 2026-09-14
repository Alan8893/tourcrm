"""Readiness against a real, disposable PostgreSQL (Issue #10).

The failure path (DB unavailable) is covered in tests/api/test_health.py by
monkeypatching check_connection — it needs no real database and stays fast.
This file covers exactly the one scenario that genuinely requires a live
PostgreSQL: readiness succeeding against it. Uses the same
skip-if-no-database contract as the rest of this suite (see conftest.py).
"""

from fastapi.testclient import TestClient

from app.main import app

from ._schema_reset import reset_schema, run_alembic
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
    """Unlike the rest of tests/integration, this test's own point is
    that readiness must not depend on any table existing, only on the
    ability to connect — so it deliberately drops back to a genuinely
    empty schema (the session-scoped baseline every other test relies on
    already has every migration applied by the time this test starts),
    then restores to head before finishing, matching the same self-
    contained pattern `test_database.py::test_migrations_apply_on_a_clean_database`
    and the `*_downgrade_then_upgrade_*` tests use, so the next test
    still gets the normal baseline-equivalent state conftest.py's
    per-test reset expects.
    """
    reset_schema(database_url)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    result = run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr
