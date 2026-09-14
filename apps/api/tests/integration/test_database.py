"""Real PostgreSQL integration tests for the Issue #5 storage foundation.

Run with a reachable PostgreSQL instance, e.g.:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v

Tests are skipped (not faked) when no database is configured — see
conftest.py's `requires_postgres` marker.
"""

import uuid

import pytest
from sqlalchemy import create_engine, select, text

from app.db.errors import DatabaseConnectionError
from app.db.foundation import FoundationHealthCheck
from app.db.session import check_connection, session_scope

from ._schema_reset import reset_schema, run_alembic
from .conftest import requires_postgres


@requires_postgres
def test_application_can_connect_to_postgresql(database_url: str) -> None:
    # Must not raise: proves real connectivity through our own engine wiring.
    check_connection(database_url)


@requires_postgres
def test_migrations_apply_on_a_clean_database(database_url: str) -> None:
    """Unlike the rest of tests/integration, this test's own point is to
    prove migrations apply starting from a genuinely EMPTY schema — the
    session-scoped baseline the other tests rely on (conftest.py) already
    has every migration applied by the time this test starts, so it
    deliberately drops back to an empty schema first, then restores to
    head before finishing (matching the same self-contained pattern the
    `*_downgrade_then_upgrade_*` tests use) so the next test still gets
    the normal baseline-equivalent state conftest.py's per-test reset
    expects.
    """
    reset_schema(database_url)

    result = run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars().all()
    finally:
        engine.dispose()

    assert "alembic_version" in tables
    assert "foundation_healthchecks" in tables


@requires_postgres
def test_repeated_upgrade_head_is_idempotent(database_url: str) -> None:
    first = run_alembic("upgrade", "head", database_url=database_url)
    assert first.returncode == 0, first.stderr

    second = run_alembic("upgrade", "head", database_url=database_url)
    assert second.returncode == 0, second.stderr

    current = run_alembic("current", database_url=database_url)
    assert current.returncode == 0, current.stderr
    assert "(head)" in current.stdout


@requires_postgres
def test_orm_session_reads_and_writes_through_migrated_schema(database_url: str) -> None:
    upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    # session_scope() is the same session boundary future request handlers
    # use via get_db(); this exercises it exactly as a real caller would.
    with session_scope() as session:
        row = FoundationHealthCheck(id=uuid.uuid4())
        session.add(row)
        session.commit()

        fetched = session.execute(
            select(FoundationHealthCheck).where(FoundationHealthCheck.id == row.id)
        ).scalar_one()
        assert fetched.id == row.id
        assert fetched.checked_at is not None

    # A brand new session boundary must see the committed row (no leaked
    # in-process session state carrying the data instead of the database).
    with session_scope() as verification_session:
        count = verification_session.execute(
            select(FoundationHealthCheck).where(FoundationHealthCheck.id == row.id)
        ).scalar_one()
        assert count.id == row.id


@requires_postgres
def test_connection_error_does_not_leak_credentials(database_url: str) -> None:
    from sqlalchemy.engine import make_url

    real_url = make_url(database_url)
    sentinel_password = "S3ntinel-Sh0uldNotLeak-9f3a"
    bad_url = real_url.set(password=sentinel_password, host="127.0.0.1", port=1)

    with pytest.raises(DatabaseConnectionError) as exc_info:
        check_connection(str(bad_url))

    message = str(exc_info.value)
    assert sentinel_password not in message
    # Non-secret diagnostic context should still be present for troubleshooting.
    assert "127.0.0.1" in message
