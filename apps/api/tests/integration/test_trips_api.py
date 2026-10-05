"""HTTP-level integration tests for `/api/v1/trips` (Issue #245).

Covers, against the REAL shipped app and a real PostgreSQL database:

- Trip creation as the 1:0..1 extension of an existing ordinary
  `Event(type=trip)`: allowed for every Event status except `cancelled`/
  `archived`, at most once, never for another Event type, never for an
  EventSeries/EventOccurrence id; the Trip has no lifecycle of its own and
  an Event with a Trip cannot leave the `trip` type.
- TripParticipant as the extension of an existing EventParticipation:
  `actual_participation` lifecycle (`in_progress` create/change,
  `completed` create-only then historically closed, every other status
  closed), the participation prerequisite, and — the central guarantee —
  that it never becomes a second registration source: recording never
  touches EventParticipation, cancelling the registration never deletes
  or resets the tourism fact, and Attendance stays separate.
- Authorization through the migration-seeded baseline roles (admin all;
  instructor own_groups/own_events; member self; guardian children) and
  the existing Event authorization infrastructure, with the Event API's
  existence-hiding 404.
- Unauthenticated, CSRF, invalid identifier and invalid body handling.

Self-contained factories, per this codebase's convention of not
importing helpers across test files.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.attendance import Attendance
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.db.trips import Trip, TripParticipant
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.timezone.utc)
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)

_EVENT_TO_OCCURRENCE_STATUS = {
    "draft": "scheduled",
    "published": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
    "archived": "completed",
}


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories -------------------------------------------------------------------


def _person(session, last_name: str = "Ivanova") -> Person:  # type: ignore[no-untyped-def]
    person = Person(last_name=last_name, first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _club_membership(session, club: Club, person: Person) -> ClubMembership:  # type: ignore[no-untyped-def]
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


def _user_with_role(session, club: Club, role_code: str, person: Person | None = None) -> User:  # type: ignore[no-untyped-def]
    """A real user of one canonical, migration-seeded baseline role — no
    ad hoc grant: authorization comes only from the seeded matrix."""
    person = person or _person(session)
    _club_membership(session, club, person)
    user = User(
        person=person,
        login_identifier=f"{role_code}-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()
    return user


def _event(  # type: ignore[no-untyped-def]
    session, club: Club, *, event_type: str = "trip", status: str = "published"
) -> Event:
    """ADR-0033: an ordinary Event always has its one linked occurrence."""
    event = Event(
        id=uuid.uuid4(),
        club_id=club.id,
        event_type=event_type,
        title="Поход",
        start_at=_START,
        end_at=_START + datetime.timedelta(days=2),
        timezone="UTC",
        status=status,
        cancellation_reason="weather" if status == "cancelled" else None,
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
            status=_EVENT_TO_OCCURRENCE_STATUS[status],
            cancellation_reason=event.cancellation_reason,
        )
    )
    session.flush()
    return event


def _participation(  # type: ignore[no-untyped-def]
    session, event: Event, person: Person, status: str = "registered"
) -> EventParticipation:
    participation = EventParticipation(
        event_id=event.id, person_id=person.id, registration_status=status
    )
    session.add(participation)
    session.flush()
    return participation


@dataclass(frozen=True)
class World:
    club_id: uuid.UUID
    event_id: uuid.UUID
    other_event_id: uuid.UUID
    lesson_event_id: uuid.UUID
    admin: uuid.UUID
    instructor_events: uuid.UUID
    instructor_groups: uuid.UUID
    instructor_unrelated: uuid.UUID
    member: uuid.UUID
    member_person: uuid.UUID
    member_unrelated: uuid.UUID
    guardian: uuid.UUID
    child_person: uuid.UUID
    other_person: uuid.UUID
    other_event_person: uuid.UUID
    unregistered_person: uuid.UUID


def _world(*, status: str = "published", with_trip: bool = False) -> World:
    """One Club; a trip Event (given status) targeting one Group; every
    baseline role in its relationship to that Event:

    - admin: `all`;
    - instructor_events: active EventStaffAssignment on the Event;
    - instructor_groups: GroupInstructorAssignment on the targeted Group;
    - instructor_unrelated / member_unrelated: no relationship;
    - member: registered for the Event (`self`);
    - guardian: GuardianRelationship to a registered child (`children`).

    Plus another trip Event with its own registered participant and a
    `lesson` Event.
    """
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()

        event = _event(session, club, status=status)
        other_event = _event(session, club, status=status)
        lesson_event = _event(session, club, event_type="lesson")

        admin = _user_with_role(session, club, "admin")
        instructor_events = _user_with_role(session, club, "instructor")
        instructor_groups = _user_with_role(session, club, "instructor")
        instructor_unrelated = _user_with_role(session, club, "instructor")
        member_person = _person(session, "Alekseev")
        member = _user_with_role(session, club, "member", member_person)
        member_unrelated = _user_with_role(session, club, "member")
        guardian = _user_with_role(session, club, "guardian")
        child = _person(session, "Borisova")
        _club_membership(session, club, child)
        other_person = _person(session, "Vasiliev")
        other_event_person = _person(session)
        unregistered_person = _person(session)

        group = Group(
            club_id=club.id,
            name=f"Group {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_LONG_AGO,
        )
        session.add(group)
        session.flush()
        session.add_all(
            [
                EventStaffAssignment(
                    event_id=event.id,
                    user_id=instructor_events.id,
                    role_in_event="instructor",
                    valid_from=_LONG_AGO,
                ),
                EventGroupTarget(event_id=event.id, group_id=group.id, valid_from=_LONG_AGO),
                GroupInstructorAssignment(
                    group_id=group.id,
                    user_id=instructor_groups.id,
                    role_in_group="instructor",
                    valid_from=_LONG_AGO,
                ),
                GuardianRelationship(
                    guardian_person_id=guardian.person_id,
                    child_person_id=child.id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_LONG_AGO,
                ),
            ]
        )
        _participation(session, event, member_person)
        _participation(session, event, child)
        _participation(session, event, other_person)
        _participation(session, other_event, other_event_person)
        if with_trip:
            session.add_all([Trip(event_id=event.id), Trip(event_id=other_event.id)])
        session.commit()

        return World(
            club_id=club.id,
            event_id=event.id,
            other_event_id=other_event.id,
            lesson_event_id=lesson_event.id,
            admin=admin.id,
            instructor_events=instructor_events.id,
            instructor_groups=instructor_groups.id,
            instructor_unrelated=instructor_unrelated.id,
            member=member.id,
            member_person=member_person.id,
            member_unrelated=member_unrelated.id,
            guardian=guardian.id,
            child_person=child.id,
            other_person=other_person.id,
            other_event_person=other_event_person.id,
            unregistered_person=unregistered_person.id,
        )


def _set_event_status(event_id: uuid.UUID, status: str) -> None:
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        event.status = status
        event.cancellation_reason = "weather" if status == "cancelled" else None
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _create_trip(client: TestClient, event_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/trips", json={"event_id": str(event_id)}, headers=_csrf_headers(client)
    )


def _record(  # type: ignore[no-untyped-def]
    client: TestClient, event_id: uuid.UUID, person_id: uuid.UUID, value: object
):
    return client.put(
        f"/api/v1/trips/{event_id}/participants/{person_id}",
        json={"actual_participation": value},
        headers=_csrf_headers(client),
    )


def _participant_ids(client: TestClient, event_id: uuid.UUID) -> list[str]:
    response = client.get(f"/api/v1/trips/{event_id}/participants")
    assert response.status_code == 200, response.text
    return [item["person_id"] for item in response.json()["items"]]


def _registration_status(event_id: uuid.UUID, person_id: uuid.UUID) -> str:
    with session_scope() as session:
        return session.execute(
            select(EventParticipation.registration_status).where(
                EventParticipation.event_id == event_id,
                EventParticipation.person_id == person_id,
            )
        ).scalar_one()


def _trip_participant(event_id: uuid.UUID, person_id: uuid.UUID) -> TripParticipant | None:
    with session_scope() as session:
        return session.execute(
            select(TripParticipant)
            .join(
                EventParticipation,
                EventParticipation.id == TripParticipant.event_participation_id,
            )
            .where(
                EventParticipation.event_id == event_id,
                EventParticipation.person_id == person_id,
            )
        ).scalar_one_or_none()


# --- Trip creation / Trip <-> Event ------------------------------------------------


@requires_postgres
def test_admin_creates_trip_for_trip_event(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)

    response = _create_trip(client, world.event_id)

    assert response.status_code == 201, response.text
    body = response.json()
    # Issues #264/#268: the tourism facts so far are the optional
    # TourismType and the optional Official Difficulty.
    assert set(body) == {
        "event_id",
        "tourism_type_id",
        "official_difficulty",
        "created_at",
        "updated_at",
    }
    assert body["event_id"] == str(world.event_id)
    assert body["tourism_type_id"] is None
    assert body["official_difficulty"] is None
    detail = client.get(f"/api/v1/trips/{world.event_id}")
    assert detail.status_code == 200
    assert detail.json() == body


@requires_postgres
@pytest.mark.parametrize("status", ["draft", "published", "in_progress", "completed"])
def test_trip_can_be_created_for_any_open_event_status(client: TestClient, status: str) -> None:
    world = _world(status=status)
    _authenticate_as(world.admin)

    assert _create_trip(client, world.event_id).status_code == 201


@requires_postgres
@pytest.mark.parametrize("status", ["cancelled", "archived"])
def test_trip_cannot_be_created_for_cancelled_or_archived_event(
    client: TestClient, status: str
) -> None:
    world = _world(status=status)
    _authenticate_as(world.admin)

    response = _create_trip(client, world.event_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "trip_event_lifecycle_closed"
    with session_scope() as session:
        assert session.get(Trip, world.event_id) is None


@requires_postgres
def test_trip_cannot_be_created_for_an_event_of_another_type(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)

    response = _create_trip(client, world.lesson_event_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "event_not_trip"


@requires_postgres
def test_second_trip_for_the_same_event_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    assert _create_trip(client, world.event_id).status_code == 201

    response = _create_trip(client, world.event_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "trip_already_exists"


@requires_postgres
def test_trip_cannot_be_created_for_missing_event_or_series_or_occurrence(
    client: TestClient,
) -> None:
    world = _world()
    with session_scope() as session:
        series_id = uuid.uuid4()
        series = EventSeries(
            id=series_id,
            root_series_id=series_id,
            supersedes_series_id=None,
            version=1,
            club_id=world.club_id,
            name="Weekly trip",
            event_type="trip",
            series_start_at=_START,
            duration_minutes=60,
            recurrence_rule="FREQ=WEEKLY",
            timezone="UTC",
            status="active",
        )
        session.add(series)
        session.flush()
        occurrence = EventOccurrence(
            series_id=series.id,
            club_id=world.club_id,
            name="Trip",
            event_type="trip",
            recurrence_anchor_at=_START,
            starts_at=_START,
            ends_at=_START + datetime.timedelta(hours=1),
            timezone="UTC",
            status="scheduled",
        )
        session.add(occurrence)
        session.commit()
        occurrence_id = occurrence.id
    _authenticate_as(world.admin)

    for target in (uuid.uuid4(), series_id, occurrence_id):
        response = _create_trip(client, target)
        assert response.status_code == 404
        assert client.get(f"/api/v1/trips/{target}").status_code == 404
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(Trip)).scalar_one() == 0


@requires_postgres
def test_get_trip_for_event_without_trip_is_404(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)

    assert client.get(f"/api/v1/trips/{world.event_id}").status_code == 404
    assert client.get(f"/api/v1/trips/{world.event_id}/participants").status_code == 404
    assert _record(client, world.event_id, world.member_person, True).status_code == 404


@requires_postgres
def test_trip_has_no_lifecycle_of_its_own_event_lifecycle_still_works(
    client: TestClient,
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    before = client.get(f"/api/v1/trips/{world.event_id}").json()

    for new_status in ("in_progress", "completed"):
        response = client.post(
            f"/api/v1/events/{world.event_id}/status",
            json={"status": new_status},
            headers=_csrf_headers(client),
        )
        assert response.status_code == 200, response.text
    archived = client.post(
        f"/api/v1/events/{world.event_id}/archive", headers=_csrf_headers(client)
    )
    assert archived.status_code == 200, archived.text

    assert client.get(f"/api/v1/trips/{world.event_id}").json() == before


@requires_postgres
def test_event_type_of_an_event_with_a_trip_cannot_be_changed(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)

    response = client.patch(
        f"/api/v1/events/{world.event_id}",
        json={"event_type": "lesson"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "event_type_locked_by_trip"
    with session_scope() as session:
        event = session.get(Event, world.event_id)
        assert event is not None
        assert event.event_type == "trip"
        occurrence = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == world.event_id)
        ).scalar_one()
        assert occurrence.event_type == "trip"


@requires_postgres
def test_other_event_updates_still_work_for_an_event_with_a_trip(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)

    response = client.patch(
        f"/api/v1/events/{world.event_id}",
        json={"title": "Новый поход", "event_type": "trip"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Новый поход"


@requires_postgres
def test_event_type_of_a_trip_event_without_a_trip_can_still_be_changed(
    client: TestClient,
) -> None:
    world = _world()
    _authenticate_as(world.admin)

    response = client.patch(
        f"/api/v1/events/{world.event_id}",
        json={"event_type": "lesson"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["event_type"] == "lesson"


# --- Trip list ------------------------------------------------------------------------


@requires_postgres
def test_trip_list_excludes_archived_unless_requested(client: TestClient) -> None:
    world = _world(status="completed", with_trip=True)
    _set_event_status(world.other_event_id, "archived")
    _authenticate_as(world.admin)

    default = client.get("/api/v1/trips").json()
    assert [item["event_id"] for item in default["items"]] == [str(world.event_id)]
    assert default["pagination"] == {"page": 1, "page_size": 50, "total": 1, "pages": 1}

    archived = client.get("/api/v1/trips", params={"status": "archived"}).json()
    assert [item["event_id"] for item in archived["items"]] == [str(world.other_event_id)]


@requires_postgres
def test_trip_list_never_contains_events_without_a_trip(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)

    response = client.get("/api/v1/trips")

    assert response.status_code == 200
    assert response.json()["items"] == []


# --- Authorization matrix (seeded roles) ---------------------------------------------


_ROLE_CASES = [
    # (world attribute, can read the Trip, can manage the Trip)
    ("admin", True, True),
    ("instructor_events", True, True),
    ("instructor_groups", True, True),
    ("instructor_unrelated", False, False),
    ("member", True, False),
    ("member_unrelated", False, False),
    ("guardian", True, False),
]


@requires_postgres
@pytest.mark.parametrize(("actor", "can_read", "can_manage"), _ROLE_CASES)
def test_trip_read_follows_seeded_role_scopes(
    client: TestClient, actor: str, can_read: bool, can_manage: bool
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(getattr(world, actor))

    detail = client.get(f"/api/v1/trips/{world.event_id}")
    listed = [item["event_id"] for item in client.get("/api/v1/trips").json()["items"]]

    assert detail.status_code == (200 if can_read else 404)
    assert (str(world.event_id) in listed) is can_read
    # The other Trip has no relationship to anyone but admin.
    assert (str(world.other_event_id) in listed) is (actor == "admin")


@requires_postgres
@pytest.mark.parametrize(("actor", "can_read", "can_manage"), _ROLE_CASES)
def test_trip_creation_follows_seeded_trip_manage_scopes(
    client: TestClient, actor: str, can_read: bool, can_manage: bool
) -> None:
    world = _world()
    _authenticate_as(getattr(world, actor))

    response = _create_trip(client, world.event_id)

    assert response.status_code == (201 if can_manage else 404)
    with session_scope() as session:
        assert (session.get(Trip, world.event_id) is not None) is can_manage


@requires_postgres
@pytest.mark.parametrize(("actor", "can_read", "can_manage"), _ROLE_CASES)
def test_recording_actual_participation_follows_seeded_trip_manage_scopes(
    client: TestClient, actor: str, can_read: bool, can_manage: bool
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(getattr(world, actor))

    response = _record(client, world.event_id, world.member_person, True)

    assert response.status_code == (200 if can_manage else 404)
    assert (_trip_participant(world.event_id, world.member_person) is not None) is can_manage


@requires_postgres
def test_participant_rows_are_restricted_by_scope(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    for person_id in (world.member_person, world.child_person, world.other_person):
        assert _record(client, world.event_id, person_id, True).status_code == 200
    everyone = sorted(
        str(p) for p in (world.member_person, world.child_person, world.other_person)
    )

    for actor in ("admin", "instructor_events", "instructor_groups"):
        _authenticate_as(getattr(world, actor))
        assert sorted(_participant_ids(client, world.event_id)) == everyone

    _authenticate_as(world.member)
    assert _participant_ids(client, world.event_id) == [str(world.member_person)]

    _authenticate_as(world.guardian)
    assert _participant_ids(client, world.event_id) == [str(world.child_person)]

    for actor in ("instructor_unrelated", "member_unrelated"):
        _authenticate_as(getattr(world, actor))
        assert client.get(f"/api/v1/trips/{world.event_id}/participants").status_code == 404


@requires_postgres
def test_cross_club_admin_cannot_see_or_manage_the_trip(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    with session_scope() as session:
        other_club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(other_club)
        session.flush()
        other_admin = _user_with_role(session, other_club, "admin")
        session.commit()
        other_admin_id = other_admin.id
    _authenticate_as(other_admin_id)

    assert client.get(f"/api/v1/trips/{world.event_id}").status_code == 404
    assert client.get("/api/v1/trips").json()["items"] == []
    assert _record(client, world.event_id, world.member_person, True).status_code == 404
    assert _create_trip(client, world.lesson_event_id).status_code == 404


# --- actual_participation lifecycle ----------------------------------------------------


@requires_postgres
def test_in_progress_allows_create_and_change(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    created = _record(client, world.event_id, world.member_person, True)
    assert created.status_code == 200, created.text
    body = created.json()
    assert set(body) == {
        "event_participation_id",
        "event_id",
        "person_id",
        "actual_participation",
        "created_at",
        "updated_at",
    }
    assert body["person_id"] == str(world.member_person)
    assert body["event_id"] == str(world.event_id)
    assert body["actual_participation"] is True
    with session_scope() as session:
        participation_id = session.execute(
            select(EventParticipation.id).where(
                EventParticipation.event_id == world.event_id,
                EventParticipation.person_id == world.member_person,
            )
        ).scalar_one()
    assert body["event_participation_id"] == str(participation_id)

    changed = _record(client, world.event_id, world.member_person, False)
    assert changed.status_code == 200
    assert changed.json()["actual_participation"] is False
    assert changed.json()["event_participation_id"] == body["event_participation_id"]


@requires_postgres
def test_completed_allows_recording_a_not_yet_recorded_fact(client: TestClient) -> None:
    world = _world(status="completed", with_trip=True)
    _authenticate_as(world.admin)

    response = _record(client, world.event_id, world.member_person, True)

    assert response.status_code == 200
    assert response.json()["actual_participation"] is True


@requires_postgres
def test_completed_closes_an_already_recorded_fact(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, True).status_code == 200
    _set_event_status(world.event_id, "completed")
    before = _trip_participant(world.event_id, world.member_person)
    assert before is not None

    changed = _record(client, world.event_id, world.member_person, False)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "trip_participant_historically_closed"

    same = _record(client, world.event_id, world.member_person, True)
    assert same.status_code == 200
    after = _trip_participant(world.event_id, world.member_person)
    assert after is not None
    assert after.actual_participation is True
    assert after.updated_at == before.updated_at


@requires_postgres
@pytest.mark.parametrize("status", ["draft", "published", "cancelled", "archived"])
def test_other_event_statuses_close_actual_participation(client: TestClient, status: str) -> None:
    world = _world(status="in_progress", with_trip=True)
    _set_event_status(world.event_id, status)
    _authenticate_as(world.admin)

    response = _record(client, world.event_id, world.member_person, True)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "actual_participation_lifecycle_closed"
    assert _trip_participant(world.event_id, world.member_person) is None


@requires_postgres
def test_recording_requires_a_participation_for_this_trips_event(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    for person_id in (world.unregistered_person, world.other_event_person, uuid.uuid4()):
        response = _record(client, world.event_id, person_id, True)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "participation_missing"
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(TripParticipant)).scalar_one() == 0
        # No registration was created as a side effect.
        assert (
            session.execute(
                select(func.count())
                .select_from(EventParticipation)
                .where(EventParticipation.person_id == world.unregistered_person)
            ).scalar_one()
            == 0
        )


@requires_postgres
@pytest.mark.parametrize("value", ["true", 1, None, "yes"])
def test_actual_participation_must_be_a_boolean(client: TestClient, value: object) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, value).status_code == 422
    missing = client.put(
        f"/api/v1/trips/{world.event_id}/participants/{world.member_person}",
        json={},
        headers=_csrf_headers(client),
    )
    assert missing.status_code == 422


# --- No second registration source -------------------------------------------------------


@requires_postgres
def test_recording_never_touches_registration_or_attendance(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, False).status_code == 200
    assert _record(client, world.event_id, world.member_person, True).status_code == 200

    assert _registration_status(world.event_id, world.member_person) == "registered"
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(Attendance)).scalar_one() == 0


@requires_postgres
def test_cancelling_registration_keeps_the_confirmed_tourism_fact(client: TestClient) -> None:
    """PO decision GAP-A: registered -> cancelled never deletes, resets or
    invalidates TripParticipant; `cancelled` + `actual_participation =
    true` is a valid historical state, and the registration itself stays
    owned by EventParticipation."""
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, True).status_code == 200
    assert _registration_status(world.event_id, world.member_person) == "registered"

    _authenticate_as(world.member)
    withdrawn = client.delete(
        f"/api/v1/events/{world.event_id}/participation", headers=_csrf_headers(client)
    )
    assert withdrawn.status_code == 204

    assert _registration_status(world.event_id, world.member_person) == "cancelled"
    fact = _trip_participant(world.event_id, world.member_person)
    assert fact is not None
    assert fact.actual_participation is True

    _authenticate_as(world.admin)
    assert str(world.member_person) in _participant_ids(client, world.event_id)
    # The Event roster (registration source) no longer lists the person;
    # the tourism layer did not resurrect the registration.
    roster = client.get(f"/api/v1/events/{world.event_id}/participants").json()["items"]
    assert str(world.member_person) not in [item["person_id"] for item in roster]


@requires_postgres
def test_fact_can_be_recorded_for_an_already_cancelled_registration(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    with session_scope() as session:
        participation = session.execute(
            select(EventParticipation).where(
                EventParticipation.event_id == world.event_id,
                EventParticipation.person_id == world.other_person,
            )
        ).scalar_one()
        participation.registration_status = "cancelled"
        session.commit()
    _authenticate_as(world.admin)

    response = _record(client, world.event_id, world.other_person, True)

    assert response.status_code == 200
    assert _registration_status(world.event_id, world.other_person) == "cancelled"


# --- Protocol-level errors --------------------------------------------------------------------


@requires_postgres
def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)

    assert client.get("/api/v1/trips").status_code == 401
    assert client.get(f"/api/v1/trips/{world.event_id}").status_code == 401
    assert client.get(f"/api/v1/trips/{world.event_id}/participants").status_code == 401
    assert _create_trip(client, world.lesson_event_id).status_code == 401
    assert _record(client, world.event_id, world.member_person, True).status_code == 401


@requires_postgres
def test_mutations_require_csrf_token(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)

    created = client.post("/api/v1/trips", json={"event_id": str(world.event_id)})
    assert created.status_code == 403
    with session_scope() as session:
        assert session.get(Trip, world.event_id) is None


@requires_postgres
def test_invalid_identifiers_are_rejected(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    assert client.get("/api/v1/trips/not-a-uuid").status_code == 422
    assert client.get("/api/v1/trips/not-a-uuid/participants").status_code == 422
    bad_body = client.post(
        "/api/v1/trips", json={"event_id": "not-a-uuid"}, headers=_csrf_headers(client)
    )
    assert bad_body.status_code == 422
    bad_person = client.put(
        f"/api/v1/trips/{world.event_id}/participants/not-a-uuid",
        json={"actual_participation": True},
        headers=_csrf_headers(client),
    )
    assert bad_person.status_code == 422
