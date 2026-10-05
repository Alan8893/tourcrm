"""HTTP-level integration tests for the Result Foundation (Issue #276,
docs/04-modules/trips-and-tourist-profile.md §8).

Against the REAL shipped app and a real PostgreSQL database, with
authorization coming only from the migration-seeded role matrix:

- 0..1 Result of the Trip itself: `COMPLETED`/`PARTIALLY_COMPLETED`/
  `NOT_COMPLETED` only (API and database), absent by default, clearable;
- ordinary lifecycle: open while draft/published/in_progress, closed for
  completed/cancelled/archived (`trip_editing_closed`); idempotent
  re-send;
- independence: no Event status sets, changes or requires it, setting it
  never changes the Event, and it is never inferred from any other fact;
- authorization: the existing Trip `trip.manage` scope — Administrator
  and the Instructor of the Trip's Event; an unrelated Instructor,
  Member and Guardian cannot; reads follow `trip.read`.

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
from app.db.trips import Country, Region, TourismType, Trip
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


_RESULTS = ("COMPLETED", "PARTIALLY_COMPLETED", "NOT_COMPLETED")


def _stored(event_id: uuid.UUID) -> str | None:
    with session_scope() as session:
        trip = session.get(Trip, event_id)
        assert trip is not None
        return trip.result


def _event_status(event_id: uuid.UUID) -> str:
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        return event.status


def _trip_exists(event_id: uuid.UUID) -> bool:
    with session_scope() as session:
        return session.get(Trip, event_id) is not None


def _set(client: TestClient, event_id: uuid.UUID, value):  # type: ignore[no-untyped-def]
    return _patch(client, f"/trips/{event_id}", {"result": value})


def _create(client: TestClient, event_id: uuid.UUID, **body):  # type: ignore[no-untyped-def]
    return _post(client, "/trips", {"event_id": str(event_id), **body})


def _member_person(user_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        user = session.get(User, user_id)
        assert user is not None
        return user.person_id


# --- values ----------------------------------------------------------------------------


def test_trip_without_result(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create(client, world.event_id)
    assert created.status_code == 201, created.text
    assert created.json()["result"] is None
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["result"] is None
    assert client.get("/api/v1/trips").json()["items"][0]["result"] is None
    assert _stored(world.event_id) is None


@pytest.mark.parametrize("value", _RESULTS)
def test_create_read_update_with_each_value(client: TestClient, value: str) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create(client, world.event_id, result=value)
    assert created.status_code == 201, created.text
    assert created.json()["result"] == value
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["result"] == value
    assert _stored(world.event_id) == value

    other = _world(with_trip=True)
    _authenticate_as(other.admin)
    updated = _set(client, other.event_id, value)
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"] == value
    assert _stored(other.event_id) == value


def test_result_can_be_changed_and_cleared(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    for value in ("PARTIALLY_COMPLETED", "COMPLETED", "NOT_COMPLETED"):
        assert _set(client, world.event_id, value).status_code == 200
        assert _stored(world.event_id) == value
    cleared = _set(client, world.event_id, None)
    assert cleared.status_code == 200
    assert cleared.json()["result"] is None
    assert _stored(world.event_id) is None


@pytest.mark.parametrize(
    "value",
    ["completed", "CANCELLED", "", "PARTIAL", "FAILED", "UNCLASSIFIED", 1, {"value": "COMPLETED"}],
)
def test_other_values_are_rejected(client: TestClient, value) -> None:  # type: ignore[no-untyped-def]
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "COMPLETED")
    assert _set(client, world.event_id, value).status_code == 422
    assert _stored(world.event_id) == "COMPLETED"
    other = _world()
    _authenticate_as(other.admin)
    assert _create(client, other.event_id, result=value).status_code == 422
    assert not _trip_exists(other.event_id)


@pytest.mark.parametrize("value", ["completed", "CANCELLED", "", "FAILED"])
def test_database_rejects_other_values(value: str) -> None:
    world = _world(with_trip=True)
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.result = value
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
    assert exc_info.value.orig.diag.constraint_name == "ck_trips_result"  # type: ignore[union-attr]
    assert _stored(world.event_id) is None


def test_re_sending_the_same_value_is_idempotent(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    first = _set(client, world.event_id, "PARTIALLY_COMPLETED")
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        updated_at = trip.updated_at
    again = _set(client, world.event_id, "PARTIALLY_COMPLETED")
    assert again.status_code == 200
    assert again.json() == first.json()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.updated_at == updated_at
    assert _set(client, world.event_id, None).status_code == 200
    assert _set(client, world.event_id, None).json()["result"] is None


def test_patch_without_the_field_leaves_it_unchanged(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "NOT_COMPLETED")
    assert _patch(client, f"/trips/{world.event_id}", {}).status_code == 200
    assert _stored(world.event_id) == "NOT_COMPLETED"


# --- lifecycle -------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "published", "in_progress"])
def test_result_editable_while_editing_is_open(client: TestClient, status: str) -> None:
    world = _world(status=status, with_trip=True)
    _authenticate_as(world.admin)
    assert _set(client, world.event_id, "COMPLETED").status_code == 200
    assert _set(client, world.event_id, "PARTIALLY_COMPLETED").status_code == 200
    assert _set(client, world.event_id, None).status_code == 200
    assert _stored(world.event_id) is None


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
def test_closed_trip_cannot_change_result(client: TestClient, status: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "PARTIALLY_COMPLETED")
    _set_event_status(world.event_id, status)

    attempts = [
        _set(client, world.event_id, "COMPLETED"),
        _set(client, world.event_id, None),
        # Even the stored value is not ordinary editing here.
        _set(client, world.event_id, "PARTIALLY_COMPLETED"),
    ]
    assert [response.status_code for response in attempts] == [409] * len(attempts)
    assert {response.json()["error"]["code"] for response in attempts} == {"trip_editing_closed"}
    assert _stored(world.event_id) == "PARTIALLY_COMPLETED"


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
def test_closed_trip_without_result_cannot_receive_one(client: TestClient, status: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set_event_status(world.event_id, status)
    response = _set(client, world.event_id, "COMPLETED")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "trip_editing_closed"
    assert _stored(world.event_id) is None


# --- independence from the Event lifecycle ---------------------------------------------


def test_trip_completes_without_result_and_completion_sets_none(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    _create(client, world.event_id)
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200, completed.text
    assert _event_status(world.event_id) == "completed"
    assert _stored(world.event_id) is None


def test_trip_is_cancelled_without_result_and_cancellation_sets_none(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _create(client, world.event_id)
    cancelled = _post(
        client,
        f"/events/{world.event_id}/status",
        {"status": "cancelled", "cancellation_reason": "weather"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert _event_status(world.event_id) == "cancelled"
    assert _stored(world.event_id) is None


@pytest.mark.parametrize("value", _RESULTS)
def test_setting_result_never_changes_the_event_status(client: TestClient, value: str) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _set(client, world.event_id, value).status_code == 200
    assert _event_status(world.event_id) == "in_progress"


@pytest.mark.parametrize("value", _RESULTS)
def test_event_status_changes_never_change_the_result(client: TestClient, value: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, value)
    assert _post(
        client, f"/events/{world.event_id}/status", {"status": "in_progress"}
    ).status_code == (200)
    assert _stored(world.event_id) == value
    assert _post(
        client, f"/events/{world.event_id}/status", {"status": "completed"}
    ).status_code == (200)
    assert _stored(world.event_id) == value


def test_not_completed_result_does_not_cancel_the_event(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "NOT_COMPLETED")
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200
    assert (_event_status(world.event_id), _stored(world.event_id)) == (
        "completed",
        "NOT_COMPLETED",
    )


# --- non-inference ---------------------------------------------------------------------


def test_result_is_never_inferred_from_other_facts(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    with session_scope() as session:
        ru = session.execute(select(Country.id).where(Country.code == "RU")).scalar_one()
        region = session.execute(
            select(Region.id).where(Region.code == "ALTAY_REPUBLIC")
        ).scalar_one()
    created = _create(
        client,
        world.event_id,
        tourism_type_id=str(_tourism_type()),
        official_difficulty={"mode": "CATEGORY", "value": "I", "source": "Решение МКК"},
        country_id=str(ru),
        region_id=str(region),
        duration_classification="MULTI_DAY",
    )
    assert created.status_code == 201, created.text
    assert created.json()["result"] is None
    recorded = client.put(
        f"/api/v1/trips/{world.event_id}/participants/{_member_person(world.member)}",
        json={"actual_participation": True},
        headers=_csrf(client),
    )
    assert recorded.status_code == 200, recorded.text
    assert _stored(world.event_id) is None
    _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert _stored(world.event_id) is None


def test_changing_other_facts_never_touches_the_result(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "PARTIALLY_COMPLETED")
    for body in (
        {"tourism_type_id": str(_tourism_type())},
        {"official_difficulty": {"mode": "NONE"}},
        {"duration_classification": "MULTI_DAY"},
        {"tourism_type_id": None, "official_difficulty": None},
    ):
        assert _patch(client, f"/trips/{world.event_id}", body).status_code == 200, body
    assert _stored(world.event_id) == "PARTIALLY_COMPLETED"


# --- authorization ---------------------------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "instructor_events"])
def test_trip_managers_in_scope_manage_the_result(client: TestClient, role: str) -> None:
    world = _world()
    _authenticate_as(getattr(world, role))
    created = _create(client, world.event_id, result="COMPLETED")
    assert created.status_code == 201, created.text
    assert _set(client, world.event_id, "PARTIALLY_COMPLETED").status_code == 200
    assert _stored(world.event_id) == "PARTIALLY_COMPLETED"
    assert _set(client, world.event_id, None).status_code == 200
    assert _stored(world.event_id) is None


@pytest.mark.parametrize("role", ["instructor_unrelated", "member", "guardian"])
def test_roles_without_trip_manage_scope_cannot_manage_the_result(
    client: TestClient, role: str
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "COMPLETED")
    _authenticate_as(getattr(world, role))
    # Existence-hiding 404 from the ordinary trip.manage check.
    assert _set(client, world.event_id, "NOT_COMPLETED").status_code == 404
    assert _set(client, world.event_id, None).status_code == 404
    other = _world()
    assert _create(client, other.event_id, result="COMPLETED").status_code == 404
    assert _stored(world.event_id) == "COMPLETED"
    assert not _trip_exists(other.event_id)


@pytest.mark.parametrize("role", ["member", "guardian"])
def test_trip_readers_see_the_result(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, "NOT_COMPLETED")
    _authenticate_as(getattr(world, role))
    response = client.get(f"/api/v1/trips/{world.event_id}")
    assert response.status_code == 200
    assert response.json()["result"] == "NOT_COMPLETED"


def test_result_requires_authentication_and_csrf(client: TestClient) -> None:
    world = _world(with_trip=True)
    assert _set(client, world.event_id, "COMPLETED").status_code == 401
    _authenticate_as(world.admin)
    response = client.patch(f"/api/v1/trips/{world.event_id}", json={"result": "COMPLETED"})
    assert response.status_code == 403
    assert _stored(world.event_id) is None


# --- atomic ordinary editing (PR #278 review) -----------------------------------------


def _region_of(code: str) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        region = session.execute(select(Region).where(Region.code == code)).scalar_one()
        return region.country_id, region.id


def _hr() -> uuid.UUID:
    with session_scope() as session:
        return session.execute(select(Country.id).where(Country.code == "HR")).scalar_one()


def test_patch_rejected_by_another_field_does_not_store_the_result(client: TestClient) -> None:
    """A valid `result` in the same body as a Geography pair that fails
    domain validation: the whole PATCH is rejected and nothing is stored."""
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _ru, altai = _region_of("ALTAY_REPUBLIC")
    tourism_type_id = _tourism_type()
    response = _patch(
        client,
        f"/trips/{world.event_id}",
        {
            "tourism_type_id": str(tourism_type_id),
            "result": "COMPLETED",
            "country_id": str(_hr()),
            "region_id": str(altai),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "region_country_mismatch"
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert (trip.result, trip.tourism_type_id, trip.country_id) == (None, None, None)


def test_failure_after_the_result_setter_rolls_back_the_whole_patch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Result setter has already run (and flushed) when a later setter
    in the same PATCH fails: nothing of the update may be committed."""
    from app.trips import geography as geography_service
    from app.trips import service as trips_service

    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, altai = _region_of("ALTAY_REPUBLIC")
    tourism_type_id = _tourism_type()
    applied: list[str | None] = []
    real_set_result = trips_service.set_trip_result

    def tracking_set_result(session, **kwargs):  # type: ignore[no-untyped-def]
        trip = real_set_result(session, **kwargs)
        applied.append(trip.result)
        return trip

    def failing_set_geography(session, *, event, trip, country_id, region_id):  # type: ignore[no-untyped-def]
        raise geography_service.RegionInactiveError(region_id)

    monkeypatch.setattr(trips_service, "set_trip_result", tracking_set_result)
    monkeypatch.setattr(trips_service, "set_trip_geography", failing_set_geography)
    response = _patch(
        client,
        f"/trips/{world.event_id}",
        {
            "tourism_type_id": str(tourism_type_id),
            "duration_classification": "MULTI_DAY",
            "result": "PARTIALLY_COMPLETED",
            "country_id": str(ru),
            "region_id": str(altai),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "region_inactive"
    # The Result setter did run before the failure ...
    assert applied == ["PARTIALLY_COMPLETED"]
    # ... yet nothing of the update was committed.
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.result is None
        assert trip.tourism_type_id is None
        assert trip.duration_classification == "UNCLASSIFIED"
        assert (trip.country_id, trip.region_id) == (None, None)


def test_unexpected_error_after_the_result_setter_rolls_back_the_whole_patch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.trips import service as trips_service

    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, altai = _region_of("ALTAY_REPUBLIC")

    def broken_set_geography(session, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("storage failure")

    monkeypatch.setattr(trips_service, "set_trip_geography", broken_set_geography)
    with pytest.raises(RuntimeError):
        _patch(
            client,
            f"/trips/{world.event_id}",
            {"result": "COMPLETED", "country_id": str(ru), "region_id": str(altai)},
        )
    assert _stored(world.event_id) is None


def test_successful_multi_fact_patch_is_committed_together(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, altai = _region_of("ALTAY_REPUBLIC")
    tourism_type_id = _tourism_type()
    response = _patch(
        client,
        f"/trips/{world.event_id}",
        {
            "tourism_type_id": str(tourism_type_id),
            "result": "COMPLETED",
            "country_id": str(ru),
            "region_id": str(altai),
        },
    )
    assert response.status_code == 200, response.text
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert (trip.result, trip.tourism_type_id, trip.country_id, trip.region_id) == (
            "COMPLETED",
            tourism_type_id,
            ru,
            altai,
        )


# --- create path authorization (PR #278 review) ----------------------------------------


def test_administrator_creates_a_trip_with_a_result(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    response = _create(client, world.event_id, result="NOT_COMPLETED")
    assert response.status_code == 201, response.text
    assert _stored(world.event_id) == "NOT_COMPLETED"


def test_instructor_assigned_to_the_event_creates_a_trip_with_a_result(
    client: TestClient,
) -> None:
    """The existing create contract: `trip.manage` resolved on the target
    Event (Instructor — `own_events`/`own_groups`); `result` rides on the
    same check, exactly as TourismType/Geography."""
    world = _world()
    _authenticate_as(world.instructor_events)
    response = _create(client, world.event_id, result="PARTIALLY_COMPLETED")
    assert response.status_code == 201, response.text
    assert _stored(world.event_id) == "PARTIALLY_COMPLETED"


def test_instructor_cannot_use_create_with_result_outside_their_scope(
    client: TestClient,
) -> None:
    """An Instructor who manages one Trip Event cannot create a Trip — with
    or without a Result — for an Event outside their scope."""
    own = _world()
    foreign = _world()
    _authenticate_as(own.instructor_events)
    for body in ({"result": "COMPLETED"}, {"result": None}, {}):
        response = _create(client, foreign.event_id, **body)
        assert response.status_code == 404, body
    assert not _trip_exists(foreign.event_id)
    # Still fine within their own scope.
    assert _create(client, own.event_id, result="COMPLETED").status_code == 201


@pytest.mark.parametrize("role", ["member", "guardian", "instructor_unrelated"])
def test_roles_outside_trip_manage_scope_cannot_create_with_a_result(
    client: TestClient, role: str
) -> None:
    world = _world()
    _authenticate_as(getattr(world, role))
    response = _create(client, world.event_id, result="COMPLETED")
    # Existence-hiding 404 of the ordinary trip.manage check.
    assert response.status_code == 404
    assert not _trip_exists(world.event_id)


# --- migration -------------------------------------------------------------------------


def test_migration_downgrade_and_upgrade_keep_existing_trips(database_url: str) -> None:
    world = _world(with_trip=True)
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.result = "COMPLETED"
        session.commit()
    try:
        downgrade = run_alembic("downgrade", "12de408aee0d", database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        columns = {c["name"] for c in sa.inspect(get_engine()).get_columns("trips")}
        assert "result" not in columns
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
    get_engine().dispose()
    # Existing Trips come back without a Result.
    assert _stored(world.event_id) is None
