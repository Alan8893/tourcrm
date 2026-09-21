"""HTTP-level integration tests for `POST /api/v1/persons/wizard`
(TH-0116, GitHub Issue #150): the atomic Person-creation wizard —
Person + ClubMembership + automatic account provisioning + initial
RoleAssignment + role-specific contextual setup (Instructor groups /
Member groups / Guardian children) as one transaction.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_people_api.py's pattern.
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
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


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Club {uuid.uuid4().hex[:8]}", "status": "active"}
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": "Wizardova",
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


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Group {uuid.uuid4().hex[:8]}",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


def _grant_all_wizard_permissions(user_id: uuid.UUID, club_id: uuid.UUID) -> None:
    """A real admin holds `person.create`/`role.manage`/`account.manage`/
    `group.manage`/`guardian_relationship.manage` simultaneously (all via
    the seeded `admin` role in production) — granted here individually,
    matching test_people_api.py's own `_grant_permission` shape, so this
    file does not depend on that other file's private helper."""
    with session_scope() as session:
        role = Role(code=f"wizard-tester-{uuid.uuid4().hex[:8]}", name="Wizard tester")
        session.add(role)
        session.commit()
        for code in (
            "person.create",
            "role.manage",
            "account.manage",
            "group.manage",
            "group.read",
            "guardian_relationship.manage",
        ):
            permission = session.execute(
                select(Permission).where(Permission.code == code)
            ).scalar_one_or_none()
            if permission is None:
                permission = Permission(code=code)
                session.add(permission)
                session.commit()
            session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all", club_id=club_id)
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _setup_admin(club_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        admin_person = _make_person(last_name="Adminova")
        admin_user = _make_user(admin_person)
        session.add_all([admin_person, admin_user])
        session.commit()
        admin_user_id = admin_user.id
    _grant_all_wizard_permissions(admin_user_id, club_id)
    return admin_user_id


def _base_payload(**overrides: object) -> dict:
    payload = {
        "first_name": "Anna",
        "last_name": "Petrova",
        "role_code": "admin",
        "group_ids": [],
        "child_person_ids": [],
    }
    payload.update(overrides)
    return payload


# --- Basic Person creation / account provisioning ---------------------------


@requires_postgres
def test_wizard_creates_person_with_admin_role_and_no_contextual_setup(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(email="anna@example.com"),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["person"]["first_name"] == "Anna"
    assert body["person"]["role_codes"] == ["admin"]
    assert body["temporary_credential"] is not None

    person_id = uuid.UUID(body["person"]["id"])
    with session_scope() as session:
        membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == person_id)
        ).scalar_one()
        assert membership.club_id == club_id
        user = session.execute(select(User).where(User.person_id == person_id)).scalar_one()
        assert user.status == "active"
        assert user.login_identifier == "anna@example.com"


