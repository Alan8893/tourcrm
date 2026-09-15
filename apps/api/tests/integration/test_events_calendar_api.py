"""HTTP-level integration tests for `GET /api/v1/events/calendar` (Issue
#82 / TH-0080): range validation, pagination/ordering, calendar status
visibility, canonical scope authorization (`all`/`own_groups`/
`own_events`/`self`/`children`/`none`), guardian relationships, IDOR,
ordinary Event + recurring EventOccurrence coexistence, effective
reschedule/cancellation, and on-demand materialization horizon extension.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_events_api.py's own pattern.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import threading
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
    EventOccurrenceParticipant,
    EventOccurrenceStaffAssignment,
)
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.events.materialization import materialize_occurrences
from app.events.series_service import set_occurrence_exception
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 15, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ---------------------------------------------------


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
        "membership_type": "student",
        "status": "active",
        "joined_at": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


_EVENT_TO_OCCURRENCE_STATUS = {
    "draft": "scheduled",
    "published": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
}


def _make_event(session, club: Club, **overrides: object) -> Event:
    """ADR-0033: every Event has exactly one linked EventOccurrence — so
    this helper creates both, adding them to `session` itself (the
    caller's own subsequent `session.add(event)`/`add_all([...])` is
    then a harmless no-op) rather than returning a bare, unlinked Event.
    """
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Test event",
        "start_at": _START + datetime.timedelta(hours=1),
        "end_at": _START + datetime.timedelta(hours=2),
        "timezone": "Europe/Moscow",
        "status": "published",
    }
    defaults.update(overrides)
    event = Event(id=uuid.uuid4(), **defaults)  # type: ignore[arg-type]
    session.add(event)
    session.flush()
    session.add(
        EventOccurrence(
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
    )
    return event


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
    starts_at = overrides.pop("starts_at", _START + datetime.timedelta(hours=1))
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


def _make_occurrence_participant(
    occurrence: EventOccurrence, person: Person, **overrides: object
) -> EventOccurrenceParticipant:
    defaults: dict[str, object] = {
        "occurrence_id": occurrence.id,
        "person_id": person.id,
        "registration_status": "registered",
        "valid_from": _utc(2020, 1, 1),
        "is_override": False,
    }
    defaults.update(overrides)
    return EventOccurrenceParticipant(**defaults)  # type: ignore[arg-type]


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


_DEFAULT_RANGE = _range(_START, _START + datetime.timedelta(days=7))


# --- Range validation (1-8) -------------------------------------------------


@requires_postgres
def test_missing_from_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params={"to": (_START + datetime.timedelta(days=1)).isoformat()}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_from"


@requires_postgres
def test_missing_to_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params={"from": _START.isoformat()})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_to"


@requires_postgres
def test_from_bound_is_inclusive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=1)
        )
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params=_range(_START, _START + datetime.timedelta(days=1))
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) in ids


@requires_postgres
def test_to_bound_is_exclusive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(days=1),
            end_at=_START + datetime.timedelta(days=1, hours=1),
        )
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params=_range(_START, _START + datetime.timedelta(days=1))
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) not in ids


@requires_postgres
def test_from_equal_to_to_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_range(_START, _START))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_range"


@requires_postgres
def test_from_after_to_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar",
        params=_range(_START + datetime.timedelta(days=1), _START),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_range"


@requires_postgres
def test_naive_timestamp_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "event.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar",
        params={"from": "2026-09-15T00:00:00", "to": "2026-09-16T00:00:00"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "naive_timestamp"


@requires_postgres
def test_timezone_aware_input_is_normalized_to_utc_for_selection(client: TestClient) -> None:
    """The same instant expressed in two different offsets must select the
    same rows — proving normalization happens before selection, not naive
    string comparison."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        # 21:00 Moscow (+03:00) == 18:00 UTC.
        event = _make_event(
            session,
            club,
            start_at=_utc(2026, 9, 15, 18, 0),
            end_at=_utc(2026, 9, 15, 19, 0),
        )
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar",
        params={
            "from": "2026-09-15T21:00:00+03:00",
            "to": "2026-09-15T22:00:00+03:00",
        },
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) in ids


