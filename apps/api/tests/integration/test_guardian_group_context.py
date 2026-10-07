"""Guardian child-to-group context (ADR-0046, Issue #301 / TH-0173).

`group.read` + `children` for the canonical `guardian` role: a Guardian
reads exactly the active Groups in which at least one of their children —
through an active, interval-valid `GuardianRelationship` — has an active
`GroupMembership` held through an active `ClubMembership` in the Group's
Club. Several children give the UNION. Every Guardian Group test runs
through the REAL, migration-seeded `guardian -> group.read -> children`
grant (migration c4f7a2e91b36) on the canonical `guardian` role — no ad
hoc Group grant — so a missing seed can never hide behind a test role.

`GET /me/children` is additionally gated by `guardian_relationship.read`
— also served by the real, migration-seeded `guardian ->
guardian_relationship.read -> children` grant (migration d8e3b5f02a47),
resolved Club-neutrally through the same `club`-scoped assignment.

Covers: one child / several children (UNION, no duplicates), list and
item, `GET /me/children` `groups[]`, revoked and expired
GuardianRelationship, inactive child ClubMembership, ended and expired
GroupMembership, archived Group, another Club, another Guardian's child,
direct IDOR on `/groups/{id}`, server-side pagination, nested
`members`/`instructors` reads not widened, no `group.manage`, and Admin
(`all`) / Instructor (`own_groups`) / Member (`self`) regression.
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
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_LAST_YEAR = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)
_YESTERDAY = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)


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


def _person(session: Session, last_name: str = "Иванов") -> Person:
    person = Person(last_name=last_name, first_name=f"P-{uuid.uuid4().hex[:8]}")
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


def _group(session: Session, club: Club, name: str, *, status: str = "active") -> Group:
    group = Group(club_id=club.id, name=name, status=status, valid_from=_LONG_AGO)
    session.add(group)
    session.flush()
    return group


def _group_membership(
    session: Session, group: Group, club_membership: ClubMembership, **overrides: object
) -> None:
    values: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": club_membership.id,
        "valid_from": _LONG_AGO,
        "membership_status": "active",
    }
    values.update(overrides)
    session.add(GroupMembership(**values))  # type: ignore[arg-type]
    session.flush()


def _relationship(session: Session, guardian: Person, child: Person, **overrides: object) -> None:
    values: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "valid_from": _LONG_AGO,
    }
    values.update(overrides)
    session.add(GuardianRelationship(**values))  # type: ignore[arg-type]
    session.flush()


def _assign_role(session: Session, user: User, role_code: str, club: Club | None) -> None:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=club.id if club is not None else None
        )
    )
    session.flush()


def _grant_ad_hoc(
    session: Session,
    user: User,
    permission_code: str,
    scope_type: str,
    club: Club | None,
) -> None:
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
    session.add(
        UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=club.id if club is not None else None
        )
    )
    session.flush()


def _guardian(session: Session, club: Club) -> tuple[User, Person]:
    """A real Guardian: one `club`-scoped assignment of the canonical,
    migration-seeded `guardian` role (AUTH-2A) — no ad hoc grant."""
    person = _person(session, "Родитель")
    user = _user(session, person)
    _assign_role(session, user, "guardian", club)
    return user, person


def _child(session: Session, club: Club, guardian: Person, *, last_name: str) -> ClubMembership:
    """A child of `guardian` with an active ClubMembership in `club`."""
    child = _person(session, last_name)
    _relationship(session, guardian, child)
    return _club_membership(session, club, child)


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _listed_ids(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/v1/groups", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def _assert_hidden(client: TestClient, group_id: uuid.UUID) -> None:
    """Identical to a nonexistent Group (existence hiding)."""
    hidden = client.get(f"/api/v1/groups/{group_id}")
    missing = client.get(f"/api/v1/groups/{uuid.uuid4()}")
    assert hidden.status_code == missing.status_code == 404
    for key in ("code", "message"):
        assert hidden.json()["error"][key] == missing.json()["error"][key]
    assert hidden.json()["error"]["code"] == "group_not_found"


def _children_groups(client: TestClient) -> dict[str, list[dict[str, str]]]:
    response = client.get("/api/v1/me/children")
    assert response.status_code == 200, response.text
    return {item["id"]: item["groups"] for item in response.json()["items"]}


# --- seeded grant ---------------------------------------------------------------


def test_guardian_role_is_seeded_with_group_read_children_only() -> None:
    with session_scope() as session:
        rows = session.execute(
            select(Permission.code, RolePermissionScope.scope_type)
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .join(
                RolePermissionScope, RolePermissionScope.role_permission_id == RolePermission.id
            )
            .where(Role.code == "guardian", Permission.code.like("group.%"))
        ).all()
    assert set(rows) == {("group.read", "children")}


# --- positive -------------------------------------------------------------------


def test_one_child_with_an_active_group_membership_lists_and_reads_that_group(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        group = _group(session, club, "Туристы-5")
        _group_membership(session, group, child_cm)
        _group(session, club, "Чужая группа")
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == [str(group_id)]
    detail = client.get(f"/api/v1/groups/{group_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Туристы-5"


def test_several_children_give_the_union_without_duplicates(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        ivan = _child(session, club, guardian, last_name="Иванов")
        maria = _child(session, club, guardian, last_name="Иванова")
        tourists = _group(session, club, "Туристы-5")
        orienteering = _group(session, club, "Ориентирование")
        shared = _group(session, club, "Общая")
        _group_membership(session, tourists, ivan)
        _group_membership(session, orienteering, maria)
        _group_membership(session, shared, ivan)
        _group_membership(session, shared, maria)
        session.commit()
        user_id = user.id
        expected = {str(tourists.id), str(orienteering.id), str(shared.id)}
    _authenticate_as(user_id)

    listed = _listed_ids(client)
    assert sorted(listed) == sorted(expected)
    assert len(listed) == len(set(listed)) == 3
    response = client.get("/api/v1/groups", params={"page_size": 100})
    assert response.json()["pagination"]["total"] == 3
    for group_id in expected:
        assert client.get(f"/api/v1/groups/{group_id}").status_code == 200


def test_pagination_is_computed_on_the_authorized_set(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        for index in range(3):
            _group_membership(session, _group(session, club, f"Группа {index}"), child_cm)
        for index in range(5):
            _group(session, club, f"Чужая {index}")
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    first = client.get("/api/v1/groups", params={"page": 1, "page_size": 2}).json()
    second = client.get("/api/v1/groups", params={"page": 2, "page_size": 2}).json()
    assert first["pagination"] == {"page": 1, "page_size": 2, "total": 3, "pages": 2}
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert {item["name"] for item in first["items"] + second["items"]} == {
        "Группа 0",
        "Группа 1",
        "Группа 2",
    }


def test_me_children_returns_each_childs_active_groups(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        ivan = _child(session, club, guardian, last_name="Иванов")
        maria = _child(session, club, guardian, last_name="Иванова")
        tourists = _group(session, club, "Туристы-5")
        orienteering = _group(session, club, "Ориентирование")
        archived = _group(session, club, "Архивная", status="archived")
        ended = _group(session, club, "Бывшая")
        _group_membership(session, tourists, ivan)
        _group_membership(session, orienteering, ivan)
        _group_membership(session, archived, ivan)
        _group_membership(
            session, ended, ivan, membership_status="ended", valid_to=_YESTERDAY
        )
        session.commit()
        user_id = user.id
        ivan_id, maria_id = ivan.person_id, maria.person_id
        expected_ivan = [
            {"id": str(orienteering.id), "name": "Ориентирование"},
            {"id": str(tourists.id), "name": "Туристы-5"},
        ]
    _authenticate_as(user_id)

    groups = _children_groups(client)
    assert groups[str(ivan_id)] == expected_ivan
    assert groups[str(maria_id)] == []
    for child_groups in groups.values():
        for group in child_groups:
            assert set(group) == {"id", "name"}


# --- negative -------------------------------------------------------------------


@pytest.mark.parametrize(
    "relationship",
    [
        {"status": "revoked"},
        {"valid_from": _LAST_YEAR, "valid_to": _YESTERDAY},
    ],
    ids=["revoked", "expired"],
)
def test_inactive_guardian_relationship_grants_nothing(
    client: TestClient, relationship: dict[str, object]
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child = _person(session)
        _relationship(session, guardian, child, **relationship)
        group = _group(session, club, "Туристы-5")
        _group_membership(session, group, _club_membership(session, club, child))
        session.commit()
        user_id, group_id, child_id = user.id, group.id, child.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == []
    _assert_hidden(client, group_id)
    assert str(child_id) not in _children_groups(client)


@pytest.mark.parametrize("club_membership_status", ["inactive", "suspended", "archived"])
def test_inactive_child_club_membership_grants_nothing(
    client: TestClient, club_membership_status: str
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child = _person(session)
        _relationship(session, guardian, child)
        child_cm = _club_membership(session, club, child, status=club_membership_status)
        group = _group(session, club, "Туристы-5")
        _group_membership(session, group, child_cm)
        session.commit()
        user_id, group_id, child_id = user.id, group.id, child.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == []
    _assert_hidden(client, group_id)
    assert _children_groups(client)[str(child_id)] == []


@pytest.mark.parametrize(
    "group_membership",
    [
        {"membership_status": "ended", "valid_to": _YESTERDAY},
        {"valid_from": _LAST_YEAR, "valid_to": _YESTERDAY},
    ],
    ids=["ended", "expired-interval"],
)
def test_ended_group_membership_grants_nothing(
    client: TestClient, group_membership: dict[str, object]
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        group = _group(session, club, "Туристы-5")
        _group_membership(session, group, child_cm, **group_membership)
        session.commit()
        user_id, group_id, child_id = user.id, group.id, child_cm.person_id
    _authenticate_as(user_id)

    assert _listed_ids(client) == []
    _assert_hidden(client, group_id)
    assert _children_groups(client)[str(child_id)] == []


def test_archived_group_is_not_readable(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        archived = _group(session, club, "Архивная", status="archived")
        _group_membership(session, archived, child_cm)
        session.commit()
        user_id, group_id = user.id, archived.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == []
    assert _listed_ids(client, status="archived") == []
    _assert_hidden(client, group_id)


def test_group_of_another_club_is_not_readable(client: TestClient) -> None:
    """Another Club's Group never authorizes — neither through the child's
    active ClubMembership in that other Club (the Guardian's role is
    scoped to their own Club), nor through a GroupMembership held via the
    child's ClubMembership in the Guardian's Club (Club mismatch)."""
    with session_scope() as session:
        club = _club(session)
        other_club = _club(session)
        user, guardian = _guardian(session, club)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        child = session.get(Person, child_cm.person_id)
        assert child is not None
        child_other_cm = _club_membership(session, other_club, child, membership_type="sport")
        foreign = _group(session, other_club, "Чужой клуб")
        _group_membership(session, foreign, child_other_cm)
        mismatched = _group(session, other_club, "Чужой клуб 2")
        _group_membership(session, mismatched, child_cm)
        own = _group(session, club, "Своя")
        _group_membership(session, own, child_cm)
        session.commit()
        user_id, own_id, child_id = user.id, own.id, child_cm.person_id
        foreign_ids = (foreign.id, mismatched.id)
    _authenticate_as(user_id)

    assert _listed_ids(client) == [str(own_id)]
    for foreign_id in foreign_ids:
        _assert_hidden(client, foreign_id)
    assert [group["id"] for group in _children_groups(client)[str(child_id)]] == [str(own_id)]


def test_child_of_another_guardian_grants_nothing_and_direct_idor_is_hidden(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        other_user, other_guardian = _guardian(session, club)
        own_child = _child(session, club, guardian, last_name="Свой")
        foreign_child = _child(session, club, other_guardian, last_name="Чужой")
        own = _group(session, club, "Своя")
        foreign = _group(session, club, "Чужая")
        _group_membership(session, own, own_child)
        _group_membership(session, foreign, foreign_child)
        session.commit()
        user_id, own_id, foreign_id = user.id, own.id, foreign.id
        foreign_child_id = foreign_child.person_id
        del other_user
    _authenticate_as(user_id)

    assert _listed_ids(client) == [str(own_id)]
    _assert_hidden(client, foreign_id)
    # Nothing a client sends can name another child: the projection never
    # contains the other Guardian's child, whatever is in the query string.
    response = client.get("/api/v1/me/children", params={"child_id": str(foreign_child_id)})
    assert str(foreign_child_id) not in {item["id"] for item in response.json()["items"]}
    assert client.get(
        "/api/v1/groups", params={"child_id": str(foreign_child_id), "page_size": 100}
    ).json()["items"] == [client.get(f"/api/v1/groups/{own_id}").json()]


def test_guardian_reads_no_roster_and_manages_nothing(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, guardian = _guardian(session, club)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        group = _group(session, club, "Туристы-5")
        _group_membership(session, group, child_cm)
        session.commit()
        user_id, group_id, club_id = user.id, group.id, club.id
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/groups/{group_id}").status_code == 200
    # Nested reads keep their own contracts: no participant roster.
    for nested in ("members", "instructors"):
        response = client.get(f"/api/v1/groups/{group_id}/{nested}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "group_not_found"
    headers = _csrf_headers(client)
    created = client.post(
        "/api/v1/groups",
        json={"club_id": str(club_id), "name": "Новая", "valid_from": _LONG_AGO.isoformat()},
        headers=headers,
    )
    assert created.status_code == 403
    updated = client.patch(f"/api/v1/groups/{group_id}", json={"name": "X"}, headers=headers)
    assert updated.status_code in (403, 404)
    archived = client.post(f"/api/v1/groups/{group_id}/archive", headers=headers)
    assert archived.status_code in (403, 404)
    with session_scope() as session:
        assert session.get(Group, group_id).name == "Туристы-5"  # type: ignore[union-attr]
        assert session.get(Group, group_id).status == "active"  # type: ignore[union-attr]


def test_guardian_without_the_guardian_role_reads_nothing(client: TestClient) -> None:
    """The relationship alone never authorizes: without the `group.read`
    grant there is no Group access and no `groups[]` context."""
    with session_scope() as session:
        club = _club(session)
        guardian = _person(session, "Родитель")
        user = _user(session, guardian)
        _grant_ad_hoc(session, user, "guardian_relationship.read", "children", None)
        child_cm = _child(session, club, guardian, last_name="Иванов")
        group = _group(session, club, "Туристы-5")
        _group_membership(session, group, child_cm)
        session.commit()
        user_id, group_id, child_id = user.id, group.id, child_cm.person_id
    _authenticate_as(user_id)

    assert _listed_ids(client) == []
    _assert_hidden(client, group_id)
    assert _children_groups(client)[str(child_id)] == []


# --- regression -----------------------------------------------------------------


def test_admin_access_is_unchanged(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        admin = _user(session, _person(session))
        _assign_role(session, admin, "admin", club)
        active = _group(session, club, "Активная")
        archived = _group(session, club, "Архивная", status="archived")
        session.commit()
        admin_id, active_id, archived_id = admin.id, active.id, archived.id
    _authenticate_as(admin_id)

    assert set(_listed_ids(client)) == {str(active_id), str(archived_id)}
    assert client.get(f"/api/v1/groups/{archived_id}").status_code == 200
    assert client.get(f"/api/v1/groups/{active_id}/members").status_code == 200


def test_instructor_own_groups_is_unchanged(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        instructor = _user(session, _person(session))
        _grant_ad_hoc(session, instructor, "group.read", "own_groups", club)
        assigned = _group(session, club, "Своя")
        session.add(
            GroupInstructorAssignment(
                group_id=assigned.id,
                user_id=instructor.id,
                role_in_group="instructor",
                valid_from=_LONG_AGO,
            )
        )
        other = _group(session, club, "Чужая")
        session.commit()
        instructor_id, assigned_id, other_id = instructor.id, assigned.id, other.id
    _authenticate_as(instructor_id)

    assert _listed_ids(client) == [str(assigned_id)]
    assert client.get(f"/api/v1/groups/{assigned_id}/members").status_code == 200
    _assert_hidden(client, other_id)


def test_member_self_is_unchanged_and_not_widened_by_children(client: TestClient) -> None:
    """A Member who is also a parent: `self` still means their own
    memberships; the child's Group arrives only with the `guardian` role."""
    with session_scope() as session:
        club = _club(session)
        person = _person(session, "Родитель")
        own_cm = _club_membership(session, club, person)
        user = _user(session, person)
        _assign_role(session, user, "member", club)
        own = _group(session, club, "Своя")
        _group_membership(session, own, own_cm)
        child_cm = _child(session, club, person, last_name="Ребёнок")
        childs = _group(session, club, "Детская")
        _group_membership(session, childs, child_cm)
        session.commit()
        user_id, own_id, childs_id = user.id, own.id, childs.id
    _authenticate_as(user_id)

    assert _listed_ids(client) == [str(own_id)]
    _assert_hidden(client, childs_id)

    with session_scope() as session:
        _assign_role(session, session.get(User, user_id), "guardian", None)  # type: ignore[arg-type]
        session.commit()
    assert sorted(_listed_ids(client)) == sorted([str(own_id), str(childs_id)])
