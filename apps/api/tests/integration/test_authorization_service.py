"""Real PostgreSQL integration tests for the Issue #29 `can()`/`Authorizer`
engine: permission lookup, multi-role additivity, scope evaluation and club
isolation against actual Role/Permission/RolePermission/UserRoleAssignment
rows.

No RolePermission grant is assumed to pre-exist (Issue #19 seeded roles and
permissions but deliberately no grants, and Issue #29 must not invent any)
— every test creates exactly the rows its scenario needs, per Issue #29 §12.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_authorization.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, Authorizer, can
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.session import session_scope

from .conftest import requires_postgres

API_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _migrated_schema(database_url: str) -> None:
    result = _run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {"last_name": "Ivanova", "first_name": "Anna"}
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_user(person: Person, **overrides: object) -> User:
    defaults: dict[str, object] = {
        "person": person,
        "login_identifier": f"user-{uuid.uuid4().hex[:8]}@example.com",
        "status": "active",
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Club {uuid.uuid4().hex[:8]}", "status": "active"}
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_role(**overrides: object) -> Role:
    defaults: dict[str, object] = {"code": f"role-{uuid.uuid4().hex[:8]}", "name": "Test Role"}
    defaults.update(overrides)
    return Role(**defaults)  # type: ignore[arg-type]


def _make_permission(**overrides: object) -> Permission:
    defaults: dict[str, object] = {"code": f"resource-{uuid.uuid4().hex[:8]}.read"}
    defaults.update(overrides)
    return Permission(**defaults)  # type: ignore[arg-type]


def _grant(session, role: Role, permission: Permission) -> None:
    session.add(RolePermission(role_id=role.id, permission_id=permission.id))


def _assign(session, user: User, role: Role, **overrides: object) -> UserRoleAssignment:
    defaults: dict[str, object] = {"user_id": user.id, "role_id": role.id, "scope_type": "all"}
    defaults.update(overrides)
    assignment = UserRoleAssignment(**defaults)  # type: ignore[arg-type]
    session.add(assignment)
    return assignment


# --- Permission lookup ----------------------------------------------------


@requires_postgres
def test_user_with_no_role_assignment_is_denied() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()

        assert can(session, user.id, "widget.read") is False


@requires_postgres
def test_role_assigned_but_permission_not_granted_is_denied() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-no-grant")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        # Deliberately no RolePermission row: role exists, permission
        # exists, but this role doesn't grant it.
        _assign(session, user, role, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.read") is False


@requires_postgres
def test_role_permission_grant_allows() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-with-grant")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.read") is True


@requires_postgres
def test_permission_check_is_specific_to_the_requested_code() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-single-grant")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.update") is False


@requires_postgres
def test_multiple_roles_grant_permissions_additively() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role_a = _make_role(code="role-a")
        role_b = _make_role(code="role-b")
        permission_x = _make_permission(code="widget.read")
        permission_y = _make_permission(code="widget.manage")
        session.add_all([person, user, role_a, role_b, permission_x, permission_y])
        session.commit()
        _grant(session, role_a, permission_x)
        _grant(session, role_b, permission_y)
        _assign(session, user, role_a, scope_type="all")
        _assign(session, user, role_b, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.read") is True
        assert can(session, user.id, "widget.manage") is True
        # This user has neither role granting an unrelated permission.
        assert can(session, user.id, "gadget.manage") is False


# --- Scope evaluation -------------------------------------------------------


@requires_postgres
def test_scope_all_allows_for_matching_club() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club = _make_club()
        role = _make_role(code="role-all-scope")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, club, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, club_id=club.id, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.read", ResourceContext(club_id=club.id)) is True


@requires_postgres
def test_scope_self_allows_own_resource_and_denies_others() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-self-scope")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="self")
        session.commit()

        assert can(session, user.id, "widget.read", ResourceContext(is_self=True)) is True
        assert can(session, user.id, "widget.read", ResourceContext(is_self=False)) is False


@requires_postgres
def test_unresolved_relationship_context_never_grants_access_despite_valid_grant() -> None:
    """Regression: even with a real, granted RolePermission for a
    relationship-scoped permission, an endpoint that never resolves the
    relationship (passes no context, or a context with the field left at
    its default/unresolved state) must be denied — exactly as if the
    relationship had been checked and found false, never as if it were
    true. This is the "endpoint forgot to resolve ownership" case the
    mechanism must fail closed against.
    """
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-unresolved-self-scope")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="self")
        session.commit()

        # No context at all (defaults to fully unresolved).
        assert can(session, user.id, "widget.read") is False
        # An explicit context that resolved club_id but never touched
        # is_self — a realistic "partially wired up endpoint" mistake.
        partially_resolved = ResourceContext(club_id=None)
        assert can(session, user.id, "widget.read", partially_resolved) is False

        authorizer = Authorizer(session=session, user_id=user.id, permission_code="widget.read")
        with pytest.raises(AuthorizationDenied):
            authorizer.check(partially_resolved)


@requires_postgres
def test_scope_children_allows_explicit_child_and_denies_unrelated() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-children-scope")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="children")
        session.commit()

        assert can(session, user.id, "widget.read", ResourceContext(is_child=True)) is True
        assert can(session, user.id, "widget.read", ResourceContext(is_child=False)) is False


@requires_postgres
def test_scope_own_groups_allows_owned_group_and_denies_others() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-own-groups-scope")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="own_groups")
        session.commit()

        assert can(session, user.id, "widget.read", ResourceContext(is_own_group=True)) is True
        assert can(session, user.id, "widget.read", ResourceContext(is_own_group=False)) is False


@requires_postgres
def test_scope_own_events_allows_assigned_event_and_denies_others() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-own-events-scope")
        permission = _make_permission(code="widget.update")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="own_events")
        session.commit()

        assert can(session, user.id, "widget.update", ResourceContext(is_own_event=True)) is True
        assert can(session, user.id, "widget.update", ResourceContext(is_own_event=False)) is False


@requires_postgres
def test_scope_none_always_denies() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-none-scope")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="none")
        session.commit()

        context = ResourceContext(
            is_self=True, is_child=True, is_own_group=True, is_own_event=True
        )
        assert can(session, user.id, "widget.read", context) is False


# --- Club isolation ---------------------------------------------------------


@requires_postgres
def test_club_scoped_assignment_does_not_grant_access_to_a_different_club() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club_a = _make_club()
        club_b = _make_club()
        role = _make_role(code="role-club-a-only")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, club_a, club_b, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, club_id=club_a.id, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.read", ResourceContext(club_id=club_a.id)) is True
        assert (
            can(session, user.id, "widget.read", ResourceContext(club_id=club_b.id)) is False
        )


@requires_postgres
def test_global_assignment_applies_regardless_of_club() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club = _make_club()
        role = _make_role(code="role-global")
        permission = _make_permission(code="widget.manage")
        session.add_all([person, user, club, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, club_id=None, scope_type="all")
        session.commit()

        assert can(session, user.id, "widget.manage", ResourceContext(club_id=club.id)) is True
        assert can(session, user.id, "widget.manage", ResourceContext(club_id=None)) is True


# --- Authorizer --------------------------------------------------------


@requires_postgres
def test_authorizer_check_raises_authorization_denied_on_deny() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()

        authorizer = Authorizer(session=session, user_id=user.id, permission_code="person.read")
        with pytest.raises(AuthorizationDenied):
            authorizer.check()


@requires_postgres
def test_authorizer_check_does_not_raise_on_allow() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="role-authorizer-allow")
        permission = _make_permission(code="widget.read")
        session.add_all([person, user, role, permission])
        session.commit()
        _grant(session, role, permission)
        _assign(session, user, role, scope_type="all")
        session.commit()

        authorizer = Authorizer(session=session, user_id=user.id, permission_code="widget.read")
        authorizer.check()  # must not raise
