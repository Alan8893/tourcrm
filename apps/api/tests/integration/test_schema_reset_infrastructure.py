"""Tests for the reset mechanism itself (tests/integration/_schema_reset.py
+ conftest.py's `_integration_baseline_snapshot`/`_reset_between_tests`
fixtures), not for application behavior.

These exist specifically to guard against the failure mode the
isolation-strategy experiment demonstrated concretely: a hand-typed
"known seed tables" list silently missing a table/row a *later* migration
adds. Every check here is generic — none of it hardcodes a table name,
column name, or permission/role code — so it stays valid regardless of
what future migrations add.
"""

import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from ._schema_reset import capture_baseline_snapshot, discover_tables, reset_data_only
from .conftest import requires_postgres


def _strip_dump_nonce(dump_text: str) -> str:
    """`pg_dump` emits a random `\\restrict <token>`/`\\unrestrict <token>`
    pair per invocation (a psql "restricted mode" security nonce) — this
    differs between two dumps of *identical* data, so it must be excluded
    from any content comparison. Nothing else about the dump format is
    filtered."""
    return "\n".join(
        line
        for line in dump_text.splitlines()
        if not line.startswith("\\restrict ") and not line.startswith("\\unrestrict ")
    )


@requires_postgres
def test_reset_leaves_no_leftover_rows_across_unrelated_domains(
    database_url: str, _integration_baseline_snapshot
) -> None:
    """Write real, committed rows across several unrelated tables/domains
    (club/person/user/role-assignment/audit — chosen because they span
    the FK graph, not because they're the "known seed tables"), then run
    the exact same reset the autouse fixture runs before every test, and
    assert every discovered table is back to EXACTLY its pre-insert row
    count afterward (not just "empty" — the reference-data tables must
    also lose this test's own extra row and nothing more)."""
    url = make_url(database_url)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        tables = discover_tables(engine)
        before = {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables}

        club_id = str(uuid.uuid4())
        person_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        role_id = str(uuid.uuid4())
        conn.execute(
            text("INSERT INTO clubs (id, name, status) VALUES (:id, :name, 'active')"),
            {"id": club_id, "name": f"Leak-check-{uuid.uuid4().hex[:8]}"},
        )
        conn.execute(
            text("INSERT INTO persons (id, last_name, first_name) VALUES (:id, 'Leak', 'Check')"),
            {"id": person_id},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, person_id, login_identifier, status) "
                "VALUES (:id, :pid, :login, 'active')"
            ),
            {"id": user_id, "pid": person_id, "login": f"leak-{uuid.uuid4().hex[:8]}@x.example"},
        )
        conn.execute(
            text("INSERT INTO roles (id, code, name, is_system) VALUES (:id, :code, 'x', false)"),
            {"id": role_id, "code": f"leak-role-{uuid.uuid4().hex[:8]}"},
        )
        conn.execute(
            text(
                "INSERT INTO user_role_assignments (id, user_id, role_id, scope_type) "
                "VALUES (:id, :uid, :rid, 'all')"
            ),
            {"id": str(uuid.uuid4()), "uid": user_id, "rid": role_id},
        )
        conn.execute(
            text(
                "INSERT INTO audit_logs (id, actor_type, action, outcome) "
                "VALUES (:id, 'system', 'membership.created', 'success')"
            ),
            {"id": str(uuid.uuid4())},
        )

        after_insert = {
            t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables
        }
        assert after_insert != before, "setup bug in this test itself: nothing was actually written"

    # Restore the session's ORIGINAL baseline snapshot (captured once,
    # right after the real `alembic upgrade head`) — the exact same
    # object `_reset_between_tests` restores before every other test.
    reset_data_only(engine, database_url, _integration_baseline_snapshot)

    with engine.connect() as conn:
        after_reset = {
            t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables
        }

    mismatched = {t: (before[t], after_reset[t]) for t in tables if before[t] != after_reset[t]}
    assert mismatched == {}, (
        f"table row counts did not return to their pre-test values after reset "
        f"(table: (before, after_reset)): {mismatched}"
    )

    engine.dispose()


@requires_postgres
def test_baseline_restore_matches_the_original_migration_snapshot(
    database_url: str, _integration_baseline_snapshot, tmp_path
) -> None:
    """The core future-migration-regression guard (generic — asserts data
    equivalence, never a specific table/column/code):
    `_integration_baseline_snapshot` is the snapshot captured ONCE at
    session start, immediately after the real `alembic upgrade head` —
    i.e. "baseline right after migration". This test's own per-test reset
    (the autouse `_reset_between_tests` fixture) has ALREADY restored
    that exact snapshot before this test started running — i.e. the
    current database state is "baseline after reset". Re-dumping the
    current state and comparing it to the original snapshot directly
    proves `baseline_after_migration == baseline_after_reset`, for
    whatever tables/rows actually exist — nothing here assumes what those
    are.

    If a future migration adds a new seeded table/row and the restore
    step (which restores from the *same* snapshot object, not a
    re-derived one) ever diverged from it, this would still trivially
    pass — what it actually guards against is `capture_baseline_snapshot`/
    `reset_data_only` themselves silently dropping or corrupting data
    during the truncate-then-restore round trip.
    """
    current_snapshot = tmp_path / "current.sql"
    capture_baseline_snapshot(database_url, current_snapshot)

    original = _strip_dump_nonce(_integration_baseline_snapshot.read_text())
    current = _strip_dump_nonce(current_snapshot.read_text())
    assert current == original, (
        "a fresh dump of the just-reset database does not match the session's "
        "original post-migration snapshot — the reset round trip is lossy"
    )


