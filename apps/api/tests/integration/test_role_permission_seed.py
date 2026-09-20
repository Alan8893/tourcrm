"""Integration tests for the TH-0093 / Issue #106 canonical admin
`RolePermission` seed (migration `6a99a77234ba`), implementing the
accepted PO decision in TH-0092 / Issue #105 (Variant A): the system
`admin` role receives a grant for every permission in the canonical
catalog (`app.db.authorization.DOCUMENTED_PERMISSION_CODES`).
`instructor`/`member`/`guardian` grants are intentionally out of scope
for this task and are not asserted to exist here.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_authorization.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v

Tests that deliberately exercise `alembic downgrade`/`upgrade` always
finish by restoring the schema to `head` before returning, per
tests/integration/_schema_reset.py's own documented contract for this
small class of test.
"""

import inspect
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.authentication.bootstrap import bootstrap_initial_administrator
from app.db.authorization import DOCUMENTED_PERMISSION_CODES, Permission, Role, RolePermission
from app.db.session import session_scope
from app.main import app

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_MIGRATION_REVISION = "6a99a77234ba"
_BEFORE_MIGRATION_REVISION = "91a40113d17b"
_STRONG_PASSWORD = "a-strong-bootstrap-password"


def _unique_email() -> str:
    return f"admin-{uuid.uuid4().hex[:8]}@example.com"


def _admin_permission_codes() -> set[str]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(Role, Role.id == RolePermission.role_id)
                .where(Role.code == "admin")
            )
            .scalars()
            .all()
        )
        return set(rows)


def _all_role_permission_pairs() -> set[tuple[str, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code)
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        ).all()
        return {(role_code, permission_code) for role_code, permission_code in rows}


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


# --- (1) fresh migration seeds every canonical permission for admin --------


@requires_postgres
def test_admin_role_has_a_grant_for_every_canonical_permission() -> None:
    assert _admin_permission_codes() == set(DOCUMENTED_PERMISSION_CODES)


@requires_postgres
def test_admin_role_permission_count_matches_canonical_catalog_exactly() -> None:
    with session_scope() as session:
        role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
        rows = (
            session.execute(select(RolePermission).where(RolePermission.role_id == role.id))
            .scalars()
            .all()
        )
        assert len(rows) == len(DOCUMENTED_PERMISSION_CODES)


# --- (6) no instructor/member/guardian grants are introduced ---------------
#
# TH-0107 (migration 95487f3b616b, after this one in the chain) is the one
# deliberate exception: `instructor` receives exactly one grant,
# `user.directory.read`, via its own migration/PO decision — see that
# migration's docstring and app.users.authorization. `member`/`guardian`
# still receive nothing, and `instructor` receives nothing beyond that one
# permission, at head.


@requires_postgres
def test_non_admin_baseline_roles_receive_no_unexpected_grants() -> None:
    with session_scope() as session:
        rows = set(
            session.execute(
                select(Role.code, Permission.code)
                .select_from(RolePermission)
                .join(Role, Role.id == RolePermission.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Role.code != "admin")
            ).all()
        )
        assert rows == {("instructor", "user.directory.read")}


# --- (2)/(4) partial-state convergence + unrelated rows survive ------------


