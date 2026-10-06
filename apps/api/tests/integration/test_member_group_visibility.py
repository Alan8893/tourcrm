"""Member Group visibility (Issue #282, PO decision 2026-10-05).

`group.read` + `self` for the canonical `member` role: a Member sees
exactly the active Groups in which their own Person has an active
`GroupMembership` held through an active `ClubMembership` in the Group's
Club. Every Member test runs through the REAL, migration-seeded
`member -> group.read -> self` grant (migration 5e2c8a41d7b9) on the
canonical `member` role — no ad hoc grant — so a missing seed can never
hide behind a test role.

Covers: one/several/zero visible Groups, ended and expired
GroupMembership, inactive ClubMembership, archived Group, cross-Club
membership, Club co-membership alone, unrelated-Group existence hiding,
`GET /groups/{id}` matching `GET /groups`, server-side pagination
metadata, nested `members`/`instructors` reads not widened, no
`group.manage` capability, and Admin (`all`) / Instructor (`own_groups`)
regression.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration/test_member_group_visibility.py -v
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_LAST_YEAR = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)
_YESTERDAY = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
_NEXT_YEAR = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories ----------------------------------------------------------------


def _club(session: Session) -> Club:
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _person(session: Session) -> Person:
    person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _user(session: Session, person: Person) -> User:
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _club_membership(
    session: Session,
    club: Club,
    person: Person,
    *,
    status: str = "active",
    membership_type: str = "student",
) -> ClubMembership:
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type=membership_type,
        status=status,
        joined_at=_LONG_AGO,
    )
    session.add(membership)
    session.flush()
    return membership


def _group(session: Session, club: Club, *, status: str = "active") -> Group:
    group = Group(
        club_id=club.id,
        name=f"Group {uuid.uuid4().hex[:8]}",
        status=status,
        valid_from=_LONG_AGO,
    )
    session.add(group)
    session.flush()
    return group


def _group_membership(
    session: Session, group: Group, club_membership: ClubMembership, **overrides: object
) -> GroupMembership:
    values: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": club_membership.id,
        "valid_from": _LONG_AGO,
        "membership_status": "active",
    }
    values.update(overrides)
    membership = GroupMembership(**values)  # type: ignore[arg-type]
    session.add(membership)
    session.flush()
    return membership


def _assign_role(session: Session, user: User, role_code: str, club: Club | None) -> None:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=club.id if club is not None else None
        )
    )
    session.flush()


def _member(session: Session, club: Club) -> tuple[User, ClubMembership]:
    """A real Member: an active ClubMembership in `club` plus a
    `club`-scoped assignment of the canonical, migration-seeded `member`
    role — no ad hoc grant."""
    person = _person(session)
    club_membership = _club_membership(session, club, person)
    user = _user(session, person)
    _assign_role(session, user, "member", club)
    return user, club_membership


def _grant_ad_hoc(
    session: Session, user: User, permission_code: str, scope_type: str, club: Club
) -> None:
    """Ad hoc role for the Instructor regression: the canonical
    `instructor` role carries no `group.read` grant at head, so the
    existing `own_groups` behavior is exercised the way
    tests/integration/test_groups_api.py already does."""
    permission = session.execute(
        select(Permission).where(Permission.code == permission_code)
    ).scalar_one()
    role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
    session.add(role)
    session.flush()
    session.add(
        RolePermission(
            role_id=role.id,
            permission_id=permission.id,
            scopes=[RolePermissionScope(scope_type=scope_type)],
        )
    )
    session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _listed_ids(client: TestClient, **params: object) -> set[str]:
    response = client.get("/api/v1/groups", params=params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _assert_hidden(client: TestClient, group_id: uuid.UUID) -> None:
    """Identical to a nonexistent Group (existence hiding)."""
    hidden = client.get(f"/api/v1/groups/{group_id}")
    missing = client.get(f"/api/v1/groups/{uuid.uuid4()}")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"] == "group_not_found"


# --- seeded grant ---------------------------------------------------------------


@requires_postgres
def test_member_role_is_seeded_with_group_read_self_only() -> None:
    with session_scope() as session:
        rows = session.execute(
            select(Permission.code, RolePermissionScope.scope_type)
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .join(
                RolePermissionScope, RolePermissionScope.role_permission_id == RolePermission.id
            )
            .where(Role.code == "member", Permission.code.like("group.%"))
        ).all()
    assert set(rows) == {("group.read", "self")}


# --- visible Groups: one / several / zero ---------------------------------------


@requires_postgres
def test_member_with_one_active_group_sees_exactly_that_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        own = _group(session, club)
        _group(session, club)  # unrelated Group in the same Club
        _group_membership(session, own, club_membership)
        session.commit()
        user_id, own_id = user.id, own.id
    _authenticate_as(user_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(own_id)]
    assert body["pagination"]["total"] == 1
    assert body["pagination"]["pages"] == 1

    detail = client.get(f"/api/v1/groups/{own_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == str(own_id)


@requires_postgres
def test_member_with_several_active_groups_sees_all_of_them(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        first = _group(session, club)
        second = _group(session, club)
        _group(session, club)
        _group_membership(session, first, club_membership)
        _group_membership(session, second, club_membership)
        session.commit()
        user_id, first_id, second_id = user.id, first.id, second.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == {str(first_id), str(second_id)}
    assert client.get(f"/api/v1/groups/{first_id}").status_code == 200
    assert client.get(f"/api/v1/groups/{second_id}").status_code == 200


@requires_postgres
def test_member_pagination_metadata_is_computed_on_the_authorized_set(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        for _ in range(3):
            _group(session, club)  # not visible: must not count toward total
        for _ in range(2):
            _group_membership(session, _group(session, club), club_membership)
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    response = client.get("/api/v1/groups", params={"page_size": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["pagination"] == {"page": 1, "page_size": 1, "total": 2, "pages": 2}


@requires_postgres
def test_member_without_group_membership_sees_no_groups(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _ = _member(session, club)
        group = _group(session, club)
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    response = client.get("/api/v1/groups")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["pagination"]["total"] == 0
    _assert_hidden(client, group_id)


# --- historical / inactive relationships never grant ---------------------------


@requires_postgres
def test_ended_group_membership_does_not_grant_visibility(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        group = _group(session, club)
        _group_membership(
            session,
            group,
            club_membership,
            membership_status="ended",
            valid_to=_YESTERDAY,
        )
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, group_id)


@requires_postgres
def test_group_membership_outside_its_validity_interval_does_not_grant_visibility(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        expired = _group(session, club)
        future = _group(session, club)
        _group_membership(
            session, expired, club_membership, valid_from=_LAST_YEAR, valid_to=_YESTERDAY
        )
        _group_membership(session, future, club_membership, valid_from=_NEXT_YEAR)
        session.commit()
        user_id, expired_id, future_id = user.id, expired.id, future.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, expired_id)
    _assert_hidden(client, future_id)


@requires_postgres
def test_group_membership_through_inactive_club_membership_does_not_grant_visibility(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        person = _person(session)
        user = _user(session, person)
        _assign_role(session, user, "member", club)
        suspended = _club_membership(session, club, person, status="suspended")
        group = _group(session, club)
        _group_membership(session, group, suspended)
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, group_id)


# --- archived Group -------------------------------------------------------------


@requires_postgres
def test_archived_group_is_not_visible_to_member(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        active = _group(session, club)
        archived = _group(session, club, status="archived")
        _group_membership(session, active, club_membership)
        _group_membership(session, archived, club_membership)
        session.commit()
        user_id, active_id, archived_id = user.id, active.id, archived.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == {str(active_id)}
    assert _listed_ids(client, status="archived") == set()
    _assert_hidden(client, archived_id)


# --- Club boundary --------------------------------------------------------------


@requires_postgres
def test_membership_in_another_club_never_grants_visibility(client: TestClient) -> None:
    """The Member role is scoped to Club A; the same Person's active
    ClubMembership + GroupMembership in Club B must not leak Club B's
    Group through the Club A assignment."""
    with session_scope() as session:
        club_a = _club(session)
        club_b = _club(session)
        user, _ = _member(session, club_a)
        club_b_membership = _club_membership(
            session, club_b, user.person, membership_type="member"
        )
        other_club_group = _group(session, club_b)
        _group_membership(session, other_club_group, club_b_membership)
        session.commit()
        user_id, group_id = user.id, other_club_group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, group_id)


@requires_postgres
def test_group_membership_through_another_clubs_membership_never_grants_visibility(
    client: TestClient,
) -> None:
    """A GroupMembership whose ClubMembership belongs to a different Club
    than the Group (written bypassing app.groups.service's cross-Club
    check) must not satisfy `self`."""
    with session_scope() as session:
        club_a = _club(session)
        club_b = _club(session)
        user, club_a_membership = _member(session, club_a)
        _assign_role(session, user, "member", club_b)
        club_b_group = _group(session, club_b)
        _group_membership(session, club_b_group, club_a_membership)
        session.commit()
        user_id, group_id = user.id, club_b_group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, group_id)


@requires_postgres
def test_club_co_membership_alone_never_grants_visibility(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _ = _member(session, club)
        _, other_club_membership = _member(session, club)
        group = _group(session, club)
        _group_membership(session, group, other_club_membership)
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, group_id)


@requires_postgres
def test_member_without_member_role_sees_nothing(client: TestClient) -> None:
    """The relationship alone is not enough: no grant, no visibility."""
    with session_scope() as session:
        club = _club(session)
        person = _person(session)
        user = _user(session, person)
        club_membership = _club_membership(session, club, person)
        group = _group(session, club)
        _group_membership(session, group, club_membership)
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == set()
    _assert_hidden(client, group_id)


# --- list / item consistency ----------------------------------------------------


@requires_postgres
def test_item_endpoint_matches_collection_visibility(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        other_club = _club(session)
        user, club_membership = _member(session, club)
        visible = _group(session, club)
        _group_membership(session, visible, club_membership)
        ended = _group(session, club)
        _group_membership(
            session, ended, club_membership, membership_status="ended", valid_to=_YESTERDAY
        )
        archived = _group(session, club, status="archived")
        _group_membership(session, archived, club_membership)
        unrelated = _group(session, club)
        foreign = _group(session, other_club)
        session.commit()
        user_id = user.id
        all_ids = [visible.id, ended.id, archived.id, unrelated.id, foreign.id]
    _authenticate_as(user_id)

    listed = _listed_ids(client)
    for group_id in all_ids:
        status_code = client.get(f"/api/v1/groups/{group_id}").status_code
        assert (status_code == 200) == (str(group_id) in listed), group_id
    assert listed == {str(all_ids[0])}


# --- nested reads and group.manage are not widened ------------------------------


@requires_postgres
def test_member_nested_members_and_instructors_reads_are_not_widened(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        group = _group(session, club)
        _group_membership(session, group, club_membership)
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/groups/{group_id}").status_code == 200
    for path in ("members", "instructors"):
        response = client.get(f"/api/v1/groups/{group_id}/{path}")
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == "group_not_found"


@requires_postgres
def test_member_cannot_obtain_group_manage_capabilities(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        group = _group(session, club)
        own_membership = _group_membership(session, group, club_membership)
        session.commit()
        user_id, group_id, club_id = user.id, group.id, club.id
        person_id, membership_id = user.person_id, own_membership.id
    _authenticate_as(user_id)
    headers = _csrf_headers(client)

    create = client.post(
        "/api/v1/groups",
        json={"club_id": str(club_id), "name": "Nope", "valid_from": _LONG_AGO.isoformat()},
        headers=headers,
    )
    assert create.status_code == 403, create.text

    for method, path, payload in (
        ("patch", f"/api/v1/groups/{group_id}", {"name": "Renamed"}),
        ("post", f"/api/v1/groups/{group_id}/archive", None),
        (
            "post",
            f"/api/v1/groups/{group_id}/members",
            {"person_id": str(person_id), "valid_from": _LONG_AGO.isoformat()},
        ),
        (
            "post",
            f"/api/v1/groups/{group_id}/instructors",
            {
                "user_id": str(user_id),
                "role_in_group": "instructor",
                "valid_from": _LONG_AGO.isoformat(),
            },
        ),
    ):
        response = client.request(method.upper(), path, json=payload, headers=headers)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == "group_not_found"

    end = client.post(f"/api/v1/group-memberships/{membership_id}/end", headers=headers)
    assert end.status_code == 404, end.text
    assert end.json()["error"]["code"] == "group_membership_not_found"

    with session_scope() as session:
        unchanged = session.get(Group, group_id)
        assert unchanged is not None
        assert unchanged.status == "active"
        assert session.get(GroupMembership, membership_id).membership_status == "active"  # type: ignore[union-attr]


# --- Admin / Instructor regression ----------------------------------------------


@requires_postgres
def test_admin_regression_sees_every_group_of_the_club(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        other_club = _club(session)
        person = _person(session)
        _club_membership(session, club, person)
        admin = _user(session, person)
        _assign_role(session, admin, "admin", club)
        active = _group(session, club)
        archived = _group(session, club, status="archived")
        foreign = _group(session, other_club)
        session.commit()
        admin_id, active_id, archived_id, foreign_id = admin.id, active.id, archived.id, foreign.id
    _authenticate_as(admin_id)

    assert _listed_ids(client) == {str(active_id), str(archived_id)}
    assert client.get(f"/api/v1/groups/{active_id}").status_code == 200
    assert client.get(f"/api/v1/groups/{archived_id}").status_code == 200
    assert client.get(f"/api/v1/groups/{active_id}/members").status_code == 200
    _assert_hidden(client, foreign_id)


@requires_postgres
def test_instructor_regression_own_groups_is_not_broadened_by_own_membership(
    client: TestClient,
) -> None:
    """`own_groups` keeps meaning an active GroupInstructorAssignment: the
    Instructor's own GroupMembership elsewhere grants nothing without a
    `self` grant, and nested reads for the assigned Group still work."""
    with session_scope() as session:
        club = _club(session)
        person = _person(session)
        club_membership = _club_membership(session, club, person)
        instructor = _user(session, person)
        _grant_ad_hoc(session, instructor, "group.read", "own_groups", club)
        assigned = _group(session, club)
        session.add(
            GroupInstructorAssignment(
                group_id=assigned.id,
                user_id=instructor.id,
                role_in_group="instructor",
                valid_from=_LONG_AGO,
            )
        )
        participating = _group(session, club)
        _group_membership(session, participating, club_membership)
        session.commit()
        instructor_id, assigned_id, participating_id = (
            instructor.id,
            assigned.id,
            participating.id,
        )
    _authenticate_as(instructor_id)

    assert _listed_ids(client) == {str(assigned_id)}
    assert client.get(f"/api/v1/groups/{assigned_id}").status_code == 200
    assert client.get(f"/api/v1/groups/{assigned_id}/members").status_code == 200
    _assert_hidden(client, participating_id)


@requires_postgres
def test_instructor_who_is_also_member_gets_the_union_of_both_scopes(
    client: TestClient,
) -> None:
    """Permissions are additive (roles-and-permissions.md §13): own_groups
    for the assigned Group, self for the Group the Person participates in
    — and nested reads only through own_groups."""
    with session_scope() as session:
        club = _club(session)
        user, club_membership = _member(session, club)
        _grant_ad_hoc(session, user, "group.read", "own_groups", club)
        assigned = _group(session, club)
        session.add(
            GroupInstructorAssignment(
                group_id=assigned.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_LONG_AGO,
            )
        )
        participating = _group(session, club)
        _group_membership(session, participating, club_membership)
        _group(session, club)
        session.commit()
        user_id, assigned_id, participating_id = user.id, assigned.id, participating.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == {str(assigned_id), str(participating_id)}
    assert client.get(f"/api/v1/groups/{assigned_id}/members").status_code == 200
    assert client.get(f"/api/v1/groups/{participating_id}").status_code == 200
    assert client.get(f"/api/v1/groups/{participating_id}/members").status_code == 404
