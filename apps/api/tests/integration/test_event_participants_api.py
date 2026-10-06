"""HTTP-level integration tests for `GET /api/v1/events/{event_id}/participants`
(events-api.md §18; backend gap found while preparing Issue #175).

Covers: the registered-only participant set (cancelled and any other
non-`registered` row excluded in SQL), the minimal Person projection,
deterministic order, canonical pagination, `event.read` object
authorization with existence-hiding 404, per-scope participant-row
visibility (all/own_events/self/children, ADR-0020 §2-§3 + the ADR-0032
row rule), and a constant query count (no N+1).

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_event_attendance_api.py.
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.events import Event, EventParticipation, EventStaffAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import get_engine, session_scope
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": f"L-{uuid.uuid4().hex[:8]}",
        "first_name": f"P-{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_user(person: Person) -> User:
    return User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )


def _make_club_membership(club: Club, person: Person) -> ClubMembership:
    return ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="student",
        status="active",
        joined_at=_utc(2020, 1, 1),
    )


def _make_event(session, club: Club, **overrides: object) -> Event:
    """ADR-0033: every Event has exactly one linked EventOccurrence."""
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "competition",
        "title": "Соревнование",
        "start_at": _START,
        "end_at": _START + datetime.timedelta(hours=3),
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
            event_type=event.event_type,
            recurrence_anchor_at=event.start_at,
            starts_at=event.start_at,
            ends_at=event.end_at,
            timezone=event.timezone,
            status="scheduled",
        )
    )
    return event


def _participation(event: Event, person: Person, status: str = "registered") -> EventParticipation:
    return EventParticipation(event_id=event.id, person_id=person.id, registration_status=status)


def _grant_permission(
    user_id: uuid.UUID, permission_code: str, scope_type: str, club_id: uuid.UUID
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
        session.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                scopes=[RolePermissionScope(scope_type=scope_type)],
            )
        )
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, club_id=club_id))
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _get(client: TestClient, event_id: uuid.UUID, **params: object):
    return client.get(f"/api/v1/events/{event_id}/participants", params=params)


def _new_actor(session) -> User:
    person = _make_person()
    user = _make_user(person)
    session.add_all([person, user])
    return user


def _admin_setup(
    participants: list[tuple[dict, str]],
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Creates a Club, an Event, Persons with the given (person fields,
    registration_status) pairs, and an actor with `event.read`/`all`.
    Returns (event_id, actor_user_id, person_ids in input order)."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        actor = _new_actor(session)
        session.add(club)
        session.flush()
        event = _make_event(session, club)
        persons = [_make_person(**fields) for fields, _status in participants]
        session.add_all(persons)
        session.flush()
        statuses = [status for _fields, status in participants]
        session.add_all(
            [_participation(event, person, status) for person, status in zip(persons, statuses)]
        )
        session.commit()
        ids = (event.id, actor.id, club.id, [p.id for p in persons])
    event_id, actor_id, club_id, person_ids = ids
    _grant_permission(actor_id, "event.read", "all", club_id)
    _authenticate_as(actor_id)
    return event_id, actor_id, person_ids


# --- Happy path / projection / order ----------------------------------------


@requires_postgres
def test_returns_all_registered_participants_with_projection_and_order(
    client: TestClient,
) -> None:
    event_id, _actor, (p_b, p_a, p_c) = _admin_setup(
        [
            (
                {"last_name": "Борисова", "first_name": "Анна", "middle_name": "Петровна"},
                "registered",
            ),
            ({"last_name": "Алексеев", "first_name": "Иван"}, "registered"),
            ({"last_name": "Борисова", "first_name": "Вера"}, "registered"),
        ]
    )

    response = _get(client, event_id)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {
            "person_id": str(p_a),
            "first_name": "Иван",
            "last_name": "Алексеев",
            "middle_name": None,
        },
        {
            "person_id": str(p_b),
            "first_name": "Анна",
            "last_name": "Борисова",
            "middle_name": "Петровна",
        },
        {
            "person_id": str(p_c),
            "first_name": "Вера",
            "last_name": "Борисова",
            "middle_name": None,
        },
    ]
    assert body["pagination"] == {"page": 1, "page_size": 50, "total": 3, "pages": 1}


@requires_postgres
def test_event_without_participants_returns_empty_list(client: TestClient) -> None:
    event_id, _actor, _ids = _admin_setup([])

    response = _get(client, event_id)

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "pagination": {"page": 1, "page_size": 50, "total": 0, "pages": 0},
    }


@requires_postgres
def test_participants_of_other_events_are_not_returned(client: TestClient) -> None:
    event_id, actor_id, (registered,) = _admin_setup([({}, "registered")])
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        club = session.get(Club, event.club_id)
        other_event = _make_event(session, club)
        outsider = _make_person()
        session.add(outsider)
        session.flush()
        session.add(_participation(other_event, outsider))
        session.commit()

    response = _get(client, event_id)

    assert [item["person_id"] for item in response.json()["items"]] == [str(registered)]


# --- Cancellation / non-registered statuses ----------------------------------


@requires_postgres
def test_cancelled_participation_is_excluded(client: TestClient) -> None:
    event_id, _actor, (registered, cancelled) = _admin_setup(
        [({}, "registered"), ({}, "cancelled")]
    )

    body = _get(client, event_id).json()

    person_ids = [item["person_id"] for item in body["items"]]
    assert person_ids == [str(registered)]
    assert str(cancelled) not in person_ids
    assert body["pagination"]["total"] == 1


@requires_postgres
def test_event_with_only_cancelled_participation_returns_empty_list(client: TestClient) -> None:
    event_id, _actor, _ids = _admin_setup([({}, "cancelled"), ({}, "cancelled")])

    body = _get(client, event_id).json()

    assert body["items"] == []
    assert body["pagination"]["total"] == 0


@requires_postgres
def test_other_non_registered_reference_statuses_are_excluded(client: TestClient) -> None:
    # ADR-0020 §4 reference values are not "registered participants".
    event_id, _actor, (registered, *_rest) = _admin_setup(
        [({}, "registered"), ({}, "invited"), ({}, "waitlisted"), ({}, "declined"), ({}, "removed")]
    )

    body = _get(client, event_id).json()

    assert [item["person_id"] for item in body["items"]] == [str(registered)]


@requires_postgres
def test_cancelling_registration_removes_participant_from_list(client: TestClient) -> None:
    event_id, _actor, (person_id,) = _admin_setup([({}, "registered")])
    assert _get(client, event_id).json()["pagination"]["total"] == 1

    with session_scope() as session:
        row = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id, EventParticipation.person_id == person_id
            )
        ).scalar_one()
        row.registration_status = "cancelled"
        session.commit()

    assert _get(client, event_id).json()["items"] == []


# --- Pagination ---------------------------------------------------------------


@requires_postgres
def test_pagination_counts_only_registered_and_pages_deterministically(
    client: TestClient,
) -> None:
    participants = [({"last_name": f"Участник-{i:02d}"}, "registered") for i in range(5)]
    participants += [({"last_name": f"Отменён-{i:02d}"}, "cancelled") for i in range(3)]
    event_id, _actor, person_ids = _admin_setup(participants)
    registered_ids = [str(pid) for pid in person_ids[:5]]

    page1 = _get(client, event_id, page=1, page_size=2).json()
    page2 = _get(client, event_id, page=2, page_size=2).json()
    page3 = _get(client, event_id, page=3, page_size=2).json()
    page4 = _get(client, event_id, page=4, page_size=2).json()

    for body, page in ((page1, 1), (page2, 2), (page3, 3), (page4, 4)):
        assert body["pagination"] == {"page": page, "page_size": 2, "total": 5, "pages": 3}
    collected = [item["person_id"] for body in (page1, page2, page3) for item in body["items"]]
    assert collected == registered_ids
    assert page4["items"] == []

    # Re-requesting a page yields exactly the same result.
    assert _get(client, event_id, page=2, page_size=2).json() == page2


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
@requires_postgres
def test_invalid_pagination_parameters_are_rejected(client: TestClient, params: dict) -> None:
    event_id, _actor, _ids = _admin_setup([])

    assert _get(client, event_id, **params).status_code == 422


@requires_postgres
def test_query_count_does_not_grow_with_participants(client: TestClient) -> None:
    event_id, _actor, _ids = _admin_setup([({}, "registered") for _ in range(12)])
    engine = get_engine()
    statements: list[str] = []

    def _count(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _count)
    try:
        _get(client, event_id, page_size=2)
        small = len(statements)
        statements.clear()
        _get(client, event_id, page_size=12)
        large = len(statements)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _count)

    assert small == large


# --- Errors / existence hiding --------------------------------------------------


@requires_postgres
def test_nonexistent_event_is_404(client: TestClient) -> None:
    _admin_setup([])

    response = _get(client, uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Event not found"


@requires_postgres
def test_event_without_event_read_is_existence_hidden_404(client: TestClient) -> None:
    event_id, _actor, _ids = _admin_setup([({}, "registered")])
    with session_scope() as session:
        stranger = _new_actor(session)
        session.commit()
        stranger_id = stranger.id
    _authenticate_as(stranger_id)

    response = _get(client, event_id)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Event not found"


@requires_postgres
def test_event_read_in_another_club_does_not_grant_access(client: TestClient) -> None:
    event_id, _actor, _ids = _admin_setup([({}, "registered")])
    with session_scope() as session:
        other_club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        stranger = _new_actor(session)
        session.add(other_club)
        session.commit()
        other_club_id, stranger_id = other_club.id, stranger.id
    _grant_permission(stranger_id, "event.read", "all", other_club_id)
    _authenticate_as(stranger_id)

    assert _get(client, event_id).status_code == 404


@requires_postgres
def test_recurring_occurrence_id_is_not_an_event_and_is_404(client: TestClient) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        actor = _new_actor(session)
        session.add(club)
        session.flush()
        series_id = uuid.uuid4()
        series = EventSeries(
            id=series_id,
            root_series_id=series_id,
            supersedes_series_id=None,
            version=1,
            club_id=club.id,
            name="Weekly lesson",
            event_type="lesson",
            series_start_at=_START,
            duration_minutes=60,
            recurrence_rule="FREQ=DAILY",
            timezone="UTC",
            status="active",
        )
        session.add(series)
        session.flush()
        occurrence = EventOccurrence(
            series_id=series.id,
            club_id=club.id,
            name="Lesson",
            event_type="lesson",
            recurrence_anchor_at=_START,
            starts_at=_START,
            ends_at=_START + datetime.timedelta(hours=1),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occurrence)
        session.commit()
        club_id, actor_id, occurrence_id = club.id, actor.id, occurrence.id
    _grant_permission(actor_id, "event.read", "all", club_id)
    _authenticate_as(actor_id)

    assert _get(client, occurrence_id).status_code == 404


# --- Scope-based participant-row visibility -----------------------------------


@requires_postgres
def test_instructor_own_events_sees_full_registered_set(client: TestClient) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        instructor = _new_actor(session)
        session.add(club)
        session.flush()
        event = _make_event(session, club)
        p1, p2, cancelled = _make_person(), _make_person(), _make_person()
        session.add_all([p1, p2, cancelled])
        session.flush()
        session.add_all(
            [
                _participation(event, p1),
                _participation(event, p2),
                _participation(event, cancelled, "cancelled"),
                EventStaffAssignment(
                    event_id=event.id,
                    user_id=instructor.id,
                    role_in_event="instructor",
                    valid_from=_utc(2020, 1, 1),
                ),
            ]
        )
        session.commit()
        club_id, instructor_id, event_id = club.id, instructor.id, event.id
        expected = {str(p1.id), str(p2.id)}
    _grant_permission(instructor_id, "event.read", "own_events", club_id)
    _authenticate_as(instructor_id)

    body = _get(client, event_id).json()

    assert {item["person_id"] for item in body["items"]} == expected
    assert body["pagination"]["total"] == 2


@requires_postgres
def test_instructor_own_events_without_assignment_is_404(client: TestClient) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        instructor = _new_actor(session)
        session.add(club)
        session.flush()
        event = _make_event(session, club)
        session.commit()
        club_id, instructor_id, event_id = club.id, instructor.id, event.id
    _grant_permission(instructor_id, "event.read", "own_events", club_id)
    _authenticate_as(instructor_id)

    assert _get(client, event_id).status_code == 404


@requires_postgres
def test_member_self_scope_sees_only_own_row(client: TestClient) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        member_person = _make_person()
        member_user = _make_user(member_person)
        other = _make_person()
        session.add_all([member_person, member_user, other])
        session.flush()
        # Issue #285: the club-wide Event is visible through the Member's
        # active ClubMembership (Member Event object policy); the row
        # restriction to the Member's own participation is unchanged.
        session.add(_make_club_membership(club, member_person))
        event = _make_event(session, club)
        session.add_all([_participation(event, member_person), _participation(event, other)])
        session.commit()
        club_id, member_id, event_id, member_person_id = (
            club.id,
            member_user.id,
            event.id,
            member_person.id,
        )
    _grant_permission(member_id, "event.read", "self", club_id)
    _authenticate_as(member_id)

    body = _get(client, event_id).json()

    assert [item["person_id"] for item in body["items"]] == [str(member_person_id)]
    assert body["pagination"]["total"] == 1


@requires_postgres
def test_member_with_cancelled_own_participation_sees_empty_list(client: TestClient) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        member_person = _make_person()
        member_user = _make_user(member_person)
        other = _make_person()
        session.add_all([member_person, member_user, other])
        session.flush()
        session.add(_make_club_membership(club, member_person))
        event = _make_event(session, club)
        session.add_all(
            [_participation(event, member_person, "cancelled"), _participation(event, other)]
        )
        session.commit()
        club_id, member_id, event_id = club.id, member_user.id, event.id
    _grant_permission(member_id, "event.read", "self", club_id)
    _authenticate_as(member_id)

    response = _get(client, event_id)

    assert response.status_code == 200
    assert response.json()["items"] == []


@requires_postgres
def test_member_self_scope_without_club_membership_is_404(client: TestClient) -> None:
    # Issue #285: under the Member Event object policy the requester needs an
    # active ClubMembership in the Event's Club; this one has none.
    event_id, _actor, _ids = _admin_setup([({}, "registered")])
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        member = _new_actor(session)
        session.commit()
        member_id, club_id = member.id, event.club_id
    _grant_permission(member_id, "event.read", "self", club_id)
    _authenticate_as(member_id)

    assert _get(client, event_id).status_code == 404


@requires_postgres
def test_guardian_children_scope_sees_only_registered_child_rows(client: TestClient) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child = _make_person()
        cancelled_child = _make_person()
        unrelated = _make_person()
        session.add_all([guardian_person, guardian_user, child, cancelled_child, unrelated])
        session.flush()
        session.add_all(
            [
                _make_club_membership(club, guardian_person),
                _make_club_membership(club, child),
                _make_club_membership(club, cancelled_child),
            ]
        )
        for kid in (child, cancelled_child):
            session.add(
                GuardianRelationship(
                    guardian_person_id=guardian_person.id,
                    child_person_id=kid.id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_utc(2020, 1, 1),
                )
            )
        event = _make_event(session, club)
        session.add_all(
            [
                _participation(event, child),
                _participation(event, cancelled_child, "cancelled"),
                _participation(event, unrelated),
            ]
        )
        session.commit()
        club_id, guardian_id, event_id, child_id = club.id, guardian_user.id, event.id, child.id
    _grant_permission(guardian_id, "event.read", "children", club_id)
    _authenticate_as(guardian_id)

    body = _get(client, event_id).json()

    assert [item["person_id"] for item in body["items"]] == [str(child_id)]
    assert body["pagination"]["total"] == 1
