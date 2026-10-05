"""HTTP-level integration tests for the Duration Classification
Foundation (Issue #274, docs/04-modules/trips-and-tourist-profile.md §10).

Against the REAL shipped app and a real PostgreSQL database, with
authorization coming only from the migration-seeded role matrix:

- every Trip carries one Duration Classification — `UNCLASSIFIED` until
  set; `ONE_DAY`/`MULTI_DAY`/`UNCLASSIFIED` only (API and database);
- `ONE_DAY`/`MULTI_DAY` must agree, when set, with the Event's planned
  interval: a local midnight (Event.timezone) strictly inside it means
  `MULTI_DAY`; no hour threshold; Event re-planning never changes it;
- ordinary lifecycle: open while draft/published/in_progress, closed for
  completed/cancelled/archived; idempotent re-send;
- authorization: the Administrator's `trip.manage` with `all` scope;
  Instructor (in or out of the Trip's scope), Member and Guardian never
  set it; reads follow `trip.read`;
- independence from TourismType/Difficulty/Geography and no automatic
  classification.

Self-contained factories, per this codebase's convention of not
importing helpers across test files.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation, EventStaffAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import get_engine, session_scope
from app.db.trips import TourismType, Trip
from app.main import app

from ._schema_reset import run_alembic
from .conftest import requires_postgres

pytestmark = requires_postgres

_START = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.timezone.utc)
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_OCCURRENCE_STATUS = {
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


def _membership(session, club: Club, person: Person) -> None:  # type: ignore[no-untyped-def]
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="member",
            status="active",
            joined_at=_LONG_AGO,
        )
    )
    session.flush()


def _user_with_role(session, club: Club, role_code: str, person: Person | None = None) -> User:  # type: ignore[no-untyped-def]
    person = person or _person(session)
    _membership(session, club, person)
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


def _trip_event(session, club: Club, *, status: str = "published") -> Event:  # type: ignore[no-untyped-def]
    event = Event(
        id=uuid.uuid4(),
        club_id=club.id,
        event_type="trip",
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
            status=_OCCURRENCE_STATUS[status],
            cancellation_reason=event.cancellation_reason,
        )
    )
    session.flush()
    return event


@dataclass(frozen=True)
class World:
    event_id: uuid.UUID
    admin: uuid.UUID
    instructor_events: uuid.UUID
    instructor_unrelated: uuid.UUID
    member: uuid.UUID
    guardian: uuid.UUID


def _world(*, status: str = "published", with_trip: bool = False) -> World:
    """One Club and one trip Event; every baseline role in its relation
    to it: admin (`all`), an instructor assigned to the Event
    (`own_events`), an unrelated instructor, a registered member (`self`)
    and the guardian of a registered child (`children`)."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        event = _trip_event(session, club, status=status)
        admin = _user_with_role(session, club, "admin")
        instructor_events = _user_with_role(session, club, "instructor")
        instructor_unrelated = _user_with_role(session, club, "instructor")
        member = _user_with_role(session, club, "member")
        guardian = _user_with_role(session, club, "guardian")
        child = _person(session, "Child")
        _membership(session, club, child)
        session.add_all(
            [
                EventStaffAssignment(
                    event_id=event.id,
                    user_id=instructor_events.id,
                    role_in_event="instructor",
                    valid_from=_LONG_AGO,
                ),
                GuardianRelationship(
                    guardian_person_id=guardian.person_id,
                    child_person_id=child.id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_LONG_AGO,
                ),
                EventParticipation(
                    event_id=event.id, person_id=member.person_id, registration_status="registered"
                ),
                EventParticipation(
                    event_id=event.id, person_id=child.id, registration_status="registered"
                ),
            ]
        )
        if with_trip:
            session.add(Trip(event_id=event.id))
        session.commit()
        return World(
            event_id=event.id,
            admin=admin.id,
            instructor_events=instructor_events.id,
            instructor_unrelated=instructor_unrelated.id,
            member=member.id,
            guardian=guardian.id,
        )


