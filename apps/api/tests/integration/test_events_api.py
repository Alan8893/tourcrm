"""HTTP-level integration tests for /api/v1/events (Issue #40): deterministic
backend authorization, canonical scope resolution (`all`/`own_groups`/
`own_events`/`self`/`children`/`none`), cross-Club ownership enforcement
(ADR-0022), ADR-0018 lifecycle transitions, and IDOR regression coverage.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching the pattern established by
tests/integration/test_authentication_api.py. Authentication is
substituted via `app.dependency_overrides[get_current_principal]`
(the same "minimal test authentication abstraction" already used by
tests/integration/test_authorization_enforcement.py) — no real
register/login flow is needed since these tests exercise authorization,
not authentication.

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
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
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


# --- fixtures / factories --------------------------------------------------


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
    # `person=person` (relationship), not `person_id=person.id`: this helper
    # is sometimes called before `person` has been flushed (its `.id` is
    # still None at that point) — SQLAlchemy's unit-of-work resolves the FK
    # from the relationship at flush time regardless of construction order.
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
        "membership_type": "student",
        "status": "active",
        "joined_at": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Test event",
        "start_at": _utc(2026, 9, 20, 17, 0),
        "end_at": _utc(2026, 9, 20, 19, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


_EVENT_TO_OCCURRENCE_STATUS = {
    "draft": "scheduled",
    "published": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
}


def _make_event_occurrence_for(event: Event) -> EventOccurrence:
    """ADR-0033: every Event has exactly one linked EventOccurrence — a
    test that constructs an Event directly (bypassing create_event) and
    then exercises update/status-transition/archive must also create the
    matching occurrence, mirroring that invariant."""
    return EventOccurrence(  # type: ignore[arg-type]
        event_id=event.id,
        series_id=None,
        club_id=event.club_id,
        name=event.title,
        description=event.description,
        event_type=event.event_type,
        recurrence_anchor_at=event.start_at,
        starts_at=event.start_at,
        ends_at=event.end_at,
        timezone=event.timezone,
        status=_EVENT_TO_OCCURRENCE_STATUS.get(event.status, "scheduled"),
        cancellation_reason=event.cancellation_reason,
    )


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


def _make_event_staff_assignment(
    event: Event, user: User, **overrides: object
) -> EventStaffAssignment:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "user_id": user.id,
        "role_in_event": "instructor",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return EventStaffAssignment(**defaults)  # type: ignore[arg-type]


def _make_event_group_target(
    event: Event, group: Group, **overrides: object
) -> EventGroupTarget:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "group_id": group.id,
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return EventGroupTarget(**defaults)  # type: ignore[arg-type]


def _make_event_participation(
    event: Event, person: Person, **overrides: object
) -> EventParticipation:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "person_id": person.id,
        "registration_status": "registered",
    }
    defaults.update(overrides)
    return EventParticipation(**defaults)  # type: ignore[arg-type]


def _make_guardian_relationship(
    guardian: Person, child: Person, **overrides: object
) -> GuardianRelationship:
    defaults: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GuardianRelationship(**defaults)  # type: ignore[arg-type]


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


# --- create -----------------------------------------------------------------


@requires_postgres
def test_create_event_with_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json={
            "club_id": str(club_id),
            "event_type": "lesson",
            "title": "Orienteering",
            "start_at": "2026-09-20T17:00:00+03:00",
            "end_at": "2026-09-20T19:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["club_id"] == str(club_id)
    assert body["status"] == "draft"
    assert body["cancellation_reason"] is None
    assert body["created_by"] == str(user_id)
    assert body["updated_by"] == str(user_id)


@requires_postgres
def test_create_event_without_permission_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_id, user_id = club.id, user.id
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json={
            "club_id": str(club_id),
            "event_type": "lesson",
            "title": "Orienteering",
            "start_at": "2026-09-20T17:00:00+03:00",
            "end_at": "2026-09-20T19:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_create_event_club_scoped_assignment_for_other_club_is_forbidden(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club_a, club_b, person, user])
        session.commit()
        club_a_id, club_b_id, user_id = club_a.id, club_b.id, user.id
    # Assignment grants event.create only within club_a.
    _grant_permission(user_id, "event.create", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json={
            "club_id": str(club_b_id),
            "event_type": "lesson",
            "title": "Orienteering",
            "start_at": "2026-09-20T17:00:00+03:00",
            "end_at": "2026-09-20T19:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


@requires_postgres
def test_create_event_with_nonexistent_club_id_returns_422(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json={
            "club_id": str(uuid.uuid4()),
            "event_type": "lesson",
            "title": "Orienteering",
            "start_at": "2026-09-20T17:00:00+03:00",
            "end_at": "2026-09-20T19:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_club_id"


@requires_postgres
def test_create_event_rejects_invalid_time_range(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json={
            "club_id": str(club_id),
            "event_type": "lesson",
            "title": "Orienteering",
            "start_at": "2026-09-20T19:00:00+03:00",
            "end_at": "2026-09-20T17:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_event_data"


# --- detail / IDOR -----------------------------------------------------------


@requires_postgres
def test_get_event_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(event_id)


@requires_postgres
def test_get_event_none_scope_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="none")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_get_event_no_assignment_at_all_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_get_event_nonexistent_id_returns_identical_404(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    real_missing = client.get(f"/api/v1/events/{uuid.uuid4()}")
    assert real_missing.status_code == 404
    assert real_missing.json()["error"]["code"] == "not_found"


@requires_postgres
def test_instructor_own_events_scope_sees_assigned_event_but_not_unrelated_event(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        assigned_event = _make_event(club)
        unrelated_event = _make_event(club)
        session.add_all([assigned_event, unrelated_event])
        session.commit()
        session.add(_make_event_staff_assignment(assigned_event, user))
        session.commit()
        user_id = user.id
        assigned_id, unrelated_id = assigned_event.id, unrelated_event.id
    _grant_permission(user_id, "event.read", scope_type="own_events")
    _authenticate_as(user_id)

    ok = client.get(f"/api/v1/events/{assigned_id}")
    assert ok.status_code == 200

    # IDOR: swapping the path id to an unrelated Event must not leak access.
    denied = client.get(f"/api/v1/events/{unrelated_id}")
    assert denied.status_code == 404


@requires_postgres
def test_own_groups_scope_sees_event_targeted_to_responsible_group(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="own_groups")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200


@requires_postgres
def test_own_groups_scope_denies_when_not_the_responsible_instructor(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()
        session.add(_make_event_group_target(event, group))
        # No GroupInstructorAssignment for this user at all.
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="own_groups")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_own_groups_scope_enforces_group_club_matches_event_club(
    client: TestClient,
) -> None:
    """ADR-0022: even if an EventGroupTarget row exists linking a Group in
    a different Club to this Event (bypassing app.events.service, exactly
    as its own docstring says is possible for a direct ORM write), the
    `own_groups` scope predicate must still refuse to treat this as
    responsibility over the Event — the mandatory Group.club_id ==
    Event.club_id check must never be skipped.
    """
    with session_scope() as session:
        event_club = _make_club()
        group_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([event_club, group_club, person, user])
        session.commit()
        event = _make_event(event_club)
        group = _make_group(group_club)
        session.add_all([event, group])
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        user_id, event_id = user.id, event.id
    # Deliberately a global (club_id=None) assignment so only the
    # in-predicate Club check below can protect against this.
    _grant_permission(user_id, "event.read", scope_type="own_groups")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_self_scope_sees_event_with_own_participation_but_not_others(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        participated_event = _make_event(club)
        other_event = _make_event(club)
        session.add_all([participated_event, other_event])
        session.commit()
        session.add(_make_event_participation(participated_event, person))
        session.commit()
        user_id = user.id
        participated_id, other_id = participated_event.id, other_event.id
    _grant_permission(user_id, "event.read", scope_type="self")
    _authenticate_as(user_id)

    ok = client.get(f"/api/v1/events/{participated_id}")
    assert ok.status_code == 200
    denied = client.get(f"/api/v1/events/{other_id}")
    assert denied.status_code == 404


@requires_postgres
def test_children_scope_full_chain_via_participation_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        child_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, child_person, guardian_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_club_membership(club, guardian_person))
        session.add(_make_club_membership(club, child_person))
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.add(_make_event_participation(event, child_person))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200


@requires_postgres
def test_children_scope_full_chain_via_group_target_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        child_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, child_person, guardian_user])
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()
        guardian_membership = _make_club_membership(club, guardian_person)
        child_membership = _make_club_membership(club, child_person)
        session.add_all([guardian_membership, child_membership])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.add(_make_group_membership(group, child_membership))
        session.add(_make_event_group_target(event, group))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200


@requires_postgres
@pytest.mark.parametrize("relationship_status", ["inactive", "revoked"])
def test_children_scope_denies_when_relationship_not_active(
    client: TestClient, relationship_status: str
) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        child_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, child_person, guardian_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_club_membership(club, guardian_person))
        session.add(_make_club_membership(club, child_person))
        session.add(
            _make_guardian_relationship(
                guardian_person, child_person, status=relationship_status
            )
        )
        session.add(_make_event_participation(event, child_person))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_children_scope_denies_when_child_has_no_membership_in_events_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        child_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, child_person, guardian_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_club_membership(club, guardian_person))
        # No ClubMembership at all for child_person in this Club.
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.add(_make_event_participation(event, child_person))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_children_scope_denies_when_guardian_has_no_membership_in_events_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        child_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, child_person, guardian_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        # No ClubMembership at all for guardian_person in this Club.
        session.add(_make_club_membership(club, child_person))
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.add(_make_event_participation(event, child_person))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


@requires_postgres
def test_children_scope_denies_for_unrelated_child(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        unrelated_child_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, unrelated_child_person, guardian_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_club_membership(club, guardian_person))
        session.add(_make_club_membership(club, unrelated_child_person))
        # No GuardianRelationship between guardian_person and this child.
        session.add(_make_event_participation(event, unrelated_child_person))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404


# --- list --------------------------------------------------------------------


@requires_postgres
def test_list_events_all_scope_returns_all_club_events(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        events = [_make_event(club, title=f"Event {i}") for i in range(3)]
        session.add_all(events)
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/events")
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 3
    assert len(body["items"]) == 3


@requires_postgres
def test_list_events_none_scope_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_event(club))
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="none")
    _authenticate_as(user_id)

    response = client.get("/api/v1/events")
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 0
    assert body["items"] == []


@requires_postgres
def test_list_events_own_events_scope_filters_to_assigned_events_only(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        assigned = _make_event(club, title="Assigned")
        unrelated = _make_event(club, title="Unrelated")
        session.add_all([assigned, unrelated])
        session.commit()
        session.add(_make_event_staff_assignment(assigned, user))
        session.commit()
        user_id, assigned_id = user.id, assigned.id
    _grant_permission(user_id, "event.read", scope_type="own_events")
    _authenticate_as(user_id)

    response = client.get("/api/v1/events")
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == str(assigned_id)


@requires_postgres
def test_list_events_children_scope_filters_to_related_childs_events_only(
    client: TestClient,
) -> None:
    """Regression for a list-level correlation defect in `_child_condition`
    (app.events.authorization): the two EXISTS subqueries nested two levels
    deep inside `eligible_child_exists` (`child_via_participation`/
    `child_via_group_target`) were not correlated to the outer `Event`
    being tested when embedded in `event_visibility_filter`'s per-row
    query (unlike the already-covered single-Event `GET /events/{id}`
    path, where `event_id`/`event_club_id` are literal values and no
    correlation is needed at all) — SQLAlchemy's automatic correlation
    only reaches one enclosing SELECT by default. Uncorrelated, both
    subqueries degenerated into an unrestricted cross join satisfied by
    any Event in the same Club, so a guardian's `children` scope silently
    became unrestricted `all` for the list endpoint — exactly what
    ADR-0020 §"Consequences" prohibits ("Guardian children scope never
    becomes unrestricted all"). Fixed with explicit `.correlate(...)` on
    both subqueries.
    """
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        session.add_all([club, guardian_person, guardian_user, child_person])
        session.commit()
        session.add_all(
            [
                _make_club_membership(club, guardian_person),
                _make_club_membership(club, child_person),
            ]
        )
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.commit()
        childs_event = _make_event(club, title="childs")
        unrelated_event = _make_event(club, title="unrelated")
        session.add_all([childs_event, unrelated_event])
        session.commit()
        session.add(_make_event_participation(childs_event, child_person))
        session.commit()
        guardian_user_id = guardian_user.id
        childs_event_id, unrelated_event_id = childs_event.id, unrelated_event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events")
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert [item["id"] for item in body["items"]] == [str(childs_event_id)]
    assert str(unrelated_event_id) not in {item["id"] for item in body["items"]}


@requires_postgres
def test_list_events_cross_club_assignment_does_not_leak_other_club_events(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club_a, club_b, person, user])
        session.commit()
        event_a = _make_event(club_a, title="In club A")
        event_b = _make_event(club_b, title="In club B")
        session.add_all([event_a, event_b])
        session.commit()
        user_id, club_a_id, event_a_id = user.id, club_a.id, event_a.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events")
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == str(event_a_id)


@requires_postgres
def test_list_events_pagination(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        for i in range(5):
            session.add(
                _make_event(
                    club,
                    title=f"Event {i}",
                    start_at=_utc(2026, 9, 20 + i, 10),
                    end_at=_utc(2026, 9, 20 + i, 12),
                )
            )
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    page_1 = client.get("/api/v1/events", params={"page": 1, "page_size": 2}).json()
    assert page_1["pagination"]["total"] == 5
    assert page_1["pagination"]["pages"] == 3
    assert len(page_1["items"]) == 2

    page_3 = client.get("/api/v1/events", params={"page": 3, "page_size": 2}).json()
    assert len(page_3["items"]) == 1


# --- update ------------------------------------------------------------------


@requires_postgres
def test_update_event_with_permission_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/events/{event_id}",
        json={"title": "Updated title"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Updated title"
    assert response.json()["updated_by"] == str(user_id)


@requires_postgres
def test_update_event_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/events/{event_id}",
        json={"title": "Updated title"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


@requires_postgres
def test_update_event_cross_club_scope_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club_a, club_b, person, user])
        session.commit()
        event_b = _make_event(club_b)
        session.add(event_b)
        session.commit()
        user_id, club_a_id, event_b_id = user.id, club_a.id, event_b.id
    _grant_permission(user_id, "event.update", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/events/{event_b_id}",
        json={"title": "Should not apply"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


@requires_postgres
def test_update_event_rejects_null_required_field(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/events/{event_id}",
        json={"title": None},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_event_data"


@requires_postgres
def test_update_event_ignores_status_field_in_body(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/events/{event_id}",
        json={"status": "cancelled", "title": "Still draft"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "draft"


# --- targeting (TH-0108 / ADR-0037 §1-§2) ------------------------------------


def _create_event_payload(club_id: uuid.UUID, **overrides: object) -> dict:
    payload = {
        "club_id": str(club_id),
        "event_type": "lesson",
        "title": "Orienteering",
        "start_at": "2026-09-20T17:00:00+03:00",
        "end_at": "2026-09-20T19:00:00+03:00",
        "timezone": "Europe/Moscow",
    }
    payload.update(overrides)
    return payload


@requires_postgres
def test_create_event_with_no_groups_is_club_wide(client: TestClient) -> None:
    """Test 1: an Event created without `group_ids` is club-wide — no
    EventGroupTarget row is created, and the response reflects this."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events", json=_create_event_payload(club_id), headers=_csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["group_ids"] == []
    assert body["instructor_ids"] == []

    with session_scope() as session:
        targets = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == uuid.UUID(body["id"]))
        ).scalars().all()
        assert targets == []


