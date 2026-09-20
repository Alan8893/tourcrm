"""HTTP-level integration tests for /api/v1/users (TH-0107): the User
directory endpoint backing the Calendar instructor filter.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_people_api.py's pattern and
reusing its exact fixture/factory shapes (this endpoint reuses
`person.read` + `person_visibility_filter` verbatim, so its authorization
tests mirror that file's Person-list tests directly).

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
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories (mirrors test_people_api.py) ----------------------


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
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
        session.commit()


def _assign_baseline_role(
    user_id: uuid.UUID,
    role_code: str,
    *,
    valid_from: datetime.datetime | None = None,
    valid_to: datetime.datetime | None = None,
) -> None:
    """Assign one of the canonical, migration-seeded baseline roles
    (admin/instructor/member/guardian) to `user_id` — used here purely as
    an *effective-role fact about the target user* (task §7's `role=
    instructor` filter), never to grant `person.read` itself.
    """
    with session_scope() as session:
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
        assignment = UserRoleAssignment(
            user_id=user_id, role_id=role.id, scope_type="self", club_id=None
        )
        if valid_from is not None:
            assignment.valid_from = valid_from
        if valid_to is not None:
            assignment.valid_to = valid_to
        session.add(assignment)
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


# --- authorization / scope --------------------------------------------------


@requires_postgres
def test_list_users_with_all_scope_permission_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        target_person = _make_person(last_name="Sidorov", first_name="Ivan")
        target_user = _make_user(target_person)
        session.add_all([requester_person, requester_user, target_person, target_user])
        session.commit()
        requester_id, target_id = requester_user.id, target_user.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert str(target_id) in ids
    assert str(requester_id) in ids


@requires_postgres
def test_list_users_response_projection_has_no_sensitive_fields(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert set(item.keys()) == {"id", "person_id", "first_name", "last_name", "middle_name"}


@requires_postgres
def test_list_users_without_applicable_permission_returns_empty_not_forbidden(
    client: TestClient,
) -> None:
    """Mirrors test_people_api.py's `test_list_persons_none_scope_returns_
    empty` — an authenticated caller with no applicable `person.read`
    assignment (e.g. a bare `member`/`guardian`) gets a silently empty
    200, not a 403. This is `person.read`'s existing, documented
    convention (app/api/v1/persons.py's own module docstring), reused
    verbatim here rather than inventing a different failure mode for this
    one endpoint.
    """
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([person, user, other_person, other_user])
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 0


# --- role filter -------------------------------------------------------


@requires_postgres
def test_list_users_role_instructor_filter(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        instructor_person = _make_person(last_name="Kuznetsova", first_name="Olga")
        instructor_user = _make_user(instructor_person)
        member_person = _make_person(last_name="Fedorov", first_name="Petr")
        member_user = _make_user(member_person)
        session.add_all(
            [
                requester_person,
                requester_user,
                instructor_person,
                instructor_user,
                member_person,
                member_user,
            ]
        )
        session.commit()
        requester_id = requester_user.id
        instructor_id, member_id = instructor_user.id, member_user.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _assign_baseline_role(instructor_id, "instructor")
    _assign_baseline_role(member_id, "member")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"role": "instructor"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(instructor_id) in ids
    assert str(member_id) not in ids


@requires_postgres
def test_list_users_instructor_without_group_instructor_assignment_is_still_included(
    client: TestClient,
) -> None:
    """Task §7: directory inclusion for `role=instructor` must depend only
    on an effective UserRoleAssignment, never on a
    `GroupInstructorAssignment` — this instructor has no Group
    responsibility at all and must still appear.
    """
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        instructor_person = _make_person(last_name="Volkova", first_name="Nina")
        instructor_user = _make_user(instructor_person)
        session.add_all([requester_person, requester_user, instructor_person, instructor_user])
        session.commit()
        requester_id, instructor_id = requester_user.id, instructor_user.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _assign_baseline_role(instructor_id, "instructor")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"role": "instructor"})
    assert response.status_code == 200, response.text
    assert str(instructor_id) in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_list_users_invalid_role_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/users", params={"role": "not-a-real-role"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_role"


# --- search --------------------------------------------------------------


@requires_postgres
def test_list_users_search_matches_all_three_name_fields(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        by_last = _make_person(last_name="Zvyagintseva", first_name="A")
        by_first = _make_person(last_name="B", first_name="Zvyagintsev")
        by_middle = _make_person(last_name="C", first_name="D", middle_name="Zvyagintsevich")
        unrelated = _make_person(last_name="Nobody", first_name="Nobody")
        users = [_make_user(p) for p in (by_last, by_first, by_middle, unrelated)]
        session.add_all(
            [requester_person, requester_user, by_last, by_first, by_middle, unrelated, *users]
        )
        session.commit()
        requester_id = requester_user.id
        expected_ids = {str(u.id) for u in users[:3]}
        unrelated_id = str(users[3].id)
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"search": "Zvyagints"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert expected_ids <= ids
    assert unrelated_id not in ids


# --- pagination ------------------------------------------------------------


@requires_postgres
def test_list_users_pagination(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        others = [_make_person() for _ in range(5)]
        other_users = [_make_user(p) for p in others]
        session.add_all([requester_person, requester_user, *others, *other_users])
        session.commit()
        requester_id = requester_user.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"page": 1, "page_size": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 2
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 2
    assert body["pagination"]["total"] >= 6


# --- club boundary -----------------------------------------------------


@requires_postgres
def test_list_users_club_filter_shows_active_membership_in_that_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        target_person = _make_person(last_name="Orlova", first_name="Maria")
        target_user = _make_user(target_person)
        session.add_all([requester_person, requester_user, club_a, target_person, target_user])
        session.commit()
        session.add(_make_club_membership(club_a, target_person, status="active"))
        session.commit()
        requester_id, target_id, club_a_id = requester_user.id, target_user.id, club_a.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"club_id": str(club_a_id)})
    assert response.status_code == 200, response.text
    assert str(target_id) in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_list_users_same_user_not_visible_for_a_different_club(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        club_b = _make_club()
        target_person = _make_person(last_name="Orlova", first_name="Maria")
        target_user = _make_user(target_person)
        session.add_all(
            [requester_person, requester_user, club_a, club_b, target_person, target_user]
        )
        session.commit()
        session.add(_make_club_membership(club_a, target_person, status="active"))
        session.commit()
        requester_id, target_id, club_b_id = requester_user.id, target_user.id, club_b.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"club_id": str(club_b_id)})
    assert response.status_code == 200, response.text
    assert str(target_id) not in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_list_users_instructor_from_another_club_absent(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        club_b = _make_club()
        instructor_person = _make_person(last_name="Belova", first_name="Elena")
        instructor_user = _make_user(instructor_person)
        session.add_all(
            [
                requester_person,
                requester_user,
                club_a,
                club_b,
                instructor_person,
                instructor_user,
            ]
        )
        session.commit()
        session.add(_make_club_membership(club_b, instructor_person, status="active"))
        session.commit()
        requester_id, instructor_id, club_a_id = (
            requester_user.id,
            instructor_user.id,
            club_a.id,
        )
    _grant_permission(requester_id, "person.read", scope_type="all")
    _assign_baseline_role(instructor_id, "instructor")
    _authenticate_as(requester_id)

    response = client.get(
        "/api/v1/users", params={"role": "instructor", "club_id": str(club_a_id)}
    )
    assert response.status_code == 200, response.text
    assert str(instructor_id) not in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_list_users_inactive_membership_does_not_grant_club_visibility(
    client: TestClient,
) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        target_person = _make_person(last_name="Orlova", first_name="Maria")
        target_user = _make_user(target_person)
        session.add_all([requester_person, requester_user, club_a, target_person, target_user])
        session.commit()
        session.add(
            _make_club_membership(
                club_a, target_person, status="inactive", left_at=_utc(2021, 1, 1)
            )
        )
        session.commit()
        requester_id, target_id, club_a_id = requester_user.id, target_user.id, club_a.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"club_id": str(club_a_id)})
    assert response.status_code == 200, response.text
    assert str(target_id) not in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_list_users_person_without_membership_absent_from_club_filtered_results(
    client: TestClient,
) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        target_person = _make_person(last_name="NoClub", first_name="Someone")
        target_user = _make_user(target_person)
        session.add_all([requester_person, requester_user, club_a, target_person, target_user])
        session.commit()
        requester_id, target_id, club_a_id = requester_user.id, target_user.id, club_a.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"club_id": str(club_a_id)})
    assert response.status_code == 200, response.text
    assert str(target_id) not in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_list_users_multiple_clubs_do_not_cross_leak(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        club_b = _make_club()
        multi_person = _make_person(last_name="Dvukhklubnaya", first_name="Vera")
        multi_user = _make_user(multi_person)
        session.add_all(
            [requester_person, requester_user, club_a, club_b, multi_person, multi_user]
        )
        session.commit()
        # Different membership_type per club: the person<->club_memberships
        # table's exclusion constraint (ck_club_memberships_no_overlapping_
        # active) keys on (person_id, membership_type), not club_id — two
        # simultaneously-active *same-type* memberships for one person
        # would collide regardless of club, so a genuinely multi-club
        # active person needs distinct types here (a real product
        # constraint, not a test artifact to route around silently).
        session.add(
            _make_club_membership(club_a, multi_person, status="active", membership_type="member")
        )
        session.add(
            _make_club_membership(
                club_b, multi_person, status="active", membership_type="instructor"
            )
        )
        session.commit()
        requester_id, multi_id, club_a_id, club_b_id = (
            requester_user.id,
            multi_user.id,
            club_a.id,
            club_b.id,
        )
    _grant_permission(requester_id, "person.read", scope_type="all")
    _authenticate_as(requester_id)

    resp_a = client.get("/api/v1/users", params={"club_id": str(club_a_id)})
    resp_b = client.get("/api/v1/users", params={"club_id": str(club_b_id)})
    assert str(multi_id) in {item["id"] for item in resp_a.json()["items"]}
    assert str(multi_id) in {item["id"] for item in resp_b.json()["items"]}


@requires_postgres
def test_list_users_multiple_role_assignments_resolve_correctly(client: TestClient) -> None:
    """A user holding both `instructor` and `member` role assignments must
    still match `role=instructor` (additive roles — task §7/§12 "multiple
    role assignments" case), and must not match some other role code.
    """
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        dual_person = _make_person(last_name="Dual", first_name="Role")
        dual_user = _make_user(dual_person)
        session.add_all([requester_person, requester_user, dual_person, dual_user])
        session.commit()
        requester_id, dual_id = requester_user.id, dual_user.id
    _grant_permission(requester_id, "person.read", scope_type="all")
    _assign_baseline_role(dual_id, "instructor")
    _assign_baseline_role(dual_id, "member")
    _authenticate_as(requester_id)

    instructor_response = client.get("/api/v1/users", params={"role": "instructor"})
    admin_response = client.get("/api/v1/users", params={"role": "admin"})
    assert str(dual_id) in {item["id"] for item in instructor_response.json()["items"]}
    assert str(dual_id) not in {item["id"] for item in admin_response.json()["items"]}


@requires_postgres
def test_list_users_club_scoped_requester_cannot_see_other_club_via_all_scope(
    client: TestClient,
) -> None:
    """A requester whose `all`-scope `person.read` assignment is itself
    club-scoped to Club A must not see a Person whose only ClubMembership
    is in Club B, even without any explicit `club_id` query filter —
    exercises `person_visibility_filter`'s own club-scoped `all` predicate
    (reused, not re-derived, by this endpoint).
    """
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        club_a = _make_club()
        club_b = _make_club()
        other_person = _make_person(last_name="OnlyClubB", first_name="X")
        other_user = _make_user(other_person)
        session.add_all(
            [requester_person, requester_user, club_a, club_b, other_person, other_user]
        )
        session.commit()
        session.add(_make_club_membership(club_b, other_person, status="active"))
        session.commit()
        requester_id, other_id, club_a_id = requester_user.id, other_user.id, club_a.id
    _grant_permission(requester_id, "person.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    assert str(other_id) not in {item["id"] for item in response.json()["items"]}
