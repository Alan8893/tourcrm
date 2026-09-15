"""HTTP-level integration tests for `GET /api/v1/me/instructor-schedule`
(Issue #88 / TH-0083): `me` resolves the authenticated User, direct
EventStaffAssignment / GroupInstructorAssignment+EventGroupTarget
relationship qualification, effectivity boundaries evaluated at the
Event/Occurrence's own scheduled start (not "now"), recurring occurrence
staffing/GroupTarget, multi-Club isolation, `all` staying relationship-
based (never club-wide), `assigned_events == own_events`, pagination/
counting after authorization, `[from,to)` boundaries and deterministic
ordering.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_events_calendar_api.py's own
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
from app.authorization.context import normalize_scope_type
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceStaffAssignment,
)
from app.db.events import Event, EventGroupTarget, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 15, 0, 0, tzinfo=datetime.timezone.utc)


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


def _make_event(club: Club, **overrides: object) -> Event:
    start_at = overrides.pop("start_at", _START + datetime.timedelta(hours=1))
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Test event",
        "start_at": start_at,
        "end_at": start_at + datetime.timedelta(hours=1),  # type: ignore[operator]
        "timezone": "UTC",
        "status": "published",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


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


def _make_event_group_target(event: Event, group: Group, **overrides: object) -> EventGroupTarget:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "group_id": group.id,
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return EventGroupTarget(**defaults)  # type: ignore[arg-type]


def _make_series(session, *, club_id: uuid.UUID, **overrides: object) -> EventSeries:
    series_id = uuid.uuid4()
    defaults: dict[str, object] = dict(
        id=series_id,
        root_series_id=series_id,
        supersedes_series_id=None,
        version=1,
        club_id=club_id,
        name="Weekly lesson",
        event_type="lesson",
        series_start_at=_START,
        duration_minutes=60,
        recurrence_rule="FREQ=DAILY",
        timezone="UTC",
        status="active",
    )
    defaults.update(overrides)
    series = EventSeries(**defaults)  # type: ignore[arg-type]
    session.add(series)
    session.commit()
    return series


def _make_occurrence(
    *, series: EventSeries, club_id: uuid.UUID, **overrides: object
) -> EventOccurrence:
    starts_at = overrides.pop("starts_at", _START + datetime.timedelta(days=10))
    defaults: dict[str, object] = dict(
        series_id=series.id,
        club_id=club_id,
        name="Lesson",
        event_type="lesson",
        recurrence_anchor_at=starts_at,
        starts_at=starts_at,
        ends_at=starts_at + datetime.timedelta(hours=1),  # type: ignore[operator]
        timezone="UTC",
        status="scheduled",
    )
    defaults.update(overrides)
    return EventOccurrence(**defaults)  # type: ignore[arg-type]


def _make_occurrence_staff_assignment(
    occurrence: EventOccurrence, user: User, **overrides: object
) -> EventOccurrenceStaffAssignment:
    defaults: dict[str, object] = {
        "occurrence_id": occurrence.id,
        "user_id": user.id,
        "role_in_event": "instructor",
        "is_primary": True,
        "valid_from": _utc(2020, 1, 1),
        "is_override": False,
    }
    defaults.update(overrides)
    return EventOccurrenceStaffAssignment(**defaults)  # type: ignore[arg-type]


def _make_occurrence_group_target(
    occurrence: EventOccurrence, group: Group, **overrides: object
) -> EventOccurrenceGroupTarget:
    defaults: dict[str, object] = {
        "occurrence_id": occurrence.id,
        "group_id": group.id,
        "valid_from": _utc(2020, 1, 1),
        "is_override": False,
    }
    defaults.update(overrides)
    return EventOccurrenceGroupTarget(**defaults)  # type: ignore[arg-type]


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


def _range(from_dt: datetime.datetime, to_dt: datetime.datetime) -> dict:
    return {"from": from_dt.isoformat(), "to": to_dt.isoformat()}


_WIDE_RANGE = _range(
    _START - datetime.timedelta(days=365), _START + datetime.timedelta(days=365)
)


def _instructor_schedule(client: TestClient, params: dict | None = None):
    return client.get("/api/v1/me/instructor-schedule", params={**_WIDE_RANGE, **(params or {})})


# --- `me` resolves the authenticated User -----------------------------------


@requires_postgres
def test_me_resolves_the_authenticated_user_not_someone_elses_assignment(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([club, person, user, other_person, other_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_staff_assignment(event, other_user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert response.json()["items"] == []


# --- Direct EventStaffAssignment --------------------------------------------


@requires_postgres
def test_direct_event_staffing_is_allowed(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        assigned = _make_event(club)
        unrelated = _make_event(club, start_at=_START + datetime.timedelta(hours=3))
        session.add_all([assigned, unrelated])
        session.commit()
        session.add(_make_event_staff_assignment(assigned, user))
        session.commit()
        club_id, user_id, assigned_id, unrelated_id = club.id, user.id, assigned.id, unrelated.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(assigned_id) in ids
    assert str(unrelated_id) not in ids


@requires_postgres
def test_no_staffing_relationship_denies(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert response.json()["items"] == []


# --- GroupInstructorAssignment + explicit GroupTarget ------------------------


@requires_postgres
def test_group_instructor_assignment_with_group_target_is_allowed(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        targeted = _make_event(club)
        untargeted = _make_event(club, start_at=_START + datetime.timedelta(hours=3))
        session.add_all([targeted, untargeted])
        session.commit()
        session.add(_make_event_group_target(targeted, group))
        session.commit()
        club_id, user_id, targeted_id, untargeted_id = (
            club.id,
            user.id,
            targeted.id,
            untargeted.id,
        )
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(targeted_id) in ids
    assert str(untargeted_id) not in ids


@requires_postgres
def test_group_target_without_instructor_assignment_denies(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        # No GroupInstructorAssignment for `user`.
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert response.json()["items"] == []


@requires_postgres
def test_group_instructor_assignment_is_not_copied_into_event_staff_assignment(
    client: TestClient,
) -> None:
    """A GroupInstructorAssignment must never create a *direct*
    EventStaffAssignment row anywhere — the projection resolves the Event
    through GroupTarget + GroupInstructorAssignment explicitly, on every
    request; nothing is materialized into EventStaffAssignment merely by
    this endpoint being called."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(event_id) in {item["id"] for item in response.json()["items"]}

    with session_scope() as session:
        rows = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.event_id == event_id)
        ).scalars().all()
        assert rows == []