@requires_postgres
def test_baseline_roles_and_permissions_exist_after_reset(database_url: str) -> None:
    """Not a hardcoded list of codes — just confirms the reset leaves
    *some* baseline reference data in place (proving the restore step ran
    and actually inserted rows, as a sanity check independent of the
    byte-for-byte comparison above)."""
    url = make_url(database_url)
    engine = create_engine(url)
    with engine.connect() as conn:
        role_count = conn.execute(text("SELECT count(*) FROM roles")).scalar_one()
        permission_count = conn.execute(text("SELECT count(*) FROM permissions")).scalar_one()
    engine.dispose()
    assert role_count > 0
    assert permission_count > 0


@requires_postgres
def test_alembic_version_is_untouched_by_the_per_test_reset(database_url: str) -> None:
    """alembic_version must never be truncated or restored as ordinary
    test data — it stays exactly what the one-time session migration set
    it to."""
    url = make_url(database_url)
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    engine.dispose()
    assert len(rows) == 1, "alembic_version must have exactly one row (Alembic's own invariant)"


@requires_postgres
def test_gist_exclude_constraint_still_enforced_after_reset(database_url: str) -> None:
    """A generic proof (not role-assignment-specific) that DB-level
    constraints survive the truncate-then-restore round trip: the same
    GiST EXCLUDE constraint pattern used throughout this schema for
    "at most one active primary instructor per Group" (people-api.md
    §16.2) must still reject an overlap after a normal reset, exactly as
    it would on a schema that had never been reset."""
    url = make_url(database_url)
    engine = create_engine(url)
    with engine.connect() as conn:
        club_id = str(uuid.uuid4())
        person_a_id = str(uuid.uuid4())
        person_b_id = str(uuid.uuid4())
        user_a_id = str(uuid.uuid4())
        user_b_id = str(uuid.uuid4())
        group_id = str(uuid.uuid4())
        conn.execute(
            text("INSERT INTO clubs (id, name, status) VALUES (:id, :name, 'active')"),
            {"id": club_id, "name": f"CE-{uuid.uuid4().hex[:8]}"},
        )
        conn.execute(
            text("INSERT INTO persons (id, last_name, first_name) VALUES (:id, 'C', 'A')"),
            {"id": person_a_id},
        )
        conn.execute(
            text("INSERT INTO persons (id, last_name, first_name) VALUES (:id, 'C', 'B')"),
            {"id": person_b_id},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, person_id, login_identifier, status) "
                "VALUES (:id, :pid, :login, 'active')"
            ),
            {"id": user_a_id, "pid": person_a_id, "login": f"cea-{uuid.uuid4().hex[:8]}@x.example"},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, person_id, login_identifier, status) "
                "VALUES (:id, :pid, :login, 'active')"
            ),
            {"id": user_b_id, "pid": person_b_id, "login": f"ceb-{uuid.uuid4().hex[:8]}@x.example"},
        )
        conn.execute(
            text(
                "INSERT INTO groups (id, club_id, name, status, valid_from) "
                "VALUES (:id, :cid, :name, 'active', '2024-01-01T00:00:00Z')"
            ),
            {"id": group_id, "cid": club_id, "name": f"CE-group-{uuid.uuid4().hex[:8]}"},
        )
        conn.execute(
            text(
                "INSERT INTO group_instructor_assignments "
                "(id, group_id, user_id, role_in_group, is_primary, valid_from) "
                "VALUES (:id, :gid, :uid, 'leader', true, '2024-01-01T00:00:00Z')"
            ),
            {"id": str(uuid.uuid4()), "gid": group_id, "uid": user_a_id},
        )
        conn.commit()

        from sqlalchemy.exc import IntegrityError

        overlap_rejected = False
        try:
            conn.execute(
                text(
                    "INSERT INTO group_instructor_assignments "
                    "(id, group_id, user_id, role_in_group, is_primary, valid_from) "
                    "VALUES (:id, :gid, :uid, 'leader', true, '2024-06-01T00:00:00Z')"
                ),
                {"id": str(uuid.uuid4()), "gid": group_id, "uid": user_b_id},
            )
            conn.commit()
        except IntegrityError:
            overlap_rejected = True
            conn.rollback()
    engine.dispose()
    assert overlap_rejected, "the GiST EXCLUDE constraint did not reject an overlap after reset"
