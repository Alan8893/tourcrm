"""HTTP-level integration tests for participant self-registration
(TH-0108.2 / Issue #140-series): `POST/DELETE /api/v1/events/{event_id}/
participation`, implementing ADR-0037's canonical MVP self-registration
policy.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching the pattern established by
tests/integration/test_events_api.py. Authentication is substituted via
`app.dependency_overrides[get_current_principal]`, the same "minimal
test authentication abstraction" used throughout this test suite.

Deliberately does NOT grant any Event permission (`event.create`,
`event.read`, etc.) to the registering users in most tests below — per
ADR-0037 §12, self-registration is a self-service operation gated by
identity/ClubMembership/GroupMembership/lifecycle, never by the generic
Event permission/scope engine. Test Q makes this explicit.

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
from app.db.attendance import Attendance
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventGroupTarget, EventParticipation
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


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


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Orienteering",
        "start_at": _utc(2026, 9, 20, 17, 0),
        "end_at": _utc(2026, 9, 20, 19, 0),
        "timezone": "Europe/Moscow",
        "status": "published",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_event_occurrence_for(event: Event) -> EventOccurrence:
    return EventOccurrence(  # type: ignore[arg-type]
        event_id=event.id,
        series_id=None,
        club_id=event.club_id,
        name=event.title,
        description=None,
        event_type=event.event_type,
        recurrence_anchor_at=event.start_at,
        starts_at=event.start_at,
        ends_at=event.end_at,
        timezone=event.timezone,
        status="scheduled",
        cancellation_reason=getattr(event, "cancellation_reason", None),
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


def _make_event_group_target(event: Event, group: Group, **overrides: object) -> EventGroupTarget:
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


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment

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


def _setup_member(club: Club) -> tuple[Person, User]:
    person = _make_person()
    user = _make_user(person)
    return person, user


# --- A. club-wide published Event: active Club member can register ----------


@requires_postgres
def test_active_club_member_can_register_for_club_wide_published_event(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, person_id, event_id = user.id, person.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event_id"] == str(event_id)
    assert body["person_id"] == str(person_id)
    assert body["registration_status"] == "registered"

    with session_scope() as session:
        row = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id, EventParticipation.person_id == person_id
            )
        ).scalar_one()
        assert row.registration_status == "registered"


# --- B. targeted published Event: target-Group member can register ---------


@requires_postgres
def test_target_group_member_can_register_for_targeted_published_event(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    assert response.json()["registration_status"] == "registered"


# --- C. targeted published Event: non-member of target Group is rejected ---


@requires_postgres
def test_non_target_group_member_is_rejected_for_targeted_event(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        # Active ClubMembership, but no membership in the target Group.
        session.add(_make_club_membership(club, person))
        group = _make_group(club)
        session.add(group)
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "not_eligible_for_event"

    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation).where(EventParticipation.event_id == event_id)
        ).scalars().all()
        assert rows == []


# --- D. no active ClubMembership: rejected -----------------------------------


@requires_postgres
def test_person_without_active_club_membership_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "not_eligible_for_event"


@requires_postgres
def test_person_with_inactive_club_membership_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person, status="inactive"))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "not_eligible_for_event"


# --- E. Event of another Club: registration impossible -----------------------


@requires_postgres
def test_cannot_register_for_event_of_another_club(client: TestClient) -> None:
    with session_scope() as session:
        home_club = _make_club()
        other_club = _make_club()
        person, user = _setup_member(home_club)
        session.add_all([home_club, other_club, person, user])
        session.commit()
        # Active membership only in home_club, not in the Event's Club.
        session.add(_make_club_membership(home_club, person))
        event = _make_event(other_club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "not_eligible_for_event"

    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation).where(EventParticipation.event_id == event_id)
        ).scalars().all()
        assert rows == []


# --- F-I. Event lifecycle gating ---------------------------------------------


@requires_postgres
@pytest.mark.parametrize("event_status", ["draft", "completed", "cancelled", "archived"])
def test_registration_is_rejected_for_non_published_event(
    client: TestClient, event_status: str
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        overrides: dict[str, object] = {"status": event_status}
        if event_status == "cancelled":
            overrides["cancellation_reason"] = "weather"
        event = _make_event(club, **overrides)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "event_not_published"

    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation).where(EventParticipation.event_id == event_id)
        ).scalars().all()
        assert rows == []


# --- J/K. idempotency and restoring a cancelled registration -----------------


@requires_postgres
def test_repeated_registration_is_idempotent_and_creates_no_duplicate(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id, person_id = user.id, event.id, person.id
    _authenticate_as(user_id)
    headers = _csrf_headers(client)

    first = client.post(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]

    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id, EventParticipation.person_id == person_id
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].registration_status == "registered"


@requires_postgres
def test_registering_again_after_cancellation_restores_the_same_row(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id, person_id = user.id, event.id, person.id
    _authenticate_as(user_id)
    headers = _csrf_headers(client)

    register = client.post(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert register.status_code == 200, register.text
    participation_id = register.json()["id"]

    withdraw = client.delete(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert withdraw.status_code == 204, withdraw.text

    with session_scope() as session:
        cancelled = session.execute(
            select(EventParticipation).where(EventParticipation.id == uuid.UUID(participation_id))
        ).scalar_one()
        assert cancelled.registration_status == "cancelled"

    re_register = client.post(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert re_register.status_code == 200, re_register.text
    assert re_register.json()["id"] == participation_id
    assert re_register.json()["registration_status"] == "registered"

    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id, EventParticipation.person_id == person_id
            )
        ).scalars().all()
        assert len(rows) == 1


# --- L/M. withdrawal and its idempotency -------------------------------------


@requires_postgres
def test_delete_cancels_the_authenticated_participants_own_registration(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_participation(event, person))
        session.commit()
        user_id, event_id, person_id = user.id, event.id, person.id
    _authenticate_as(user_id)

    response = client.delete(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 204, response.text

    with session_scope() as session:
        row = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id, EventParticipation.person_id == person_id
            )
        ).scalar_one()
        assert row.registration_status == "cancelled"


@requires_postgres
def test_repeated_delete_is_idempotent(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_participation(event, person, registration_status="cancelled"))
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.delete(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 204, response.text


@requires_postgres
def test_delete_with_no_existing_participation_is_idempotent_no_op(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    response = client.delete(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 204, response.text


# --- N/R. IDOR: person_id is always server-resolved, never client input ------


@requires_postgres
def test_client_supplied_person_id_in_request_body_is_ignored(client: TestClient) -> None:
    """ADR-0037 §13: the client MUST NOT be able to register an arbitrary
    Person by supplying a `person_id` — the endpoint takes no such field
    at all, so even a client that injects one must have it silently
    ignored, with the caller's own Person always used instead."""
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        other_person = _make_person()
        session.add_all([club, person, user, other_person])
        session.commit()
        session.add(_make_club_membership(club, person))
        session.add(_make_club_membership(club, other_person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, person_id, other_person_id, event_id = (
            user.id,
            person.id,
            other_person.id,
            event.id,
        )
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation",
        json={"person_id": str(other_person_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["person_id"] == str(person_id)

    with session_scope() as session:
        other_rows = session.execute(
            select(EventParticipation).where(EventParticipation.person_id == other_person_id)
        ).scalars().all()
        assert other_rows == []


@requires_postgres
def test_delete_never_cancels_another_persons_registration(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person_a, user_a = _setup_member(club)
        person_b, user_b = _setup_member(club)
        session.add_all([club, person_a, user_a, person_b, user_b])
        session.commit()
        session.add(_make_club_membership(club, person_a))
        session.add(_make_club_membership(club, person_b))
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_participation(event, person_a))
        session.commit()
        user_b_id, event_id, person_a_id = user_b.id, event.id, person_a.id
    # Authenticate as B, who never registered — B's own DELETE must be a
    # harmless no-op, never touching A's row.
    _authenticate_as(user_b_id)

    response = client.delete(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 204, response.text

    with session_scope() as session:
        row_a = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id, EventParticipation.person_id == person_a_id
            )
        ).scalar_one()
        assert row_a.registration_status == "registered"


# --- O. no side-effect creation ----------------------------------------------


@requires_postgres
def test_registration_creates_no_attendance_group_membership_or_guardian_relationship(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_event_occurrence_for(event))
        session.commit()
        user_id, event_id, person_id = user.id, event.id, person.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text

    with session_scope() as session:
        attendance_rows = session.execute(select(Attendance)).scalars().all()
        assert attendance_rows == []
        group_memberships = session.execute(select(GroupMembership)).scalars().all()
        assert group_memberships == []
        guardian_rows = session.execute(
            select(GuardianRelationship).where(
                (GuardianRelationship.guardian_person_id == person_id)
                | (GuardianRelationship.child_person_id == person_id)
            )
        ).scalars().all()
        assert guardian_rows == []


# --- P. Group targeting alone does not create EventParticipation ------------


@requires_postgres
def test_group_targeting_alone_never_creates_a_participation(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, _user = _setup_member(club)
        session.add_all([club, person, _user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        event_id = event.id

    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation).where(EventParticipation.event_id == event_id)
        ).scalars().all()
        assert rows == []


# --- Q. backend-authoritative: no generic Event permission required/bypassed


@requires_postgres
def test_registration_requires_no_generic_event_permission(client: TestClient) -> None:
    """ADR-0037 §12: self-registration is a self-service operation — an
    eligible member with *zero* Event permissions granted must still be
    able to register."""
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    # No _grant_permission call at all: this user holds no Event
    # permission whatsoever.
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text


@requires_postgres
def test_role_name_alone_does_not_bypass_eligibility(client: TestClient) -> None:
    """ADR-0037 §12: holding a role (even one that would normally carry
    broad Event permissions, like admin) must never substitute for the
    actual membership/target-Group eligibility check."""
    with session_scope() as session:
        home_club = _make_club()
        other_club = _make_club()
        person, user = _setup_member(home_club)
        session.add_all([home_club, other_club, person, user])
        session.commit()
        session.add(_make_club_membership(home_club, person))
        event = _make_event(other_club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id

    # An `all`-scope admin-style grant on event.manage — broad Event
    # permissions, but irrelevant to self-registration eligibility, which
    # never consults the permission engine at all.
    _grant_permission(user_id, "event.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "not_eligible_for_event"


# --- EventOut.my_registration_status (frontend "am I registered" read) ------


@requires_postgres
def test_event_response_reports_null_registration_status_before_registering(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200, response.text
    assert response.json()["my_registration_status"] is None


@requires_postgres
def test_event_response_reports_registered_status_after_registering(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)
    headers = _csrf_headers(client)

    register = client.post(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert register.status_code == 200, register.text

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200, response.text
    assert response.json()["my_registration_status"] == "registered"


@requires_postgres
def test_event_response_reports_cancelled_status_after_withdrawal(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person, user = _setup_member(club)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        event = _make_event(club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)
    headers = _csrf_headers(client)

    client.post(f"/api/v1/events/{event_id}/participation", headers=headers)
    withdraw = client.delete(f"/api/v1/events/{event_id}/participation", headers=headers)
    assert withdraw.status_code == 204, withdraw.text

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200, response.text
    assert response.json()["my_registration_status"] == "cancelled"


@requires_postgres
def test_event_response_never_reports_another_persons_registration_status(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person_a, user_a = _setup_member(club)
        person_b, user_b = _setup_member(club)
        session.add_all([club, person_a, user_a, person_b, user_b])
        session.commit()
        session.add(_make_club_membership(club, person_a))
        session.add(_make_club_membership(club, person_b))
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_participation(event, person_a))
        session.commit()
        user_b_id, event_id = user_b.id, event.id
    _grant_permission(user_b_id, "event.read", scope_type="all")
    # B never registered, but A did — B's own view of the Event must not
    # reflect A's registration.
    _authenticate_as(user_b_id)

    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200, response.text
    assert response.json()["my_registration_status"] is None
