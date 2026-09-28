"""HTTP-level integration tests for /api/v1/role-assignments (Issue #74,
implementing ADR-0026 and ADR-0027): authorization matrix (global/club-
scoped `role.manage`, cross-Club denial, role-name-alone insufficiency),
scope combination matrix, cross-Club/ClubMembership integrity, temporal
lifecycle (create/revoke/repeat-revoke/re-assignment), pagination/
filters/sorting, IDOR existence-hiding, and audit recording.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_groups_api.py's pattern.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v

ADR-0027 (RoleAssignment and ClubMembership effectivity): ending a
ClubMembership never deletes/mutates the RoleAssignment row, and — since
this ADR settles what was an open question during initial
implementation — never makes an otherwise temporally-effective
RoleAssignment ineffective either. Both are asserted here:
`test_ending_target_club_membership_leaves_the_role_assignment_row_untouched`
checks the row, and
`test_role_manage_remains_effective_after_callers_own_club_membership_ends`
checks it through the real authorization boundary these endpoints
enforce (a club-scoped `role.manage` caller can still use them after
their own ClubMembership in that Club ends).
"""

import datetime
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.authorization.service import applicable_grants
from app.db.audit import AuditLog
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app

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


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Club {uuid.uuid4().hex[:8]}", "status": "active"}
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": "Ivanova",
        "first_name": f"P-{uuid.uuid4().hex[:8]}",
    }
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


def _make_club_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "person_id": person.id,
        "membership_type": "member",
        "status": "active",
        "joined_at": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(code=permission_code)
            session.add(permission)
            session.commit()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.commit()
        session.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                scopes=[RolePermissionScope(scope_type=scope_type)],
            )
        )
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, club_id=club_id
            )
        )
        session.commit()


def _baseline_role_id(code: str) -> uuid.UUID:
    with session_scope() as session:
        return session.execute(select(Role.id).where(Role.code == code)).scalar_one()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _latest_audit_row(*, action: str, resource_id: uuid.UUID) -> AuditLog | None:
    with session_scope() as session:
        return (
            session.execute(
                select(AuditLog)
                .where(AuditLog.action == action, AuditLog.resource_id == resource_id)
                .order_by(AuditLog.occurred_at.desc())
            )
            .scalars()
            .first()
        )


def _setup_target_with_membership(session, club: Club) -> tuple[uuid.UUID, uuid.UUID]:
    """Creates a target Person/User with an active ClubMembership in
    `club`; returns (user_id, club_membership_id) — used for every
    "target already belongs to this club" scenario below. `club` may be
    transient (not yet added/committed) — Club.id is server-generated, so
    it is added/committed here before being referenced by the
    membership."""
    session.add(club)
    person = _make_person(first_name=f"Target-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.commit()
    user = _make_user(person)
    membership = _make_club_membership(club, person)
    session.add_all([user, membership])
    session.commit()
    return user.id, membership.id


# --- Authorization matrix --------------------------------------------------


@requires_postgres
def test_list_role_assignments_unauthenticated_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/role-assignments")
    assert response.status_code == 401


@requires_postgres
def test_create_role_assignment_unauthenticated_is_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(uuid.uuid4()), "role_id": str(uuid.uuid4())},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 401


@requires_postgres
def test_revoke_role_assignment_unauthenticated_is_401(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/role-assignments/{uuid.uuid4()}/revoke", headers=_csrf_headers(client)
    )
    assert response.status_code == 401


@requires_postgres
def test_create_role_assignment_global_role_manage_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("admin")

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["club_id"] is None
    assert body["valid_to"] is None

    audit_row = _latest_audit_row(
        action="role_assignment.created", resource_id=uuid.UUID(body["id"])
    )
    assert audit_row is not None


@requires_postgres
def test_create_role_assignment_club_scoped_role_manage_succeeds_within_own_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_id = caller.id, club.id
        target_id, _ = _setup_target_with_membership(session, club)
    _grant_permission(caller_id, "role.manage", scope_type="all", club_id=club_id)
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("instructor")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["club_id"] == str(club_id)


