"""`GET /me/children` for the standard Guardian (PO decision, Issue #301;
role-permission-scope-matrix.md §6, people-api.md `GET /me/children`).

The canonical `guardian` role carries `guardian_relationship.read` with
scope `children` (migration d8e3b5f02a47). AUTH-2A requires every
`guardian` RoleAssignment to name a Club, while GuardianRelationship is
Club-neutral, so `children` is resolved Club-neutrally for this
projection: any currently-effective assignment counts, whatever its
`club_id`, and the result is still only the requester's OWN active,
interval-valid relationships.

Club-neutral permission resolution is not a global Guardian role: AUTH-2A
still rejects a global `guardian` assignment, `self`/`all` grants keep
the global-only rule, and Group/Event authorization keep their Club
boundaries. Every Guardian here is created through the real
`create_role_assignment` service with the migration-seeded role — no ad
hoc grant — except where a test deliberately builds a non-standard
fixture to pin a boundary.
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
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventGroupTarget
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app
from app.role_assignments.lifecycle import InvalidRoleAssignmentScopeError
from app.role_assignments.service import create_role_assignment

from .conftest import requires_postgres

pytestmark = requires_postgres

_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_NOW = datetime.datetime.now(datetime.timezone.utc)
_LAST_YEAR = _NOW - datetime.timedelta(days=365)
_YESTERDAY = _NOW - datetime.timedelta(days=1)
_NEXT_MONTH = _NOW + datetime.timedelta(days=30)


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


def _person(session: Session, last_name: str) -> Person:
    person = Person(last_name=last_name, first_name=f"P-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.flush()
    return person


def _club_membership(session: Session, club: Club, person: Person) -> ClubMembership:
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="student",
        status="active",
        joined_at=_LONG_AGO,
    )
    session.add(membership)
    session.flush()
    return membership


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


def _role_id(session: Session, code: str) -> uuid.UUID:
    return session.execute(select(Role.id).where(Role.code == code)).scalar_one()


def _standard_guardian(session: Session, club: Club, last_name: str = "Родитель") -> User:
    """The standard production Guardian: a Person with an active
    ClubMembership in `club`, a User, and ONE `club`-scoped `guardian`
    assignment created through the real service (which enforces AUTH-2A)."""
    person = _person(session, last_name)
    _club_membership(session, club, person)
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    create_role_assignment(
        session,
        user_id=user.id,
        role_id=_role_id(session, "guardian"),
        actor_user_id=user.id,
        club_id=club.id,
    )
    return user


def _person_of(session: Session, user: User) -> Person:
    person = session.get(Person, user.person_id)
    assert person is not None
    return person


def _group_with_child(session: Session, club: Club, child_cm: ClubMembership) -> Group:
    group = Group(club_id=club.id, name=f"G-{uuid.uuid4().hex[:6]}", status="active",
                  valid_from=_LONG_AGO)
    session.add(group)
    session.flush()
    session.add(
        GroupMembership(
            group_id=group.id,
            club_membership_id=child_cm.id,
            membership_status="active",
            valid_from=_LONG_AGO,
        )
    )
    session.flush()
    return group


def _event_targeting(session: Session, club: Club, group: Group) -> Event:
    event = Event(
        club_id=club.id,
        event_type="trip",
        title=f"E-{uuid.uuid4().hex[:6]}",
        start_at=_NEXT_MONTH,
        end_at=_NEXT_MONTH + datetime.timedelta(hours=4),
        timezone="Europe/Moscow",
        status="published",
    )
    session.add(event)
    session.flush()
    session.add(
        EventOccurrence(
            event_id=event.id,
            series_id=None,
            club_id=club.id,
            name=event.title,
            event_type=event.event_type,
            recurrence_anchor_at=event.start_at,
            starts_at=event.start_at,
            ends_at=event.end_at,
            timezone=event.timezone,
            status="scheduled",
        )
    )
    session.add(EventGroupTarget(event_id=event.id, group_id=group.id, valid_from=_LONG_AGO))
    session.flush()
    return event


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _children_ids(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/v1/me/children", params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# --- 1. standard Guardian ---------------------------------------------------------


def test_standard_guardian_sees_own_child_through_the_seeded_role_grant(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user = _standard_guardian(session, club)
        child = _person(session, "Ребёнок")
        _relationship(session, _person_of(session, user), child)
        session.commit()
        user_id, child_id = user.id, child.id
        # The only grants are the canonical role's: one club-scoped
        # assignment, nothing ad hoc.
        assignments = session.execute(
            select(Role.code, UserRoleAssignment.club_id)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .where(UserRoleAssignment.user_id == user_id)
        ).all()
        assert assignments == [("guardian", club.id)]
    _authenticate_as(user_id)

    assert _children_ids(client) == [str(child_id)]


def test_several_children_are_all_returned(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user = _standard_guardian(session, club)
        guardian = _person_of(session, user)
        first, second = _person(session, "Анна"), _person(session, "Борис")
        _relationship(session, guardian, first)
        _relationship(session, guardian, second, relationship_type="legal_representative")
        session.commit()
        user_id, expected = user.id, {str(first.id), str(second.id)}
    _authenticate_as(user_id)

    assert set(_children_ids(client)) == expected


# --- 2. another Guardian -------------------------------------------------------------


def test_guardian_never_sees_another_guardians_children(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user = _standard_guardian(session, club, "Первый")
        other = _standard_guardian(session, club, "Второй")
        own_child, foreign_child = _person(session, "Свой"), _person(session, "Чужой")
        _relationship(session, _person_of(session, user), own_child)
        _relationship(session, _person_of(session, other), foreign_child)
        session.commit()
        user_id, other_id = user.id, other.id
        own_id, foreign_id = own_child.id, foreign_child.id
    _authenticate_as(user_id)
    # No client-supplied id can redirect the projection.
    for params in ({}, {"person_id": str(foreign_id)}, {"guardian_id": str(other_id)},
                   {"child_id": str(foreign_id)}):
        assert _children_ids(client, **params) == [str(own_id)]

    _authenticate_as(other_id)
    assert _children_ids(client) == [str(foreign_id)]


# --- 3/4. inactive relationships ------------------------------------------------------


@pytest.mark.parametrize(
    "relationship",
    [
        {"valid_from": _LAST_YEAR, "valid_to": _YESTERDAY},
        {"status": "inactive"},
        {"status": "revoked"},
        {"valid_from": _NEXT_MONTH},
    ],
    ids=["expired", "inactive", "revoked", "not-yet-valid"],
)
def test_non_current_relationship_returns_no_child(
    client: TestClient, relationship: dict[str, object]
) -> None:
    with session_scope() as session:
        club = _club(session)
        user = _standard_guardian(session, club)
        child = _person(session, "Ребёнок")
        _relationship(session, _person_of(session, user), child, **relationship)
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    assert _children_ids(client) == []


def test_expired_guardian_role_assignment_returns_no_child(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user = _standard_guardian(session, club)
        _relationship(session, _person_of(session, user), _person(session, "Ребёнок"))
        assignment = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
        ).scalar_one()
        assignment.valid_to = _YESTERDAY
        assignment.valid_from = _LAST_YEAR
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    assert _children_ids(client) == []


# --- 5. Club boundary -----------------------------------------------------------------


def test_club_neutral_children_resolution_keeps_group_and_event_club_boundaries(
    client: TestClient,
) -> None:
    """Guardian assigned in Club A; their child belongs to Club B only.
    `/me/children` lists the child (GuardianRelationship is Club-neutral),
    but the child's Club B Group and the Event targeting it stay hidden:
    the Club A assignment's `group.read`/`event.read` never cross Clubs."""
    with session_scope() as session:
        club_a, club_b = _club(session), _club(session)
        user = _standard_guardian(session, club_a)
        child = _person(session, "Ребёнок")
        _relationship(session, _person_of(session, user), child)
        group_b = _group_with_child(session, club_b, _club_membership(session, club_b, child))
        event_b = _event_targeting(session, club_b, group_b)
        session.commit()
        user_id, child_id, group_id, event_id = user.id, child.id, group_b.id, event_b.id
    _authenticate_as(user_id)

    response = client.get("/api/v1/me/children")
    assert [(item["id"], item["groups"]) for item in response.json()["items"]] == [
        (str(child_id), [])
    ]
    assert client.get("/api/v1/groups").json()["items"] == []
    assert client.get(f"/api/v1/groups/{group_id}").status_code == 404
    assert client.get(f"/api/v1/events/{event_id}").status_code == 404


