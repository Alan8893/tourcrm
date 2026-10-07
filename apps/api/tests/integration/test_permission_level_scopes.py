"""AUTH-2A — permission-level scopes: engine regressions, `/auth/me`, and
the `20e1297d4e1a` legacy-data migration.

Authorization scope is a property of each permission grant
(`UserRoleAssignment -> RolePermission -> RolePermissionScope`), never of
the assignment. Pinned here:

- one grant may carry several scopes; any one matching scope allows;
- the scopes of one permission never apply to another permission, even
  of the same role;
- the legacy `UserRoleAssignment.scope_type` never widens (or narrows) a
  grant;
- a grant without scope rows grants nothing;
- the migration seeds scopes only for existing grants, closes legacy
  non-`all` admin assignments (GAP-8) and duplicate active assignments of
  one role (GAP-5) with a system audit record, and grants nothing new.

Run with a reachable PostgreSQL instance (see tests/integration/conftest.py).
Tests that downgrade/upgrade always restore `head` before returning.
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import CurrentPrincipal, get_current_principal
from app.authorization.context import ResourceContext
from app.authorization.service import applicable_grants, can
from app.db.audit import AuditLog
from app.db.authorization import (
    DOCUMENTED_PERMISSION_CODES,
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_AUTH_2A_REVISION = "20e1297d4e1a"
_BEFORE_AUTH_2A_REVISION = "4b1f0c2d9a7e"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _permission(session, code: str) -> Permission:  # type: ignore[no-untyped-def]
    permission = session.execute(
        select(Permission).where(Permission.code == code)
    ).scalar_one_or_none()
    if permission is None:
        permission = Permission(code=code)
        session.add(permission)
        session.flush()
    return permission


def _role(session, code: str) -> Role:  # type: ignore[no-untyped-def]
    role = session.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
    if role is None:
        role = Role(code=code, name=code)
        session.add(role)
        session.flush()
    return role


def _grant(session, role: Role, permission_code: str, *scope_types: str) -> None:  # type: ignore[no-untyped-def]
    session.add(
        RolePermission(
            role_id=role.id,
            permission_id=_permission(session, permission_code).id,
            scopes=[RolePermissionScope(scope_type=scope_type) for scope_type in scope_types],
        )
    )
    session.flush()


def _club_and_user(session) -> tuple[Club, User]:  # type: ignore[no-untyped-def]
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    person = Person(last_name="Ivanova", first_name=f"A-{uuid.uuid4().hex[:6]}")
    session.add_all([club, person])
    session.flush()
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="member",
            status="active",
            joined_at=_utc(2020, 1, 1),
        )
    )
    session.flush()
    return club, user


def _assign(  # type: ignore[no-untyped-def]
    session, user: User, role: Role, club: Club | None, **extra: object
) -> UserRoleAssignment:
    assignment = UserRoleAssignment(
        user_id=user.id, role_id=role.id, club_id=club.id if club else None, **extra
    )
    session.add(assignment)
    session.flush()
    return assignment


# --- Engine: several scopes on one permission ----------------------------------


@requires_postgres
def test_one_permission_may_carry_several_scopes_and_any_one_allows() -> None:
    """instructor `event.read` with `own_groups` + `own_events` through one
    RoleAssignment (the canonical seeded grant, migration a7c3e5f19d24)."""
    with session_scope() as session:
        club, user = _club_and_user(session)
        instructor = _role(session, "instructor")
        _assign(session, user, instructor, club)
        session.commit()

        grants = applicable_grants(session, user.id, "event.read")
        assert sorted(grant.scope_type for grant in grants) == ["own_events", "own_groups"]
        assert {grant.assignment.id for grant in grants} == {grants[0].assignment.id}

        own_group = ResourceContext(club_id=club.id, is_own_group=True, is_own_event=False)
        own_event = ResourceContext(club_id=club.id, is_own_group=False, is_own_event=True)
        neither = ResourceContext(club_id=club.id, is_own_group=False, is_own_event=False)
        assert can(session, user.id, "event.read", own_group) is True
        assert can(session, user.id, "event.read", own_event) is True
        assert can(session, user.id, "event.read", neither) is False


@requires_postgres
def test_guardian_person_read_children_and_self_through_one_assignment() -> None:
    with session_scope() as session:
        club, user = _club_and_user(session)
        guardian = _role(session, "guardian")
        _grant(session, guardian, "person.read", "children", "self")
        _assign(session, user, guardian, club)
        session.commit()

        assert can(session, user.id, "person.read", ResourceContext(club_id=club.id, is_child=True))
        assert can(session, user.id, "person.read", ResourceContext(club_id=club.id, is_self=True))
        assert not can(
            session,
            user.id,
            "person.read",
            ResourceContext(club_id=club.id, is_child=False, is_self=False),
        )


# --- Engine: scope isolation between permissions ---------------------------------


@requires_postgres
def test_scopes_of_one_permission_never_apply_to_another_permission() -> None:
    with session_scope() as session:
        club, user = _club_and_user(session)
        role = _role(session, f"role-{uuid.uuid4().hex[:8]}")
        _grant(session, role, "widget.read", "all")
        _grant(session, role, "widget.update", "self")
        _assign(session, user, role, club)
        session.commit()

        foreign = ResourceContext(club_id=club.id, is_self=False)
        assert can(session, user.id, "widget.read", foreign) is True
        # `all` belongs to widget.read only — no escalation of widget.update.
        assert can(session, user.id, "widget.update", foreign) is False
        update_grants = applicable_grants(session, user.id, "widget.update")
        assert [grant.scope_type for grant in update_grants] == ["self"]


@requires_postgres
def test_grant_without_any_scope_grants_nothing() -> None:
    with session_scope() as session:
        club, user = _club_and_user(session)
        role = _role(session, f"role-{uuid.uuid4().hex[:8]}")
        _grant(session, role, "widget.read")
        _assign(session, user, role, club)
        session.commit()

        assert applicable_grants(session, user.id, "widget.read") == []
        assert can(session, user.id, "widget.read", ResourceContext(club_id=club.id)) is False


# --- Engine: the legacy assignment scope is not an input -------------------------


@requires_postgres
@pytest.mark.parametrize("legacy_scope", ["all", "self", "children", "own_groups", "own_events"])
def test_legacy_assignment_scope_never_widens_a_grant(legacy_scope: str) -> None:
    with session_scope() as session:
        club, user = _club_and_user(session)
        role = _role(session, f"role-{uuid.uuid4().hex[:8]}")
        _grant(session, role, "widget.read", "self")
        _assign(session, user, role, club, scope_type=legacy_scope)
        session.commit()

        wide = ResourceContext(
            club_id=club.id, is_self=False, is_child=True, is_own_group=True, is_own_event=True
        )
        assert can(session, user.id, "widget.read", wide) is False
        assert [g.scope_type for g in applicable_grants(session, user.id, "widget.read")] == [
            "self"
        ]


@requires_postgres
def test_legacy_assignment_scope_never_narrows_a_grant() -> None:
    with session_scope() as session:
        club, user = _club_and_user(session)
        role = _role(session, f"role-{uuid.uuid4().hex[:8]}")
        _grant(session, role, "widget.read", "all")
        _assign(session, user, role, club, scope_type="none")
        session.commit()

        assert can(session, user.id, "widget.read", ResourceContext(club_id=club.id)) is True


@requires_postgres
def test_legacy_instructor_all_assignment_gets_no_new_permission() -> None:
    """A legacy `instructor` + `all` assignment does not mean
    `person.read`/`group.read`/`event.read` = `all`: the instructor role
    only holds its canonical grants, at their canonical scopes."""
    with session_scope() as session:
        club, user = _club_and_user(session)
        _assign(session, user, _role(session, "instructor"), club, scope_type="all")
        session.commit()

        wide = ResourceContext(club_id=club.id)
        for permission_code in ("person.read", "person.update", "group.read"):
            assert applicable_grants(session, user.id, permission_code) == []
            assert can(session, user.id, permission_code, wide) is False
        # Issue #305 (migration a7c3e5f19d24): event.read is seeded only at
        # own_groups/own_events — the legacy `all` assignment scope still
        # does not widen it.
        event_grants = applicable_grants(session, user.id, "event.read")
        assert sorted(grant.scope_type for grant in event_grants) == ["own_events", "own_groups"]
        assert can(session, user.id, "event.read", wide) is False
        directory_grants = applicable_grants(session, user.id, "user.directory.read")
        assert [grant.scope_type for grant in directory_grants] == ["all"]


# --- Canonical seed ---------------------------------------------------------------


@requires_postgres
def test_canonical_grants_carry_exactly_the_decided_scopes() -> None:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code, RolePermissionScope.scope_type)
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .outerjoin(
                RolePermissionScope, RolePermissionScope.role_permission_id == RolePermission.id
            )
        ).all()

    by_grant: dict[tuple[str, str], set[str | None]] = {}
    for role_code, permission_code, scope_type in rows:
        by_grant.setdefault((role_code, permission_code), set()).add(scope_type)

    expected = {("admin", code): {"all"} for code in DOCUMENTED_PERMISSION_CODES}
    expected[("instructor", "user.directory.read")] = {"all"}
    # TH-0172 / ADR-0043, migration 127da2741f20.
    expected[("guardian", "event.read")] = {"children"}
    # Issue #245, migration 3c1e9a7d5b20 (admin's trip grants are covered
    # by the `all` set above).
    expected[("instructor", "trip.read")] = {"own_groups", "own_events"}
    expected[("instructor", "trip.manage")] = {"own_groups", "own_events"}
    expected[("member", "trip.read")] = {"self"}
    expected[("guardian", "trip.read")] = {"children"}
    # Issue #282, migration 5e2c8a41d7b9.
    expected[("member", "group.read")] = {"self"}
    # Issue #285, migration 9b4d6e2f8a10.
    expected[("member", "event.read")] = {"self"}
    # ADR-0046 / Issue #301, migration c4f7a2e91b36.
    expected[("guardian", "group.read")] = {"children"}
    # PO decision on GET /me/children, migration d8e3b5f02a47.
    expected[("guardian", "guardian_relationship.read")] = {"children"}
    # Issue #305, migration a7c3e5f19d24.
    expected[("instructor", "attendance.read")] = {"own_groups", "own_events"}
    expected[("instructor", "attendance.update")] = {"own_groups", "own_events"}
    expected[("instructor", "event.read")] = {"own_groups", "own_events"}
    expected[("member", "attendance.read")] = {"self"}
    expected[("guardian", "attendance.read")] = {"children"}
    assert by_grant == expected


# --- /auth/me ---------------------------------------------------------------------


@requires_postgres
def test_auth_me_lists_each_role_once(client: TestClient) -> None:
    with session_scope() as session:
        club, user = _club_and_user(session)
        for code in ("instructor", "guardian"):
            _assign(session, user, _role(session, code), club)
        session.commit()
        user_id = user.id
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200, response.text
    role_codes = [item["role_code"] for item in response.json()["role_assignments"]]
    assert sorted(role_codes) == ["guardian", "instructor"]


# --- Migration 20e1297d4e1a on legacy data ------------------------------------------


def _legacy_assignment(
    session,  # type: ignore[no-untyped-def]
    *,
    user_id: uuid.UUID,
    role_code: str,
    club_id: uuid.UUID | None,
    scope_type: str,
    valid_from: datetime.datetime,
    valid_to: datetime.datetime | None = None,
) -> uuid.UUID:
    """Raw insert: runs against the pre-AUTH-2A schema."""
    assignment_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO user_role_assignments "
            "(id, user_id, role_id, club_id, scope_type, valid_from, valid_to) "
            "SELECT :id, :user_id, r.id, :club_id, :scope_type, :valid_from, :valid_to "
            "FROM roles r WHERE r.code = :role_code"
        ),
        {
            "id": assignment_id,
            "user_id": user_id,
            "club_id": club_id,
            "scope_type": scope_type,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "role_code": role_code,
        },
    )
    return assignment_id


def _assignment_state(
    assignment_id: uuid.UUID,
) -> tuple[datetime.datetime, datetime.datetime | None]:
    with session_scope() as session:
        row = session.execute(
            select(UserRoleAssignment.valid_from, UserRoleAssignment.valid_to).where(
                UserRoleAssignment.id == assignment_id
            )
        ).one()
        return row.valid_from, row.valid_to


def _revoked_audit_rows(assignment_id: uuid.UUID) -> list[AuditLog]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "role_assignment.revoked",
                    AuditLog.resource_id == assignment_id,
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return list(rows)


@requires_postgres
def test_migration_consolidates_legacy_assignments_without_widening_rights(
    database_url: str,
) -> None:
    downgrade = run_alembic("downgrade", _BEFORE_AUTH_2A_REVISION, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    try:
        with session_scope() as session:
            club, instructor_user = _club_and_user(session)
            _, admin_restricted_user = _club_and_user(session)
            _, admin_mixed_user = _club_and_user(session)
            _, admin_global_none_user = _club_and_user(session)
            _, member_user = _club_and_user(session)
            _, history_user = _club_and_user(session)
            session.commit()
            ids = {
                # AUTH-2 pair: instructor own_groups (earlier) + self (later).
                "instructor_kept": _legacy_assignment(
                    session,
                    user_id=instructor_user.id,
                    role_code="instructor",
                    club_id=club.id,
                    scope_type="own_groups",
                    valid_from=_utc(2024, 1, 1),
                ),
                "instructor_duplicate": _legacy_assignment(
                    session,
                    user_id=instructor_user.id,
                    role_code="instructor",
                    club_id=club.id,
                    scope_type="self",
                    valid_from=_utc(2024, 2, 1),
                ),
                # GAP-8: restricted admin (future valid_to — still active).
                "admin_restricted": _legacy_assignment(
                    session,
                    user_id=admin_restricted_user.id,
                    role_code="admin",
                    club_id=club.id,
                    scope_type="own_groups",
                    valid_from=_utc(2024, 1, 1),
                    valid_to=_utc(2999, 1, 1),
                ),
                # GAP-8 before GAP-5: earliest is restricted, later is `all`.
                "admin_mixed_restricted": _legacy_assignment(
                    session,
                    user_id=admin_mixed_user.id,
                    role_code="admin",
                    club_id=club.id,
                    scope_type="self",
                    valid_from=_utc(2023, 1, 1),
                ),
                "admin_mixed_all": _legacy_assignment(
                    session,
                    user_id=admin_mixed_user.id,
                    role_code="admin",
                    club_id=club.id,
                    scope_type="all",
                    valid_from=_utc(2024, 1, 1),
                ),
                # GAP-8: a global `none` admin must not become a global admin.
                "admin_global_none": _legacy_assignment(
                    session,
                    user_id=admin_global_none_user.id,
                    role_code="admin",
                    club_id=None,
                    scope_type="none",
                    valid_from=_utc(2024, 1, 1),
                ),
                # A single legacy assignment is left as it is.
                "member_single": _legacy_assignment(
                    session,
                    user_id=member_user.id,
                    role_code="member",
                    club_id=club.id,
                    scope_type="all",
                    valid_from=_utc(2024, 1, 1),
                ),
                # Closed, overlapping AUTH-2 history is kept untouched.
                "history_a": _legacy_assignment(
                    session,
                    user_id=history_user.id,
                    role_code="guardian",
                    club_id=club.id,
                    scope_type="children",
                    valid_from=_utc(2024, 1, 1),
                    valid_to=_utc(2024, 6, 1),
                ),
                "history_b": _legacy_assignment(
                    session,
                    user_id=history_user.id,
                    role_code="guardian",
                    club_id=club.id,
                    scope_type="self",
                    valid_from=_utc(2024, 1, 1),
                    valid_to=_utc(2024, 6, 1),
                ),
            }
            session.commit()
            users = {
                "instructor": instructor_user.id,
                "admin_restricted": admin_restricted_user.id,
                "admin_mixed": admin_mixed_user.id,
                "admin_global_none": admin_global_none_user.id,
                "member": member_user.id,
            }
            club_id = club.id

        upgrade = run_alembic("upgrade", _AUTH_2A_REVISION, database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr

        closed = {
            "instructor_duplicate",
            "admin_restricted",
            "admin_mixed_restricted",
            "admin_global_none",
        }
        kept_open = {"instructor_kept", "admin_mixed_all", "member_single"}
        untouched_history = {"history_a", "history_b"}

        for key in closed:
            valid_from, valid_to = _assignment_state(ids[key])
            assert valid_to is not None and valid_to >= valid_from, key
            assert valid_to < _utc(2999, 1, 1), key
            [audit] = _revoked_audit_rows(ids[key])
            assert audit.actor_type == "system"
            assert audit.actor_user_id is None
            assert audit.outcome == "success"
            assert audit.resource_type == "role_assignment"
            changes = (audit.details or {})["changes"]["valid_to"]
            assert changes["to"] is not None
        assert (_revoked_audit_rows(ids["admin_restricted"])[0].details or {})["changes"][
            "valid_to"
        ]["from"].startswith("2999-01-01")
        assert (_revoked_audit_rows(ids["instructor_duplicate"])[0].details or {})["changes"][
            "valid_to"
        ]["from"] is None

        for key in kept_open:
            assert _assignment_state(ids[key])[1] is None, key
            assert _revoked_audit_rows(ids[key]) == [], key
        for key in untouched_history:
            assert _assignment_state(ids[key])[1] == _utc(2024, 6, 1), key
            assert _revoked_audit_rows(ids[key]) == [], key

        with session_scope() as session:
            club_wide = ResourceContext(club_id=club_id)
            other_club = ResourceContext(club_id=uuid.uuid4())
            # No widening: restricted/`none` admins lost admin reach...
            assert not can(session, users["admin_restricted"], "person.update", club_wide)
            assert not can(session, users["admin_global_none"], "person.update", other_club)
            # ...a user who also held `admin` + `all` keeps it...
            assert can(session, users["admin_mixed"], "person.update", club_wide)
            # ...and legacy instructor/member rows gain nothing new.
            assert not can(session, users["instructor"], "person.read", club_wide)
            assert not can(session, users["member"], "person.read", club_wide)
            assert can(session, users["instructor"], "user.directory.read", club_wide)

        # The one-active-role invariant now holds regardless of scope.
        with session_scope() as session, pytest.raises(Exception) as exc_info:
            _legacy_assignment(
                session,
                user_id=users["instructor"],
                role_code="instructor",
                club_id=club_id,
                scope_type="own_events",
                valid_from=_utc(2025, 1, 1),
            )
            session.commit()
        assert "uq_user_role_assignments_one_active_role" in str(exc_info.value)
    finally:
        run_alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_migration_downgrade_restores_the_composite_role_permission_key(
    database_url: str,
) -> None:
    downgrade = run_alembic("downgrade", _BEFORE_AUTH_2A_REVISION, database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr
    try:
        with session_scope() as session:
            columns = set(
                session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'role_permissions'"
                    )
                ).scalars()
            )
            scope_table = session.execute(
                text("SELECT to_regclass('public.role_permission_scopes')")
            ).scalar_one()
            pk_columns = set(
                session.execute(
                    text(
                        "SELECT a.attname FROM pg_index i "
                        "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                        "AND a.attnum = ANY(i.indkey) "
                        "WHERE i.indrelid = 'role_permissions'::regclass AND i.indisprimary"
                    )
                ).scalars()
            )
        assert columns == {"role_id", "permission_id"}
        assert scope_table is None
        assert pk_columns == {"role_id", "permission_id"}
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
