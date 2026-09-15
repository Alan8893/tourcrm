"""HTTP-level integration tests for `GET /api/v1/events/conflicts`
(Issue #91 / TH-0085, per ADR-0031/ODR-0003): interval overlap semantics,
touching boundaries, Event/EventOccurrence combinations, the three MVP
conflict domains (instructor/group/participant), lifecycle exclusions,
recurrence (materialized occurrences, rescheduled effective time),
canonical scope authorization, cross-Club IDOR/existence-hiding,
`user_id`/`group_id` narrowing, deterministic identity/ordering, and the
standard API contract (pagination, error envelope).

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
    EventOccurrenceParticipant,
    EventOccurrenceStaffAssignment,
)
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.events.series_relationships import create_series_staff_assignment
from app.events.series_service import set_occurrence_exception
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 20, 0, 0, tzinfo=datetime.timezone.utc)


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
    start_at = overrides.pop("start_at", _START)
    end_at = overrides.pop("end_at", start_at + datetime.timedelta(hours=1))  # type: ignore[operator]
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Test event",
        "start_at": start_at,
        "end_at": end_at,
        "timezone": "UTC",
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
    starts_at = overrides.pop("starts_at", _START)
    ends_at = overrides.pop("ends_at", starts_at + datetime.timedelta(hours=1))  # type: ignore[operator]
    defaults: dict[str, object] = dict(
        series_id=series.id,
        club_id=club_id,
        name="Lesson",
        event_type="lesson",
        recurrence_anchor_at=starts_at,
        starts_at=starts_at,
        ends_at=ends_at,
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


_WIDE_RANGE = _range(_START - datetime.timedelta(days=365), _START + datetime.timedelta(days=365))


def _conflicts(client: TestClient, params: dict | None = None):
    return client.get("/api/v1/events/conflicts", params={**_WIDE_RANGE, **(params or {})})


def _parse_ts(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _pair_ids(body: dict) -> set[frozenset[str]]:
    return {
        frozenset({item["first_object"]["object_id"], item["second_object"]["object_id"]})
        for item in body["items"]
    }


# --- Interval overlap semantics (module-level helper, no HTTP) -------------


@requires_postgres
def test_overlapping_intervals_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=1)
        )
        e2 = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(minutes=30),
            end_at=_START + datetime.timedelta(hours=2),
        )
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id, e1_id, e2_id = club.id, user.id, e1.id, e2.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    conflict = body["items"][0]
    assert conflict["domain"] == "instructor"
    assert {e1_id.__str__(), e2_id.__str__()} == {
        conflict["first_object"]["object_id"],
        conflict["second_object"]["object_id"],
    }
    assert _parse_ts(conflict["overlap_start_at"]) == _START + datetime.timedelta(minutes=30)
    assert _parse_ts(conflict["overlap_end_at"]) == _START + datetime.timedelta(hours=1)


@requires_postgres
def test_touching_boundary_does_not_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=1)
        )
        # e2 starts exactly when e1 ends.
        e2 = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(hours=1),
            end_at=_START + datetime.timedelta(hours=2),
        )
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_exact_same_interval_conflicts(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=1)
        )
        e2 = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=1)
        )
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 1


@requires_postgres
def test_contained_interval_conflicts(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        outer = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=4)
        )
        inner = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(hours=1),
            end_at=_START + datetime.timedelta(hours=2),
        )
        session.add_all([outer, inner])
        session.commit()
        session.add(_make_event_staff_assignment(outer, user))
        session.add(_make_event_staff_assignment(inner, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert _parse_ts(body["items"][0]["overlap_start_at"]) == _START + datetime.timedelta(hours=1)
    assert _parse_ts(body["items"][0]["overlap_end_at"]) == _START + datetime.timedelta(hours=2)


@requires_postgres
def test_no_overlap_no_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(
            session, club, start_at=_START, end_at=_START + datetime.timedelta(hours=1)
        )
        e2 = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(hours=5),
            end_at=_START + datetime.timedelta(hours=6),
        )
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


# --- Object combinations ----------------------------------------------------


@requires_postgres
def test_event_event_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert {
        body["items"][0]["first_object"]["object_type"],
        body["items"][0]["second_object"]["object_type"],
    } == {"event"}


@requires_postgres
def test_event_occurrence_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        event = _make_event(session, club)
        session.add(event)
        session.commit()
        session.add(_make_event_staff_assignment(event, user))
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(minutes=30)
        )
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ, user))
        session.commit()
        club_id, user_id, event_id, occ_id = club.id, user.id, event.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    types = {
        body["items"][0]["first_object"]["object_type"],
        body["items"][0]["second_object"]["object_type"],
    }
    assert types == {"event", "occurrence"}
    ids = _pair_ids(body)
    assert frozenset({str(event_id), str(occ_id)}) in ids


@requires_postgres
def test_occurrence_occurrence_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ1 = _make_occurrence(series=series, club_id=club.id, starts_at=_START)
        occ2 = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(minutes=30)
        )
        session.add_all([occ1, occ2])
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ1, user))
        session.add(_make_occurrence_staff_assignment(occ2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert {
        body["items"][0]["first_object"]["object_type"],
        body["items"][0]["second_object"]["object_type"],
    } == {"occurrence"}


# --- Instructor domain -------------------------------------------------------


@requires_postgres
def test_instructor_domain_positive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "instructor"


@requires_postgres
def test_instructor_domain_different_instructors_no_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person()
        user_a = _make_user(person_a)
        person_b = _make_person()
        user_b = _make_user(person_b)
        session.add_all([club, person_a, user_a, person_b, user_b])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user_a))
        session.add(_make_event_staff_assignment(e2, user_b))
        session.commit()
        club_id, user_a_id = club.id, user_a.id
    _grant_permission(user_a_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_a_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_instructor_domain_unauthorized_opposing_event_is_hidden(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        visible_event = _make_event(session, club)
        hidden_event = _make_event(
            session, other_club, start_at=_START + datetime.timedelta(minutes=30)
        )
        session.add_all([visible_event, hidden_event])
        session.commit()
        session.add(_make_event_staff_assignment(visible_event, user))
        session.add(_make_event_staff_assignment(hidden_event, user))
        session.commit()
        club_id, user_id, hidden_id = club.id, user.id, hidden_event.id
    # Only authorized in `club`, never `other_club`.
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 0
    assert str(hidden_id) not in {item["first_object"]["object_id"] for item in body["items"]} | {
        item["second_object"]["object_id"] for item in body["items"]
    }


# --- Group domain -------------------------------------------------------


@requires_postgres
def test_group_domain_positive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_group_target(e1, group))
        session.add(_make_event_group_target(e2, group))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "group"


@requires_postgres
def test_group_domain_different_groups_no_conflict(client: TestClient) -> None:
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
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_group_target(e1, group_a))
        session.add(_make_event_group_target(e2, group_b))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_group_membership_alone_does_not_create_group_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        # Neither event has an EventGroupTarget for `group` -- membership
        # alone must never create a group-domain conflict.
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


# --- Participant domain -------------------------------------------------------


@requires_postgres
def test_participant_domain_positive(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_participation(e1, person))
        session.add(_make_event_participation(e2, person))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "participant"


@requires_postgres
def test_participant_domain_different_participants_no_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person()
        user_a = _make_user(person_a)
        person_b = _make_person()
        session.add_all([club, person_a, user_a, person_b])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_participation(e1, person_a))
        session.add(_make_event_participation(e2, person_b))
        session.commit()
        club_id, user_a_id = club.id, user_a.id
    _grant_permission(user_a_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_a_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_participant_group_membership_alone_does_not_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        # No EventParticipation anywhere -- GroupMembership alone must not
        # create a participant conflict.
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_participant_guardian_relationship_alone_does_not_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        session.add_all([club, guardian_person, guardian_user, child_person])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        # No EventParticipation for the child anywhere.
        club_id, guardian_user_id = club.id, guardian_user.id
    _grant_permission(guardian_user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


# --- Lifecycle ---------------------------------------------------------------


@pytest.mark.parametrize("status_value", ["published", "in_progress"])
@requires_postgres
def test_event_operational_statuses_included(client: TestClient, status_value: str) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club, status=status_value)
        e2 = _make_event(
            session, club, start_at=_START + datetime.timedelta(minutes=30), status=status_value
        )
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 1


@pytest.mark.parametrize("status_value", ["draft", "completed", "cancelled", "archived"])
@requires_postgres
def test_event_non_operational_statuses_excluded(client: TestClient, status_value: str) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        extra: dict[str, object] = (
            {"cancellation_reason": "test"} if status_value == "cancelled" else {}
        )
        e1 = _make_event(session, club, status=status_value, **extra)
        e2 = _make_event(
            session,
            club,
            start_at=_START + datetime.timedelta(minutes=30),
            status=status_value,
            **extra,
        )
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@pytest.mark.parametrize("status_value", ["scheduled", "in_progress"])
@requires_postgres
def test_occurrence_operational_statuses_included(client: TestClient, status_value: str) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ1 = _make_occurrence(series=series, club_id=club.id, status=status_value)
        occ2 = _make_occurrence(
            series=series,
            club_id=club.id,
            starts_at=_START + datetime.timedelta(minutes=30),
            status=status_value,
        )
        session.add_all([occ1, occ2])
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ1, user))
        session.add(_make_occurrence_staff_assignment(occ2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 1


@pytest.mark.parametrize("status_value", ["completed", "cancelled"])
@requires_postgres
def test_occurrence_non_operational_statuses_excluded(
    client: TestClient, status_value: str
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        extra: dict[str, object] = (
            {"cancellation_reason": "test"} if status_value == "cancelled" else {}
        )
        occ1 = _make_occurrence(series=series, club_id=club.id, status=status_value, **extra)
        occ2 = _make_occurrence(
            series=series,
            club_id=club.id,
            starts_at=_START + datetime.timedelta(minutes=30),
            status=status_value,
            **extra,
        )
        session.add_all([occ1, occ2])
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ1, user))
        session.add(_make_occurrence_staff_assignment(occ2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


# --- Recurrence --------------------------------------------------------------


@requires_postgres
def test_rescheduled_occurrence_uses_effective_time(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        # Occurrence originally scheduled well away from e2, then
        # rescheduled (exception) to actually overlap.
        occ = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(hours=10)
        )
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_staff_assignment(occ, user))
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add(e2)
        session.commit()
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()

        set_occurrence_exception(
            session,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=_START,
            effective_end_at=_START + datetime.timedelta(hours=1),
            overrides=None,
            cancellation_reason=None,
            actor_user_id=user.id,
            request_id="test-request",
        )
        club_id, user_id, occ_id, e2_id = club.id, user.id, occ.id, e2.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert frozenset({str(occ_id), str(e2_id)}) in _pair_ids(body)


@requires_postgres
def test_materialization_extends_beyond_default_horizon_and_still_conflicts(
    client: TestClient,
) -> None:
    far_future_start = _START + datetime.timedelta(days=200)
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        session.add(_make_club_membership(club, person))
        session.commit()
        series = _make_series(session, club_id=club.id, series_start_at=far_future_start)
        create_series_staff_assignment(
            session,
            event_series_id=series.id,
            user_id=user.id,
            role_in_event="instructor",
            valid_from=_utc(2020, 1, 1),
            actor_user_id=user.id,
            request_id="test-request",
        )
        e2 = _make_event(session, club, start_at=far_future_start + datetime.timedelta(minutes=30))
        session.add(e2)
        session.commit()
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id, series_id, e2_id = club.id, user.id, series.id, e2.id

    with session_scope() as verify:
        # Confirm the far-future series has NOT been materialized yet --
        # the default 180-day horizon does not reach 200 days out.
        assert (
            verify.execute(select(EventOccurrence).where(EventOccurrence.series_id == series_id))
            .scalars()
            .first()
            is None
        )

    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(
        client,
        {
            "from": (far_future_start - datetime.timedelta(days=1)).isoformat(),
            "to": (far_future_start + datetime.timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1

    with session_scope() as verify:
        materialized = (
            verify.execute(select(EventOccurrence).where(EventOccurrence.series_id == series_id))
            .scalars()
            .all()
        )
        assert len(materialized) >= 1
        occ_id = str(materialized[0].id)
    assert frozenset({occ_id, str(e2_id)}) in _pair_ids(body)


# --- Authorization: canonical scopes -----------------------------------------


@requires_postgres
def test_own_events_scope_allows_conflict_between_staffed_events(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.add(_make_event_group_target(e1, group))
        session.add(_make_event_group_target(e2, group))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    # Both a shared instructor (the caller, staffed on both -- which is
    # also exactly what makes both events own_events-visible) and a shared
    # group target legitimately produce two distinct derived conflicts for
    # the same underlying pair.
    assert body["pagination"]["total"] == 2
    assert {item["domain"] for item in body["items"]} == {"instructor", "group"}


@requires_postgres
def test_own_events_scope_denies_when_opposing_event_not_staffed(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        # Only e1 is staffed by the caller -- e2 stays invisible under
        # own_events even though both share a group target.
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_group_target(e1, group))
        session.add(_make_event_group_target(e2, group))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_assigned_events_alias_behaves_identically_to_own_events(client: TestClient) -> None:
    assert normalize_scope_type("assigned_events") == "own_events"
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(
        user_id, "event.read", scope_type=normalize_scope_type("assigned_events"), club_id=club_id
    )
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 1


@requires_postgres
def test_own_groups_scope_allows_visible_pair_via_independent_domain(client: TestClient) -> None:
    """own_groups visibility (two *different* groups, so it never itself
    creates a group-domain match) is independent of the actual conflict
    domain being detected (here: participant)."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        participant = _make_person()
        session.add_all([club, person, user, participant])
        session.commit()
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([group_a, group_b])
        session.commit()
        session.add(_make_group_instructor_assignment(group_a, user))
        session.add(_make_group_instructor_assignment(group_b, user))
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_group_target(e1, group_a))
        session.add(_make_event_group_target(e2, group_b))
        session.add(_make_event_participation(e1, participant))
        session.add(_make_event_participation(e2, participant))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "participant"