@requires_postgres
def test_migration_converges_partial_admin_grants_and_preserves_unrelated_rows(
    database_url: str,
) -> None:
    downgrade = run_alembic("downgrade", _BEFORE_MIGRATION_REVISION, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr

    partial_codes = ("person.read", "person.update", "group.read")
    try:
        with session_scope() as session:
            admin_role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
            instructor_role = session.execute(
                select(Role).where(Role.code == "instructor")
            ).scalar_one()
            permissions = {
                code: session.execute(
                    select(Permission).where(Permission.code == code)
                ).scalar_one()
                for code in (*partial_codes, "event.read")
            }
            for code in partial_codes:
                session.add(
                    RolePermission(role_id=admin_role.id, permission_id=permissions[code].id)
                )
            # An unrelated/custom grant for a different role — outside this
            # migration's own fixed (admin, canonical-code) list — must
            # survive the migration untouched.
            session.add(
                RolePermission(
                    role_id=instructor_role.id, permission_id=permissions["event.read"].id
                )
            )
            session.commit()

        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr

        assert _admin_permission_codes() == set(DOCUMENTED_PERMISSION_CODES)
        assert ("instructor", "event.read") in _all_role_permission_pairs()
    finally:
        run_alembic("upgrade", "head", database_url=database_url)


# --- (3) idempotency of repeated seed/migration -----------------------------


@requires_postgres
def test_reapplying_admin_seed_after_downgrade_and_upgrade_is_idempotent(
    database_url: str,
) -> None:
    downgrade = run_alembic("downgrade", _BEFORE_MIGRATION_REVISION, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr

    upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    assert _admin_permission_codes() == set(DOCUMENTED_PERMISSION_CODES)
    with session_scope() as session:
        rows = session.execute(select(RolePermission)).scalars().all()
        # admin's full canonical set, plus TH-0107's one additional
        # (instructor, user.directory.read) grant (migration 95487f3b616b,
        # re-applied by the same upgrade-to-head above).
        assert len(rows) == len(DOCUMENTED_PERMISSION_CODES) + 1


# --- (5) downgrade removes only rows this migration introduced -------------


@requires_postgres
def test_downgrade_removes_only_admin_canonical_grants_and_preserves_everything_else(
    database_url: str,
) -> None:
    with session_scope() as session:
        instructor_role = session.execute(
            select(Role).where(Role.code == "instructor")
        ).scalar_one()
        permission = session.execute(
            select(Permission).where(Permission.code == "person.read")
        ).scalar_one()
        session.add(RolePermission(role_id=instructor_role.id, permission_id=permission.id))
        session.commit()

    try:
        downgrade = run_alembic("downgrade", _BEFORE_MIGRATION_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr

        with session_scope() as session:
            role_codes = set(session.execute(select(Role.code)).scalars().all())
            permission_codes = set(session.execute(select(Permission.code)).scalars().all())

        assert {"admin", "instructor", "member", "guardian"} <= role_codes
        assert permission_codes == set(DOCUMENTED_PERMISSION_CODES)
        # Only the unrelated custom grant remains; every admin/canonical
        # pair this migration seeded is gone.
        assert _all_role_permission_pairs() == {("instructor", "person.read")}
    finally:
        run_alembic("upgrade", "head", database_url=database_url)


# --- (7) bootstrap does not own RolePermission creation ---------------------


@requires_postgres
def test_bootstrap_module_never_references_role_permission() -> None:
    from app.authentication import bootstrap as bootstrap_module

    source = inspect.getsource(bootstrap_module)
    assert "RolePermission" not in source


@requires_postgres
def test_bootstrap_does_not_create_or_modify_role_permission_rows() -> None:
    with session_scope() as session:
        before = set(
            session.execute(select(RolePermission.role_id, RolePermission.permission_id)).all()
        )

    with session_scope() as session:
        bootstrap_initial_administrator(
            session,
            club_name="Bootstrap Boundary Club",
            email=_unique_email(),
            password=_STRONG_PASSWORD,
        )

    with session_scope() as session:
        after = set(
            session.execute(select(RolePermission.role_id, RolePermission.permission_id)).all()
        )

    assert before == after


# --- (8) end-to-end: bootstrap -> login -> protected admin operation -------


@requires_postgres
def test_end_to_end_bootstrap_login_and_create_group_is_authorized_via_seeded_grant() -> None:
    email = _unique_email()
    with session_scope() as session:
        result = bootstrap_initial_administrator(
            session,
            club_name="E2E Seed Club",
            email=email,
            password=_STRONG_PASSWORD,
        )

    client = TestClient(app, raise_server_exceptions=True)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"identifier": email, "password": _STRONG_PASSWORD},
    )
    assert login_response.status_code == 200, login_response.text

    # No manual permission grant anywhere in this test: `group.manage`
    # reaches the bootstrap-created admin solely through the migration's
    # own seeded RolePermission row.
    response = client.post(
        "/api/v1/groups",
        json={
            "club_id": str(result.club_id),
            "name": "E2E Seed Group",
            "valid_from": "2024-01-01T00:00:00+00:00",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["club_id"] == str(result.club_id)
