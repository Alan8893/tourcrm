"""HTTP-level integration tests for the Official Difficulty Foundation
(Issue #268, docs/04-modules/trips-and-tourist-profile.md §4).

Against the REAL shipped app and a real PostgreSQL database, with
authorization coming only from the migration-seeded role matrix:

- one optional structured Official Difficulty per Trip through
  `POST /trips`, `GET /trips[/{event_id}]` and `PATCH /trips/{event_id}`:
  NONE / DEGREE I–III / CATEGORY I–VI / WEEKEND, `source` required except
  for NONE, every other combination rejected (API and database);
- ordinary lifecycle: open while draft/published/in_progress, closed for
  completed/cancelled/archived; absence never blocks creation or
  completion; idempotent re-send;
- independence: no TourismType applicability check, no automatic
  classification;
- authorization: the administrative `trip.manage` with `all` scope
  (Administrator); Instructor — in or out of the Trip's scope —,
  Member and Guardian can never set it; reads follow `trip.read`.

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


def _stored_difficulty(event_id: uuid.UUID) -> tuple[str | None, str | None, str | None]:
    with session_scope() as session:
        trip = session.get(Trip, event_id)
        assert trip is not None
        return (
            trip.official_difficulty_mode,
            trip.official_difficulty_value,
            trip.official_difficulty_source,
        )


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


_SOURCE = "Решение МКК № 12/2026"
_NO_DIFFICULTY = (None, None, None)


def _difficulty(mode: str, value: str | None = None, source: str | None = _SOURCE) -> dict:
    body: dict = {"mode": mode}
    if value is not None:
        body["value"] = value
    if source is not None:
        body["source"] = source
    return body


def _create(client: TestClient, event_id: uuid.UUID, difficulty: dict | None = None):  # type: ignore[no-untyped-def]
    body: dict = {"event_id": str(event_id)}
    if difficulty is not None:
        body["official_difficulty"] = difficulty
    return _post(client, "/trips", body)


def _set(client: TestClient, event_id: uuid.UUID, difficulty: dict | None):  # type: ignore[no-untyped-def]
    return _patch(client, f"/trips/{event_id}", {"official_difficulty": difficulty})


def _trip_exists(event_id: uuid.UUID) -> bool:
    with session_scope() as session:
        return session.get(Trip, event_id) is not None


# --- absence -----------------------------------------------------------------------------


def test_trip_without_difficulty_is_created_read_and_completed(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create(client, world.event_id)
    assert created.status_code == 201, created.text
    assert created.json()["official_difficulty"] is None
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["official_difficulty"] is None
    listed = client.get("/api/v1/trips").json()["items"]
    assert [item["official_difficulty"] for item in listed] == [None]

    _post(client, f"/events/{world.event_id}/status", {"status": "in_progress"})
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200, completed.text
    assert _stored_difficulty(world.event_id) == _NO_DIFFICULTY


def test_patch_without_difficulty_field_leaves_it_unchanged(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _create(client, world.event_id, _difficulty("DEGREE", "II"))
    response = _patch(client, f"/trips/{world.event_id}", {})
    assert response.status_code == 200, response.text
    assert _stored_difficulty(world.event_id) == ("DEGREE", "II", _SOURCE)


# --- approved combinations --------------------------------------------------------------


@pytest.mark.parametrize(
    ("difficulty", "expected"),
    [
        (_difficulty("NONE", source=None), {"mode": "NONE", "value": None, "source": None}),
        (_difficulty("NONE"), {"mode": "NONE", "value": None, "source": _SOURCE}),
        *[
            (_difficulty("DEGREE", value), {"mode": "DEGREE", "value": value, "source": _SOURCE})
            for value in ("I", "II", "III")
        ],
        *[
            (
                _difficulty("CATEGORY", value),
                {"mode": "CATEGORY", "value": value, "source": _SOURCE},
            )
            for value in ("I", "II", "III", "IV", "V", "VI")
        ],
        (_difficulty("WEEKEND"), {"mode": "WEEKEND", "value": None, "source": _SOURCE}),
    ],
)
def test_approved_difficulty_on_create_read_and_edit(
    client: TestClient, difficulty: dict, expected: dict
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _create(client, world.event_id, difficulty)
    assert created.status_code == 201, created.text
    assert created.json()["official_difficulty"] == expected
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["official_difficulty"] == expected
    stored = _stored_difficulty(world.event_id)
    assert stored == (expected["mode"], expected["value"], expected["source"])

    other = _world(with_trip=True)
    _authenticate_as(other.admin)
    edited = _set(client, other.event_id, difficulty)
    assert edited.status_code == 200, edited.text
    assert edited.json()["official_difficulty"] == expected


def test_source_is_trimmed(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    response = _create(client, world.event_id, _difficulty("WEEKEND", source=f"  {_SOURCE}  "))
    assert response.json()["official_difficulty"]["source"] == _SOURCE


# --- rejected combinations --------------------------------------------------------------

_REJECTED = [
    # NONE never takes a value.
    _difficulty("NONE", "II"),
    _difficulty("NONE", "I", source=None),
    # DEGREE: value I–III and a source.
    _difficulty("DEGREE"),
    _difficulty("DEGREE", "IV"),
    _difficulty("DEGREE", "VI"),
    _difficulty("DEGREE", "II", source=None),
    _difficulty("DEGREE", "II", source="   "),
    # CATEGORY: value I–VI and a source.
    _difficulty("CATEGORY"),
    _difficulty("CATEGORY", "VII"),
    _difficulty("CATEGORY", "VI", source=None),
    _difficulty("CATEGORY", "I", source=""),
    # WEEKEND: no value, a source; it is not degree 0.
    _difficulty("WEEKEND", "III"),
    _difficulty("WEEKEND", "0"),
    _difficulty("WEEKEND", source=None),
    # Unknown modes/values, a missing mode, a second classification.
    _difficulty("CLUB", "II"),
    _difficulty("degree", "II"),
    {"value": "II", "source": _SOURCE},
    {"mode": "DEGREE", "value": "II", "category": "VI", "source": _SOURCE},
    {"mode": "DEGREE", "value": 2, "source": _SOURCE},
    "DEGREE II",
]


@pytest.mark.parametrize("difficulty", _REJECTED)
def test_unapproved_difficulty_is_rejected_on_create(client: TestClient, difficulty) -> None:  # type: ignore[no-untyped-def]
    world = _world()
    _authenticate_as(world.admin)
    response = _post(
        client, "/trips", {"event_id": str(world.event_id), "official_difficulty": difficulty}
    )
    assert response.status_code == 422, response.text
    assert not _trip_exists(world.event_id)


@pytest.mark.parametrize("difficulty", _REJECTED)
def test_unapproved_difficulty_is_rejected_on_edit(client: TestClient, difficulty) -> None:  # type: ignore[no-untyped-def]
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, _difficulty("CATEGORY", "III"))
    response = _set(client, world.event_id, difficulty)
    assert response.status_code == 422, response.text
    assert _stored_difficulty(world.event_id) == ("CATEGORY", "III", _SOURCE)


def test_combination_errors_carry_the_domain_code(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    response = _set(client, world.event_id, _difficulty("WEEKEND", "III"))
    assert response.json()["error"]["code"] == "invalid_official_difficulty"


@pytest.mark.parametrize(
    ("mode", "value", "source"),
    [
        ("NONE", "II", None),
        ("DEGREE", "IV", _SOURCE),
        ("DEGREE", "II", None),
        ("CATEGORY", None, _SOURCE),
        ("CATEGORY", "VII", _SOURCE),
        ("CATEGORY", "VI", None),
        ("WEEKEND", "III", _SOURCE),
        ("WEEKEND", None, None),
        ("OTHER", None, _SOURCE),
        (None, "II", None),
        (None, None, _SOURCE),
        ("WEEKEND", None, "   "),
    ],
)
def test_database_rejects_unapproved_combinations(
    mode: str | None, value: str | None, source: str | None
) -> None:
    world = _world(with_trip=True)
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.official_difficulty_mode = mode
        trip.official_difficulty_value = value
        trip.official_difficulty_source = source
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
    assert exc_info.value.orig.diag.constraint_name in {  # type: ignore[union-attr]
        "ck_trips_official_difficulty_combination",
        "ck_trips_official_difficulty_source_not_blank",
    }
    assert _stored_difficulty(world.event_id) == _NO_DIFFICULTY


# --- ordinary lifecycle -----------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "published", "in_progress"])
def test_difficulty_set_changed_and_cleared_while_editing_is_open(
    client: TestClient, status: str
) -> None:
    world = _world(status=status, with_trip=True)
    _authenticate_as(world.admin)

    added = _set(client, world.event_id, _difficulty("DEGREE", "I"))
    assert added.status_code == 200, added.text
    assert _stored_difficulty(world.event_id) == ("DEGREE", "I", _SOURCE)

    # DEGREE -> CATEGORY replaces the single classification.
    changed = _set(client, world.event_id, _difficulty("CATEGORY", "II", source="Другое основание"))
    assert changed.status_code == 200, changed.text
    assert _stored_difficulty(world.event_id) == ("CATEGORY", "II", "Другое основание")

    weekend = _set(client, world.event_id, _difficulty("WEEKEND"))
    assert _stored_difficulty(world.event_id) == ("WEEKEND", None, _SOURCE)
    assert weekend.json()["official_difficulty"]["value"] is None

    none = _set(client, world.event_id, _difficulty("NONE", source=None))
    assert _stored_difficulty(world.event_id) == ("NONE", None, None)
    assert none.json()["official_difficulty"] == {"mode": "NONE", "value": None, "source": None}

    cleared = _set(client, world.event_id, None)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["official_difficulty"] is None
    assert _stored_difficulty(world.event_id) == _NO_DIFFICULTY


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
def test_closed_trip_cannot_change_difficulty(client: TestClient, status: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, _difficulty("CATEGORY", "IV"))
    _set_event_status(world.event_id, status)

    attempts = [
        _set(client, world.event_id, _difficulty("CATEGORY", "V")),
        _set(client, world.event_id, _difficulty("NONE", source=None)),
        _set(client, world.event_id, None),
        # Even re-sending the stored value is not ordinary editing here.
        _set(client, world.event_id, _difficulty("CATEGORY", "IV")),
    ]
    assert [response.status_code for response in attempts] == [409] * len(attempts)
    assert {response.json()["error"]["code"] for response in attempts} == {"trip_editing_closed"}
    assert _stored_difficulty(world.event_id) == ("CATEGORY", "IV", _SOURCE)


@pytest.mark.parametrize("status", ["cancelled", "archived"])
def test_trip_with_difficulty_cannot_be_created_for_closed_event(
    client: TestClient, status: str
) -> None:
    world = _world(status=status)
    _authenticate_as(world.admin)
    response = _create(client, world.event_id, _difficulty("DEGREE", "I"))
    assert response.status_code == 409
    assert not _trip_exists(world.event_id)


def test_completed_trip_keeps_its_difficulty(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    _create(client, world.event_id, _difficulty("DEGREE", "III"))
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200, completed.text
    assert client.get(f"/api/v1/trips/{world.event_id}").json()["official_difficulty"] == {
        "mode": "DEGREE",
        "value": "III",
        "source": _SOURCE,
    }


def test_re_sending_the_same_difficulty_is_idempotent(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    first = _set(client, world.event_id, _difficulty("CATEGORY", "VI"))
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        updated_at = trip.updated_at
    again = _set(client, world.event_id, _difficulty("CATEGORY", "VI", source=f" {_SOURCE} "))
    assert again.status_code == 200
    assert again.json() == first.json()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.updated_at == updated_at
        assert session.execute(select(Trip).where(Trip.event_id == world.event_id)).scalars().all()
    assert _stored_difficulty(world.event_id) == ("CATEGORY", "VI", _SOURCE)
    # Clearing twice is a no-op as well.
    assert _set(client, world.event_id, None).status_code == 200
    assert _set(client, world.event_id, None).json()["official_difficulty"] is None


def test_one_difficulty_per_trip(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _create(client, world.event_id, _difficulty("DEGREE", "II"))
    duplicate = _create(client, world.event_id, _difficulty("CATEGORY", "I"))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "trip_already_exists"
    assert _stored_difficulty(world.event_id) == ("DEGREE", "II", _SOURCE)


# --- independence -----------------------------------------------------------------------


def test_difficulty_is_independent_of_tourism_type(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    tourism_type_id = _tourism_type()
    created = _post(
        client,
        "/trips",
        {
            "event_id": str(world.event_id),
            "tourism_type_id": str(tourism_type_id),
            "official_difficulty": _difficulty("CATEGORY", "VI"),
        },
    )
    assert created.status_code == 201, created.text
    for difficulty in (_difficulty("DEGREE", "I"), _difficulty("WEEKEND"), None):
        assert _set(client, world.event_id, difficulty).status_code == 200
        assert _stored_difficulty(world.event_id)[0] == (difficulty or {}).get("mode")

    # Changing or clearing the TourismType never touches the Difficulty.
    _set(client, world.event_id, _difficulty("DEGREE", "III"))
    assert _patch(client, f"/trips/{world.event_id}", {"tourism_type_id": None}).status_code == 200
    assert _stored_difficulty(world.event_id) == ("DEGREE", "III", _SOURCE)

    # Both in one body are applied together.
    both = _patch(
        client,
        f"/trips/{world.event_id}",
        {"tourism_type_id": str(tourism_type_id), "official_difficulty": _difficulty("WEEKEND")},
    )
    assert both.status_code == 200
    assert both.json()["tourism_type_id"] == str(tourism_type_id)
    assert both.json()["official_difficulty"]["mode"] == "WEEKEND"


def test_rejected_difficulty_does_not_apply_the_tourism_type_of_the_same_body(
    client: TestClient,
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    tourism_type_id = _tourism_type()
    response = _patch(
        client,
        f"/trips/{world.event_id}",
        {"tourism_type_id": str(tourism_type_id), "official_difficulty": _difficulty("DEGREE")},
    )
    assert response.status_code == 422
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.tourism_type_id is None


def test_difficulty_is_never_assigned_automatically(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    _post(
        client,
        "/trips",
        {"event_id": str(world.event_id), "tourism_type_id": str(_tourism_type())},
    )
    client.put(
        f"/api/v1/trips/{world.event_id}/participants/{_member_person(world.member)}",
        json={"actual_participation": True},
        headers=_csrf(client),
    )
    _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert _stored_difficulty(world.event_id) == _NO_DIFFICULTY


def _member_person(user_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        user = session.get(User, user_id)
        assert user is not None
        return user.person_id


# --- authorization ----------------------------------------------------------------------


def test_administrator_manages_difficulty(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    assert _create(client, world.event_id, _difficulty("DEGREE", "I")).status_code == 201
    assert _set(client, world.event_id, _difficulty("CATEGORY", "V")).status_code == 200
    assert _set(client, world.event_id, None).status_code == 200


def test_instructor_in_trip_scope_cannot_set_difficulty(client: TestClient) -> None:
    """§4: Instructor gets no right to set or change Official Difficulty —
    not even for a Trip it may otherwise manage (`own_events`)."""
    world = _world()
    _authenticate_as(world.instructor_events)

    denied_create = _create(client, world.event_id, _difficulty("DEGREE", "I"))
    assert denied_create.status_code == 403
    assert denied_create.json()["error"]["code"] == "forbidden"
    assert not _trip_exists(world.event_id)
    # Even an explicit NONE is a Difficulty value.
    assert _create(client, world.event_id, _difficulty("NONE", source=None)).status_code == 403

    # The Instructor still creates and edits the Trip without Difficulty.
    assert _create(client, world.event_id).status_code == 201
    tourism_type_id = _tourism_type()
    edited = _patch(client, f"/trips/{world.event_id}", {"tourism_type_id": str(tourism_type_id)})
    assert edited.status_code == 200
    assert client.get(f"/api/v1/trips/{world.event_id}").status_code == 200

    _authenticate_as(world.admin)
    _set(client, world.event_id, _difficulty("CATEGORY", "II"))
    _authenticate_as(world.instructor_events)
    attempts = [
        _set(client, world.event_id, _difficulty("CATEGORY", "III")),
        _set(client, world.event_id, _difficulty("CATEGORY", "II")),
        _set(client, world.event_id, None),
        _patch(
            client,
            f"/trips/{world.event_id}",
            {"tourism_type_id": None, "official_difficulty": _difficulty("WEEKEND")},
        ),
    ]
    assert [response.status_code for response in attempts] == [403] * len(attempts)
    assert _stored_difficulty(world.event_id) == ("CATEGORY", "II", _SOURCE)
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.tourism_type_id == tourism_type_id
    # Reading the Difficulty follows trip.read.
    read = client.get(f"/api/v1/trips/{world.event_id}").json()["official_difficulty"]
    assert read == {"mode": "CATEGORY", "value": "II", "source": _SOURCE}


@pytest.mark.parametrize("role", ["instructor_unrelated", "member", "guardian"])
def test_roles_without_trip_manage_scope_cannot_set_difficulty(
    client: TestClient, role: str
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, _difficulty("DEGREE", "II"))

    _authenticate_as(getattr(world, role))
    # Existence-hiding 404 from the ordinary trip.manage check comes first.
    assert _set(client, world.event_id, _difficulty("CATEGORY", "VI")).status_code == 404
    assert _set(client, world.event_id, None).status_code == 404
    other = _world()
    assert _create(client, other.event_id, _difficulty("WEEKEND")).status_code == 404
    assert not _trip_exists(other.event_id)
    assert _stored_difficulty(world.event_id) == ("DEGREE", "II", _SOURCE)


@pytest.mark.parametrize("role", ["member", "guardian"])
def test_trip_readers_see_the_difficulty(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set(client, world.event_id, _difficulty("WEEKEND"))
    _authenticate_as(getattr(world, role))
    response = client.get(f"/api/v1/trips/{world.event_id}")
    assert response.status_code == 200
    assert response.json()["official_difficulty"] == {
        "mode": "WEEKEND",
        "value": None,
        "source": _SOURCE,
    }


def test_difficulty_requires_authentication_and_csrf(client: TestClient) -> None:
    world = _world(with_trip=True)
    assert _set(client, world.event_id, _difficulty("WEEKEND")).status_code == 401
    _authenticate_as(world.admin)
    response = client.patch(
        f"/api/v1/trips/{world.event_id}", json={"official_difficulty": _difficulty("WEEKEND")}
    )
    assert response.status_code == 403
    assert _stored_difficulty(world.event_id) == _NO_DIFFICULTY


# --- migration --------------------------------------------------------------------------


def test_migration_downgrade_and_upgrade_keep_existing_trips(database_url: str) -> None:
    world = _world(with_trip=True)
    tourism_type_id = _tourism_type()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.tourism_type_id = tourism_type_id
        trip.official_difficulty_mode = "DEGREE"
        trip.official_difficulty_value = "I"
        trip.official_difficulty_source = _SOURCE
        session.commit()
    try:
        downgrade = run_alembic("downgrade", "c4f8a2e61d93", database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        columns = {c["name"] for c in sa.inspect(get_engine()).get_columns("trips")}
        assert not {c for c in columns if c.startswith("official_difficulty")}
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
    get_engine().dispose()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.tourism_type_id == tourism_type_id
    # Existing Trips come back without a Difficulty.
    assert _stored_difficulty(world.event_id) == _NO_DIFFICULTY