def test_same_club_group_and_event_access_is_unchanged(client: TestClient) -> None:
    """Regression: with the child in the Guardian's own Club, `group.read
    (children)` and Guardian `event.read(children)` work exactly as before."""
    with session_scope() as session:
        club = _club(session)
        user = _standard_guardian(session, club)
        child = _person(session, "Ребёнок")
        _relationship(session, _person_of(session, user), child)
        group = _group_with_child(session, club, _club_membership(session, club, child))
        event = _event_targeting(session, club, group)
        session.commit()
        user_id, child_id, group_id, event_id = user.id, child.id, group.id, event.id
        group_name = group.name
    _authenticate_as(user_id)

    response = client.get("/api/v1/me/children")
    assert response.json()["items"][0]["id"] == str(child_id)
    assert response.json()["items"][0]["groups"] == [{"id": str(group_id), "name": group_name}]
    assert [item["id"] for item in client.get("/api/v1/groups").json()["items"]] == [
        str(group_id)
    ]
    assert client.get(f"/api/v1/groups/{group_id}").status_code == 200
    assert client.get(f"/api/v1/events/{event_id}").status_code == 200


# --- AUTH-2A unchanged / resolution is scope-specific ---------------------------------


def test_guardian_role_still_cannot_be_assigned_globally() -> None:
    """AUTH-2A: Club-neutral resolution is not a global Guardian role."""
    with session_scope() as session:
        person = _person(session, "Родитель")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add(user)
        session.flush()
        with pytest.raises(InvalidRoleAssignmentScopeError):
            create_role_assignment(
                session,
                user_id=user.id,
                role_id=_role_id(session, "guardian"),
                actor_user_id=user.id,
                club_id=None,
            )