@requires_postgres
def test_create_role_assignment_club_scoped_role_manage_cross_club_is_forbidden(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_a_id, club_b_id = caller.id, club_a.id, club_b.id
        target_id, _ = _setup_target_with_membership(session, club_b)
    # Caller's role.manage only covers Club A.
    _grant_permission(caller_id, "role.manage", scope_type="all", club_id=club_a_id)
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("instructor")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_b_id),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


@requires_postgres
def test_create_role_assignment_role_manage_with_non_all_scope_is_ineffective(
    client: TestClient,
) -> None:
    """ADR-0026 §5: role.manage is only effective with scope_type='all' —
    a role.manage grant with any other scope (even a structurally valid
    one, like own_groups+club) must never authorize these endpoints."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_id = caller.id, club.id
        target_id, _ = _setup_target_with_membership(session, club)
    _grant_permission(caller_id, "role.manage", scope_type="own_groups", club_id=club_id)
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


@requires_postgres
def test_create_role_assignment_role_name_alone_without_permission_grant_is_forbidden(
    client: TestClient,
) -> None:
    """roles-and-permissions.md §2/business-rules.md §8: holding a role
    name alone is not itself sufficient — RolePermission must actually
    grant role.manage. `instructor` is used as the role-under-test
    because it is a baseline role left with zero RolePermission grants by
    design (TH-0092/TH-0093 only define canonical grants for `admin`;
    `instructor`/`member`/`guardian` remain intentionally ungranted) —
    `admin` itself no longer fits this scenario now that TH-0093 seeds it
    with every canonical permission, `role.manage` included."""
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        instructor_role_id = _baseline_role_id("instructor")
        session.add(caller)
        session.commit()
        session.add(
            UserRoleAssignment(user_id=caller.id, role_id=instructor_role_id, scope_type="all")
        )
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


@requires_postgres
def test_create_role_assignment_without_any_permission_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


# --- AUTH-2A: a RoleAssignment grants a role, never a scope -----------------


def _caller_and_member_target() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(caller_id with global `role.manage`, target_id, club_id) — the
    target has an active ClubMembership in `club_id`."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_id = caller.id, club.id
        target_id, _ = _setup_target_with_membership(session, club)
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    return caller_id, target_id, club_id


def _role_permission_scope_snapshot(role_id: uuid.UUID) -> set[tuple[uuid.UUID, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(RolePermission.permission_id, RolePermissionScope.scope_type)
            .join(RolePermissionScope, RolePermissionScope.role_permission_id == RolePermission.id)
            .where(RolePermission.role_id == role_id)
        ).all()
        return {(permission_id, scope_type) for permission_id, scope_type in rows}


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "member", "guardian", "admin"])
def test_create_role_assignment_club_scoped_succeeds_for_every_baseline_role(
    client: TestClient, role_code: str
) -> None:
    _, target_id, club_id = _caller_and_member_target()
    role_id = _baseline_role_id(role_code)

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id), "club_id": str(club_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    # Legacy column, always `all` — not a permission scope.
    assert response.json()["scope_type"] == "all"
    with session_scope() as session:
        rows = (
            session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == target_id,
                    UserRoleAssignment.role_id == role_id,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
@pytest.mark.parametrize(
    "caller_scope_type", ["own_groups", "self", "children", "none", "own_records", "all"]
)
def test_create_role_assignment_ignores_a_caller_supplied_scope(
    client: TestClient, caller_scope_type: str
) -> None:
    """AUTH-2A: `scope_type` is no longer an input. A legacy client that
    still sends one can neither choose the stored value nor change any
    permission scope of the role."""
    _, target_id, club_id = _caller_and_member_target()
    role_id = _baseline_role_id("instructor")
    scopes_before = _role_permission_scope_snapshot(role_id)

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
            "scope_type": caller_scope_type,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["scope_type"] == "all"
    assert _role_permission_scope_snapshot(role_id) == scopes_before
    with session_scope() as session:
        # `instructor` holds no `person.read` grant: no scope the caller
        # sent can make one appear.
        assert applicable_grants(session, target_id, "person.read") == []
        directory_grants = applicable_grants(session, target_id, "user.directory.read")
        assert [grant.scope_type for grant in directory_grants] == ["all"]


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "member", "guardian"])
def test_create_non_admin_role_assignment_without_club_id_is_rejected(
    client: TestClient, role_code: str
) -> None:
    """AUTH-2A PO decision: a Club is required for every RoleAssignment
    except the canonical global `admin`."""
    _, target_id, _ = _caller_and_member_target()
    role_id = _baseline_role_id(role_code)

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id), "scope_type": "all"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_role_assignment_scope"


@requires_postgres
def test_create_role_assignment_duplicate_ignores_the_legacy_scope_of_the_active_row(
    client: TestClient,
) -> None:
    """AUTH-2A: an active role is a duplicate whatever the legacy
    `scope_type` of the row that already holds it."""
    _, target_id, club_id = _caller_and_member_target()
    role_id = _baseline_role_id("guardian")
    with session_scope() as session:
        session.add(
            UserRoleAssignment(
                user_id=target_id, role_id=role_id, club_id=club_id, scope_type="children"
            )
        )
        session.commit()

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id), "club_id": str(club_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_role_assignment"


@requires_postgres
def test_create_role_assignment_rejects_non_null_scope_ref_id(client: TestClient) -> None:
    _, target_id, club_id = _caller_and_member_target()
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
            "scope_ref_id": str(uuid.uuid4()),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_role_assignment_scope"



# --- Cross-Club / ClubMembership integrity ---------------------------------


@requires_postgres
def test_create_role_assignment_club_scoped_without_active_membership_is_rejected(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_id = caller.id, club.id
        target_person = _make_person(first_name="Target")
        session.add(target_person)
        session.commit()
        target = _make_user(target_person)  # no ClubMembership at all
        session.add(target)
        session.commit()
        target_id = target.id
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "role_assignment_club_membership_missing"


@requires_postgres
def test_create_role_assignment_membership_in_another_club_is_rejected(
    client: TestClient,
) -> None:
    """The target's active ClubMembership must be in the *target* Club of
    the assignment being created — an active membership in some other
    Club does not satisfy ADR-0026 §3's cross-Club integrity check."""
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_a_id = caller.id, club_a.id
        # Target has an active membership, but only in Club B.
        target_id, _ = _setup_target_with_membership(session, club_b)
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_a_id),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "role_assignment_club_membership_missing"


