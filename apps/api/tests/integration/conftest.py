"""Fixtures for real PostgreSQL integration tests (Issue #5).

These tests need a reachable PostgreSQL instance. They read connection
details from TEST_DATABASE_URL (falling back to DATABASE_URL) and are
skipped — not silently passed — when neither is set, so a missing
dependency is always visible rather than masked.

Schema/data lifecycle (test-isolation-strategy experiment, superseding
the previous per-test `DROP SCHEMA + alembic upgrade head` design that
used to be duplicated across 22 test files as a private `_migrated_schema`
fixture in each one):

    session start  -> reset_schema()        (DROP/CREATE SCHEMA, once)
                    -> alembic upgrade head  (once, subprocess)
                    -> capture_baseline_snapshot() (pg_dump, once)
    before EVERY integration test
                    -> reset_data_only()     (TRUNCATE + restore snapshot)

See tests/integration/_schema_reset.py for the mechanism itself and its
full rationale (in particular: why the baseline snapshot is captured via
`pg_dump` from a real migration run rather than reproduced by hand as
Python constants).
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from tests.integration._schema_reset import (
    capture_baseline_snapshot,
    reset_data_only,
    reset_schema,
    run_alembic,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

# app.core.config.get_settings() only reads DATABASE_URL; mirror the resolved
# value so tests can exercise our own session/config wiring directly instead
# of re-deriving it, without requiring both variables to be set by hand.
if TEST_DATABASE_URL and not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Issue #33: TestClient talks to the app over plain HTTP (http://testserver);
# a `Secure` session/CSRF cookie would never be resent by the client on the
# next request, which is correct client behavior, not a bug — set the same
# way a real plain-HTTP/LAN deployment would (see apps/api/.env.example).
os.environ.setdefault("COOKIE_SECURE", "false")

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL/DATABASE_URL is not set; no PostgreSQL instance configured",
)


@pytest.fixture
def database_url() -> str:
    assert TEST_DATABASE_URL is not None
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def _integration_baseline_snapshot(tmp_path_factory: pytest.TempPathFactory):
    """Runs ONCE for the whole `tests/integration` session (not skipped
    entirely unless every test in the session is skipped — see
    `requires_postgres` above, which is what actually makes that safe: a
    session with no DATABASE_URL configured never runs any test that
    needs this fixture in the first place). Resets the schema from
    scratch, applies every migration for real, then captures a data-only
    snapshot of whatever that migration run produced — this is the
    session's one-time cost; see the isolation-strategy report for why it
    replaces a per-test `DROP SCHEMA + alembic upgrade head` (~0.6-0.9s
    paid ~685 times) with a single ~0.8s cost for the whole run.

    Uses the module-level TEST_DATABASE_URL directly rather than the
    `database_url` fixture: a session-scoped fixture cannot depend on a
    function-scoped one, and this constant is exactly what that fixture
    itself wraps.

    Returns the snapshot file's path, consumed by `_reset_between_tests`.
    """
    assert TEST_DATABASE_URL is not None
    reset_schema(TEST_DATABASE_URL)
    result = run_alembic("upgrade", "head", database_url=TEST_DATABASE_URL)
    assert result.returncode == 0, result.stderr

    snapshot_path = tmp_path_factory.mktemp("integration-baseline") / "baseline.sql"
    capture_baseline_snapshot(TEST_DATABASE_URL, snapshot_path)
    return snapshot_path


@pytest.fixture(autouse=True)
def _reset_between_tests(database_url: str, _integration_baseline_snapshot) -> None:
    """Runs before EVERY integration test: TRUNCATE every application
    table (auto-discovered from the PostgreSQL catalog — see
    `_schema_reset.discover_tables`) and restore the session's baseline
    snapshot. Schema, constraints, indexes, triggers and `alembic_version`
    are never touched here — only row-level data changes, so each test
    still gets a logically clean, baseline-equivalent database, exactly
    as the previous per-test full-migration design guaranteed, just
    without re-running the migrations themselves every time.

    A handful of tests deliberately manipulate migration state themselves
    (`alembic downgrade`/`upgrade` round-trip tests) — they import
    `run_alembic` directly from `_schema_reset` and are responsible for
    leaving the schema at `head` before they finish, exactly as they
    already were under the previous per-file fixture design; this
    fixture's own reset still runs before and after them like any other
    test, so a broken state can never leak into a subsequent test.
    """
    url = make_url(database_url)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        reset_data_only(engine, database_url, _integration_baseline_snapshot)
    finally:
        engine.dispose()
    yield