# --- Pagination / ordering (9-12) -------------------------------------------


@requires_postgres
def test_pagination_envelope_shape(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert set(body["pagination"].keys()) == {"page", "page_size", "total", "pages"}
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 50


@requires_postgres
def test_results_are_sorted_by_start_at_ascending(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(
            session,
            club,
            title="third",
            start_at=_START + datetime.timedelta(hours=3),
            end_at=_START + datetime.timedelta(hours=4),
        )
        e2 = _make_event(
            session,
            club,
            title="first",
            start_at=_START + datetime.timedelta(hours=1),
            end_at=_START + datetime.timedelta(hours=2),
        )
        e3 = _make_event(
            session,
            club,
            title="second",
            start_at=_START + datetime.timedelta(hours=2),
            end_at=_START + datetime.timedelta(hours=3),
        )
        session.add_all([e1, e2, e3])
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()["items"]]
    assert titles == ["first", "second", "third"]


@requires_postgres
def test_same_start_at_is_tie_broken_by_id_ascending(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        same_start = _START + datetime.timedelta(hours=1)
        e1 = _make_event(
            session, club, start_at=same_start, end_at=same_start + datetime.timedelta(hours=1)
        )
        e2 = _make_event(
            session, club, start_at=same_start, end_at=same_start + datetime.timedelta(hours=1)
        )
        session.add_all([e1, e2])
        session.commit()
        club_id, user_id = club.id, user.id
        expected_ids = sorted([str(e1.id), str(e2.id)])
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    items = [item for item in response.json()["items"] if item["id"] in expected_ids]
    assert [item["id"] for item in items] == expected_ids


@requires_postgres
def test_total_only_counts_authorized_rows(client: TestClient) -> None:
    with session_scope() as session:
        club_visible = _make_club()
        club_hidden = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club_visible, club_hidden, person, user])
        session.commit()
        visible_event = _make_event(session, club_visible)
        hidden_event = _make_event(session, club_hidden)
        session.add_all([visible_event, hidden_event])
        session.commit()
        visible_club_id, user_id = club_visible.id, user.id
    # scope_type=all but bound to club_visible only — club_hidden's Event
    # must never contribute to items or total.
    _grant_permission(user_id, "event.read", scope_type="all", club_id=visible_club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["club_id"] == str(visible_club_id)


# --- Status visibility (13-18) ----------------------------------------------


@requires_postgres
@pytest.mark.parametrize("status_value", ["published", "in_progress", "completed", "cancelled"])
def test_calendar_visible_event_statuses_are_included(
    client: TestClient, status_value: str
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        overrides: dict[str, object] = {"status": status_value}
        if status_value == "cancelled":
            overrides["cancellation_reason"] = "weather"
        event = _make_event(session, club, **overrides)
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) in ids


@requires_postgres
@pytest.mark.parametrize("status_value", ["draft", "archived"])
def test_calendar_excluded_event_statuses_are_excluded(
    client: TestClient, status_value: str
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(session, club, status=status_value)
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) not in ids


@requires_postgres
@pytest.mark.parametrize("status_value", ["scheduled", "in_progress", "completed", "cancelled"])
def test_calendar_visible_occurrence_statuses_are_included(
    client: TestClient, status_value: str
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = EventOccurrence(
            series_id=series.id,
            club_id=club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START + datetime.timedelta(hours=1),
            starts_at=_START + datetime.timedelta(hours=1),
            ends_at=_START + datetime.timedelta(hours=2),
            timezone="UTC",
            status=status_value,
            cancellation_reason="weather" if status_value == "cancelled" else None,
        )
        session.add(occ)
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) in ids


# --- Authorization scopes (19-24) -------------------------------------------


@requires_postgres
def test_all_scope_sees_every_eligible_club_event(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(session, club)
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert str(event_id) in {item["id"] for item in response.json()["items"]}


@requires_postgres
def test_own_events_scope_sees_only_assigned_events(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        assigned = _make_event(session, club, title="assigned")
        other = _make_event(session, club, title="other")
        session.add_all([assigned, other])
        session.commit()
        staff = _make_event_staff_assignment(assigned, user)
        session.add(staff)
        session.commit()
        club_id, user_id = club.id, user.id
        assigned_id, other_id = assigned.id, other.id
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(assigned_id) in ids
    assert str(other_id) not in ids


@requires_postgres
def test_own_groups_scope_sees_only_targeted_group_events(client: TestClient) -> None:
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
        targeted = _make_event(session, club, title="targeted")
        untargeted = _make_event(session, club, title="untargeted")
        session.add_all([targeted, untargeted])
        session.commit()
        session.add(_make_event_group_target(targeted, group))
        session.commit()
        club_id, user_id = club.id, user.id
        targeted_id, untargeted_id = targeted.id, untargeted.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(targeted_id) in ids
    assert str(untargeted_id) not in ids


@requires_postgres
def test_self_scope_sees_only_own_participation(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        mine = _make_event(session, club, title="mine")
        other = _make_event(session, club, title="other")
        session.add_all([mine, other])
        session.commit()
        session.add(_make_event_participation(mine, person))
        session.commit()
        club_id, user_id = club.id, user.id
        mine_id, other_id = mine.id, other.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(mine_id) in ids
    assert str(other_id) not in ids


@requires_postgres
def test_children_scope_sees_only_related_childs_events(client: TestClient) -> None:
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
        childs_event = _make_event(session, club, title="childs")
        other = _make_event(session, club, title="other")
        session.add_all([childs_event, other])
        session.commit()
        session.add(_make_event_participation(childs_event, child_person))
        session.commit()
        club_id, guardian_user_id = club.id, guardian_user.id
        childs_event_id, other_id = childs_event.id, other.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(childs_event_id) in ids
    assert str(other_id) not in ids


@requires_postgres
def test_none_scope_sees_nothing(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(session, club)
        session.add(event)
        session.commit()
        club_id, user_id, event_id = club.id, user.id, event.id
    _grant_permission(user_id, "event.read", scope_type="none", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0
    assert str(event_id) not in {item["id"] for item in body["items"]}


# --- Guardian relationship correctness (25-28) ------------------------------


@requires_postgres
def test_inactive_guardian_relationship_denies_access(client: TestClient) -> None:
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
        session.add(_make_guardian_relationship(guardian_person, child_person, status="inactive"))
        session.commit()
        childs_event = _make_event(session, club)
        session.add(childs_event)
        session.commit()
        session.add(_make_event_participation(childs_event, child_person))
        session.commit()
        club_id, guardian_user_id, childs_event_id = club.id, guardian_user.id, childs_event.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(childs_event_id) not in ids


@requires_postgres
def test_unrelated_child_is_denied(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        unrelated_child = _make_person()
        session.add_all([club, guardian_person, guardian_user, unrelated_child])
        session.commit()
        session.add_all(
            [
                _make_club_membership(club, guardian_person),
                _make_club_membership(club, unrelated_child),
            ]
        )
        session.commit()
        # No GuardianRelationship at all between guardian and this child.
        unrelated_event = _make_event(session, club)
        session.add(unrelated_event)
        session.commit()
        session.add(_make_event_participation(unrelated_event, unrelated_child))
        session.commit()
        club_id, guardian_user_id, unrelated_event_id = (
            club.id,
            guardian_user.id,
            unrelated_event.id,
        )
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(unrelated_event_id) not in ids


@requires_postgres
def test_guardian_club_boundary_is_respected(client: TestClient) -> None:
    """The child's ClubMembership must be in the SAME Club as the Event,
    not merely anywhere in the system."""
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        session.add_all([club_a, club_b, guardian_person, guardian_user, child_person])
        session.commit()
        session.add_all(
            [
                _make_club_membership(club_a, guardian_person),
                # Child is only a member of club_b, not club_a.
                _make_club_membership(club_b, child_person),
            ]
        )
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.commit()
        event_in_club_a = _make_event(session, club_a)
        session.add(event_in_club_a)
        session.commit()
        session.add(_make_event_participation(event_in_club_a, child_person))
        session.commit()
        club_a_id, guardian_user_id, event_id = club_a.id, guardian_user.id, event_in_club_a.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_a_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) not in ids


# --- Recurring occurrence authorization scopes (TH-0082 / PR #86) ----------
#
# `own_events`/`own_groups`/`self`/`children` for ordinary Events are
# already covered above (19-24). PR #86 replaced the previous fail-closed
# occurrence_visibility_filter with real occurrence-level relationship
# resolution (SeriesStaffAssignment/SeriesGroupTarget/SeriesParticipant
# materialized onto EventOccurrenceStaffAssignment/GroupTarget/Participant).
# These tests exercise that resolution end-to-end through the calendar
# endpoint itself — the same contract, applied to the Occurrence branch of
# the UNION ALL, not a re-test of TH-0082's own unit-level authorization
# suite (tests/integration/test_event_occurrence_authorization.py).


@requires_postgres
def test_own_events_scope_sees_only_occurrence_with_active_staff_assignment(
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
        series = _make_series(session, club_id=club.id)
        assigned = _make_occurrence(series=series, club_id=club.id)
        unrelated = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(hours=3)
        )
        session.add_all([assigned, unrelated])
        session.commit()
        session.add(_make_occurrence_staff_assignment(assigned, user))
        session.commit()
        club_id, user_id = club.id, user.id
        assigned_id, unrelated_id = assigned.id, unrelated.id
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(assigned_id) in ids
    assert str(unrelated_id) not in ids


@requires_postgres
def test_assigned_events_alias_sees_the_same_occurrence_as_own_events(client: TestClient) -> None:
    assert normalize_scope_type("assigned_events") == "own_events"
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
    # Persist using the canonical, normalized scope_type — `assigned_events`
    # is never written to the DB literally (app.authorization.context.
    # normalize_scope_type resolves it to `own_events` at the write
    # boundary; the DB CHECK constraint only accepts canonical values).
    _grant_permission(
        user_id, "event.read", scope_type=normalize_scope_type("assigned_events"), club_id=club_id
    )
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) in ids


@requires_postgres
def test_own_groups_scope_sees_only_occurrence_with_active_group_target(
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
        targeted = _make_occurrence(series=series, club_id=club.id)
        untargeted = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(hours=3)
        )
        session.add_all([targeted, untargeted])
        session.commit()
        session.add(_make_occurrence_group_target(targeted, group))
        session.commit()
        club_id, user_id = club.id, user.id
        targeted_id, untargeted_id = targeted.id, untargeted.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(targeted_id) in ids
    assert str(untargeted_id) not in ids


@requires_postgres
def test_own_groups_scope_denies_unrelated_instructor(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        other_group = _make_group(club)
        session.add_all([group, other_group])
        session.commit()
        # `user` instructs `group`, never `other_group`.
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(series=series, club_id=club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_group_target(occ, other_group))
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) not in ids


@requires_postgres
def test_self_scope_sees_only_occurrence_with_own_active_participation(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        mine = _make_occurrence(series=series, club_id=club.id)
        other = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(hours=3)
        )
        session.add_all([mine, other])
        session.commit()
        session.add(_make_occurrence_participant(mine, person))
        session.commit()
        club_id, user_id = club.id, user.id
        mine_id, other_id = mine.id, other.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(mine_id) in ids
    assert str(other_id) not in ids


@requires_postgres
def test_children_scope_sees_only_occurrence_related_to_guardians_child(
    client: TestClient,
) -> None:
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
        series = _make_series(session, club_id=club.id)
        childs_occ = _make_occurrence(series=series, club_id=club.id)
        other_occ = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(hours=3)
        )
        session.add_all([childs_occ, other_occ])
        session.commit()
        session.add(_make_occurrence_participant(childs_occ, child_person))
        session.commit()
        club_id, guardian_user_id = club.id, guardian_user.id
        childs_occ_id, other_occ_id = childs_occ.id, other_occ.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(childs_occ_id) in ids
    assert str(other_occ_id) not in ids


@requires_postgres
def test_children_scope_denies_inactive_guardian_relationship_for_occurrence(
    client: TestClient,
) -> None:
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
        session.add(_make_guardian_relationship(guardian_person, child_person, status="inactive"))
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(series=series, club_id=club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_participant(occ, child_person))
        session.commit()
        club_id, guardian_user_id, occ_id = club.id, guardian_user.id, occ.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) not in ids


@requires_postgres
def test_children_scope_denies_unrelated_child_for_occurrence(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        unrelated_child = _make_person()
        session.add_all([club, guardian_person, guardian_user, unrelated_child])
        session.commit()
        session.add_all(
            [
                _make_club_membership(club, guardian_person),
                _make_club_membership(club, unrelated_child),
            ]
        )
        session.commit()
        # No GuardianRelationship at all between guardian and this child.
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(series=series, club_id=club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_participant(occ, unrelated_child))
        session.commit()
        club_id, guardian_user_id, occ_id = club.id, guardian_user.id, occ.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) not in ids


@requires_postgres
def test_none_scope_sees_no_occurrence(client: TestClient) -> None:
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
    _grant_permission(user_id, "event.read", scope_type="none", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    body = response.json()
    assert str(occ_id) not in {item["id"] for item in body["items"]}
    assert body["pagination"]["total"] == 0


@requires_postgres
def test_own_events_scope_denies_occurrence_relationship_in_a_different_club(
    client: TestClient,
) -> None:
    """IDOR / cross-Club: a staff assignment on an occurrence in a Club the
    user has no `own_events` grant for must never leak through, even though
    the relationship row itself is genuinely active."""
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        series = _make_series(session, club_id=other_club.id)
        occ = _make_occurrence(series=series, club_id=other_club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ, user))
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    # Grant is for `club`, but the occurrence + staff assignment are in
    # `other_club` — existence must be hidden exactly like every other
    # unauthorized row in this file (never a 403, never a leaked total).
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    body = response.json()
    assert str(occ_id) not in {item["id"] for item in body["items"]}
    assert body["pagination"]["total"] == 0


# --- IDOR (29-33) ------------------------------------------------------------


@requires_postgres
def test_foreign_group_id_filter_does_not_leak_or_expand_access(client: TestClient) -> None:
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
        foreign_event = _make_event(session, other_club)
        session.add(foreign_event)
        session.commit()
        session.add(_make_event_group_target(foreign_event, foreign_group))
        session.commit()
        club_id, user_id, foreign_group_id = club.id, user.id, foreign_group.id
    # User only has access within `club`, never `other_club`.
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params={**_DEFAULT_RANGE, "group_id": str(foreign_group_id)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


@requires_postgres
def test_foreign_user_id_filter_does_not_leak_or_expand_access(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([club, other_club, person, user, other_person, other_user])
        session.commit()
        foreign_event = _make_event(session, other_club)
        session.add(foreign_event)
        session.commit()
        session.add(_make_event_staff_assignment(foreign_event, other_user))
        session.commit()
        club_id, user_id, other_user_id = club.id, user.id, other_user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params={**_DEFAULT_RANGE, "user_id": str(other_user_id)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


@requires_postgres
def test_unauthorized_occurrence_is_never_visible_under_all_scope_of_a_different_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        series = _make_series(session, club_id=other_club.id)
        occ = EventOccurrence(
            series_id=series.id,
            club_id=other_club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START + datetime.timedelta(hours=1),
            starts_at=_START + datetime.timedelta(hours=1),
            ends_at=_START + datetime.timedelta(hours=2),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occ)
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) not in ids


@requires_postgres
def test_unauthorized_event_returns_no_data_and_zero_total(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(session, club)
        session.add(event)
        session.commit()
        user_id, event_id = user.id, event.id
    # No permission grant at all.
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0
    assert str(event_id) not in {item["id"] for item in body["items"]}


@requires_postgres
def test_unauthorized_resource_never_inflates_total(client: TestClient) -> None:
    with session_scope() as session:
        visible_club = _make_club()
        hidden_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([visible_club, hidden_club, person, user])
        session.commit()
        visible_event = _make_event(session, visible_club)
        for _ in range(5):
            session.add(_make_event(session, hidden_club))
        session.add(visible_event)
        session.commit()
        visible_club_id, user_id, visible_event_id = (
            visible_club.id,
            user.id,
            visible_event.id,
        )
    _grant_permission(user_id, "event.read", scope_type="all", club_id=visible_club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert [item["id"] for item in body["items"]] == [str(visible_event_id)]


# --- Recurrence (34-39) ------------------------------------------------------


@requires_postgres
def test_ordinary_event_and_recurring_occurrence_coexist(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(hours=1),
            end_at=_START + datetime.timedelta(hours=2),
        )
        session.add(event)
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = EventOccurrence(
            series_id=series.id,
            club_id=club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START + datetime.timedelta(hours=3),
            starts_at=_START + datetime.timedelta(hours=3),
            ends_at=_START + datetime.timedelta(hours=4),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occ)
        session.commit()
        club_id, user_id = club.id, user.id
        event_id, occ_id = event.id, occ.id
        series_id, series_version = series.id, series.version
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    items_by_id = {item["id"]: item for item in response.json()["items"]}
    assert str(event_id) in items_by_id
    assert str(occ_id) in items_by_id
    assert items_by_id[str(event_id)]["kind"] == "event"
    assert items_by_id[str(event_id)]["series_id"] is None
    assert items_by_id[str(occ_id)]["kind"] == "occurrence"
    assert items_by_id[str(occ_id)]["series_id"] == str(series_id)
    assert items_by_id[str(occ_id)]["series_version"] == series_version


@requires_postgres
def test_effective_rescheduled_time_is_returned(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = EventOccurrence(
            series_id=series.id,
            club_id=club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START + datetime.timedelta(hours=1),
            starts_at=_START + datetime.timedelta(hours=1),
            ends_at=_START + datetime.timedelta(hours=2),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occ)
        session.commit()
        rescheduled_start = _START + datetime.timedelta(hours=5)
        rescheduled_end = _START + datetime.timedelta(hours=6)
        set_occurrence_exception(
            session,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=rescheduled_start,
            effective_end_at=rescheduled_end,
            actor_user_id=user.id,
        )
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    items_by_id = {item["id"]: item for item in response.json()["items"]}
    item = items_by_id[str(occ_id)]
    assert item["start_at"] == rescheduled_start.isoformat().replace("+00:00", "Z")
    assert item["end_at"] == rescheduled_end.isoformat().replace("+00:00", "Z")


@requires_postgres
def test_cancelled_occurrence_is_visible_with_cancelled_status(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = EventOccurrence(
            series_id=series.id,
            club_id=club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START + datetime.timedelta(hours=1),
            starts_at=_START + datetime.timedelta(hours=1),
            ends_at=_START + datetime.timedelta(hours=2),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occ)
        session.commit()
        set_occurrence_exception(
            session,
            occurrence=occ,
            exception_type="cancelled",
            cancellation_reason="weather",
            actor_user_id=user.id,
        )
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    items_by_id = {item["id"]: item for item in response.json()["items"]}
    assert str(occ_id) in items_by_id
    assert items_by_id[str(occ_id)]["status"] == "cancelled"
    assert items_by_id[str(occ_id)]["cancellation_reason"] == "weather"


@requires_postgres
def test_occurrence_id_is_stable_and_no_duplicate_identity_is_created(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = EventOccurrence(
            series_id=series.id,
            club_id=club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START + datetime.timedelta(hours=1),
            starts_at=_START + datetime.timedelta(hours=1),
            ends_at=_START + datetime.timedelta(hours=2),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occ)
        session.commit()
        club_id, user_id, occ_id = club.id, user.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    first = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    second = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    first_items = [item for item in first.json()["items"] if item["id"] == str(occ_id)]
    second_items = [item for item in second.json()["items"] if item["id"] == str(occ_id)]
    assert len(first_items) == 1
    assert len(second_items) == 1
    assert first_items[0]["id"] == second_items[0]["id"] == str(occ_id)


# --- Materialization (40-43) -------------------------------------------------


@requires_postgres
def test_requested_to_within_horizon_uses_already_materialized_rows(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id, series_start_at=_START)
        materialize_occurrences(
            session, series=series, horizon_end=_START + datetime.timedelta(days=5)
        )
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params=_range(_START, _START + datetime.timedelta(days=3))
    )
    assert response.status_code == 200
    occurrence_items = [item for item in response.json()["items"] if item["kind"] == "occurrence"]
    assert len(occurrence_items) == 3  # days 0, 1, 2


@requires_postgres
def test_requested_to_beyond_horizon_triggers_materialization_extension(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id, series_start_at=_START)
        club_id, user_id = club.id, user.id

    with session_scope() as verify:
        before = (
            verify.execute(select(EventOccurrence).where(EventOccurrence.series_id == series.id))
            .scalars()
            .all()
        )
        assert before == []  # nothing materialized yet

    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(
        "/api/v1/events/calendar", params=_range(_START, _START + datetime.timedelta(days=3))
    )
    assert response.status_code == 200
    occurrence_items = [item for item in response.json()["items"] if item["kind"] == "occurrence"]
    assert len(occurrence_items) == 3  # the calendar call itself extended materialization

    with session_scope() as verify:
        after = (
            verify.execute(select(EventOccurrence).where(EventOccurrence.series_id == series.id))
            .scalars()
            .all()
        )
        assert len(after) >= 3


@requires_postgres
def test_repeated_calendar_request_is_idempotent(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id, series_start_at=_START)
        club_id, user_id, series_id = club.id, user.id, series.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    range_params = _range(_START, _START + datetime.timedelta(days=3))
    first = client.get("/api/v1/events/calendar", params=range_params)
    second = client.get("/api/v1/events/calendar", params=range_params)
    assert first.status_code == second.status_code == 200
    first_ids = sorted(item["id"] for item in first.json()["items"])
    second_ids = sorted(item["id"] for item in second.json()["items"])
    assert first_ids == second_ids

    with session_scope() as verify:
        rows = (
            verify.execute(select(EventOccurrence).where(EventOccurrence.series_id == series_id))
            .scalars()
            .all()
        )
        anchors = [row.recurrence_anchor_at for row in rows]
        assert len(anchors) == len(set(anchors))  # no duplicates from repeated calls


@requires_postgres
def test_concurrent_calendar_requests_do_not_duplicate_materialized_occurrences(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id, series_start_at=_START)
        club_id, user_id, series_id = club.id, user.id, series.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    range_params = _range(_START, _START + datetime.timedelta(days=5))
    start_gate = threading.Barrier(2, timeout=10)
    results: list[int] = []

    def attempt() -> None:
        start_gate.wait()
        response = client.get("/api/v1/events/calendar", params=range_params)
        results.append(response.status_code)

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert results == [200, 200]
    with session_scope() as verify:
        rows = (
            verify.execute(select(EventOccurrence).where(EventOccurrence.series_id == series_id))
            .scalars()
            .all()
        )
        anchors = [row.recurrence_anchor_at for row in rows]
        assert len(anchors) == len(set(anchors))


# --- API contract (44-45) ----------------------------------------------------


@requires_postgres
def test_response_matches_the_explicit_calendar_schema(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(session, club)
        session.add(event)
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/events/calendar", params=_DEFAULT_RANGE)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item.keys()) == {
        "id",
        "kind",
        "club_id",
        "event_type",
        "title",
        "description",
        "start_at",
        "end_at",
        "timezone",
        "status",
        "cancellation_reason",
        "series_id",
        "series_version",
    }


def test_openapi_documents_the_calendar_endpoint() -> None:
    from app.main import app as fastapi_app

    schema = fastapi_app.openapi()
    assert "/api/v1/events/calendar" in schema["paths"]
    assert "get" in schema["paths"]["/api/v1/events/calendar"]
    assert "CalendarItemOut" in schema["components"]["schemas"]