@requires_postgres
def test_own_groups_scope_denies_when_opposing_event_not_group_targeted(client: TestClient) -> None:
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
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_group_target(e1, group))
        # e2 has no target at all for `group`.
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_self_scope_allows_own_participation_conflict(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_participation(e1, person))
        session.add(_make_event_participation(e2, person))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "participant"


@requires_postgres
def test_self_scope_denies_when_not_a_participant(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        other_person = _make_person()
        session.add_all([club, person, user, other_person])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_participation(e1, other_person))
        session.add(_make_event_participation(e2, other_person))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_children_scope_allows_eligible_child_participation_conflict(client: TestClient) -> None:
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
        session.add(_make_guardian_relationship(guardian_person, child_person))
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_participation(e1, child_person))
        session.add(_make_event_participation(e2, child_person))
        session.commit()
        club_id, guardian_user_id = club.id, guardian_user.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "participant"


@requires_postgres
def test_children_scope_denies_unrelated_child(client: TestClient) -> None:
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
        # No GuardianRelationship at all -- this is the one condition
        # under test; ClubMembership for both is otherwise satisfied.
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_participation(e1, unrelated_child))
        session.add(_make_event_participation(e2, unrelated_child))
        session.commit()
        club_id, guardian_user_id = club.id, guardian_user.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_none_scope_denies_everything(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="none", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


# --- Cross-Club IDOR / no leakage --------------------------------------------


@requires_postgres
def test_cross_club_group_domain_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        group = _make_group(other_club)
        session.add(group)
        session.commit()
        visible_event = _make_event(session, club)
        hidden_event = _make_event(
            session, other_club, start_at=_START + datetime.timedelta(minutes=30)
        )
        session.add_all([visible_event, hidden_event])
        session.commit()
        session.add(_make_event_group_target(visible_event, group))
        session.add(_make_event_group_target(hidden_event, group))
        session.commit()
        club_id, user_id, hidden_id = club.id, user.id, hidden_event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 0
    assert str(hidden_id) not in {i["first_object"]["object_id"] for i in body["items"]} | {
        i["second_object"]["object_id"] for i in body["items"]
    }


@requires_postgres
def test_no_leakage_through_count_or_pagination(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        # Two authorized conflicting pairs in `club`.
        accessible_ids = []
        for i in range(2):
            e1 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=i * 3))
            e2 = _make_event(
                session, club, start_at=_START + datetime.timedelta(hours=i * 3, minutes=30)
            )
            session.add_all([e1, e2])
            session.commit()
            session.add(_make_event_staff_assignment(e1, user))
            session.add(_make_event_staff_assignment(e2, user))
            session.commit()
            accessible_ids += [str(e1.id), str(e2.id)]
        # One inaccessible pair in `other_club` -- must never be counted.
        h1 = _make_event(session, other_club, start_at=_START + datetime.timedelta(hours=20))
        h2 = _make_event(
            session, other_club, start_at=_START + datetime.timedelta(hours=20, minutes=30)
        )
        session.add_all([h1, h2])
        session.commit()
        session.add(_make_event_staff_assignment(h1, user))
        session.add(_make_event_staff_assignment(h2, user))
        session.commit()
        club_id, user_id, hidden_ids = club.id, user.id, {str(h1.id), str(h2.id)}
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client, {"page_size": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["pages"] == 2
    seen = {
        i
        for item in body["items"]
        for i in (item["first_object"]["object_id"], item["second_object"]["object_id"])
    }
    assert seen.issubset(set(accessible_ids))
    assert seen.isdisjoint(hidden_ids)


# --- Filters: user_id / group_id narrow only ---------------------------------


@requires_postgres
def test_user_id_filter_narrows_to_instructor_domain_for_that_user(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person()
        user_a = _make_user(person_a)
        person_b = _make_person()
        user_b = _make_user(person_b)
        session.add_all([club, person_a, user_a, person_b, user_b])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        e3 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=5))
        e4 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=5, minutes=30))
        session.add_all([e1, e2, e3, e4])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user_a))
        session.add(_make_event_staff_assignment(e2, user_a))
        session.add(_make_event_staff_assignment(e3, user_b))
        session.add(_make_event_staff_assignment(e4, user_b))
        session.commit()
        club_id, user_a_id, user_b_id = club.id, user_a.id, user_b.id
    _grant_permission(user_a_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_a_id)

    response = _conflicts(client, {"user_id": str(user_b_id)})
    assert response.status_code == 200
    # user_a is authorized to see everything (all scope), but the filter
    # narrows results to conflicts involving user_b as the shared instructor.
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "instructor"


