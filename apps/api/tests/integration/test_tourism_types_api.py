"""HTTP-level integration tests for the TourismType Foundation (Issue #264).

Against the REAL shipped app and a real PostgreSQL database, with
authorization coming only from the migration-seeded role matrix:

- the `/api/v1/tourism-types` catalog: create/read/list/update,
  activate/deactivate/reactivate, no DELETE, no seeded values;
- Trip assignment through `POST /trips` and ordinary editing through
  `PATCH /trips/{event_id}`: null allowed, nonexistent and inactive
  rejected, completed/cancelled/archived Trips closed, deactivation never
  touching referencing Trips, no automatic classification;
- authorization: catalog management Administrator-only (`trip.manage`
  with `all`), catalog reads for `trip.read` holders, Trip assignment by
  the existing `trip.manage` scope.

Self-contained factories, per this codebase's convention of not
importing helpers across test files.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation, EventStaffAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.db.trips import TourismType, Trip
from app.main import app

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


def _trip_tourism_type(event_id: uuid.UUID) -> uuid.UUID | None:
    with session_scope() as session:
        trip = session.get(Trip, event_id)
        assert trip is not None
        return trip.tourism_type_id


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


def _create_type(client: TestClient, code: str = "code-a", name: str = "Тип А") -> dict:
    response = _post(client, "/tourism-types", {"code": code, "name": name})
    assert response.status_code == 201, response.text
    return response.json()


# --- catalog ---------------------------------------------------------------------------


def test_catalog_starts_empty_no_seeded_values(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    response = client.get("/api/v1/tourism-types")
    assert response.status_code == 200
    assert response.json()["items"] == []
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(TourismType)).scalar_one() == 0


def test_catalog_create_read_list_update(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create_type(client, code="  code-a ", name=" Тип А ")
    assert (created["code"], created["name"], created["active"]) == ("code-a", "Тип А", True)

    fetched = client.get(f"/api/v1/tourism-types/{created['id']}").json()
    assert fetched == created

    second = _create_type(client, code="code-b", name="Б-тип")
    listed = client.get("/api/v1/tourism-types").json()
    assert [item["id"] for item in listed["items"]] == [second["id"], created["id"]]
    assert listed["pagination"]["total"] == 2

    updated = _patch(client, f"/tourism-types/{created['id']}", {"name": "Новое имя"}).json()
    assert (updated["code"], updated["name"]) == ("code-a", "Новое имя")
    recoded = _patch(client, f"/tourism-types/{created['id']}", {"code": "code-c"}).json()
    assert recoded["code"] == "code-c"


def test_catalog_validation_and_duplicate_code(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create_type(client, code="dup")
    duplicate = _post(client, "/tourism-types", {"code": "dup", "name": "x"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "tourism_type_code_conflict"
    assert _post(client, "/tourism-types", {"code": "   ", "name": "x"}).status_code == 422
    assert _post(client, "/tourism-types", {"code": "x"}).status_code == 422
    other = _create_type(client, code="other")
    conflict = _patch(client, f"/tourism-types/{other['id']}", {"code": "dup"})
    assert conflict.status_code == 409
    assert client.get(f"/api/v1/tourism-types/{created['id']}").json()["code"] == "dup"
    assert client.get(f"/api/v1/tourism-types/{uuid.uuid4()}").status_code == 404


def test_catalog_deactivate_and_reactivate_keeps_the_entry(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create_type(client)
    deactivated = _post(client, f"/tourism-types/{created['id']}/deactivate").json()
    assert deactivated["active"] is False
    # Idempotent and still stored.
    assert _post(client, f"/tourism-types/{created['id']}/deactivate").json()["active"] is False
    assert client.get(f"/api/v1/tourism-types/{created['id']}").json()["active"] is False
    inactive = client.get("/api/v1/tourism-types?active=false").json()["items"]
    assert [item["id"] for item in inactive] == [created["id"]]
    assert client.get("/api/v1/tourism-types?active=true").json()["items"] == []

    reactivated = _post(client, f"/tourism-types/{created['id']}/activate").json()
    assert reactivated["active"] is True


def test_catalog_has_no_delete(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create_type(client)
    response = client.delete(f"/api/v1/tourism-types/{created['id']}", headers=_csrf(client))
    assert response.status_code == 405
    assert client.get(f"/api/v1/tourism-types/{created['id']}").status_code == 200


def test_referenced_tourism_type_cannot_be_physically_deleted() -> None:
    world = _world(with_trip=True)
    tourism_type_id = _tourism_type()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.tourism_type_id = tourism_type_id
        session.commit()
    with session_scope() as session:
        session.delete(session.get(TourismType, tourism_type_id))
        with pytest.raises(IntegrityError):
            session.commit()
    assert _trip_tourism_type(world.event_id) == tourism_type_id


# --- catalog authorization -----------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["instructor_events", "instructor_unrelated", "member", "guardian"]
)
def test_only_administrator_manages_the_catalog(client: TestClient, role: str) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create_type(client)

    _authenticate_as(getattr(world, role))
    denied = [
        _post(client, "/tourism-types", {"code": "new", "name": "n"}),
        _patch(client, f"/tourism-types/{created['id']}", {"name": "hacked"}),
        _post(client, f"/tourism-types/{created['id']}/deactivate"),
        _post(client, f"/tourism-types/{created['id']}/activate"),
        _patch(client, f"/tourism-types/{uuid.uuid4()}", {"name": "x"}),
    ]
    assert [response.status_code for response in denied] == [403] * len(denied)

    # Reading is allowed to every role holding trip.read.
    assert client.get("/api/v1/tourism-types").status_code == 200
    assert client.get(f"/api/v1/tourism-types/{created['id']}").json() == created


def test_catalog_requires_authentication_and_csrf(client: TestClient) -> None:
    world = _world()
    assert client.get("/api/v1/tourism-types").status_code == 401
    _authenticate_as(world.admin)
    response = client.post("/api/v1/tourism-types", json={"code": "c", "name": "n"})
    assert response.status_code == 403


# --- Trip assignment -----------------------------------------------------------------


def test_trip_without_tourism_type(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    response = _post(client, "/trips", {"event_id": str(world.event_id)})
    assert response.status_code == 201, response.text
    assert response.json()["tourism_type_id"] is None
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["tourism_type_id"] is None


def test_trip_created_with_active_tourism_type(client: TestClient) -> None:
    world = _world()
    tourism_type_id = _tourism_type()
    _authenticate_as(world.admin)
    response = _post(
        client, "/trips", {"event_id": str(world.event_id), "tourism_type_id": str(tourism_type_id)}
    )
    assert response.status_code == 201, response.text
    assert response.json()["tourism_type_id"] == str(tourism_type_id)
    listed = client.get("/api/v1/trips").json()["items"]
    assert [item["tourism_type_id"] for item in listed] == [str(tourism_type_id)]


@pytest.mark.parametrize(
    ("active", "code"), [(None, "tourism_type_not_found"), (False, "tourism_type_inactive")]
)
def test_trip_creation_rejects_nonexistent_or_inactive_tourism_type(
    client: TestClient, active: bool | None, code: str
) -> None:
    world = _world()
    tourism_type_id = uuid.uuid4() if active is None else _tourism_type(active=active)
    _authenticate_as(world.admin)
    response = _post(
        client, "/trips", {"event_id": str(world.event_id), "tourism_type_id": str(tourism_type_id)}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    with session_scope() as session:
        assert session.get(Trip, world.event_id) is None


@pytest.mark.parametrize("status", ["draft", "published", "in_progress"])
def test_tourism_type_can_be_set_changed_and_cleared_on_editable_trip(
    client: TestClient, status: str
) -> None:
    world = _world(status=status, with_trip=True)
    first, second = _tourism_type(), _tourism_type()
    _authenticate_as(world.admin)
    path = f"/trips/{world.event_id}"

    assert _patch(client, path, {"tourism_type_id": str(first)}).json()["tourism_type_id"] == (
        str(first)
    )
    assert _patch(client, path, {"tourism_type_id": str(second)}).json()["tourism_type_id"] == (
        str(second)
    )
    # A body without the field changes nothing.
    assert _patch(client, path, {}).json()["tourism_type_id"] == str(second)
    assert _patch(client, path, {"tourism_type_id": None}).json()["tourism_type_id"] is None
    assert _trip_tourism_type(world.event_id) is None


def test_editing_rejects_nonexistent_or_inactive_tourism_type(client: TestClient) -> None:
    world = _world(with_trip=True)
    current = _tourism_type()
    inactive = _tourism_type(active=False)
    _authenticate_as(world.admin)
    path = f"/trips/{world.event_id}"
    _patch(client, path, {"tourism_type_id": str(current)})

    response = _patch(client, path, {"tourism_type_id": str(inactive)})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tourism_type_inactive"
    missing = _patch(client, path, {"tourism_type_id": str(uuid.uuid4())})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "tourism_type_not_found"
    assert _trip_tourism_type(world.event_id) == current


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
def test_closed_trip_cannot_change_tourism_type(client: TestClient, status: str) -> None:
    world = _world(with_trip=True)
    original, other = _tourism_type(), _tourism_type()
    _authenticate_as(world.admin)
    path = f"/trips/{world.event_id}"
    _patch(client, path, {"tourism_type_id": str(original)})
    _set_event_status(world.event_id, status)

    for body in ({"tourism_type_id": str(other)}, {"tourism_type_id": None}):
        response = _patch(client, path, body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "trip_editing_closed"
    assert _trip_tourism_type(world.event_id) == original


def test_deactivation_keeps_historical_trip_reference(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    created = _create_type(client)
    path = f"/trips/{world.event_id}"
    _patch(client, path, {"tourism_type_id": created["id"]})
    _set_event_status(world.event_id, "completed")

    _post(client, f"/tourism-types/{created['id']}/deactivate")
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["tourism_type_id"] == created["id"]
    # Re-sending the stored value on an editable Trip is a no-op, even if inactive.
    _set_event_status(world.event_id, "in_progress")
    unchanged = _patch(client, path, {"tourism_type_id": created["id"]})
    assert unchanged.status_code == 200
    assert unchanged.json()["tourism_type_id"] == created["id"]


def test_tourism_type_is_never_assigned_automatically(client: TestClient) -> None:
    world = _world(status="in_progress")
    _tourism_type()
    _authenticate_as(world.admin)
    _post(client, "/trips", {"event_id": str(world.event_id)})
    with session_scope() as session:
        person_id = (
            session.execute(
                select(EventParticipation.person_id).where(
                    EventParticipation.event_id == world.event_id
                )
            )
            .scalars()
            .first()
        )
    client.put(
        f"/api/v1/trips/{world.event_id}/participants/{person_id}",
        json={"actual_participation": True},
        headers=_csrf(client),
    )
    _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert _trip_tourism_type(world.event_id) is None


# --- Trip assignment authorization ---------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "instructor_events"])
def test_trip_managers_in_scope_can_assign(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    tourism_type_id = _tourism_type()
    _authenticate_as(getattr(world, role))
    response = _patch(client, f"/trips/{world.event_id}", {"tourism_type_id": str(tourism_type_id)})
    assert response.status_code == 200, response.text
    assert response.json()["tourism_type_id"] == str(tourism_type_id)


@pytest.mark.parametrize("role", ["instructor_unrelated", "member", "guardian"])
def test_roles_without_trip_manage_scope_cannot_assign(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    tourism_type_id = _tourism_type()
    _authenticate_as(getattr(world, role))
    response = _patch(client, f"/trips/{world.event_id}", {"tourism_type_id": str(tourism_type_id)})
    assert response.status_code == 404
    created = _post(
        client, "/trips", {"event_id": str(uuid.uuid4()), "tourism_type_id": str(tourism_type_id)}
    )
    assert created.status_code == 404
    assert _trip_tourism_type(world.event_id) is None
