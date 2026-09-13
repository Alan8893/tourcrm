"""HTTP-level integration tests for /api/v1/persons and
/api/v1/memberships (Issue #62): deterministic backend authorization,
canonical scope resolution (`all`/`own_groups`/`self`/`none`), IDOR
regression coverage, sensitive-field withholding, audit recording, and
transaction/fail-closed behavior.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_events_api.py's pattern.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
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
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.audit.service import record_audit_event
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
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


# --- fixtures / factories ----------------------------------------------------


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


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Group {uuid.uuid4().hex[:8]}",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


def _make_group_membership(
    group: Group, club_membership: ClubMembership, **overrides: object
) -> GroupMembership:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": club_membership.id,
        "membership_status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GroupMembership(**defaults)  # type: ignore[arg-type]


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
        return session.execute(
            select(AuditLog)
            .where(AuditLog.action == action, AuditLog.resource_id == resource_id)
            .order_by(AuditLog.occurred_at.desc())
        ).scalars().first()


# --- Person: create -----------------------------------------------------


@requires_postgres
def test_create_person_with_global_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={
            "first_name": "Anna",
            "last_name": "Petrova",
            "birth_date": "2010-05-01",
            "phone": "+70000000000",
            "email": "anna@example.com",
            "address": "1 Main St",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["first_name"] == "Anna"
    assert body["last_name"] == "Petrova"
    assert body["birth_date"] == "2010-05-01"
    # Sensitive fields never returned, even though they were just submitted.
    assert "phone" not in body
    assert "email" not in body
    assert "address" not in body
    assert "status" not in body

    audit_row = _latest_audit_row(action="person.created", resource_id=uuid.UUID(body["id"]))
    assert audit_row is not None
    assert audit_row.actor_type == "user"
    assert audit_row.actor_user_id == user_id
    assert audit_row.resource_type == "person"
    assert audit_row.outcome == "success"


@requires_postgres
def test_create_person_without_permission_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_create_person_with_club_scoped_all_assignment_is_forbidden(client: TestClient) -> None:
    """Person is Club-neutral (ADR-0017): a club-scoped `all` assignment
    never authorizes Person creation — only a global assignment does.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_create_person_rejects_missing_required_field(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons", json={"last_name": "Petrova"}, headers=_csrf_headers(client)
    )
    assert response.status_code == 422, response.text


# --- Person: read -------------------------------------------------------