@requires_postgres
def test_group_id_filter_narrows_to_group_domain_for_that_group(client: TestClient) -> None:
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
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        e3 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=5))
        e4 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=5, minutes=30))
        session.add_all([e1, e2, e3, e4])
        session.commit()
        session.add(_make_event_group_target(e1, group_a))
        session.add(_make_event_group_target(e2, group_a))
        session.add(_make_event_group_target(e3, group_b))
        session.add(_make_event_group_target(e4, group_b))
        session.commit()
        club_id, user_id, group_b_id = club.id, user.id, group_b.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client, {"group_id": str(group_b_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["domain"] == "group"


@requires_postgres
def test_filter_cannot_bypass_authorization(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([club, other_club, person, user, other_person, other_user])
        session.commit()
        h1 = _make_event(session, other_club)
        h2 = _make_event(session, other_club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([h1, h2])
        session.commit()
        session.add(_make_event_staff_assignment(h1, other_user))
        session.add(_make_event_staff_assignment(h2, other_user))
        session.commit()
        club_id, user_id, other_user_id = club.id, user.id, other_user.id
    # Requester only ever authorized in `club`, never `other_club`.
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client, {"user_id": str(other_user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 0
    assert body["items"] == []


# --- Deterministic identity / ordering ----------------------------------------


@requires_postgres
def test_conflict_id_is_deterministic_across_repeated_queries(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    first = _conflicts(client).json()["items"][0]["id"]
    second = _conflicts(client).json()["items"][0]["id"]
    assert first == second


@requires_postgres
def test_object_pair_order_does_not_change_the_id(client: TestClient) -> None:
    """The same unordered pair (regardless of which object happens to sort
    first/second in a given response) must always yield the same id --
    verified directly against the reusable detector, independent of which
    of the two objects the DB engine happens to list first."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id, e1_id, e2_id = club.id, user.id, e1.id, e2.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    body = _conflicts(client).json()
    conflict_id = body["items"][0]["id"]
    # The id must be exactly domain:kind:id:kind:id for the canonically
    # (kind, id)-sorted pair, independent of insertion/creation order.
    first, second = sorted([("event", str(e1_id)), ("event", str(e2_id))])
    assert conflict_id == f"instructor:{first[0]}:{first[1]}:{second[0]}:{second[1]}"


@requires_postgres
def test_deterministic_ordering_by_overlap_start_then_id(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        # Pair 1: overlap starts later.
        a1 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=5))
        a2 = _make_event(session, club, start_at=_START + datetime.timedelta(hours=5, minutes=30))
        # Pair 2: overlap starts earlier.
        b1 = _make_event(session, club, start_at=_START)
        b2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([a1, a2, b1, b2])
        session.commit()
        session.add(_make_event_staff_assignment(a1, user))
        session.add(_make_event_staff_assignment(a2, user))
        session.add(_make_event_staff_assignment(b1, user))
        session.add(_make_event_staff_assignment(b2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    body = _conflicts(client).json()
    assert body["pagination"]["total"] == 2
    starts = [_parse_ts(item["overlap_start_at"]) for item in body["items"]]
    assert starts == sorted(starts)


# --- API contract -------------------------------------------------------------


@requires_postgres
def test_missing_from_is_rejected(client: TestClient) -> None:
    user_id = uuid.uuid4()
    _authenticate_as(user_id)
    response = client.get("/api/v1/events/conflicts", params={"to": _START.isoformat()})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_from"


@requires_postgres
def test_missing_to_is_rejected(client: TestClient) -> None:
    user_id = uuid.uuid4()
    _authenticate_as(user_id)
    response = client.get("/api/v1/events/conflicts", params={"from": _START.isoformat()})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_to"


@requires_postgres
def test_naive_timestamp_is_rejected(client: TestClient) -> None:
    user_id = uuid.uuid4()
    _authenticate_as(user_id)
    response = client.get(
        "/api/v1/events/conflicts",
        params={"from": "2026-09-20T00:00:00", "to": "2026-09-21T00:00:00"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "naive_timestamp"


@requires_postgres
def test_invalid_range_is_rejected(client: TestClient) -> None:
    user_id = uuid.uuid4()
    _authenticate_as(user_id)
    response = client.get(
        "/api/v1/events/conflicts",
        params=_range(_START + datetime.timedelta(days=1), _START),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_range"


@requires_postgres
def test_empty_result_is_a_valid_200(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0
    assert body["pagination"]["pages"] == 0


@requires_postgres
def test_pagination_envelope_shape(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        e1 = _make_event(session, club)
        e2 = _make_event(session, club, start_at=_START + datetime.timedelta(minutes=30))
        session.add_all([e1, e2])
        session.commit()
        session.add(_make_event_staff_assignment(e1, user))
        session.add(_make_event_staff_assignment(e2, user))
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _conflicts(client, {"page": 1, "page_size": 10})
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert set(body["pagination"].keys()) == {"page", "page_size", "total", "pages"}
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 10