def test_club_scoped_self_grant_still_does_not_match(client: TestClient) -> None:
    """Only `children` resolves Club-neutrally; a club-scoped `self`
    `guardian_relationship.read` grant keeps the global-only rule (a
    deliberately non-standard fixture — no `guardian` role at all)."""
    with session_scope() as session:
        club = _club(session)
        person = _person(session, "Родитель")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add(user)
        session.flush()
        permission = session.execute(
            select(Permission).where(Permission.code == "guardian_relationship.read")
        ).scalar_one()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.flush()
        session.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                scopes=[RolePermissionScope(scope_type="self")],
            )
        )
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
        _relationship(session, person, _person(session, "Ребёнок"))
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    assert _children_ids(client) == []


def test_user_without_guardian_role_gets_no_children(client: TestClient) -> None:
    """The relationship alone never authorizes: a Member with an active
    GuardianRelationship but no `guardian` role sees no children."""
    with session_scope() as session:
        club = _club(session)
        person = _person(session, "Родитель")
        _club_membership(session, club, person)
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add(user)
        session.flush()
        create_role_assignment(
            session,
            user_id=user.id,
            role_id=_role_id(session, "member"),
            actor_user_id=user.id,
            club_id=club.id,
        )
        _relationship(session, person, _person(session, "Ребёнок"))
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    assert _children_ids(client) == []
