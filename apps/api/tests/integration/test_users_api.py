"""HTTP-level integration tests for /api/v1/users (TH-0107): the User
directory endpoint backing the Calendar instructor filter.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_people_api.py's pattern for
fixture/factory shapes.

Authorization here is gated by the standalone `user.directory.read`
permission (`app.users.authorization`), deliberately NOT `person.read` —
see that module's docstring, and docs/05-api/users-api.md, for the PO
decision and full reasoning (ADR-0035 §11 explicitly rejects bare Club
co-membership as sufficient grounds for Person access, so `person.read`'s
`own_groups` scope cannot simply be widened for this directory). The
headline regression test below
(`test_instructor_requester_sees_instructor_target_with_no_shared_group`)
exercises the real, migration-seeded `(instructor, user.directory.read)`
RolePermission grant end-to-end — not just the bespoke resolver in
isolation — to prove an instructor requester can see a fellow instructor
in the same Club who shares no Group/GroupInstructorAssignment with them.

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
    """Ad hoc grant via a throwaway role — used here to test
    `user.directory.read` in isolation from the real `instructor`/`admin`
    role wiring. `scope_type` is passed only for readability/convention
    (e.g. "all"): `app.users.authorization` never inspects it for this
    permission, only `club_id` matters (see that module's docstring).
    """
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
    club_id: uuid.UUID | None = None,
    valid_from: datetime.datetime | None = None,
    valid_to: datetime.datetime | None = None,
) -> None:
    """Assign one of the canonical, migration-seeded baseline roles
    (admin/instructor/member/guardian) to `user_id`. `club_id` matters
    here in two ways depending on the test: as an *effective-role fact*
    about a target user (role=instructor filter, club_id irrelevant to
    that check), or — when the role itself carries `user.directory.read`
    via the real migration-seeded RolePermission grant (`instructor`/
    `admin`) — as the actual authorization boundary for a *requester*.
    """
    with session_scope() as session:
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
        assignment = UserRoleAssignment(
            user_id=user_id, role_id=role.id, scope_type="self", club_id=club_id
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


# --- authorization / permission gate ----------------------------------------


@requires_postgres
def test_list_users_with_global_directory_read_grant_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        target_person = _make_person(last_name="Sidorov", first_name="Ivan")
        target_user = _make_user(target_person)
        session.add_all([requester_person, requester_user, target_person, target_user])
        session.commit()
        requester_id, target_id = requester_user.id, target_user.id
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(user_id, "user.directory.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert set(item.keys()) == {"id", "person_id", "first_name", "last_name", "middle_name"}


@requires_postgres
def test_list_users_without_user_directory_read_permission_is_forbidden(
    client: TestClient,
) -> None:
    """Unlike `person.read`'s list-endpoint "silently empty" convention
    (`GET /persons`), `GET /users` is gated by its own standalone
    permission whose absence is a hard 403 (PO decision — this permission
    exists specifically to grant/withhold the whole directory capability,
    not to scope an already-shared resource many other permissions read).
    A caller holding no `user.directory.read` assignment at all — e.g. a
    bare `member` — must be rejected outright, never handed a filtered
    empty page.
    """
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _assign_baseline_role(user_id, "member")
    _authenticate_as(user_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


# --- headline regression test: instructor sees instructor, no shared group -


@requires_postgres
def test_instructor_requester_sees_instructor_target_with_no_shared_group(
    client: TestClient,
) -> None:
    """THE regression test for this PO decision. Both A and B hold the
    real, migration-seeded `instructor` role (which now carries
    `user.directory.read` via `RolePermission` — no ad hoc test grant),
    both have an active `ClubMembership` in the same Club, and B has NO
    `GroupInstructorAssignment`/`GroupMembership` relationship with A
    whatsoever (no Group is even created in this test). Under the old
    `person.read`/`own_groups` design this would have returned nothing —
    B is not a member of any group A instructs. Under
    `user.directory.read`, B must appear.
    """
    with session_scope() as session:
        club = _make_club()
        a_person = _make_person(last_name="Antonova", first_name="Anna")
        a_user = _make_user(a_person)
        b_person = _make_person(last_name="Borisov", first_name="Boris")
        b_user = _make_user(b_person)
        session.add_all([club, a_person, a_user, b_person, b_user])
        session.commit()
        session.add(_make_club_membership(club, a_person, status="active"))
        session.add(_make_club_membership(club, b_person, status="active"))
        session.commit()
        club_id, a_id, b_id = club.id, a_user.id, b_user.id

    # Both A (requester) and B (target) are plain instructors of the same
    # Club — the only two facts the new policy is allowed to require.
    _assign_baseline_role(a_id, "instructor", club_id=club_id)
    _assign_baseline_role(b_id, "instructor", club_id=club_id)
    _authenticate_as(a_id)

    response = client.get(
        "/api/v1/users", params={"role": "instructor", "club_id": str(club_id)}
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(b_id) in ids


@requires_postgres
def test_instructor_requester_cannot_see_a_different_clubs_instructor(
    client: TestClient,
) -> None:
    """The flip side of the headline test: A's real `instructor`
    `UserRoleAssignment` is scoped to Club X, so A must not see an
    instructor C whose only active ClubMembership is in Club Y — even
    though both A and C hold the `instructor` role. Cross-club isolation
    must hold through the real RolePermission-granted path, not just the
    ad hoc `_grant_permission` helper.
    """
    with session_scope() as session:
        club_x = _make_club()
        club_y = _make_club()
        a_person = _make_person(last_name="Antonova", first_name="Anna")
        a_user = _make_user(a_person)
        c_person = _make_person(last_name="Egorova", first_name="Ekaterina")
        c_user = _make_user(c_person)
        session.add_all([club_x, club_y, a_person, a_user, c_person, c_user])
        session.commit()
        session.add(_make_club_membership(club_x, a_person, status="active"))
        session.add(_make_club_membership(club_y, c_person, status="active"))
        session.commit()
        club_x_id, a_id, c_id = club_x.id, a_user.id, c_user.id

    _assign_baseline_role(a_id, "instructor", club_id=club_x_id)
    _assign_baseline_role(c_id, "instructor", club_id=club_x_id)  # role only, no membership in X
    _authenticate_as(a_id)

    response = client.get(
        "/api/v1/users", params={"role": "instructor", "club_id": str(club_x_id)}
    )
    assert response.status_code == 200, response.text
    assert str(c_id) not in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_instructor_requester_reach_is_bounded_by_own_assignment_club_even_without_query_filter(
    client: TestClient,
) -> None:
    """Omitting `club_id` from the query must NOT default to "every
    club" — the requester's own club-scoped `user.directory.read`
    assignment is itself the authorization boundary (mirrors how
    `event_visibility_filter`/`GET /events/calendar` treat filters as
    narrowing an already-authorized set, never expanding it).
    """
    with session_scope() as session:
        club_x = _make_club()
        club_y = _make_club()
        a_person = _make_person(last_name="Antonova", first_name="Anna")
        a_user = _make_user(a_person)
        other_person = _make_person(last_name="OnlyClubY", first_name="X")
        other_user = _make_user(other_person)
        session.add_all([club_x, club_y, a_person, a_user, other_person, other_user])
        session.commit()
        session.add(_make_club_membership(club_y, other_person, status="active"))
        session.commit()
        club_x_id, a_id, other_id = club_x.id, a_user.id, other_user.id

    _assign_baseline_role(a_id, "instructor", club_id=club_x_id)
    _authenticate_as(a_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    assert str(other_id) not in {item["id"] for item in response.json()["items"]}


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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(user_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users", params={"page": 1, "page_size": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 2
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 2
    assert body["pagination"]["total"] >= 6


# --- club boundary (target eligibility) -------------------------------


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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
    _authenticate_as(requester_id)

    resp_a = client.get("/api/v1/users", params={"club_id": str(club_a_id)})
    resp_b = client.get("/api/v1/users", params={"club_id": str(club_b_id)})
    assert str(multi_id) in {item["id"] for item in resp_a.json()["items"]}
    assert str(multi_id) in {item["id"] for item in resp_b.json()["items"]}


@requires_postgres
def test_list_users_multiple_role_assignments_resolve_correctly(client: TestClient) -> None:
    """A user holding both `instructor` and `member` role assignments must
    still match `role=instructor` (additive roles), and must not match
    some other role code.
    """
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        dual_person = _make_person(last_name="Dual", first_name="Role")
        dual_user = _make_user(dual_person)
        session.add_all([requester_person, requester_user, dual_person, dual_user])
        session.commit()
        requester_id, dual_id = requester_user.id, dual_user.id
    _grant_permission(requester_id, "user.directory.read", scope_type="all")
    _assign_baseline_role(dual_id, "instructor")
    _assign_baseline_role(dual_id, "member")
    _authenticate_as(requester_id)

    instructor_response = client.get("/api/v1/users", params={"role": "instructor"})
    admin_response = client.get("/api/v1/users", params={"role": "admin"})
    assert str(dual_id) in {item["id"] for item in instructor_response.json()["items"]}
    assert str(dual_id) not in {item["id"] for item in admin_response.json()["items"]}


@requires_postgres
def test_list_users_club_scoped_requester_cannot_see_other_club(
    client: TestClient,
) -> None:
    """A requester whose `user.directory.read` assignment is itself
    club-scoped to Club A must not see a Person whose only ClubMembership
    is in Club B, even without any explicit `club_id` query filter —
    exercises `app.users.authorization.directory_reach_filter`'s club
    boundary (the requester's own authorized reach, independent of any
    query-parameter filter).
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
    _grant_permission(requester_id, "user.directory.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(requester_id)

    response = client.get("/api/v1/users")
    assert response.status_code == 200, response.text
    assert str(other_id) not in {item["id"] for item in response.json()["items"]}