@requires_postgres
def test_create_role_assignment_with_ended_membership_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_id = caller.id, club.id
        target_person = _make_person(first_name="Target")
        session.add(target_person)
        session.commit()
        target = _make_user(target_person)
        ended_membership = _make_club_membership(
            club, target_person, status="inactive", left_at=_utc(2024, 6, 1)
        )
        session.add_all([target, ended_membership])
        session.commit()
        target_id = target.id
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "role_assignment_club_membership_missing"


@requires_postgres
def test_ending_target_club_membership_leaves_the_role_assignment_row_untouched(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_id = caller.id, club.id
        target_id, membership_id = _setup_target_with_membership(session, club)
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("instructor")

    created = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["id"]

    with session_scope() as session:
        membership = session.get(ClubMembership, membership_id)
        assert membership is not None
        membership.status = "inactive"
        membership.left_at = _utc(2024, 6, 1)
        session.commit()

    response = client.get("/api/v1/role-assignments", params={"user_id": str(target_id)})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert assignment_id in ids
    row = next(item for item in response.json()["items"] if item["id"] == assignment_id)
    assert row["valid_to"] is None


@requires_postgres
def test_role_manage_remains_effective_after_callers_own_club_membership_ends(
    client: TestClient,
) -> None:
    """ADR-0027 regression test: create a club-scoped RoleAssignment
    (here, the caller's own club-scoped role.manage grant), end the
    target User's ClubMembership in that Club, and confirm the
    assignment remains valid/effective according to its own temporal
    interval — proven through the real authorization boundary (the
    caller can still use these endpoints in that Club), not just by
    inspecting the row. The shared Authorizer/applicable_grants
    engine is not modified to make this pass (ADR-0027 §2)."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        caller_id, membership_id = _setup_target_with_membership(session, club)
        club_id = club.id
        other_target_id, _ = _setup_target_with_membership(session, club)
    _grant_permission(caller_id, "role.manage", scope_type="all", club_id=club_id)
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    with session_scope() as session:
        membership = session.get(ClubMembership, membership_id)
        assert membership is not None
        membership.status = "inactive"
        membership.left_at = _utc(2024, 6, 1)
        session.commit()

    # The caller's own role.manage assignment is unaffected by their
    # ClubMembership ending — they can still create...
    created = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(other_target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assert created.status_code == 201, created.text

    # ...and revoke RoleAssignments in that Club.
    revoke = client.post(
        f"/api/v1/role-assignments/{created.json()['id']}/revoke", headers=_csrf_headers(client)
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["valid_to"] is not None


# --- Target/Role existence ----------------------------------------------


@requires_postgres
def test_create_role_assignment_invalid_user_id_is_422(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("member")

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(uuid.uuid4()), "role_id": str(role_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_user_id"


@requires_postgres
def test_create_role_assignment_invalid_role_id_is_422(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)

    response = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(uuid.uuid4())},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_role_id"


# --- Duplicate / temporal invariant -----------------------------------


@requires_postgres
def test_create_role_assignment_duplicate_overlapping_is_409(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("admin")
    headers = _csrf_headers(client)
    payload = {"user_id": str(target_id), "role_id": str(role_id)}

    first = client.post("/api/v1/role-assignments", json=payload, headers=headers)
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/role-assignments", json=payload, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_role_assignment"


# --- GET /role-assignments: pagination, filters, sorting -------------------


@requires_postgres
def test_list_role_assignments_filters_by_user_id(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_a_id, _ = _setup_target_with_membership(session, _make_club())
        target_b_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("admin")
    headers = _csrf_headers(client)
    client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_a_id), "role_id": str(role_id)},
        headers=headers,
    )
    client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_b_id), "role_id": str(role_id)},
        headers=headers,
    )

    response = client.get("/api/v1/role-assignments", params={"user_id": str(target_a_id)})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["user_id"] == str(target_a_id)


@requires_postgres
def test_list_role_assignments_filters_by_club_id_and_role_id(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id, club_a_id, club_b_id = caller.id, club_a.id, club_b.id
        target_a_id, _ = _setup_target_with_membership(session, club_a)
        target_b_id, _ = _setup_target_with_membership(session, club_b)
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    member_role_id = _baseline_role_id("member")
    instructor_role_id = _baseline_role_id("instructor")
    headers = _csrf_headers(client)
    club_a_resp = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_a_id),
            "role_id": str(member_role_id),
            "club_id": str(club_a_id),
        },
        headers=headers,
    )
    assert club_a_resp.status_code == 201, club_a_resp.text
    club_b_resp = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_b_id),
            "role_id": str(instructor_role_id),
            "club_id": str(club_b_id),
        },
        headers=headers,
    )
    assert club_b_resp.status_code == 201, club_b_resp.text

    by_club = client.get("/api/v1/role-assignments", params={"club_id": str(club_a_id)})
    assert by_club.status_code == 200, by_club.text
    club_ids = {item["club_id"] for item in by_club.json()["items"]}
    assert club_ids == {str(club_a_id)}

    by_role = client.get("/api/v1/role-assignments", params={"role_id": str(instructor_role_id)})
    assert by_role.status_code == 200, by_role.text
    role_ids = {item["role_id"] for item in by_role.json()["items"]}
    assert role_ids == {str(instructor_role_id)}


@requires_postgres
def test_list_role_assignments_has_ended_filters(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("admin")
    headers = _csrf_headers(client)

    created = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id)},
        headers=headers,
    )
    assignment_id = created.json()["id"]
    revoke = client.post(f"/api/v1/role-assignments/{assignment_id}/revoke", headers=headers)
    assert revoke.status_code == 200, revoke.text

    with session_scope() as session:
        other_target_id, _ = _setup_target_with_membership(session, _make_club())
    still_open = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(other_target_id), "role_id": str(role_id)},
        headers=headers,
    )
    assert still_open.status_code == 201, still_open.text
    open_id = still_open.json()["id"]

    ended = client.get("/api/v1/role-assignments", params={"has_ended": "true"})
    assert ended.status_code == 200, ended.text
    ended_ids = {item["id"] for item in ended.json()["items"]}
    assert assignment_id in ended_ids
    assert open_id not in ended_ids

    active = client.get("/api/v1/role-assignments", params={"has_ended": "false"})
    assert active.status_code == 200, active.text
    active_ids = {item["id"] for item in active.json()["items"]}
    assert open_id in active_ids
    assert assignment_id not in active_ids


@requires_postgres
def test_list_role_assignments_invalid_sort_is_422(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)

    response = client.get("/api/v1/role-assignments", params={"sort": "bogus"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sort"


@requires_postgres
def test_list_role_assignments_without_permission_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
    _authenticate_as(caller_id)

    response = client.get("/api/v1/role-assignments")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_list_role_assignments_cross_club_enumeration_is_blocked(client: TestClient) -> None:
    """A club-scoped role.manage caller must not see another Club's
    RoleAssignments through any filter combination (no cross-Club
    enumeration via filters, per Issue #74 §9)."""
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        global_caller_person = _make_person(first_name="GlobalCaller")
        session.add(global_caller_person)
        session.commit()
        global_caller = _make_user(global_caller_person)
        session.add(global_caller)
        session.commit()
        global_caller_id, club_a_id, club_b_id = global_caller.id, club_a.id, club_b.id
        target_b_id, _ = _setup_target_with_membership(session, club_b)

        scoped_caller_person = _make_person(first_name="ScopedCaller")
        session.add(scoped_caller_person)
        session.commit()
        scoped_caller = _make_user(scoped_caller_person)
        session.add(scoped_caller)
        session.commit()
        scoped_caller_id = scoped_caller.id
    _grant_permission(global_caller_id, "role.manage", scope_type="all")
    _grant_permission(scoped_caller_id, "role.manage", scope_type="all", club_id=club_a_id)
    _authenticate_as(global_caller_id)
    role_id = _baseline_role_id("member")

    created = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_b_id),
            "role_id": str(role_id),
            "club_id": str(club_b_id),
        },
        headers=_csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["id"]

    _authenticate_as(scoped_caller_id)
    response = client.get("/api/v1/role-assignments", params={"club_id": str(club_b_id)})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert assignment_id not in ids