@requires_postgres
def test_get_person_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        target = _make_person(first_name="Target")
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([target, requester_person, requester_user])
        session.commit()
        target_id, user_id = target.id, requester_user.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{target_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(target_id)
    assert "phone" not in body
    assert "email" not in body
    assert "address" not in body


@requires_postgres
def test_get_person_none_scope_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        target = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([target, requester_person, requester_user])
        session.commit()
        target_id, user_id = target.id, requester_user.id
    _grant_permission(user_id, "person.read", scope_type="none")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{target_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_get_person_nonexistent_id_returns_identical_404(client: TestClient) -> None:
    # Two separate users: permission grants are additive, so reusing one
    # user for both the "all" and "none" cases would leave the earlier
    # "all" grant still in effect for the second request.
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        denied_person = _make_person()
        denied_user = _make_user(denied_person)
        other_person = _make_person()
        session.add_all(
            [requester_person, requester_user, denied_person, denied_user, other_person]
        )
        session.commit()
        user_id, denied_user_id, other_id = requester_user.id, denied_user.id, other_person.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _grant_permission(denied_user_id, "person.read", scope_type="none")

    _authenticate_as(user_id)
    missing_response = client.get(f"/api/v1/persons/{uuid.uuid4()}")

    _authenticate_as(denied_user_id)
    denied_response = client.get(f"/api/v1/persons/{other_id}")

    assert missing_response.status_code == denied_response.status_code == 404
    assert missing_response.json()["error"]["code"] == denied_response.json()["error"]["code"]


@requires_postgres
def test_self_scope_sees_own_person_but_not_others(client: TestClient) -> None:
    with session_scope() as session:
        own_person = _make_person()
        own_user = _make_user(own_person)
        other_person = _make_person()
        session.add_all([own_person, own_user, other_person])
        session.commit()
        own_person_id, user_id, other_person_id = own_person.id, own_user.id, other_person.id
    _grant_permission(user_id, "person.read", scope_type="self")
    _authenticate_as(user_id)

    own_response = client.get(f"/api/v1/persons/{own_person_id}")
    assert own_response.status_code == 200, own_response.text

    other_response = client.get(f"/api/v1/persons/{other_person_id}")
    assert other_response.status_code == 404, other_response.text


@requires_postgres
def test_own_groups_scope_sees_person_in_responsible_group_but_not_unrelated_person(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        unrelated_person = _make_person()
        session.add_all(
            [
                club,
                instructor_person,
                instructor_user,
                member_person,
                unrelated_person,
            ]
        )
        session.commit()
        group = _make_group(club)
        member_club_membership = _make_club_membership(club, member_person)
        session.add_all([group, member_club_membership])
        session.commit()
        session.add(_make_group_membership(group, member_club_membership))
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id = instructor_user.id
        member_person_id = member_person.id
        unrelated_person_id = unrelated_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    member_response = client.get(f"/api/v1/persons/{member_person_id}")
    assert member_response.status_code == 200, member_response.text

    unrelated_response = client.get(f"/api/v1/persons/{unrelated_person_id}")
    assert unrelated_response.status_code == 404, unrelated_response.text


@requires_postgres
def test_list_persons_all_scope_returns_all_persons(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        p1 = _make_person()
        p2 = _make_person()
        session.add_all([requester_person, requester_user, p1, p2])
        session.commit()
        user_id = requester_user.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/persons")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] >= 3  # requester + p1 + p2
    for item in body["items"]:
        assert "phone" not in item


@requires_postgres
def test_list_persons_none_scope_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([requester_person, requester_user])
        session.commit()
        user_id = requester_user.id
    _grant_permission(user_id, "person.read", scope_type="none")
    _authenticate_as(user_id)

    response = client.get("/api/v1/persons")
    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 0


# --- Person: update -------------------------------------------------------


@requires_postgres
def test_update_person_with_self_scope_succeeds_and_audits(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person(first_name="Old")
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"first_name": "New", "phone": "+79999999999"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["first_name"] == "New"

    audit_row = _latest_audit_row(action="person.updated", resource_id=person_id)
    assert audit_row is not None
    assert audit_row.details is not None
    changes = audit_row.details["changes"]
    assert changes["first_name"] == {"from": "Old", "to": "New"}
    # Sensitive field: recorded as changed, but the value itself is never stored.
    assert changes["phone"] == {"changed": True}
    for value in changes.values():
        assert "+79999999999" not in str(value)


@requires_postgres
def test_update_person_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([person, requester_person, requester_user])
        session.commit()
        person_id, user_id = person.id, requester_user.id
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"first_name": "Hacked"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


@requires_postgres
def test_update_person_idor_member_cannot_update_another_person(client: TestClient) -> None:
    with session_scope() as session:
        own_person = _make_person()
        own_user = _make_user(own_person)
        other_person = _make_person(first_name="Victim")
        session.add_all([own_person, own_user, other_person])
        session.commit()
        user_id, other_id = own_user.id, other_person.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{other_id}",
        json={"first_name": "Pwned"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    with session_scope() as session:
        victim = session.get(Person, other_id)
        assert victim.first_name == "Victim"


# --- Membership: create -----------------------------------------------------


@requires_postgres
def test_create_membership_with_all_scope_succeeds_and_audits(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        club_id, person_id, user_id = club.id, person.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "pending",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["left_at"] is None

    audit_row = _latest_audit_row(action="membership.created", resource_id=uuid.UUID(body["id"]))
    assert audit_row is not None
    assert audit_row.club_id == club_id
    assert audit_row.outcome == "success"


@requires_postgres
def test_create_membership_cross_club_assignment_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club_a, club_b, person, requester_person, requester_user])
        session.commit()
        club_a_id, club_b_id, person_id, user_id = (
            club_a.id,
            club_b.id,
            person.id,
            requester_user.id,
        )
    _grant_permission(user_id, "membership.manage", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_b_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_create_membership_nonexistent_person_returns_422(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, requester_person, requester_user])
        session.commit()
        club_id, user_id = club.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(uuid.uuid4()),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_person_id"


@requires_postgres
def test_create_membership_overlapping_active_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        existing = _make_club_membership(club, person, membership_type="member", status="active")
        session.add(existing)
        session.commit()
        club_id, person_id, user_id = club.id, person.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


# --- Membership: read ---------------------------------------------------


@requires_postgres
def test_get_membership_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person)
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(membership_id)


@requires_postgres
def test_list_memberships_cross_club_assignment_does_not_leak_other_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person_a = _make_person()
        person_b = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all(
            [
                club_a,
                club_b,
                person_a,
                person_b,
                requester_person,
                requester_user,
            ]
        )
        session.commit()
        membership_a = _make_club_membership(club_a, person_a)
        membership_b = _make_club_membership(club_b, person_b)
        session.add_all([membership_a, membership_b])
        session.commit()
        club_a_id, user_id = club_a.id, requester_user.id
        membership_a_id, membership_b_id = membership_a.id, membership_b.id
    _grant_permission(user_id, "membership.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(membership_a_id) in ids
    assert str(membership_b_id) not in ids


@requires_postgres
def test_get_membership_of_other_club_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all(
            [club_a, club_b, person, requester_person, requester_user]
        )
        session.commit()
        membership = _make_club_membership(club_b, person)
        session.add(membership)
        session.commit()
        club_a_id, membership_id, user_id = club_a.id, membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_person_memberships_endpoint_returns_person_history(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person)
        session.add(membership)
        session.commit()
        person_id, membership_id, user_id = person.id, membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{person_id}/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(membership_id) in ids


@requires_postgres
def test_person_memberships_endpoint_nonexistent_person_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([requester_person, requester_user])
        session.commit()
        user_id = requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{uuid.uuid4()}/memberships")
    assert response.status_code == 404, response.text


# --- Membership: update (membership_type) --------------------------------


@requires_postgres
def test_update_membership_type_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, membership_type="member")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/memberships/{membership_id}",
        json={"membership_type": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["membership_type"] == "instructor"


@requires_postgres
def test_update_membership_type_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, membership_type="member")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/memberships/{membership_id}",
        json={"membership_type": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


# --- Membership: status transitions --------------------------------------


@requires_postgres
def test_transition_active_to_suspended_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"
    assert response.json()["left_at"] is None

    audit_row = _latest_audit_row(action="membership.status_changed", resource_id=membership_id)
    assert audit_row is not None
    assert audit_row.details["changes"]["status"] == {"from": "active", "to": "suspended"}


@requires_postgres
def test_transition_active_to_inactive_sets_left_at_and_emits_ended(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "inactive", "reason": "left the club"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "inactive"
    assert body["left_at"] is not None

    audit_row = _latest_audit_row(action="membership.ended", resource_id=membership_id)
    assert audit_row is not None
    assert audit_row.details["reason"] == "left the club"


@requires_postgres
def test_transition_disallowed_edge_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="pending")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


@requires_postgres
def test_transition_from_archived_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="archived")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "active"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text


@requires_postgres
def test_transition_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


@requires_postgres
def test_membership_history_endpoint_returns_current_state(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}/history")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(membership_id)


# --- Audit: fail-closed transaction behavior ------------------------------


@requires_postgres
def test_person_create_rolls_back_when_audit_insert_fails() -> None:
    """Simulates ADR-0024 §5's fail-closed contract directly against the
    service layer: if the audit insert fails, the Person row must not
    survive either — mirroring tests/integration/test_audit_log.py's
    proof, applied to app.people.service.
    """
    from app.people.service import create_person

    with session_scope() as session:
        # actor_user_id references a nonexistent User -> the AuditLog
        # actor_user_id FK violates on flush inside create_person's own
        # try/except, forcing a rollback of the Person insert too.
        bogus_actor_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            create_person(
                session,
                first_name="Ghost",
                last_name="Person",
                middle_name=None,
                birth_date=None,
                phone=None,
                email=None,
                address=None,
                actor_user_id=bogus_actor_id,
            )

    with session_scope() as verify_session:
        remaining = verify_session.execute(
            select(Person).where(Person.first_name == "Ghost")
        ).scalar_one_or_none()
        assert remaining is None, "Person must not survive a rolled-back audit insert"


@requires_postgres
def test_record_audit_event_used_directly_still_respects_secret_prohibition() -> None:
    """Sanity check that app.people.service reuses the shared
    app.audit.service boundary (ADR-0024) rather than a parallel
    mechanism: the same secret-prohibition rule applies here too.
    """
    from app.audit.security import AuditDetailsError

    with session_scope() as session:
        with pytest.raises(AuditDetailsError):
            record_audit_event(
                session,
                action="person.created",
                actor_type="system",
                outcome="success",
                details={"password": "hunter2"},
            )