# --- Effectivity boundaries (evaluated at the Event's own start) -----------


@requires_postgres
def test_assignment_effective_at_event_start_is_allowed_even_if_since_ended(
    client: TestClient,
) -> None:
    """The Instructor Schedule must keep showing a historical Event the
    User genuinely staffed, evaluated at the Event's own scheduled start —
    even though the assignment has since ended and is no longer active
    "now" (group-and-instructor-schedule-api.md §3 Effectivity)."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        past_event = _make_event(club, start_at=_START - datetime.timedelta(days=30))
        session.add(past_event)
        session.commit()
        session.add(
            _make_event_staff_assignment(
                past_event,
                user,
                valid_from=_START - datetime.timedelta(days=60),
                valid_to=_START - datetime.timedelta(days=10),
            )
        )
        session.commit()
        club_id, user_id, past_event_id = club.id, user.id, past_event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(past_event_id) in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_valid_from_boundary_is_inclusive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, start_at=_START)
        session.add(event)
        session.commit()
        session.add(_make_event_staff_assignment(event, user, valid_from=_START, valid_to=None))
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(event_id) in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_valid_to_boundary_is_exclusive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, start_at=_START)
        session.add(event)
        session.commit()
        # valid_to == event.start_at exactly -> not effective at start (touching
        # the boundary from the exclusive side).
        session.add(
            _make_event_staff_assignment(
                event, user, valid_from=_START - datetime.timedelta(days=1), valid_to=_START
            )
        )
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(event_id) not in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_ended_assignment_still_authorizes_the_event_it_covered(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club, start_at=_START + datetime.timedelta(hours=5))
        session.add(event)
        session.commit()
        session.add(
            _make_event_staff_assignment(
                event,
                user,
                valid_from=_START,
                valid_to=_START + datetime.timedelta(hours=10),
            )
        )
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(event_id) in {item["id"] for item in response.json()["items"]}


# --- Recurring occurrence staffing / GroupTarget -----------------------------


@requires_postgres
def test_recurring_occurrence_direct_staffing_is_allowed(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(series=series, club_id=club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ, user))
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(occ_id) in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_recurring_occurrence_group_target_with_instructor_assignment_is_allowed(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(series=series, club_id=club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_group_target(occ, group))
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(occ_id) in {item["id"] for item in response.json()["items"]}


# --- Multiple Clubs isolation ------------------------------------------------


@requires_postgres
def test_multiple_clubs_stay_isolated(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club_a, club_b, person, user])
        session.commit()
        event_a = _make_event(club_a)
        event_b = _make_event(club_b)
        session.add_all([event_a, event_b])
        session.commit()
        session.add(_make_event_staff_assignment(event_a, user))
        session.add(_make_event_staff_assignment(event_b, user))
        session.commit()
        club_a_id, user_id, event_a_id, event_b_id = club_a.id, user.id, event_a.id, event_b.id
    # Only granted event.read in club_a — club_b's staffed event must not appear.
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_a_id) in ids
    assert str(event_b_id) not in ids


@requires_postgres
def test_multiple_clubs_both_covered_when_both_granted(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club_a, club_b, person, user])
        session.commit()
        event_a = _make_event(club_a)
        event_b = _make_event(club_b)
        session.add_all([event_a, event_b])
        session.commit()
        session.add(_make_event_staff_assignment(event_a, user))
        session.add(_make_event_staff_assignment(event_b, user))
        session.commit()
        club_a_id, club_b_id, user_id, event_a_id, event_b_id = (
            club_a.id,
            club_b.id,
            user.id,
            event_a.id,
            event_b.id,
        )
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_a_id)
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_b_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_a_id) in ids
    assert str(event_b_id) in ids


# --- `all` is relationship-based, never club-wide --------------------------


@requires_postgres
def test_all_scope_does_not_turn_endpoint_into_club_wide_calendar(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        staffed = _make_event(club)
        unstaffed = _make_event(club, start_at=_START + datetime.timedelta(hours=3))
        session.add_all([staffed, unstaffed])
        session.commit()
        session.add(_make_event_staff_assignment(staffed, user))
        session.commit()
        club_id, user_id, staffed_id, unstaffed_id = club.id, user.id, staffed.id, unstaffed.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(staffed_id) in ids
    assert str(unstaffed_id) not in ids


@requires_postgres
def test_none_scope_denies_relationship_based_access_too(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_staff_assignment(event, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="none", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert response.json()["items"] == []


# --- `assigned_events == own_events` -----------------------------------------


@requires_postgres
def test_assigned_events_alias_behaves_identically_to_own_events(client: TestClient) -> None:
    assert normalize_scope_type("assigned_events") == "own_events"
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_staff_assignment(event, user))
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(
        user_id, "event.read", scope_type=normalize_scope_type("assigned_events"), club_id=club_id
    )
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    assert str(event_id) in {item["id"] for item in response.json()["items"]}


# --- Pagination / counting + [from,to) + ordering ---------------------------


@requires_postgres
def test_pagination_and_counting_happen_after_relationship_filtering(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        staffed_ids = []
        for i in range(3):
            event = _make_event(club, start_at=_START + datetime.timedelta(hours=i + 1))
            session.add(event)
            session.commit()
            session.add(_make_event_staff_assignment(event, user))
            session.commit()
            staffed_ids.append(str(event.id))
        for i in range(2):
            unrelated = _make_event(club, start_at=_START + datetime.timedelta(hours=i + 20))
            session.add(unrelated)
            session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client, {"page": 1, "page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["pages"] == 2
    assert len(body["items"]) == 2
    seen_ids = {item["id"] for item in body["items"]}
    assert seen_ids.issubset(set(staffed_ids))


@requires_postgres
def test_from_to_boundary_is_half_open(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        at_from = _make_event(club, start_at=_START)
        at_to = _make_event(club, start_at=_START + datetime.timedelta(days=1))
        session.add_all([at_from, at_to])
        session.commit()
        session.add(_make_event_staff_assignment(at_from, user))
        session.add(_make_event_staff_assignment(at_to, user))
        session.commit()
        club_id, user_id, at_from_id, at_to_id = club.id, user.id, at_from.id, at_to.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(
        client,
        {"from": _START.isoformat(), "to": (_START + datetime.timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(at_from_id) in ids
    assert str(at_to_id) not in ids


@requires_postgres
def test_results_are_sorted_by_start_at_then_id(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        later = _make_event(club, start_at=_START + datetime.timedelta(hours=5))
        earlier = _make_event(club, start_at=_START + datetime.timedelta(hours=1))
        session.add_all([later, earlier])
        session.commit()
        session.add(_make_event_staff_assignment(later, user))
        session.add(_make_event_staff_assignment(earlier, user))
        session.commit()
        club_id, user_id, later_id, earlier_id = club.id, user.id, later.id, earlier.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _instructor_schedule(client)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert ids.index(str(earlier_id)) < ids.index(str(later_id))