# --- POST /role-assignments/{id}/revoke --------------------------------


@requires_postgres
def test_revoke_role_assignment_then_repeat_is_conflict(client: TestClient) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("admin")
    headers = _csrf_headers(client)

    created = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id)},
        headers=headers,
    )
    assignment_id = created.json()["id"]

    first = client.post(f"/api/v1/role-assignments/{assignment_id}/revoke", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["valid_to"] is not None

    second = client.post(f"/api/v1/role-assignments/{assignment_id}/revoke", headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_role_assignment_transition"

    audit_row = _latest_audit_row(
        action="role_assignment.revoked", resource_id=uuid.UUID(assignment_id)
    )
    assert audit_row is not None


@requires_postgres
def test_revoke_role_assignment_idor_hides_nonexistent_and_unauthorized(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        owner_person = _make_person(first_name="Owner")
        session.add(owner_person)
        session.commit()
        owner = _make_user(owner_person)
        session.add(owner)
        session.commit()
        owner_id, club_id = owner.id, club.id
        target_id, _ = _setup_target_with_membership(session, club)

        outsider_person = _make_person(first_name="Outsider")
        session.add(outsider_person)
        session.commit()
        outsider = _make_user(outsider_person)
        session.add(outsider)
        session.commit()
        outsider_id = outsider.id
    _grant_permission(owner_id, "role.manage", scope_type="all")
    _authenticate_as(owner_id)
    role_id = _baseline_role_id("member")
    created = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )
    assignment_id = created.json()["id"]

    _authenticate_as(outsider_id)  # no role.manage at all
    missing = client.post(
        f"/api/v1/role-assignments/{uuid.uuid4()}/revoke", headers=_csrf_headers(client)
    )
    unauthorized = client.post(
        f"/api/v1/role-assignments/{assignment_id}/revoke", headers=_csrf_headers(client)
    )
    assert missing.status_code == unauthorized.status_code == 404
    assert (
        missing.json()["error"]["code"]
        == unauthorized.json()["error"]["code"]
        == "role_assignment_not_found"
    )


@requires_postgres
def test_revoke_role_assignment_cross_club_caller_gets_same_404(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        owner_person = _make_person(first_name="Owner")
        session.add(owner_person)
        session.commit()
        owner = _make_user(owner_person)
        session.add(owner)
        session.commit()
        owner_id, club_b_id = owner.id, club_b.id
        target_id, _ = _setup_target_with_membership(session, club_b)

        scoped_caller_person = _make_person(first_name="ScopedCaller")
        session.add(scoped_caller_person)
        session.commit()
        scoped_caller = _make_user(scoped_caller_person)
        session.add(scoped_caller)
        session.commit()
        scoped_caller_id, club_a_id = scoped_caller.id, club_a.id
    _grant_permission(owner_id, "role.manage", scope_type="all")
    _grant_permission(scoped_caller_id, "role.manage", scope_type="all", club_id=club_a_id)
    _authenticate_as(owner_id)
    role_id = _baseline_role_id("member")
    created = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(target_id),
            "role_id": str(role_id),
            "club_id": str(club_b_id),
        },
        headers=_csrf_headers(client),
    )
    assignment_id = created.json()["id"]

    _authenticate_as(scoped_caller_id)
    response = client.post(
        f"/api/v1/role-assignments/{assignment_id}/revoke", headers=_csrf_headers(client)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "role_assignment_not_found"


# --- Audit trail sanity ------------------------------------------------


@requires_postgres
def test_full_role_assignment_lifecycle_produces_only_canonical_audit_actions(
    client: TestClient,
) -> None:
    with session_scope() as session:
        caller_person = _make_person(first_name="Caller")
        session.add(caller_person)
        session.commit()
        caller = _make_user(caller_person)
        session.add(caller)
        session.commit()
        caller_id = caller.id
        target_id, _ = _setup_target_with_membership(session, _make_club())
    _grant_permission(caller_id, "role.manage", scope_type="all")
    _authenticate_as(caller_id)
    role_id = _baseline_role_id("admin")
    headers = _csrf_headers(client)

    created = client.post(
        "/api/v1/role-assignments",
        json={"user_id": str(target_id), "role_id": str(role_id)},
        headers=headers,
    )
    assignment_id = created.json()["id"]
    client.post(f"/api/v1/role-assignments/{assignment_id}/revoke", headers=headers)

    with session_scope() as session:
        actions = (
            session.execute(
                select(AuditLog.action).where(AuditLog.resource_id == uuid.UUID(assignment_id))
            )
            .scalars()
            .all()
        )

    assert set(actions) == {"role_assignment.created", "role_assignment.revoked"}
