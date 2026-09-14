"""Centralized PostgreSQL schema/data reset mechanism for tests/integration
(replaces the 22 per-file duplicated `_migrated_schema` fixtures).

Architecture (see tests/integration/conftest.py for the pytest fixtures
that drive this):

    pytest session start
        -> DROP/CREATE SCHEMA public (once)
        -> alembic upgrade head (once, subprocess)
        -> capture a data-only baseline snapshot via `pg_dump` (once)
    before EVERY integration test
        -> TRUNCATE every application table (discovered from the
           PostgreSQL catalog, never a hand-maintained list) ... RESTART
           IDENTITY CASCADE
        -> restore the baseline snapshot via `psql`

This replaces "DROP SCHEMA + re-run all N Alembic migrations" (~0.6-0.9s,
paid 685 times) with "TRUNCATE + restore a small data snapshot" (~0.04s,
measured; see the isolation-strategy experiment this implements).

Why `pg_dump`/`psql` instead of hand-typed seed constants: `alembic
upgrade head` does not just create empty tables — some migrations also
INSERT committed reference data (confirmed for this schema: `roles` and
`permissions`, seeded across *two separate* migrations). A hand-typed
Python list of "the seed data" is a second, independently-maintained copy
that silently drifts the moment a new migration adds more reference data
— this was empirically demonstrated (not hypothesized) during the
isolation-strategy comparison: a first attempt at a hand-typed reseed
list quietly missed a second migration's 2 permission rows, caught only
by diffing against an independent, pg_dump-based snapshot. `pg_dump
--data-only` instead captures *whatever actually exists* after a real
`alembic upgrade head`, so any future migration's seed data is included
automatically with zero code change here.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

API_ROOT = Path(__file__).resolve().parents[2]

# alembic_version is Alembic's own migration-state bookkeeping, not test
# data — it must never be truncated or overwritten by a data restore. Once
# the session-scoped migration has run, it stays exactly as `alembic
# upgrade head` left it for the rest of the session; individual tests that
# deliberately move it (downgrade/upgrade round-trip tests) are
# responsible for restoring it to head themselves before finishing (see
# those tests' own docstrings) — a normal per-test reset never touches it.
EXCLUDED_FROM_RESET = frozenset({"alembic_version"})


def _psql_url(database_url: str) -> str:
    """`pg_dump`/`psql` want a plain `postgresql://` URL; the app/test
    config uses SQLAlchemy's `postgresql+psycopg://` — convert, don't
    duplicate the connection parameters by hand."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    """Run one Alembic CLI subcommand against `database_url`. Shared by
    the session-scoped one-time migration below AND by the small set of
    tests that deliberately exercise `alembic downgrade`/`upgrade`
    themselves (see those tests' own docstrings for why they need this
    directly rather than going through the reset fixtures)."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )


def reset_schema(database_url: str) -> None:
    """DROP/CREATE the `public` schema from scratch. Session-scoped use
    only (once, before the one-time migration) — this is deliberately NOT
    what runs between individual tests anymore; see `reset_data_only`."""
    url = make_url(database_url)
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text(f'GRANT ALL ON SCHEMA public TO "{url.username}"'))
    finally:
        engine.dispose()


def discover_tables(engine: sa.Engine) -> list[str]:
    """Every real table in the `public` schema, via the PostgreSQL
    catalog (`pg_tables`) — never a hand-maintained list, so a table
    added by a future migration is included automatically on the very
    next call. Also checks for views/materialized views/partitioned
    tables (none exist in this schema today; see module docstring in the
    isolation-strategy report) so a future one isn't silently
    mishandled: TRUNCATE cannot target a view, so if one is ever added
    this raises loudly instead of silently skipping it.
    """
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars().all()
        views = conn.execute(
            text(
                "SELECT table_name FROM information_schema.views WHERE table_schema = 'public' "
                "UNION SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'"
            )
        ).scalars().all()
    if views:
        raise RuntimeError(
            f"tests/integration/_schema_reset.py: found view(s) {sorted(views)} in the "
            "public schema — this reset mechanism only knows how to TRUNCATE tables. "
            "Update discover_tables()/reset_data_only() deliberately before adding a "
            "view-backed integration test."
        )
    return sorted(t for t in tables if t not in EXCLUDED_FROM_RESET)


def capture_baseline_snapshot(database_url: str, snapshot_path: Path) -> float:
    """`pg_dump --data-only`, excluding `alembic_version`, of the
    database's current state — intended to be called exactly once, right
    after the one-time `alembic upgrade head`, so the snapshot is
    whatever a real migration run actually produced (schema/table
    structure is NOT part of this snapshot; only row data)."""
    t0 = time.monotonic()
    result = subprocess.run(
        [
            "pg_dump",
            "--data-only",
            "--exclude-table=alembic_version",
            "--no-owner",
            "--no-privileges",
            "-f",
            str(snapshot_path),
            _psql_url(database_url),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed capturing baseline snapshot: {result.stderr}")
    return time.monotonic() - t0


def restore_baseline_snapshot(database_url: str, snapshot_path: Path) -> float:
    """Restore a snapshot captured by `capture_baseline_snapshot`, inside
    one transaction (all-or-nothing), failing loudly on any error rather
    than silently leaving a partially-restored baseline."""
    t0 = time.monotonic()
    result = subprocess.run(
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "--single-transaction",
            "-q",
            "-f",
            str(snapshot_path),
            _psql_url(database_url),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed restoring baseline snapshot: {result.stderr}")
    return time.monotonic() - t0


def truncate_all(engine: sa.Engine, tables: list[str]) -> float:
    t0 = time.monotonic()
    if tables:
        quoted = ", ".join(f'"{t}"' for t in tables)
        with engine.connect() as conn:
            conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
            conn.commit()
    return time.monotonic() - t0


def reset_data_only(engine: sa.Engine, database_url: str, snapshot_path: Path) -> None:
    """The function-scoped, per-test reset: TRUNCATE every application
    table (auto-discovered, alembic_version excluded), then restore the
    session's baseline snapshot. Schema/constraints/indexes/triggers are
    completely untouched — only row data changes."""
    tables = discover_tables(engine)
    truncate_all(engine, tables)
    restore_baseline_snapshot(database_url, snapshot_path)


__all__ = [
    "EXCLUDED_FROM_RESET",
    "run_alembic",
    "reset_schema",
    "discover_tables",
    "capture_baseline_snapshot",
    "restore_baseline_snapshot",
    "truncate_all",
    "reset_data_only",
]
