"""Fixtures for real PostgreSQL integration tests (Issue #5).

These tests need a reachable PostgreSQL instance. They read connection
details from TEST_DATABASE_URL (falling back to DATABASE_URL) and are
skipped — not silently passed — when neither is set, so a missing
dependency is always visible rather than masked.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

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


@pytest.fixture(autouse=True)
def _clean_database(database_url: str):
    """Reset to a clean schema before each test so migrations run on a
    genuinely clean database, matching the Issue #5 acceptance criterion.
    """
    url = make_url(database_url)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text(f'GRANT ALL ON SCHEMA public TO "{url.username}"'))
    finally:
        engine.dispose()
    yield