def _tourism_type(*, active: bool = True) -> uuid.UUID:
    with session_scope() as session:
        row = TourismType(code=f"tt-{uuid.uuid4().hex[:8]}", name="Тип", active=active)
        session.add(row)
        session.commit()
        return row.id


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


def _csrf(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _post(client: TestClient, path: str, body: dict | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1{path}", json=body or {}, headers=_csrf(client))


def _patch(client: TestClient, path: str, body: dict):  # type: ignore[no-untyped-def]
    return client.patch(f"/api/v1{path}", json=body, headers=_csrf(client))


_MOSCOW = "Europe/Moscow"


def _local(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    from zoneinfo import ZoneInfo

    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo(_MOSCOW))


def _set_event_interval(
    event_id: uuid.UUID,
    start: datetime.datetime,
    end: datetime.datetime,
    tz: str = _MOSCOW,
) -> None:
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        event.start_at, event.end_at, event.timezone = start, end, tz
        session.commit()


def _same_day(event_id: uuid.UUID) -> None:
    _set_event_interval(event_id, _local(20, 10), _local(20, 20))


def _overnight(event_id: uuid.UUID) -> None:
    _set_event_interval(event_id, _local(20, 10), _local(21, 15))


def _stored(event_id: uuid.UUID) -> str:
    with session_scope() as session:
        trip = session.get(Trip, event_id)
        assert trip is not None
        return trip.duration_classification


def _trip_exists(event_id: uuid.UUID) -> bool:
    with session_scope() as session:
        return session.get(Trip, event_id) is not None


def _set(client: TestClient, event_id: uuid.UUID, value):  # type: ignore[no-untyped-def]
    return _patch(client, f"/trips/{event_id}", {"duration_classification": value})


def _create(client: TestClient, event_id: uuid.UUID, **body):  # type: ignore[no-untyped-def]
    return _post(client, "/trips", {"event_id": str(event_id), **body})


# --- default and values ----------------------------------------------------------------


def test_trip_without_classification_is_unclassified(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    created = _create(client, world.event_id)
    assert created.status_code == 201, created.text
    assert created.json()["duration_classification"] == "UNCLASSIFIED"
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["duration_classification"] == (
        "UNCLASSIFIED"
    )
    assert client.get("/api/v1/trips").json()["items"][0]["duration_classification"] == (
        "UNCLASSIFIED"
    )
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200, completed.text
    assert _stored(world.event_id) == "UNCLASSIFIED"


def test_existing_trip_rows_default_to_unclassified() -> None:
    world = _world(with_trip=True)
    assert _stored(world.event_id) == "UNCLASSIFIED"


@pytest.mark.parametrize(
    ("interval", "value"),
    [(_same_day, "ONE_DAY"), (_overnight, "MULTI_DAY"), (_same_day, "UNCLASSIFIED")],
)
def test_create_read_update_with_each_value(client: TestClient, interval, value: str) -> None:  # type: ignore[no-untyped-def]
    world = _world()
    _authenticate_as(world.admin)
    interval(world.event_id)
    created = _create(client, world.event_id, duration_classification=value)
    assert created.status_code == 201, created.text
    assert created.json()["duration_classification"] == value
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["duration_classification"] == value
    assert _stored(world.event_id) == value

    other = _world(with_trip=True)
    _authenticate_as(other.admin)
    interval(other.event_id)
    updated = _set(client, other.event_id, value)
    assert updated.status_code == 200, updated.text
    assert updated.json()["duration_classification"] == value
    assert _stored(other.event_id) == value


@pytest.mark.parametrize(
    "value", ["one_day", "WEEKEND", "", "NONE", "MULTIDAY", 1, None, {"value": "ONE_DAY"}]
)
def test_other_values_are_rejected(client: TestClient, value) -> None:  # type: ignore[no-untyped-def]
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _same_day(world.event_id)
    assert _set(client, world.event_id, value).status_code == 422
    assert _stored(world.event_id) == "UNCLASSIFIED"
    other = _world()
    _authenticate_as(other.admin)
    _same_day(other.event_id)
    assert _create(client, other.event_id, duration_classification=value).status_code == 422
    assert not _trip_exists(other.event_id)


@pytest.mark.parametrize("value", ["one_day", "WEEKEND", "", "SEVERAL_DAYS"])
def test_database_rejects_other_values(value: str) -> None:
    world = _world(with_trip=True)
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.duration_classification = value
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
    assert exc_info.value.orig.diag.constraint_name == "ck_trips_duration_classification"  # type: ignore[union-attr]
    assert _stored(world.event_id) == "UNCLASSIFIED"


def test_database_rejects_null() -> None:
    world = _world(with_trip=True)
    with session_scope() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                sa.update(Trip)
                .where(Trip.event_id == world.event_id)
                .values(duration_classification=None)
            )
            session.commit()


# --- planned-interval consistency (midnight rule) --------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "consistent", "inconsistent"),
    [
        # §10 examples.
        (_local(20, 10), _local(20, 20), "ONE_DAY", "MULTI_DAY"),
        (_local(20, 10), _local(21, 15), "MULTI_DAY", "ONE_DAY"),
        # No 24-hour threshold: 2 hours across midnight is MULTI_DAY, a
        # nearly full day without crossing it is ONE_DAY.
        (_local(20, 23), _local(21, 1), "MULTI_DAY", "ONE_DAY"),
        (_local(20, 0, 30), _local(20, 23, 30), "ONE_DAY", "MULTI_DAY"),
        # Ending exactly at local midnight does not cross it.
        (_local(20, 10), _local(21, 0), "ONE_DAY", "MULTI_DAY"),
    ],
)
def test_classification_must_match_the_planned_interval(
    client: TestClient,
    start,
    end,
    consistent: str,
    inconsistent: str,  # type: ignore[no-untyped-def]
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set_event_interval(world.event_id, start, end)

    rejected = _set(client, world.event_id, inconsistent)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "duration_classification_mismatch"
    assert _stored(world.event_id) == "UNCLASSIFIED"
    assert _set(client, world.event_id, consistent).status_code == 200
    assert _stored(world.event_id) == consistent

    other = _world()
    _authenticate_as(other.admin)
    _set_event_interval(other.event_id, start, end)
    mismatch = _create(client, other.event_id, duration_classification=inconsistent)
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "duration_classification_mismatch"
    assert not _trip_exists(other.event_id)


def test_midnight_is_taken_in_the_event_timezone(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    # 01:00–04:00 Moscow = 22:00–01:00 UTC: no local midnight inside.
    _set_event_interval(world.event_id, _local(20, 1), _local(20, 4), _MOSCOW)
    assert _set(client, world.event_id, "ONE_DAY").status_code == 200
    # The same instants planned in UTC do cross a midnight.
    other = _world(with_trip=True)
    _authenticate_as(other.admin)
    _set_event_interval(other.event_id, _local(20, 1), _local(20, 4), "UTC")
    assert _set(client, other.event_id, "ONE_DAY").status_code == 422
    assert _set(client, other.event_id, "MULTI_DAY").status_code == 200


def test_an_18_to_02_plan_gets_no_special_value(client: TestClient) -> None:
    """§10: 18:00 → 02:00 is an invalid children's plan, not a separate
    classification. Existing Event validation accepts it (only
    `end_at > start_at`); by the midnight rule only MULTI_DAY or
    UNCLASSIFIED is consistent with it."""
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set_event_interval(world.event_id, _local(20, 18), _local(21, 2))
    assert _set(client, world.event_id, "ONE_DAY").status_code == 422
    assert _set(client, world.event_id, "MULTI_DAY").status_code == 200
    assert _set(client, world.event_id, "UNCLASSIFIED").status_code == 200


def test_unclassified_is_always_accepted(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _overnight(world.event_id)
    _set(client, world.event_id, "MULTI_DAY")
    _same_day(world.event_id)
    assert _set(client, world.event_id, "UNCLASSIFIED").status_code == 200
    assert _stored(world.event_id) == "UNCLASSIFIED"


def test_event_replanning_never_changes_the_classification(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _overnight(world.event_id)
    assert _set(client, world.event_id, "MULTI_DAY").status_code == 200
    # Re-planned to a single day through the Event API: the Trip fact is
    # neither recomputed nor overwritten, and the change is not blocked.
    response = client.patch(
        f"/api/v1/events/{world.event_id}",
        json={
            "start_at": _local(20, 10).isoformat(),
            "end_at": _local(20, 20).isoformat(),
        },
        headers=_csrf(client),
    )
    assert response.status_code == 200, response.text
    assert _stored(world.event_id) == "MULTI_DAY"
    # Re-sending the stored value stays a no-op; a new value is checked.
    assert _set(client, world.event_id, "MULTI_DAY").status_code == 200
    assert _set(client, world.event_id, "ONE_DAY").status_code == 200
    assert _stored(world.event_id) == "ONE_DAY"


# --- lifecycle -------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "published", "in_progress"])
def test_classification_editable_while_editing_is_open(client: TestClient, status: str) -> None:
    world = _world(status=status, with_trip=True)
    _authenticate_as(world.admin)
    _same_day(world.event_id)
    assert _set(client, world.event_id, "ONE_DAY").status_code == 200
    assert _set(client, world.event_id, "UNCLASSIFIED").status_code == 200
    _overnight(world.event_id)
    assert _set(client, world.event_id, "MULTI_DAY").status_code == 200
    assert _stored(world.event_id) == "MULTI_DAY"


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
def test_closed_trip_cannot_change_classification(client: TestClient, status: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _overnight(world.event_id)
    _set(client, world.event_id, "MULTI_DAY")
    _set_event_status(world.event_id, status)

    attempts = [
        _set(client, world.event_id, "UNCLASSIFIED"),
        _set(client, world.event_id, "ONE_DAY"),
        # Even the stored value is not ordinary editing here.
        _set(client, world.event_id, "MULTI_DAY"),
    ]
    assert [response.status_code for response in attempts] == [409] * len(attempts)
    assert {response.json()["error"]["code"] for response in attempts} == {"trip_editing_closed"}
    assert _stored(world.event_id) == "MULTI_DAY"


def test_completion_keeps_the_classification(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    _overnight(world.event_id)
    _create(client, world.event_id, duration_classification="MULTI_DAY")
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200, completed.text
    assert _stored(world.event_id) == "MULTI_DAY"


def test_re_sending_the_same_value_is_idempotent(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _same_day(world.event_id)
    first = _set(client, world.event_id, "ONE_DAY")
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        updated_at = trip.updated_at
    again = _set(client, world.event_id, "ONE_DAY")
    assert again.status_code == 200
    assert again.json() == first.json()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.updated_at == updated_at


def test_patch_without_the_field_leaves_it_unchanged(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _overnight(world.event_id)
    _set(client, world.event_id, "MULTI_DAY")
    assert _patch(client, f"/trips/{world.event_id}", {}).status_code == 200
    assert _stored(world.event_id) == "MULTI_DAY"


# --- independence ----------------------------------------------------------------------


def test_classification_is_independent_of_other_facts(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _same_day(world.event_id)
    tourism_type_id = _tourism_type()
    created = _create(
        client,
        world.event_id,
        tourism_type_id=str(tourism_type_id),
        official_difficulty={"mode": "WEEKEND", "source": "Решение"},
        duration_classification="ONE_DAY",
    )
    assert created.status_code == 201, created.text
    # Changing other facts never touches it.
    assert _patch(client, f"/trips/{world.event_id}", {"tourism_type_id": None}).status_code == 200
    assert _patch(
        client, f"/trips/{world.event_id}", {"official_difficulty": None}
    ).status_code == (200)
    assert _stored(world.event_id) == "ONE_DAY"


def test_rejected_classification_does_not_apply_other_fields_of_the_same_body(
    client: TestClient,
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _same_day(world.event_id)
    tourism_type_id = _tourism_type()
    response = _patch(
        client,
        f"/trips/{world.event_id}",
        {"tourism_type_id": str(tourism_type_id), "duration_classification": "MULTI_DAY"},
    )
    assert response.status_code == 422
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.tourism_type_id is None
        assert trip.duration_classification == "UNCLASSIFIED"


def test_classification_is_never_assigned_automatically(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    _overnight(world.event_id)
    _create(client, world.event_id, tourism_type_id=str(_tourism_type()))
    with session_scope() as session:
        member = session.get(User, world.member)
        assert member is not None
        person_id = member.person_id
    client.put(
        f"/api/v1/trips/{world.event_id}/participants/{person_id}",
        json={"actual_participation": True},
        headers=_csrf(client),
    )
    _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert _stored(world.event_id) == "UNCLASSIFIED"


# --- authorization ---------------------------------------------------------------------


def test_instructor_in_trip_scope_cannot_set_classification(client: TestClient) -> None:
    """§10 and the roles matrix: the Administrator sets it; Instructor gets
    no duration-classification permission — not even for a Trip it may
    otherwise manage (`own_events`)."""
    world = _world()
    _same_day(world.event_id)
    _authenticate_as(world.instructor_events)

    denied = _create(client, world.event_id, duration_classification="ONE_DAY")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    # Even an explicit UNCLASSIFIED is a classification value.
    denied_unclassified = _create(client, world.event_id, duration_classification="UNCLASSIFIED")
    assert denied_unclassified.status_code == 403
    assert not _trip_exists(world.event_id)

    # The Instructor still creates and edits the Trip without it.
    assert _create(client, world.event_id).status_code == 201
    assert _patch(client, f"/trips/{world.event_id}", {"tourism_type_id": None}).status_code == 200
    attempts = [
        _set(client, world.event_id, "ONE_DAY"),
        _set(client, world.event_id, "UNCLASSIFIED"),
    ]
    assert [response.status_code for response in attempts] == [403, 403]
    assert _stored(world.event_id) == "UNCLASSIFIED"
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["duration_classification"] == (
        "UNCLASSIFIED"
    )


@pytest.mark.parametrize("role", ["instructor_unrelated", "member", "guardian"])
def test_roles_without_trip_manage_scope_cannot_set_classification(
    client: TestClient, role: str
) -> None:
    world = _world(with_trip=True)
    _same_day(world.event_id)
    _authenticate_as(getattr(world, role))
    # Existence-hiding 404 from the ordinary trip.manage check comes first.
    assert _set(client, world.event_id, "ONE_DAY").status_code == 404
    other = _world()
    assert _create(client, other.event_id, duration_classification="MULTI_DAY").status_code == 404
    assert _stored(world.event_id) == "UNCLASSIFIED"
    assert not _trip_exists(other.event_id)


@pytest.mark.parametrize("role", ["member", "guardian"])
def test_trip_readers_see_the_classification(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    _overnight(world.event_id)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "MULTI_DAY")
    _authenticate_as(getattr(world, role))
    response = client.get(f"/api/v1/trips/{world.event_id}")
    assert response.status_code == 200
    assert response.json()["duration_classification"] == "MULTI_DAY"


def test_classification_requires_authentication_and_csrf(client: TestClient) -> None:
    world = _world(with_trip=True)
    _same_day(world.event_id)
    assert _set(client, world.event_id, "ONE_DAY").status_code == 401
    _authenticate_as(world.admin)
    response = client.patch(
        f"/api/v1/trips/{world.event_id}", json={"duration_classification": "ONE_DAY"}
    )
    assert response.status_code == 403
    assert _stored(world.event_id) == "UNCLASSIFIED"


# --- migration -------------------------------------------------------------------------


def test_migration_downgrade_and_upgrade_keep_existing_trips(database_url: str) -> None:
    world = _world(with_trip=True)
    _overnight(world.event_id)
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.duration_classification = "MULTI_DAY"
        session.commit()
    try:
        downgrade = run_alembic("downgrade", "2c28e7b34462", database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        columns = {c["name"] for c in sa.inspect(get_engine()).get_columns("trips")}
        assert "duration_classification" not in columns
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
    get_engine().dispose()
    # Existing Trips come back unclassified.
    assert _stored(world.event_id) == "UNCLASSIFIED"