@requires_postgres
def test_create_event_with_one_group_creates_target(client: TestClient) -> None:
    """Test 2: an Event created with one `group_ids` entry creates
    exactly one active EventGroupTarget for it."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, group_ids=[str(group_id)]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["group_ids"] == [str(group_id)]

    with session_scope() as session:
        targets = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == uuid.UUID(body["id"]))
        ).scalars().all()
        assert len(targets) == 1
        assert targets[0].group_id == group_id
        assert targets[0].valid_to is None


@requires_postgres
def test_create_event_with_multiple_groups_creates_all_targets(client: TestClient) -> None:
    """Test 3: an Event created with several `group_ids` targets all of
    them."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([group_a, group_b])
        session.commit()
        club_id, user_id = club.id, user.id
        group_ids = {group_a.id, group_b.id}
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, group_ids=[str(gid) for gid in group_ids]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(uuid.UUID(gid) for gid in body["group_ids"]) == group_ids

    with session_scope() as session:
        targets = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == uuid.UUID(body["id"]))
        ).scalars().all()
        assert {t.group_id for t in targets} == group_ids


@requires_postgres
def test_create_event_with_cross_club_group_is_rejected(client: TestClient) -> None:
    """Test 4: a Group belonging to a different Club than the Event is
    rejected — backend-enforced, not a frontend-only validation."""
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        foreign_group = _make_group(other_club)
        session.add(foreign_group)
        session.commit()
        club_id, user_id, foreign_group_id = club.id, user.id, foreign_group.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(
            club_id, title="Cross-club attempt", group_ids=[str(foreign_group_id)]
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "group_club_mismatch"


@requires_postgres
def test_create_event_with_instructor_assignment_succeeds(client: TestClient) -> None:
    """Tests 5 & 7: a User with active ClubMembership in the Event's
    Club can be assigned as a responsible instructor via `instructor_ids`
    — creating exactly one active EventStaffAssignment."""
    with session_scope() as session:
        club = _make_club()
        creator_person = _make_person()
        creator = _make_user(creator_person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all([club, creator_person, creator, instructor_person, instructor])
        session.commit()
        session.add(_make_club_membership(club, instructor_person))
        session.commit()
        club_id, creator_id, instructor_id = club.id, creator.id, instructor.id
    _grant_permission(creator_id, "event.create", scope_type="all")
    _authenticate_as(creator_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, instructor_ids=[str(instructor_id)]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["instructor_ids"] == [str(instructor_id)]

    with session_scope() as session:
        assignments = session.execute(
            select(EventStaffAssignment).where(
                EventStaffAssignment.event_id == uuid.UUID(body["id"])
            )
        ).scalars().all()
        assert len(assignments) == 1
        assert assignments[0].user_id == instructor_id
        assert assignments[0].role_in_event == "instructor"
        assert assignments[0].is_primary is False


@requires_postgres
def test_create_event_with_instructor_without_active_membership_is_rejected(
    client: TestClient,
) -> None:
    """Test 6: a User with no ClubMembership at all in the Event's Club
    cannot be assigned."""
    with session_scope() as session:
        club = _make_club()
        creator_person = _make_person()
        creator = _make_user(creator_person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all([club, creator_person, creator, instructor_person, instructor])
        session.commit()
        club_id, creator_id, instructor_id = club.id, creator.id, instructor.id
    _grant_permission(creator_id, "event.create", scope_type="all")
    _authenticate_as(creator_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, instructor_ids=[str(instructor_id)]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "instructor_club_membership_missing"


@requires_postgres
def test_create_event_with_instructor_from_another_club_is_rejected(client: TestClient) -> None:
    """Test 8: a User whose only active ClubMembership is in a different
    Club cannot be assigned — cross-Club assignment stays forbidden."""
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        creator_person = _make_person()
        creator = _make_user(creator_person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all(
            [club, other_club, creator_person, creator, instructor_person, instructor]
        )
        session.commit()
        session.add(_make_club_membership(other_club, instructor_person))
        session.commit()
        club_id, creator_id, instructor_id = club.id, creator.id, instructor.id
    _grant_permission(creator_id, "event.create", scope_type="all")
    _authenticate_as(creator_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, instructor_ids=[str(instructor_id)]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "instructor_club_membership_missing"


@requires_postgres
def test_group_targeting_does_not_create_participation(client: TestClient) -> None:
    """Test 9: targeting a Group is audience selection only — it never
    creates EventParticipation, even for members of that Group."""
    with session_scope() as session:
        club = _make_club()
        creator_person = _make_person()
        creator = _make_user(creator_person)
        member_person = _make_person()
        session.add_all([club, creator_person, creator, member_person])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        club_membership = _make_club_membership(club, member_person)
        session.add(club_membership)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()
        club_id, creator_id, group_id = club.id, creator.id, group.id
    _grant_permission(creator_id, "event.create", scope_type="all")
    _authenticate_as(creator_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, group_ids=[str(group_id)]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        participations = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == uuid.UUID(response.json()["id"])
            )
        ).scalars().all()
        assert participations == []


@requires_postgres
def test_instructor_assignment_does_not_create_group_membership(client: TestClient) -> None:
    """Test 10: assigning a responsible instructor never creates
    GroupInstructorAssignment or GroupMembership — an instructor may be
    responsible for an Event without being a Group instructor."""
    with session_scope() as session:
        club = _make_club()
        creator_person = _make_person()
        creator = _make_user(creator_person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all([club, creator_person, creator, instructor_person, instructor])
        session.commit()
        session.add(_make_club_membership(club, instructor_person))
        session.commit()
        club_id, creator_id, instructor_id = club.id, creator.id, instructor.id
    _grant_permission(creator_id, "event.create", scope_type="all")
    _authenticate_as(creator_id)

    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, instructor_ids=[str(instructor_id)]),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    with session_scope() as session:
        instructor_assignments = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.user_id == instructor_id
            )
        ).scalars().all()
        assert instructor_assignments == []
        group_memberships = session.execute(select(GroupMembership)).scalars().all()
        assert group_memberships == []


@requires_postgres
def test_repeated_identical_update_does_not_duplicate_targeting(client: TestClient) -> None:
    """Test 11: PATCHing the same `group_ids`/`instructor_ids` twice in a
    row must not create a second active row for the same (event, group)
    or (event, user) pair — the sync stays idempotent."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all([club, person, user, instructor_person, instructor])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_club_membership(club, instructor_person))
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id, group_id, instructor_id = user.id, event.id, group.id, instructor.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    payload = {"group_ids": [str(group_id)], "instructor_ids": [str(instructor_id)]}
    first = client.patch(
        f"/api/v1/events/{event_id}", json=payload, headers=_csrf_headers(client)
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/api/v1/events/{event_id}", json=payload, headers=_csrf_headers(client)
    )
    assert second.status_code == 200, second.text
    assert second.json()["group_ids"] == [str(group_id)]
    assert second.json()["instructor_ids"] == [str(instructor_id)]

    with session_scope() as session:
        targets = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == event_id)
        ).scalars().all()
        assert len(targets) == 1
        assignments = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.event_id == event_id)
        ).scalars().all()
        assert len(assignments) == 1


@requires_postgres
def test_removing_a_group_ends_its_target(client: TestClient) -> None:
    """Test 12: dropping a Group from `group_ids` on update ends
    (`valid_to`) the corresponding EventGroupTarget — it is never
    deleted, matching this codebase's historical-interval convention."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([group_a, group_b])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id = user.id, event.id
        group_a_id, group_b_id = group_a.id, group_b.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    setup = client.patch(
        f"/api/v1/events/{event_id}",
        json={"group_ids": [str(group_a_id), str(group_b_id)]},
        headers=_csrf_headers(client),
    )
    assert setup.status_code == 200, setup.text

    drop_b = client.patch(
        f"/api/v1/events/{event_id}",
        json={"group_ids": [str(group_a_id)]},
        headers=_csrf_headers(client),
    )
    assert drop_b.status_code == 200, drop_b.text
    assert drop_b.json()["group_ids"] == [str(group_a_id)]

    with session_scope() as session:
        target_b = session.execute(
            select(EventGroupTarget).where(
                EventGroupTarget.event_id == event_id, EventGroupTarget.group_id == group_b_id
            )
        ).scalar_one()
        assert target_b.valid_to is not None
        target_a = session.execute(
            select(EventGroupTarget).where(
                EventGroupTarget.event_id == event_id, EventGroupTarget.group_id == group_a_id
            )
        ).scalar_one()
        assert target_a.valid_to is None


@requires_postgres
def test_removing_an_instructor_ends_its_assignment(client: TestClient) -> None:
    """Test 13: dropping a User from `instructor_ids` on update ends the
    corresponding EventStaffAssignment."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all([club, person, user, instructor_person, instructor])
        session.commit()
        session.add(_make_club_membership(club, instructor_person))
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id, instructor_id = user.id, event.id, instructor.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    setup = client.patch(
        f"/api/v1/events/{event_id}",
        json={"instructor_ids": [str(instructor_id)]},
        headers=_csrf_headers(client),
    )
    assert setup.status_code == 200, setup.text

    drop = client.patch(
        f"/api/v1/events/{event_id}",
        json={"instructor_ids": []},
        headers=_csrf_headers(client),
    )
    assert drop.status_code == 200, drop.text
    assert drop.json()["instructor_ids"] == []

    with session_scope() as session:
        assignment = session.execute(
            select(EventStaffAssignment).where(
                EventStaffAssignment.event_id == event_id,
                EventStaffAssignment.user_id == instructor_id,
            )
        ).scalar_one()
        assert assignment.valid_to is not None


@requires_postgres
def test_create_event_rolls_back_entirely_on_group_target_failure(client: TestClient) -> None:
    """Test 14 (create): if targeting fails (cross-Club Group), the
    Event itself must not remain in the database either — no partially
    created Event."""
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        foreign_group = _make_group(other_club)
        session.add(foreign_group)
        session.commit()
        club_id, user_id, foreign_group_id = club.id, user.id, foreign_group.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _authenticate_as(user_id)

    unique_title = f"Atomicity check {uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/events",
        json=_create_event_payload(
            club_id, title=unique_title, group_ids=[str(foreign_group_id)]
        ),
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text

    with session_scope() as session:
        orphaned = session.execute(
            select(Event).where(Event.title == unique_title)
        ).scalars().all()
        assert orphaned == []


@requires_postgres
def test_update_event_rolls_back_field_changes_on_group_target_failure(
    client: TestClient,
) -> None:
    """Test 14 (update): if a PATCH's targeting fails, its field changes
    (title, here) must also roll back — the two are one transaction."""
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        foreign_group = _make_group(other_club)
        session.add(foreign_group)
        session.commit()
        event = _make_event(club, title="Original title")
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id, foreign_group_id = user.id, event.id, foreign_group.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/events/{event_id}",
        json={"title": "Should not apply", "group_ids": [str(foreign_group_id)]},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text

    with session_scope() as session:
        event = session.execute(select(Event).where(Event.id == event_id)).scalar_one()
        assert event.title == "Original title"


@requires_postgres
def test_calendar_group_filter_sees_targeted_event(client: TestClient) -> None:
    """Test 15: an Event created (and published) with a target Group is
    found by Calendar's `group_id` filter, via the same EventGroupTarget
    row this endpoint wrote."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _grant_permission(user_id, "event.update", scope_type="all")
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    created = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, group_ids=[str(group_id)]),
        headers=_csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    publish = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "published"},
        headers=_csrf_headers(client),
    )
    assert publish.status_code == 200, publish.text

    response = client.get(
        "/api/v1/events/calendar",
        params={
            "from": "2026-09-01T00:00:00Z",
            "to": "2026-10-01T00:00:00Z",
            "group_id": str(group_id),
        },
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert event_id in ids


@requires_postgres
def test_calendar_instructor_filter_sees_assigned_event(client: TestClient) -> None:
    """Test 16: an Event created (and published) with a responsible
    instructor is found by Calendar's `user_id` filter."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        instructor_person = _make_person()
        instructor = _make_user(instructor_person)
        session.add_all([club, person, user, instructor_person, instructor])
        session.commit()
        session.add(_make_club_membership(club, instructor_person))
        session.commit()
        club_id, user_id, instructor_id = club.id, user.id, instructor.id
    _grant_permission(user_id, "event.create", scope_type="all")
    _grant_permission(user_id, "event.update", scope_type="all")
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    created = client.post(
        "/api/v1/events",
        json=_create_event_payload(club_id, instructor_ids=[str(instructor_id)]),
        headers=_csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    publish = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "published"},
        headers=_csrf_headers(client),
    )
    assert publish.status_code == 200, publish.text

    response = client.get(
        "/api/v1/events/calendar",
        params={
            "from": "2026-09-01T00:00:00Z",
            "to": "2026-10-01T00:00:00Z",
            "user_id": str(instructor_id),
        },
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert event_id in ids


# --- lifecycle / status transitions ------------------------------------------


@requires_postgres
def test_transition_draft_to_published_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="draft")
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "published"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"


@requires_postgres
def test_transition_published_to_cancelled_requires_reason(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="published")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.cancel", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "cancelled"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cancellation_reason_required"


@requires_postgres
def test_transition_published_to_cancelled_with_reason_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="published")
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.cancel", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "cancelled", "cancellation_reason": "Bad weather"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancellation_reason"] == "Bad weather"


@requires_postgres
def test_transition_requires_event_update_not_event_cancel_for_non_cancel_target(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="draft")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    # Only event.cancel granted, not event.update.
    _grant_permission(user_id, "event.cancel", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "published"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


@requires_postgres
def test_transition_requires_event_cancel_not_event_update_for_cancel_target(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="published")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    # Only event.update granted, not event.cancel.
    _grant_permission(user_id, "event.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "cancelled", "cancellation_reason": "x"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


@requires_postgres
def test_transition_disallowed_edge_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="draft")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.update", scope_type="all")
    _grant_permission(user_id, "event.cancel", scope_type="all")
    _authenticate_as(user_id)

    # draft -> cancelled is not an ADR-0018 edge (only published/in_progress can cancel).
    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "cancelled", "cancellation_reason": "x"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_status_transition"


@requires_postgres
def test_transition_to_archived_via_status_endpoint_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="completed")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "archived"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "use_archive_endpoint"


# --- archive -----------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("from_status", ["completed", "cancelled"])
def test_archive_with_manage_permission_succeeds(client: TestClient, from_status: str) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        extra = {"cancellation_reason": "x"} if from_status == "cancelled" else {}
        event = _make_event(club, status=from_status, **extra)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(f"/api/v1/events/{event_id}/archive", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"


@requires_postgres
def test_archive_without_manage_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="completed")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    # event.update/event.cancel must not substitute for event.manage.
    _grant_permission(user_id, "event.update", scope_type="all")
    _grant_permission(user_id, "event.cancel", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(f"/api/v1/events/{event_id}/archive", headers=_csrf_headers(client))
    assert response.status_code == 404


@requires_postgres
def test_archive_from_invalid_state_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, status="draft")
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(f"/api/v1/events/{event_id}/archive", headers=_csrf_headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_status_transition"


# --- IDOR: cross-endpoint instructor/guardian regression coverage -----------


@requires_postgres
def test_instructor_cannot_update_status_cancel_or_archive_an_unrelated_event(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        assigned_event = _make_event(club, title="Assigned")
        unrelated_event = _make_event(club, title="Unrelated", status="completed")
        session.add_all([assigned_event, unrelated_event])
        session.commit()
        session.add(_make_event_staff_assignment(assigned_event, user))
        session.commit()
        user_id, unrelated_id = user.id, unrelated_event.id
    _grant_permission(user_id, "event.update", scope_type="own_events")
    _grant_permission(user_id, "event.cancel", scope_type="own_events")
    _grant_permission(user_id, "event.manage", scope_type="own_events")
    _authenticate_as(user_id)

    headers = _csrf_headers(client)
    assert client.get(f"/api/v1/events/{unrelated_id}").status_code == 404
    patch_response = client.patch(
        f"/api/v1/events/{unrelated_id}", json={"title": "x"}, headers=headers
    )
    assert patch_response.status_code == 404
    assert (
        client.post(
            f"/api/v1/events/{unrelated_id}/status",
            json={"status": "cancelled", "cancellation_reason": "x"},
            headers=headers,
        ).status_code
        == 404
    )
    assert client.post(f"/api/v1/events/{unrelated_id}/archive", headers=headers).status_code == 404


@requires_postgres
def test_guardian_cannot_access_event_of_unrelated_persons_child(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        someone_elses_child = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, someone_elses_child, guardian_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_club_membership(club, guardian_person))
        session.add(_make_club_membership(club, someone_elses_child))
        # someone_elses_child participates, but has no GuardianRelationship
        # to this guardian at all.
        session.add(_make_event_participation(event, someone_elses_child))
        session.commit()
        guardian_user_id, event_id = guardian_user.id, event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 404