@requires_postgres
def test_wizard_without_email_creates_pending_stub_account(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["temporary_credential"] is None

    person_id = uuid.UUID(body["person"]["id"])
    with session_scope() as session:
        user = session.execute(select(User).where(User.person_id == person_id)).scalar_one()
        assert user.status == "pending"
        assert user.login_identifier is None
        assert user.password_hash is None

        from app.db.authentication import PasswordResetChallenge

        challenge_count = len(
            session.execute(
                select(PasswordResetChallenge.id).where(PasswordResetChallenge.user_id == user.id)
            ).all()
        )
        assert challenge_count == 0


@requires_postgres
def test_wizard_rolls_back_everything_when_a_contextual_step_fails(client: TestClient) -> None:
    """Section 5: a nonexistent group_id must not leave a Person/User/
    RoleAssignment behind — the whole operation is one transaction."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    nonexistent_group_id = str(uuid.uuid4())
    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="rollback@example.com",
            last_name="Rollbackova",
            role_code="instructor",
            group_ids=[nonexistent_group_id],
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text

    with session_scope() as session:
        leftover = session.execute(
            select(Person).where(Person.last_name == "Rollbackova")
        ).scalar_one_or_none()
        assert leftover is None
        leftover_user = session.execute(
            select(User).where(User.login_identifier == "rollback@example.com")
        ).scalar_one_or_none()
        assert leftover_user is None


# --- Instructor role ----------------------------------------------------


@requires_postgres
def test_wizard_instructor_with_zero_groups(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="instr0@example.com", last_name="Instr0", role_code="instructor", group_ids=[]
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["person"]["role_codes"] == ["instructor"]


@requires_postgres
def test_wizard_instructor_with_multiple_groups(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group1 = _make_group(club)
        group2 = _make_group(club)
        session.add_all([group1, group2])
        session.commit()
        club_id, group1_id, group2_id = club.id, group1.id, group2.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="instrN@example.com",
            last_name="InstrN",
            role_code="instructor",
            group_ids=[str(group1_id), str(group2_id)],
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    person_id = uuid.UUID(response.json()["person"]["id"])
    with session_scope() as session:
        user_id = session.execute(select(User.id).where(User.person_id == person_id)).scalar_one()
        assignments = session.execute(
            select(GroupInstructorAssignment.group_id).where(
                GroupInstructorAssignment.user_id == user_id
            )
        ).scalars().all()
        assert set(assignments) == {group1_id, group2_id}


# --- Member role ----------------------------------------------------------


@requires_postgres
def test_wizard_member_with_zero_groups_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="member0@example.com", last_name="Member0", role_code="member", group_ids=[]
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "member_requires_at_least_one_group"

    with session_scope() as session:
        leftover = session.execute(
            select(Person).where(Person.last_name == "Member0")
        ).scalar_one_or_none()
        assert leftover is None


@requires_postgres
def test_wizard_member_with_one_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        club_id, group_id = club.id, group.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="member1@example.com",
            last_name="Member1",
            role_code="member",
            group_ids=[str(group_id)],
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    person_id = uuid.UUID(response.json()["person"]["id"])
    with session_scope() as session:
        club_membership_id = session.execute(
            select(ClubMembership.id).where(ClubMembership.person_id == person_id)
        ).scalar_one()
        membership = session.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.club_membership_id == club_membership_id,
            )
        ).scalar_one()
        assert membership.membership_status == "active"


@requires_postgres
def test_wizard_member_with_multiple_groups(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group1 = _make_group(club)
        group2 = _make_group(club)
        session.add_all([group1, group2])
        session.commit()
        club_id, group1_id, group2_id = club.id, group1.id, group2.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="memberN@example.com",
            last_name="MemberN",
            role_code="member",
            group_ids=[str(group1_id), str(group2_id)],
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    person_id = uuid.UUID(response.json()["person"]["id"])
    with session_scope() as session:
        club_membership_id = session.execute(
            select(ClubMembership.id).where(ClubMembership.person_id == person_id)
        ).scalar_one()
        group_ids = session.execute(
            select(GroupMembership.group_id).where(
                GroupMembership.club_membership_id == club_membership_id
            )
        ).scalars().all()
        assert set(group_ids) == {group1_id, group2_id}


# --- Guardian role ----------------------------------------------------------


@requires_postgres
def test_wizard_guardian_with_zero_children_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="guardian0@example.com",
            last_name="Guardian0",
            role_code="guardian",
            child_person_ids=[],
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "guardian_requires_at_least_one_child"


@requires_postgres
def test_wizard_guardian_with_multiple_children(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        child1 = _make_person(last_name="Child1")
        child2 = _make_person(last_name="Child2")
        session.add_all([club, child1, child2])
        session.commit()
        club_id, child1_id, child2_id = club.id, child1.id, child2.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="guardianN@example.com",
            last_name="GuardianN",
            role_code="guardian",
            child_person_ids=[str(child1_id), str(child2_id)],
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    person_id = uuid.UUID(response.json()["person"]["id"])
    with session_scope() as session:
        child_ids = session.execute(
            select(GuardianRelationship.child_person_id).where(
                GuardianRelationship.guardian_person_id == person_id
            )
        ).scalars().all()
        assert set(child_ids) == {child1_id, child2_id}


# --- Authorization ------------------------------------------------------


@requires_postgres
def test_wizard_rejects_requester_without_person_create(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, requester_person, requester_user])
        session.commit()
        requester_id = requester_user.id
    _authenticate_as(requester_id)

    response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(email="denied@example.com"),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_wizard_fails_closed_with_zero_clubs_configured() -> None:
    """NoClubConfiguredError is treated as unreachable elsewhere in this
    codebase (never caught by any router) — surfaces as a generic 500.
    Uses a local `raise_server_exceptions=False` client, matching
    test_people_api.py's own `test_create_person_with_zero_clubs_fails_
    closed`: with `raise_server_exceptions=True`, the uncaught exception
    propagates out of Starlette's `BaseHTTPMiddleware` layer before the
    registered handler converts it to a response.
    """
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with session_scope() as session:
            person = _make_person()
            user = _make_user(person)
            session.add_all([person, user])
            session.commit()
            user_id = user.id
        with session_scope() as session:
            role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
            session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all"))
            session.commit()
        _authenticate_as(user_id)

        response = client.post(
            "/api/v1/persons/wizard",
            json=_base_payload(email="noclub@example.com"),
            headers=_csrf_headers(client),
        )
        assert response.status_code == 500, response.text
    finally:
        app.dependency_overrides.clear()


# --- GET /persons/{person_id}/groups (TH-0116 §12/§17) ----------------------


@requires_postgres
def test_get_person_groups_reflects_group_memberships_created_by_wizard(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        club_id, group_id = club.id, group.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    create_response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(
            email="viewgroups@example.com",
            last_name="ViewGroups",
            role_code="member",
            group_ids=[str(group_id)],
        ),
        headers=_csrf_headers(client),
    )
    assert create_response.status_code == 201, create_response.text
    person_id = create_response.json()["person"]["id"]

    response = client.get(f"/api/v1/persons/{person_id}/groups")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["group_id"] == str(group_id)


@requires_postgres
def test_get_person_groups_is_empty_for_a_person_with_no_group_memberships(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        club_id = club.id
    admin_id = _setup_admin(club_id)
    _authenticate_as(admin_id)

    create_response = client.post(
        "/api/v1/persons/wizard",
        json=_base_payload(email="nogroups@example.com", last_name="NoGroups"),
        headers=_csrf_headers(client),
    )
    assert create_response.status_code == 201, create_response.text
    person_id = create_response.json()["person"]["id"]

    response = client.get(f"/api/v1/persons/{person_id}/groups")
    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 0
