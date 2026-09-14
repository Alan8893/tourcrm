"""HTTP-level integration tests for /api/v1/groups,
/api/v1/group-memberships and /api/v1/group-instructor-assignments
(Issue #71, implementing the Issue #69 specification gate):
deterministic backend authorization (`all`/`own_groups`/`none`/no
permission), IDOR regression coverage, cross-Club rejection, archived-
Group behavior, lifecycle transitions, and audit recording.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_guardian_relationships_api.py's
pattern.

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
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


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


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Group {uuid.uuid4().hex[:8]}",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


def _make_group_instructor_assignment(
    group: Group, user: User, **overrides: object
) -> GroupInstructorAssignment:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "user_id": user.id,
        "role_in_group": "instructor",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GroupInstructorAssignment(**defaults)  # type: ignore[arg-type]


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


# --- GET /groups (list, scope) ---------------------------------------------


@requires_postgres
def test_list_groups_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(group_id) in ids


@requires_postgres
def test_list_groups_without_permission_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        session.add_all([requester, _make_group(club)])
        session.commit()
        requester_id = requester.id
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_list_groups_none_scope_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        session.add_all([requester, _make_group(club)])
        session.commit()
        requester_id = requester.id
    _grant_permission(requester_id, "group.read", scope_type="none")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@requires_postgres
def test_list_groups_own_groups_scope_sees_only_assigned_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        own_group = _make_group(club, name="Own")
        other_group = _make_group(club, name="Other")
        session.add_all([requester, own_group, other_group])
        session.commit()
        session.add(_make_group_instructor_assignment(own_group, requester))
        session.commit()
        requester_id, own_group_id, other_group_id = requester.id, own_group.id, other_group.id
    _grant_permission(requester_id, "group.read", scope_type="own_groups")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(own_group_id) in ids
    assert str(other_group_id) not in ids


@requires_postgres
def test_own_groups_scope_requires_active_assignment_not_just_instructor_role(
    client: TestClient,
) -> None:
    """ADR-0021 §4: `own_groups` must be based on an explicit active
    GroupInstructorAssignment — a role grant alone (with no such
    assignment row) must not leak visibility."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.read", scope_type="own_groups")
    _authenticate_as(requester_id)

    response = client.get(f"/api/v1/groups/{group_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "group_not_found"


# --- GET /groups — status filter whitelist (people-api.md §14.1) ----------


@requires_postgres
def test_list_groups_status_filter_active(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        active_group = _make_group(club, status="active")
        archived_group = _make_group(club, status="archived")
        session.add_all([requester, active_group, archived_group])
        session.commit()
        requester_id, active_id, archived_id = requester.id, active_group.id, archived_group.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups", params={"status": "active"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(active_id) in ids
    assert str(archived_id) not in ids


@requires_postgres
def test_list_groups_status_filter_archived(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        active_group = _make_group(club, status="active")
        archived_group = _make_group(club, status="archived")
        session.add_all([requester, active_group, archived_group])
        session.commit()
        requester_id, active_id, archived_id = requester.id, active_group.id, archived_group.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups", params={"status": "archived"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(archived_id) in ids
    assert str(active_id) not in ids


@requires_postgres
def test_list_groups_without_status_filter_returns_both(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        active_group = _make_group(club, status="active")
        archived_group = _make_group(club, status="archived")
        session.add_all([requester, active_group, archived_group])
        session.commit()
        requester_id, active_id, archived_id = requester.id, active_group.id, archived_group.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert {str(active_id), str(archived_id)} <= ids


@requires_postgres
@pytest.mark.parametrize("bad_value", ["pending", "foo", "ACTIVE", "Archived"])
def test_list_groups_status_filter_rejects_non_canonical_values(
    client: TestClient, bad_value: str
) -> None:
    """people-api.md §14.1: `active`/`archived` is a closed vocabulary — an
    unknown value must be rejected with 422, never silently turned into a
    filter that matches nothing (200 + empty list)."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        session.add(requester)
        session.commit()
        requester_id = requester.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/groups", params={"status": bad_value})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


# --- GET /groups/{id} — IDOR existence-hiding -------------------------------


@requires_postgres
def test_get_group_nonexistent_and_unauthorized_return_identical_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        real_group = _make_group(club)
        session.add_all([requester, real_group])
        session.commit()
        requester_id, real_group_id = requester.id, real_group.id
    _authenticate_as(requester_id)  # no permission at all

    missing = client.get(f"/api/v1/groups/{uuid.uuid4()}")
    unauthorized = client.get(f"/api/v1/groups/{real_group_id}")

    assert missing.status_code == unauthorized.status_code == 404
    assert missing.json() == unauthorized.json() or (
        missing.json()["error"]["code"] == unauthorized.json()["error"]["code"] == "group_not_found"
    )


# --- POST /groups ------------------------------------------------------


@requires_postgres
def test_create_group_always_starts_active(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        session.add(requester)
        session.commit()
        requester_id, club_id = requester.id, club.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        "/api/v1/groups",
        json={
            "club_id": str(club_id),
            "name": "New Group",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active"

    audit_row = _latest_audit_row(action="group.created", resource_id=uuid.UUID(body["id"]))
    assert audit_row is not None


@requires_postgres
def test_create_group_ignores_client_supplied_status(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        session.add(requester)
        session.commit()
        requester_id, club_id = requester.id, club.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        "/api/v1/groups",
        json={
            "club_id": str(club_id),
            "name": "New Group",
            "status": "archived",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "active"


@requires_postgres
def test_create_group_without_permission_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        session.add(requester)
        session.commit()
        requester_id, club_id = requester.id, club.id
    _authenticate_as(requester_id)

    response = client.post(
        "/api/v1/groups",
        json={"club_id": str(club_id), "name": "New Group", "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


# --- PATCH /groups/{id} -------------------------------------------------


@requires_postgres
def test_patch_group_cannot_change_status(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.patch(
        f"/api/v1/groups/{group_id}",
        json={"name": "Renamed", "status": "archived"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["status"] == "active"


# --- POST /groups/{id}/archive ------------------------------------------


@requires_postgres
def test_archive_group_then_repeat_is_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    first = client.post(f"/api/v1/groups/{group_id}/archive", headers=_csrf_headers(client))
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "archived"

    second = client.post(f"/api/v1/groups/{group_id}/archive", headers=_csrf_headers(client))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_group_status_transition"

    audit_row = _latest_audit_row(action="group.updated", resource_id=group_id)
    assert audit_row is not None


# --- GroupMembership: create/cross-Club/duplicate/archived ----------------


@requires_postgres
def test_create_group_member_succeeds_and_resolves_person_to_club_membership(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id, person_id = requester.id, group.id, person.id
        club_membership_id = club_membership.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["club_membership_id"] == str(club_membership_id)
    assert body["membership_status"] == "active"
    assert "person_id" not in body


@requires_postgres
def test_create_group_member_rejects_person_from_another_club(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        requester = _make_user(person)
        # Person's only membership is in Club B; the Group is in Club A.
        club_membership_in_b = _make_club_membership(club_b, person)
        group_in_a = _make_group(club_a)
        session.add_all([requester, club_membership_in_b, group_in_a])
        session.commit()
        requester_id, group_id, person_id = requester.id, group_in_a.id, person.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "group_membership_club_mismatch"


@requires_postgres
def test_create_group_member_rejects_duplicate_active_in_same_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id, person_id = requester.id, group.id, person.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    first = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 6, 1))},
        headers=_csrf_headers(client),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_group_membership"


@requires_postgres
def test_create_group_member_rejected_on_archived_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club, status="archived")
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id, person_id = requester.id, group.id, person.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "group_archived"


@requires_postgres
def test_person_can_belong_to_two_different_groups_simultaneously(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group_a = _make_group(club, name="A")
        group_b = _make_group(club, name="B")
        session.add_all([requester, club_membership, group_a, group_b])
        session.commit()
        requester_id, group_a_id, group_b_id, person_id = (
            requester.id,
            group_a.id,
            group_b.id,
            person.id,
        )
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    first = client.post(
        f"/api/v1/groups/{group_a_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    second = client.post(
        f"/api/v1/groups/{group_b_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    assert first.status_code == 201
    assert second.status_code == 201


# --- PATCH /group-memberships/{id} — immutable fields ----------------------


@requires_postgres
def test_patch_group_membership_rejects_immutable_fields(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id, person_id = requester.id, group.id, person.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    created = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    membership_id = created.json()["id"]

    response = client.patch(
        f"/api/v1/group-memberships/{membership_id}",
        json={"membership_status": "ended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "group_membership_immutable_field"

    response = client.patch(
        f"/api/v1/group-memberships/{membership_id}",
        json={"valid_from": _iso(_utc(2024, 1, 5))},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text


# --- POST /group-memberships/{id}/end --------------------------------------


@requires_postgres
def test_end_group_membership_then_repeat_is_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id, person_id = requester.id, group.id, person.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    created = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=_csrf_headers(client),
    )
    membership_id = created.json()["id"]

    first = client.post(
        f"/api/v1/group-memberships/{membership_id}/end", headers=_csrf_headers(client)
    )
    assert first.status_code == 200, first.text
    assert first.json()["membership_status"] == "ended"

    second = client.post(
        f"/api/v1/group-memberships/{membership_id}/end", headers=_csrf_headers(client)
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_group_membership_transition"


@requires_postgres
def test_group_membership_idor_hides_unauthorized_existence(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()
        membership = GroupMembership(
            group_id=group.id,
            club_membership_id=club_membership.id,
            valid_from=_utc(2024, 1, 1),
            membership_status="active",
        )
        session.add(membership)
        session.commit()
        membership_id = membership.id

        outsider_person = _make_person(first_name="Outsider")
        session.add(outsider_person)
        session.commit()
        outsider = _make_user(outsider_person)
        session.add(outsider)
        session.commit()
        outsider_id = outsider.id
    _authenticate_as(outsider_id)  # no permission at all

    missing = client.post(
        f"/api/v1/group-memberships/{uuid.uuid4()}/end", headers=_csrf_headers(client)
    )
    unauthorized = client.post(
        f"/api/v1/group-memberships/{membership_id}/end", headers=_csrf_headers(client)
    )
    assert missing.status_code == unauthorized.status_code == 404
    assert (
        missing.json()["error"]["code"]
        == unauthorized.json()["error"]["code"]
        == "group_membership_not_found"
    )


# --- GroupInstructorAssignment: create/cross-Club/primary/archived --------


@requires_postgres
def test_create_group_instructor_rejects_user_from_another_club(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        requester = _make_user(person)
        session.add(requester)
        instructor_person = _make_person(first_name="Instructor")
        session.add(instructor_person)
        session.commit()
        instructor_user = _make_user(instructor_person)
        # Instructor's only membership is in Club B; the Group is in Club A.
        instructor_membership_in_b = _make_club_membership(club_b, instructor_person)
        group_in_a = _make_group(club_a)
        session.add_all([requester, instructor_user, instructor_membership_in_b, group_in_a])
        session.commit()
        requester_id, group_id, instructor_user_id = (
            requester.id,
            group_in_a.id,
            instructor_user.id,
        )
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(instructor_user_id),
            "role_in_group": "instructor",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "instructor_club_membership_missing"


@requires_postgres
def test_create_group_instructor_rejects_overlapping_primary(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id, requester_user_id = requester.id, group.id, requester.id

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_user = _make_user(second_person)
        second_membership = _make_club_membership(
            club, second_person, membership_type="regular-b"
        )
        session.add_all([second_user, second_membership])
        session.commit()
        second_user_id = second_user.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    first = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(requester_user_id),
            "role_in_group": "leader",
            "is_primary": True,
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(second_user_id),
            "role_in_group": "leader",
            "is_primary": True,
            "valid_from": _iso(_utc(2024, 6, 1)),
        },
        headers=_csrf_headers(client),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_primary_instructor"


@requires_postgres
def test_sequential_historical_primaries_are_allowed_at_api_level(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id = requester.id, group.id

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_user = _make_user(second_person)
        second_membership = _make_club_membership(
            club, second_person, membership_type="regular-b"
        )
        session.add_all([second_user, second_membership])
        session.commit()
        second_user_id = second_user.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)
    headers = _csrf_headers(client)

    first = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(requester_id),
            "role_in_group": "leader",
            "is_primary": True,
            "valid_from": _iso(_utc(2024, 1, 1)),
            "valid_to": _iso(_utc(2024, 6, 1)),
        },
        headers=headers,
    )
    assert first.status_code == 201, first.text

    # Touching boundary: the second primary starts exactly when the first
    # one ends — this must be allowed, not treated as an overlap.
    second = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(second_user_id),
            "role_in_group": "leader",
            "is_primary": True,
            "valid_from": _iso(_utc(2024, 6, 1)),
        },
        headers=headers,
    )
    assert second.status_code == 201, second.text


@requires_postgres
def test_create_group_instructor_rejected_on_archived_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club, status="archived")
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    response = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(requester_id),
            "role_in_group": "instructor",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "group_archived"


@requires_postgres
def test_end_group_instructor_assignment_then_repeat_is_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    created = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(requester_id),
            "role_in_group": "instructor",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assignment_id = created.json()["id"]

    first = client.post(
        f"/api/v1/group-instructor-assignments/{assignment_id}/end", headers=_csrf_headers(client)
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/group-instructor-assignments/{assignment_id}/end", headers=_csrf_headers(client)
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_group_instructor_assignment_transition"


@requires_postgres
def test_ending_instructor_assignment_allowed_after_group_archived(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)

    created = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(requester_id),
            "role_in_group": "instructor",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=_csrf_headers(client),
    )
    assignment_id = created.json()["id"]

    archived = client.post(f"/api/v1/groups/{group_id}/archive", headers=_csrf_headers(client))
    assert archived.status_code == 200

    ended = client.post(
        f"/api/v1/group-instructor-assignments/{assignment_id}/end", headers=_csrf_headers(client)
    )
    assert ended.status_code == 200, ended.text


# --- GET .../members, GET .../instructors — gated by parent Group ----------


@requires_postgres
def test_list_group_members_requires_group_read_and_hides_via_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _authenticate_as(requester_id)  # no permission at all

    response = client.get(f"/api/v1/groups/{group_id}/members")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "group_not_found"


@requires_postgres
def test_list_group_members_membership_status_filter_active(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        active_membership = GroupMembership(
            group_id=group.id,
            club_membership_id=club_membership.id,
            valid_from=_utc(2024, 1, 1),
            membership_status="active",
        )
        session.add(active_membership)
        session.commit()

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_club_membership = _make_club_membership(
            club, second_person, membership_type="regular-b"
        )
        session.add(second_club_membership)
        session.commit()
        ended_membership = GroupMembership(
            group_id=group.id,
            club_membership_id=second_club_membership.id,
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
            membership_status="ended",
        )
        session.add(ended_membership)
        session.commit()
        requester_id, group_id = requester.id, group.id
        active_id, ended_id = active_membership.id, ended_membership.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(
        f"/api/v1/groups/{group_id}/members", params={"membership_status": "active"}
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(active_id) in ids
    assert str(ended_id) not in ids


@requires_postgres
def test_list_group_members_membership_status_filter_ended(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        active_membership = GroupMembership(
            group_id=group.id,
            club_membership_id=club_membership.id,
            valid_from=_utc(2024, 1, 1),
            membership_status="active",
        )
        session.add(active_membership)
        session.commit()

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_club_membership = _make_club_membership(
            club, second_person, membership_type="regular-b"
        )
        session.add(second_club_membership)
        session.commit()
        ended_membership = GroupMembership(
            group_id=group.id,
            club_membership_id=second_club_membership.id,
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
            membership_status="ended",
        )
        session.add(ended_membership)
        session.commit()
        requester_id, group_id = requester.id, group.id
        active_id, ended_id = active_membership.id, ended_membership.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(
        f"/api/v1/groups/{group_id}/members", params={"membership_status": "ended"}
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(ended_id) in ids
    assert str(active_id) not in ids


@requires_postgres
def test_list_group_members_without_membership_status_filter_returns_both(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([requester, club_membership, group])
        session.commit()
        active_membership = GroupMembership(
            group_id=group.id,
            club_membership_id=club_membership.id,
            valid_from=_utc(2024, 1, 1),
            membership_status="active",
        )
        session.add(active_membership)
        session.commit()
        requester_id, group_id = requester.id, group.id
        active_id = active_membership.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(f"/api/v1/groups/{group_id}/members")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(active_id) in ids


@requires_postgres
@pytest.mark.parametrize("bad_value", ["pending", "foo", "ACTIVE"])
def test_list_group_members_membership_status_filter_rejects_non_canonical_values(
    client: TestClient, bad_value: str
) -> None:
    """people-api.md §15.1: `active`/`ended` is a closed vocabulary — an
    unknown value must be rejected with 422, never silently turned into a
    filter that matches nothing."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(
        f"/api/v1/groups/{group_id}/members", params={"membership_status": bad_value}
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


@requires_postgres
def test_list_group_instructors_after_grant(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        session.add(_make_group_instructor_assignment(group, requester))
        session.commit()
        requester_id, group_id = requester.id, group.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(f"/api/v1/groups/{group_id}/instructors")
    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 1


# --- GET /groups/{id}/instructors — activity filter (people-api.md §16.1/§16.3) ---


@requires_postgres
def test_list_group_instructors_has_ended_false_returns_active_only(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        active_assignment = _make_group_instructor_assignment(
            group, requester, valid_from=_utc(2024, 1, 1)
        )
        session.add(active_assignment)
        session.commit()

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_user = _make_user(second_person)
        session.add(second_user)
        session.commit()
        ended_assignment = _make_group_instructor_assignment(
            group, second_user, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        session.add(ended_assignment)
        session.commit()
        requester_id, group_id = requester.id, group.id
        active_id, ended_id = active_assignment.id, ended_assignment.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(
        f"/api/v1/groups/{group_id}/instructors", params={"has_ended": "false"}
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(active_id) in ids
    assert str(ended_id) not in ids


@requires_postgres
def test_list_group_instructors_has_ended_true_returns_ended_only(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        active_assignment = _make_group_instructor_assignment(
            group, requester, valid_from=_utc(2024, 1, 1)
        )
        session.add(active_assignment)
        session.commit()

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_user = _make_user(second_person)
        session.add(second_user)
        session.commit()
        ended_assignment = _make_group_instructor_assignment(
            group, second_user, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        session.add(ended_assignment)
        session.commit()
        requester_id, group_id = requester.id, group.id
        active_id, ended_id = active_assignment.id, ended_assignment.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(
        f"/api/v1/groups/{group_id}/instructors", params={"has_ended": "true"}
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(ended_id) in ids
    assert str(active_id) not in ids


@requires_postgres
def test_list_group_instructors_without_has_ended_filter_returns_both(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        group = _make_group(club)
        session.add_all([requester, group])
        session.commit()
        active_assignment = _make_group_instructor_assignment(
            group, requester, valid_from=_utc(2024, 1, 1)
        )
        session.add(active_assignment)
        session.commit()

        second_person = _make_person(first_name="Second")
        session.add(second_person)
        session.commit()
        second_user = _make_user(second_person)
        session.add(second_user)
        session.commit()
        ended_assignment = _make_group_instructor_assignment(
            group, second_user, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        session.add(ended_assignment)
        session.commit()
        requester_id, group_id = requester.id, group.id
        active_id, ended_id = active_assignment.id, ended_assignment.id
    _grant_permission(requester_id, "group.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get(f"/api/v1/groups/{group_id}/instructors")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert {str(active_id), str(ended_id)} <= ids
    assert response.json()["pagination"]["total"] == 2


# --- Audit trail sanity ------------------------------------------------


@requires_postgres
def test_full_group_lifecycle_produces_only_canonical_audit_actions(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        requester = _make_user(person)
        club_membership = _make_club_membership(club, person)
        session.add_all([requester, club_membership])
        session.commit()
        requester_id, club_id, person_id = requester.id, club.id, person.id
    _grant_permission(requester_id, "group.manage", scope_type="all")
    _authenticate_as(requester_id)
    headers = _csrf_headers(client)

    created = client.post(
        "/api/v1/groups",
        json={"club_id": str(club_id), "name": "Audit Group", "valid_from": _iso(_utc(2024, 1, 1))},
        headers=headers,
    )
    group_id = created.json()["id"]
    client.patch(f"/api/v1/groups/{group_id}", json={"name": "Renamed"}, headers=headers)

    member_resp = client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"person_id": str(person_id), "valid_from": _iso(_utc(2024, 1, 1))},
        headers=headers,
    )
    membership_id = member_resp.json()["id"]
    client.post(f"/api/v1/group-memberships/{membership_id}/end", headers=headers)

    instructor_resp = client.post(
        f"/api/v1/groups/{group_id}/instructors",
        json={
            "user_id": str(requester_id),
            "role_in_group": "instructor",
            "valid_from": _iso(_utc(2024, 1, 1)),
        },
        headers=headers,
    )
    assignment_id = instructor_resp.json()["id"]
    client.post(f"/api/v1/group-instructor-assignments/{assignment_id}/end", headers=headers)

    client.post(f"/api/v1/groups/{group_id}/archive", headers=headers)

    with session_scope() as session:
        actions = session.execute(
            select(AuditLog.action).where(
                AuditLog.resource_id.in_(
                    [uuid.UUID(group_id), uuid.UUID(membership_id), uuid.UUID(assignment_id)]
                )
            )
        ).scalars().all()

    expected = {
        "group.created",
        "group.updated",
        "group_membership.created",
        "group_membership.ended",
        "group_instructor_assignment.created",
        "group_instructor_assignment.ended",
    }
    assert set(actions) == expected
    assert "group.archived" not in actions
