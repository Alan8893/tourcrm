"""HTTP-level integration tests for Person-scoped role management
(TH-0112 / ADR-0039):

    GET    /api/v1/persons/{person_id}/role-assignments
    POST   /api/v1/persons/{person_id}/role-assignments
    DELETE /api/v1/persons/{person_id}/role-assignments/{role_code}

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_users_api.py's/
test_events_api.py's fixture/factory conventions (local, duplicated
helpers rather than importing across test files, per this codebase's own
established convention).

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.groups import GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ---------------------------------------------------


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
    """Ad hoc grant via a throwaway role, isolated from the real
    admin/instructor RolePermission wiring — mirrors
    test_users_api.py's identical helper."""
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
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _setup_admin_and_target(
    *, target_has_membership: bool = True
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, admin_user_id, target_person_id). The admin
    holds `role.manage` (scope_type="all") for the one Club; the target
    Person has an active ClubMembership in that Club (unless
    `target_has_membership=False`) but is given no User by default."""
    with session_scope() as session:
        club = _make_club()
        admin_person = _make_person()
        admin_user = _make_user(admin_person)
        target_person = _make_person(last_name="Petrova", first_name="Olga")
        session.add_all([club, admin_person, admin_user, target_person])
        session.flush()
        if target_has_membership:
            session.add(_make_club_membership(club, target_person))
        session.commit()
        club_id, admin_id, target_id = club.id, admin_user.id, target_person.id
    _grant_permission(admin_id, "role.manage", scope_type="all", club_id=club_id)
    return club_id, admin_id, target_id


def _add_user_to_person(person_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        person = session.get(Person, person_id)
        assert person is not None
        user = _make_user(person)
        session.add(user)
        session.commit()
        return user.id


# --- 1. list roles -----------------------------------------------------------


@requires_postgres
def test_admin_with_role_manage_can_list_roles_for_person(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.get(f"/api/v1/persons/{target_person_id}/role-assignments")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


# --- 2-5. add each canonical role --------------------------------------------


@pytest.mark.parametrize("role_code", ["admin", "instructor", "member", "guardian"])
@requires_postgres
def test_admin_can_add_each_canonical_role(client: TestClient, role_code: str) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": role_code},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role_code"] == role_code
    assert body["person_id"] == str(target_person_id)
    assert body["club_id"] == str(club_id)

    listed = client.get(f"/api/v1/persons/{target_person_id}/role-assignments")
    assert [item["role_code"] for item in listed.json()["items"]] == [role_code]


# --- 6. multiple roles coexist ------------------------------------------------


@requires_postgres
def test_multiple_roles_can_coexist(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    for role_code in ("instructor", "guardian"):
        response = client.post(
            f"/api/v1/persons/{target_person_id}/role-assignments",
            json={"role_code": role_code},
            headers=_csrf_headers(client),
        )
        assert response.status_code == 201, response.text

    listed = client.get(f"/api/v1/persons/{target_person_id}/role-assignments")
    role_codes = {item["role_code"] for item in listed.json()["items"]}
    assert role_codes == {"instructor", "guardian"}


# --- 7. removing one role does not remove others -----------------------------


@requires_postgres
def test_removing_one_role_does_not_remove_other_roles(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    for role_code in ("instructor", "guardian", "member"):
        client.post(
            f"/api/v1/persons/{target_person_id}/role-assignments",
            json={"role_code": role_code},
            headers=_csrf_headers(client),
        )

    response = client.delete(
        f"/api/v1/persons/{target_person_id}/role-assignments/guardian",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 204, response.text

    listed = client.get(f"/api/v1/persons/{target_person_id}/role-assignments")
    role_codes = {item["role_code"] for item in listed.json()["items"]}
    assert role_codes == {"instructor", "member"}


# --- 8-9. unauthorized user cannot add/remove --------------------------------


@requires_postgres
def test_unauthorized_user_cannot_add_role(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        bystander_person = _make_person()
        bystander_user = _make_user(bystander_person)
        target_person = _make_person(last_name="Petrova", first_name="Olga")
        session.add_all([club, bystander_person, bystander_user, target_person])
        session.flush()
        session.add(_make_club_membership(club, target_person))
        session.commit()
        bystander_id, target_person_id = bystander_user.id, target_person.id
    _add_user_to_person(target_person_id)
    _authenticate_as(bystander_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "member"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_unauthorized_user_cannot_remove_role(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)
    client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "member"},
        headers=_csrf_headers(client),
    )

    with session_scope() as session:
        bystander_person = _make_person()
        bystander_user = _make_user(bystander_person)
        session.add_all([bystander_person, bystander_user])
        session.commit()
        bystander_id = bystander_user.id
    _authenticate_as(bystander_id)

    response = client.delete(
        f"/api/v1/persons/{target_person_id}/role-assignments/member",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


# --- 10-11. no mutation of Person / ClubMembership ---------------------------


@requires_postgres
def test_role_assignment_does_not_modify_person(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    with session_scope() as session:
        before = session.get(Person, target_person_id)
        assert before is not None
        snapshot = (before.first_name, before.last_name, before.updated_at)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        after = session.get(Person, target_person_id)
        assert after is not None
        assert (after.first_name, after.last_name, after.updated_at) == snapshot


@requires_postgres
def test_role_assignment_does_not_modify_club_membership(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    with session_scope() as session:
        membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == target_person_id)
        ).scalar_one()
        snapshot = (membership.membership_type, membership.status, membership.updated_at)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == target_person_id)
        ).scalar_one()
        assert (membership.membership_type, membership.status, membership.updated_at) == snapshot


# --- 12-14. no automatic domain side effects ---------------------------------


@requires_postgres
def test_instructor_assignment_creates_no_group_instructor_assignment(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    target_user_id = _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        count = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.user_id == target_user_id
            )
        ).all()
        assert count == []
        membership_count = session.execute(
            select(GroupMembership).join(
                ClubMembership, ClubMembership.id == GroupMembership.club_membership_id
            ).where(ClubMembership.person_id == target_person_id)
        ).all()
        assert membership_count == []


@requires_postgres
def test_guardian_assignment_creates_no_guardian_relationship(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "guardian"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        rows = session.execute(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == target_person_id
            )
        ).all()
        assert rows == []


@requires_postgres
def test_member_assignment_has_no_extra_side_effects(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    with session_scope() as session:
        membership_count_before = len(
            session.execute(
                select(ClubMembership).where(ClubMembership.person_id == target_person_id)
            ).all()
        )
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "member"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        membership_count_after = len(
            session.execute(
                select(ClubMembership).where(ClubMembership.person_id == target_person_id)
            ).all()
        )
        assert membership_count_after == membership_count_before


# --- 15. duplicate active assignment -----------------------------------------


@requires_postgres
def test_duplicate_active_assignment_returns_canonical_conflict(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)
    first = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "instructor"},
        headers=_csrf_headers(client),
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "instructor"},
        headers=_csrf_headers(client),
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "duplicate_role_assignment"


# --- 16. audit rows -----------------------------------------------------------


@requires_postgres
def test_audit_rows_are_created_for_add_and_remove(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    add_response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "guardian"},
        headers=_csrf_headers(client),
    )
    assert add_response.status_code == 201, add_response.text
    assignment_id = add_response.json()["id"]

    remove_response = client.delete(
        f"/api/v1/persons/{target_person_id}/role-assignments/guardian",
        headers=_csrf_headers(client),
    )
    assert remove_response.status_code == 204, remove_response.text

    with session_scope() as session:
        rows = (
            session.execute(
                select(AuditLog)
                .where(
                    AuditLog.resource_type == "role_assignment",
                    AuditLog.resource_id == uuid.UUID(assignment_id),
                )
                .order_by(AuditLog.occurred_at.asc())
            )
            .scalars()
            .all()
        )
        actions = [row.action for row in rows]
        assert actions == ["role_assignment.created", "role_assignment.revoked"]
        assert all(row.actor_user_id == admin_id for row in rows)


# --- 17. invalid role code -----------------------------------------------------


@requires_postgres
def test_invalid_role_code_is_rejected(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "superuser"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_role_code"


@requires_postgres
def test_invalid_role_code_is_rejected_on_delete(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.delete(
        f"/api/v1/persons/{target_person_id}/role-assignments/superuser",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text


# --- 18. canonical role/permission model remains intact ----------------------


@requires_postgres
def test_canonical_role_permission_model_remains_intact(client: TestClient) -> None:
    """This endpoint must never write to `roles`/`permissions`/
    `role_permissions` for any of the four baseline roles — only to
    `user_role_assignments` (+ audit). Snapshotted after
    `_setup_admin_and_target`'s own throwaway `role.manage` grant, so
    that unrelated test scaffolding never appears as a false positive.
    """
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)

    with session_scope() as session:
        baseline_role_ids = {
            code: session.execute(select(Role.id).where(Role.code == code)).scalar_one()
            for code in ("admin", "instructor", "member", "guardian")
        }
        grants_before = {
            code: set(
                session.execute(
                    select(RolePermission.permission_id).where(RolePermission.role_id == role_id)
                )
                .scalars()
                .all()
            )
            for code, role_id in baseline_role_ids.items()
        }
        permission_codes_before = set(session.execute(select(Permission.code)).scalars().all())

    _authenticate_as(admin_id)
    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        grants_after = {
            code: set(
                session.execute(
                    select(RolePermission.permission_id).where(RolePermission.role_id == role_id)
                )
                .scalars()
                .all()
            )
            for code, role_id in baseline_role_ids.items()
        }
        permission_codes_after = set(session.execute(select(Permission.code)).scalars().all())

    assert grants_after == grants_before
    assert permission_codes_after == permission_codes_before


# --- Person has no User account ----------------------------------------------


@requires_postgres
def test_adding_role_for_person_with_no_user_account_returns_precondition_error(
    client: TestClient,
) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/role-assignments",
        json={"role_code": "member"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "person_has_no_user_account"


@requires_postgres
def test_listing_roles_for_person_with_no_user_account_returns_empty(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _authenticate_as(admin_id)

    response = client.get(f"/api/v1/persons/{target_person_id}/role-assignments")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


# --- DELETE idempotent-missing convention ------------------------------------


@requires_postgres
def test_removing_a_role_that_is_not_assigned_returns_not_found(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _add_user_to_person(target_person_id)
    _authenticate_as(admin_id)

    response = client.delete(
        f"/api/v1/persons/{target_person_id}/role-assignments/guardian",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "role_assignment_not_found"


@requires_postgres
def test_removing_role_for_person_with_no_user_account_returns_not_found(
    client: TestClient,
) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target()
    _authenticate_as(admin_id)

    response = client.delete(
        f"/api/v1/persons/{target_person_id}/role-assignments/member",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


@requires_postgres
def test_person_not_found_returns_404(client: TestClient) -> None:
    club_id, admin_id, _target_person_id = _setup_admin_and_target()
    _authenticate_as(admin_id)

    response = client.get(f"/api/v1/persons/{uuid.uuid4()}/role-assignments")
    assert response.status_code == 404, response.text
