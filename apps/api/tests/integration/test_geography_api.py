"""HTTP-level integration tests for the Geography Foundation (Issue #271,
docs/04-modules/trips-and-tourist-profile.md §9, Issue #258, PR #270).

Against the REAL shipped app and a real PostgreSQL database, with
authorization coming only from the migration-seeded role matrix:

- Country catalog: the migration-seeded ISO 3166-1 alpha-2 set with
  Russian names and provenance; create/read/list/update, unique ISO code,
  activate/deactivate/reactivate, no DELETE;
- Region catalog: no seeded values; exactly one Country; code unique
  within its Country; a Region referenced by a Trip cannot move to
  another Country; activate/deactivate/reactivate, no DELETE;
- Trip Geography through `POST /trips` and `PATCH /trips/{event_id}`:
  0..1 Country and 0..1 Region, Region of the Trip's Country only (API
  and database), nonexistent and inactive entries rejected,
  completed/cancelled/archived Trips closed, deactivation never touching
  referencing Trips, no automatic inference;
- authorization: catalog management Administrator-only (`trip.manage`
  with `all`), catalog reads for `trip.read` holders, Trip assignment by
  the existing `trip.manage` scope.

Self-contained factories, per this codebase's convention of not
importing helpers across test files.
"""

import datetime
import importlib.util
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select
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


_ADMIN = "administrative_subject"


def _country_id(code: str) -> uuid.UUID:
    with session_scope() as session:
        return session.execute(select(Country.id).where(Country.code == code)).scalar_one()


def _region(country_code: str = "RU", *, code: str | None = None, active: bool = True) -> uuid.UUID:
    with session_scope() as session:
        row = Region(
            country_id=_country_id(country_code),
            code=code or f"r-{uuid.uuid4().hex[:8]}",
            name="Район",
            semantic_type=_ADMIN,
            active=active,
        )
        session.add(row)
        session.commit()
        return row.id


def _set_country_active(code: str, active: bool) -> None:
    with session_scope() as session:
        country = session.execute(select(Country).where(Country.code == code)).scalar_one()
        country.active = active
        session.commit()


def _stored_geography(event_id: uuid.UUID) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    with session_scope() as session:
        trip = session.get(Trip, event_id)
        assert trip is not None
        return trip.country_id, trip.region_id


def _trip_exists(event_id: uuid.UUID) -> bool:
    with session_scope() as session:
        return session.get(Trip, event_id) is not None


def _geo(client: TestClient, event_id: uuid.UUID, body: dict):  # type: ignore[no-untyped-def]
    return _patch(client, f"/trips/{event_id}", body)


def _ids(country_id: uuid.UUID | None, region_id: uuid.UUID | None = None) -> dict:
    return {
        "country_id": str(country_id) if country_id else None,
        "region_id": str(region_id) if region_id else None,
    }


def _all_countries(client: TestClient) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        body = client.get(f"/api/v1/countries?page={page}&page_size=100").json()
        items.extend(body["items"])
        if page >= body["pagination"]["pages"]:
            return items
        page += 1


# --- Country catalog ------------------------------------------------------------------


def test_country_catalog_is_seeded_with_iso_alpha2_and_russian_names(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    countries = _all_countries(client)
    assert len(countries) == 249
    codes = [item["code"] for item in countries]
    assert len(set(codes)) == 249
    assert all(len(code) == 2 and code.isascii() and code.isupper() for code in codes)
    by_code = {item["code"]: item for item in countries}
    assert by_code["RU"]["name"] == "Россия"
    assert by_code["HR"]["name"] == "Хорватия"
    assert by_code["DE"]["name"] == "Германия"
    # Russian is the canonical display name — no English names.
    assert by_code["DE"]["name"] != "Germany"
    assert all(item["active"] for item in countries)
    assert {item["source_type"] for item in countries} == {"ISO_3166_1"}
    # Russian display names come from Unicode CLDR 48.2.0, locale `ru`.
    assert {item["source_reference"] for item in countries} == {
        "ISO 3166-1 alpha-2; Russian names: Unicode CLDR 48.2.0, locale ru"
    }
    assert (by_code["US"]["name"], by_code["CI"]["name"]) == ("Соединенные Штаты", "Кот-д’Ивуар")


def _seeded_ru_subjects() -> tuple[tuple[str, str, str], ...]:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "2c28e7b34462_create_countries_regions_and_trip_.py"
    )
    spec = importlib.util.spec_from_file_location("geography_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._RU_SUBJECTS


def _all_regions(client: TestClient, query: str = "") -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        body = client.get(f"/api/v1/regions?page={page}&page_size=100{query}").json()
        items.extend(body["items"])
        if page >= max(body["pagination"]["pages"], 1):
            return items
        page += 1


def test_region_catalog_is_seeded_with_the_89_ru_administrative_subjects(
    client: TestClient,
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    regions = _all_regions(client)
    assert len(regions) == 89
    ru = str(_country_id("RU"))
    assert {item["country_id"] for item in regions} == {ru}
    assert {item["semantic_type"] for item in regions} == {_ADMIN}
    assert all(item["active"] for item in regions)
    names = [item["name"] for item in regions]
    codes = [item["code"] for item in regions]
    assert len(set(names)) == 89
    assert len(set(codes)) == 89
    expected = _seeded_ru_subjects()
    assert set(names) == {name for _kind, _code, name in expected}
    assert {(item["code"], item["name"]) for item in regions} == {
        (code, name) for _kind, code, name in expected
    }
    assert {item["source_type"] for item in regions} == {"CONSTITUTION_RF_ARTICLE_65"}
    assert {item["source_reference"] for item in regions} == {
        "Constitution of the Russian Federation, Article 65, Part 1; canonical TourCRM RU "
        "administrative-subject seed fixed by PO on 2026-10-05"
    }
    by_code = {item["code"]: item["name"] for item in regions}
    assert by_code["MOSCOW"] == "город федерального значения Москва"
    assert by_code["MOSCOW_OBLAST"] == "Московская область"
    assert by_code["ADYGEA"] == "Республика Адыгея (Адыгея)"
    assert not [name for name in names if "Байконур" in name]
    # Only RU is seeded; no other Country has a Region.
    assert client.get(f"/api/v1/regions?country_id={_country_id('HR')}").json()["items"] == []
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(Region)).scalar_one() == 89
        other = session.execute(
            select(func.count()).select_from(Region).where(Region.country_id != _country_id("RU"))
        ).scalar_one()
        assert other == 0
        assert (
            session.execute(
                select(func.count()).select_from(Region).where(Region.first_used_at.is_not(None))
            ).scalar_one()
            == 0
        )


def test_seeded_region_can_be_assigned_to_a_trip(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru = _country_id("RU")
    with session_scope() as session:
        altai = session.execute(
            select(Region.id).where(Region.code == "ALTAY_REPUBLIC")
        ).scalar_one()
    response = _geo(client, world.event_id, _ids(ru, altai))
    assert response.status_code == 200, response.text
    assert _stored_geography(world.event_id) == (ru, altai)
    # Once used, the seeded subject's semantic fields are immutable.
    renamed = _patch(client, f"/regions/{altai}", {"name": "Горный Алтай"})
    assert renamed.status_code == 409


def test_country_create_read_list_update(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _post(
        client,
        "/countries",
        {"code": " xk ", "name": " Косово ", "source_type": "MANUAL", "source_reference": "  "},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["code"], body["name"], body["active"]) == ("XK", "Косово", True)
    assert (body["source_type"], body["source_reference"]) == ("MANUAL", None)
    assert client.get(f"/api/v1/countries/{body['id']}").json() == body

    listed = client.get("/api/v1/countries?page=1&page_size=10").json()
    assert listed["pagination"] == {"page": 1, "page_size": 10, "total": 250, "pages": 25}

    renamed = _patch(client, f"/countries/{body['id']}", {"name": "Республика Косово"}).json()
    assert (renamed["code"], renamed["name"]) == ("XK", "Республика Косово")
    assert renamed["source_type"] == "MANUAL"
    provenance = _patch(
        client, f"/countries/{body['id']}", {"source_type": None, "source_reference": "Решение"}
    ).json()
    assert (provenance["source_type"], provenance["source_reference"]) == (None, "Решение")
    assert client.get(f"/api/v1/countries/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize("code", ["RUS", "R", "1A", "", "  ", "Р1"])
def test_country_code_must_be_iso_alpha2(client: TestClient, code: str) -> None:
    world = _world()
    _authenticate_as(world.admin)
    response = _post(client, "/countries", {"code": code, "name": "Страна"})
    assert response.status_code == 422


def test_country_duplicate_iso_code_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    duplicate = _post(client, "/countries", {"code": "ru", "name": "Россия"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "country_code_conflict"
    hr = _country_id("HR")
    conflict = _patch(client, f"/countries/{hr}", {"code": "DE"})
    assert conflict.status_code == 409
    assert client.get(f"/api/v1/countries/{hr}").json()["code"] == "HR"


def test_country_deactivate_and_reactivate_keeps_the_entry(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    hr = _country_id("HR")
    assert _post(client, f"/countries/{hr}/deactivate").json()["active"] is False
    assert _post(client, f"/countries/{hr}/deactivate").json()["active"] is False
    inactive = client.get("/api/v1/countries?active=false").json()["items"]
    assert [item["code"] for item in inactive] == ["HR"]
    assert client.get("/api/v1/countries?active=true").json()["pagination"]["total"] == 248
    assert _post(client, f"/countries/{hr}/activate").json()["active"] is True


# --- Region catalog -------------------------------------------------------------------


def test_region_create_read_list_update(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    ru = _country_id("RU")
    created = _post(
        client,
        "/regions",
        {
            "semantic_type": _ADMIN,
            "country_id": str(ru),
            "code": " altai ",
            "name": " Алтай ",
            "source_type": "MANUAL",
            "source_reference": "Решение клуба",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["country_id"], body["code"], body["name"], body["active"]) == (
        str(ru),
        "altai",
        "Алтай",
        True,
    )
    assert (body["source_type"], body["source_reference"]) == ("MANUAL", "Решение клуба")
    assert body["semantic_type"] == "administrative_subject"
    assert client.get(f"/api/v1/regions/{body['id']}").json() == body

    hr = _country_id("HR")
    other = _post(
        client,
        "/regions",
        {"semantic_type": _ADMIN, "country_id": str(hr), "code": "altai", "name": "Б"},
    )
    # The code is unique within its Country only.
    assert other.status_code == 201, other.text
    in_hr = client.get(f"/api/v1/regions?country_id={hr}").json()
    assert [item["id"] for item in in_hr["items"]] == [other.json()["id"]]
    in_ru = _all_regions(client, f"&country_id={ru}")
    assert body["id"] in {item["id"] for item in in_ru}
    assert {item["country_id"] for item in in_ru} == {str(ru)}
    # The 89 seeded RU subjects plus the two created here.
    assert client.get("/api/v1/regions").json()["pagination"]["total"] == 91

    updated = _patch(client, f"/regions/{body['id']}", {"name": "Горный Алтай"}).json()
    assert (updated["code"], updated["name"]) == ("altai", "Горный Алтай")
    cleared = _patch(client, f"/regions/{body['id']}", {"source_reference": None}).json()
    assert (cleared["source_type"], cleared["source_reference"]) == ("MANUAL", None)
    assert client.get(f"/api/v1/regions/{uuid.uuid4()}").status_code == 404


def test_region_validation_and_duplicate_code(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    ru = str(_country_id("RU"))
    _post(
        client, "/regions", {"semantic_type": _ADMIN, "country_id": ru, "code": "dup", "name": "x"}
    )
    duplicate = _post(
        client, "/regions", {"semantic_type": _ADMIN, "country_id": ru, "code": "dup", "name": "y"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "region_code_conflict"
    missing_country = _post(
        client,
        "/regions",
        {"semantic_type": _ADMIN, "country_id": str(uuid.uuid4()), "code": "c", "name": "n"},
    )
    assert missing_country.status_code == 422
    assert missing_country.json()["error"]["code"] == "country_not_found"
    assert (
        _post(
            client,
            "/regions",
            {"semantic_type": _ADMIN, "country_id": ru, "code": " ", "name": "n"},
        ).status_code
        == 422
    )
    assert (
        _post(client, "/regions", {"semantic_type": _ADMIN, "code": "c", "name": "n"}).status_code
        == 422
    )


def test_region_semantic_type_is_required_approved_and_fixed(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    ru = str(_country_id("RU"))
    base = {"country_id": ru, "code": "c", "name": "n"}
    assert _post(client, "/regions", base).status_code == 422
    for other in ("tourist_area", "fstr_region", "", "ADMINISTRATIVE_SUBJECT"):
        response = _post(client, "/regions", {**base, "semantic_type": other})
        assert response.status_code == 422, other
    created = _post(client, "/regions", {**base, "semantic_type": _ADMIN}).json()
    # Not part of ordinary editing.
    _patch(client, f"/regions/{created['id']}", {"semantic_type": "tourist_area"})
    assert client.get(f"/api/v1/regions/{created['id']}").json()["semantic_type"] == _ADMIN


def test_database_rejects_unapproved_region_semantic_type() -> None:
    with session_scope() as session:
        session.add(
            Region(country_id=_country_id("RU"), code="x", name="x", semantic_type="tourist_area")
        )
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
    assert exc_info.value.orig.diag.constraint_name == "ck_regions_semantic_type"  # type: ignore[union-attr]


def test_unused_region_can_move_to_another_country(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    region_id = _region("RU")
    hr = _country_id("HR")
    moved = _patch(client, f"/regions/{region_id}", {"country_id": str(hr)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["country_id"] == str(hr)
    missing = _patch(client, f"/regions/{region_id}", {"country_id": str(uuid.uuid4())})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "country_not_found"


def test_region_used_by_a_trip_cannot_move_to_another_country(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru = _country_id("RU")
    region_id = _region("RU")
    assert _geo(client, world.event_id, _ids(ru, region_id)).status_code == 200
    _set_event_status(world.event_id, "completed")

    moved = _patch(client, f"/regions/{region_id}", {"country_id": str(_country_id("HR"))})
    assert moved.status_code == 409
    assert moved.json()["error"]["code"] == "region_in_use"
    assert client.get(f"/api/v1/regions/{region_id}").json()["country_id"] == str(ru)
    # Re-sending its own Country is not a change.
    same = _patch(client, f"/regions/{region_id}", {"country_id": str(ru)})
    assert same.status_code == 200
    assert _stored_geography(world.event_id) == (ru, region_id)


def test_database_rejects_moving_a_referenced_region() -> None:
    world = _world(with_trip=True)
    ru = _country_id("RU")
    region_id = _region("RU")
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.country_id, trip.region_id = ru, region_id
        session.commit()
    with session_scope() as session:
        region = session.get(Region, region_id)
        assert region is not None
        region.country_id = _country_id("HR")
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
    assert exc_info.value.orig.diag.constraint_name == "fk_trips_region_id_country_id"  # type: ignore[union-attr]


def test_region_deactivate_and_reactivate_keeps_the_entry(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    region_id = _region()
    assert _post(client, f"/regions/{region_id}/deactivate").json()["active"] is False
    assert _post(client, f"/regions/{region_id}/deactivate").json()["active"] is False
    assert [i["id"] for i in client.get("/api/v1/regions?active=false").json()["items"]] == [
        str(region_id)
    ]
    active = _all_regions(client, "&active=true")
    assert len(active) == 89
    assert str(region_id) not in {item["id"] for item in active}
    assert _post(client, f"/regions/{region_id}/activate").json()["active"] is True


def test_catalogs_have_no_delete(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    region_id = _region()
    ru = _country_id("RU")
    assert client.delete(f"/api/v1/regions/{region_id}", headers=_csrf(client)).status_code == 405
    assert client.delete(f"/api/v1/countries/{ru}", headers=_csrf(client)).status_code == 405
    assert client.get(f"/api/v1/regions/{region_id}").status_code == 200
    assert client.get(f"/api/v1/countries/{ru}").status_code == 200


def test_referenced_entries_cannot_be_physically_deleted() -> None:
    world = _world(with_trip=True)
    ru = _country_id("RU")
    region_id = _region("RU")
    unused_region_country = _country_id("HR")
    _region("HR")
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.country_id, trip.region_id = ru, region_id
        session.commit()
    for model, row_id in ((Region, region_id), (Country, ru), (Country, unused_region_country)):
        with session_scope() as session:
            session.delete(session.get(model, row_id))
            with pytest.raises(IntegrityError):
                session.commit()
    assert _stored_geography(world.event_id) == (ru, region_id)


# --- historical semantic immutability (§9) ----------------------------------------


def _use_in_trip(client: TestClient, world: World, country_id, region_id=None) -> None:  # type: ignore[no-untyped-def]
    response = _geo(client, world.event_id, _ids(country_id, region_id))
    assert response.status_code == 200, response.text


def _country_row(country_id: uuid.UUID) -> Country:
    with session_scope() as session:
        country = session.get(Country, country_id)
        assert country is not None
        session.expunge(country)
        return country


def _region_row(region_id: uuid.UUID) -> Region:
    with session_scope() as session:
        region = session.get(Region, region_id)
        assert region is not None
        session.expunge(region)
        return region


def test_unused_country_semantic_fields_are_editable(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    hr = _country_id("HR")
    assert _country_row(hr).first_used_at is None
    renamed = _patch(client, f"/countries/{hr}", {"code": "hr", "name": "Республика Хорватия"})
    assert renamed.status_code == 200, renamed.text
    recoded = _patch(client, f"/countries/{hr}", {"code": "XH"})
    assert recoded.status_code == 200, recoded.text
    assert (recoded.json()["code"], recoded.json()["name"]) == ("XH", "Республика Хорватия")


@pytest.mark.parametrize("body", [{"code": "XR"}, {"name": "Российская Федерация"}])
def test_used_country_code_and_name_are_immutable(client: TestClient, body: dict) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru = _country_id("RU")
    _use_in_trip(client, world, ru)
    assert _country_row(ru).first_used_at is not None

    response = _patch(client, f"/countries/{ru}", body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "country_in_use"
    row = _country_row(ru)
    assert (row.code, row.name) == ("RU", "Россия")
    # Re-sending the stored values is not a change.
    same = _patch(client, f"/countries/{ru}", {"code": "ru", "name": "Россия"})
    assert same.status_code == 200


def test_used_country_stays_immutable_after_the_trip_drops_it(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru = _country_id("RU")
    _use_in_trip(client, world, ru)
    _use_in_trip(client, world, None)
    assert _stored_geography(world.event_id) == (None, None)
    response = _patch(client, f"/countries/{ru}", {"name": "Другое"})
    assert response.status_code == 409


def test_used_country_lifecycle_and_provenance_stay_editable(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru = _country_id("RU")
    _use_in_trip(client, world, ru)

    assert _post(client, f"/countries/{ru}/deactivate").json()["active"] is False
    assert _post(client, f"/countries/{ru}/activate").json()["active"] is True
    provenance = _patch(
        client, f"/countries/{ru}", {"source_type": "MANUAL", "source_reference": "Уточнение"}
    )
    assert provenance.status_code == 200, provenance.text
    assert (provenance.json()["source_type"], provenance.json()["source_reference"]) == (
        "MANUAL",
        "Уточнение",
    )
    assert _stored_geography(world.event_id) == (ru, None)


def test_unused_region_semantic_fields_are_editable(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    region_id = _region("RU", code="old")
    hr = _country_id("HR")
    response = _patch(
        client,
        f"/regions/{region_id}",
        {"code": "new", "name": "Новое имя", "country_id": str(hr)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["code"], body["name"], body["country_id"]) == ("new", "Новое имя", str(hr))


@pytest.mark.parametrize(
    "body_factory",
    [
        lambda: {"code": "other"},
        lambda: {"name": "Другое имя"},
        lambda: {"country_id": str(_country_id("HR"))},
    ],
)
def test_used_region_code_name_and_country_are_immutable(
    client: TestClient,
    body_factory,  # type: ignore[no-untyped-def]
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU", code="fixed")
    _use_in_trip(client, world, ru, region_id)
    assert _region_row(region_id).first_used_at is not None

    response = _patch(client, f"/regions/{region_id}", body_factory())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "region_in_use"
    row = _region_row(region_id)
    assert (row.code, row.name, row.country_id) == ("fixed", "Район", ru)
    same = _patch(
        client, f"/regions/{region_id}", {"code": "fixed", "name": "Район", "country_id": str(ru)}
    )
    assert same.status_code == 200


def test_used_region_stays_immutable_after_the_trip_drops_it(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    _use_in_trip(client, world, ru, region_id)
    _use_in_trip(client, world, ru, None)
    assert _patch(client, f"/regions/{region_id}", {"name": "Другое"}).status_code == 409
    moved = _patch(client, f"/regions/{region_id}", {"country_id": str(_country_id("HR"))})
    assert moved.status_code == 409
    assert _region_row(region_id).country_id == ru


def test_used_region_lifecycle_and_provenance_stay_editable(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    _use_in_trip(client, world, ru, region_id)

    assert _post(client, f"/regions/{region_id}/deactivate").json()["active"] is False
    assert _post(client, f"/regions/{region_id}/activate").json()["active"] is True
    provenance = _patch(
        client, f"/regions/{region_id}", {"source_type": "MANUAL", "source_reference": None}
    )
    assert provenance.status_code == 200, provenance.text
    assert (provenance.json()["source_type"], provenance.json()["source_reference"]) == (
        "MANUAL",
        None,
    )
    assert _stored_geography(world.event_id) == (ru, region_id)


def test_trip_creation_marks_its_geography_used(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    created = _post(client, "/trips", {"event_id": str(world.event_id), **_ids(ru, region_id)})
    assert created.status_code == 201, created.text
    assert _country_row(ru).first_used_at is not None
    assert _region_row(region_id).first_used_at is not None
    # Untouched entries stay unused.
    assert _country_row(_country_id("HR")).first_used_at is None


def test_rejected_assignment_does_not_mark_entries_used(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    hr, ru_region = _country_id("HR"), _region("RU")
    response = _geo(client, world.event_id, _ids(hr, ru_region))
    assert response.status_code == 422
    assert _country_row(hr).first_used_at is None
    assert _region_row(ru_region).first_used_at is None


# --- catalog authorization ------------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["instructor_events", "instructor_unrelated", "member", "guardian"]
)
def test_only_administrator_manages_the_catalogs(client: TestClient, role: str) -> None:
    world = _world()
    region_id = _region()
    ru = _country_id("RU")

    _authenticate_as(getattr(world, role))
    denied = [
        _post(client, "/countries", {"code": "XK", "name": "n"}),
        _patch(client, f"/countries/{ru}", {"name": "hacked"}),
        _post(client, f"/countries/{ru}/deactivate"),
        _post(client, f"/countries/{ru}/activate"),
        _patch(client, f"/countries/{uuid.uuid4()}", {"name": "x"}),
        _post(
            client,
            "/regions",
            {"semantic_type": _ADMIN, "country_id": str(ru), "code": "c", "name": "n"},
        ),
        _patch(client, f"/regions/{region_id}", {"name": "hacked"}),
        _post(client, f"/regions/{region_id}/deactivate"),
        _post(client, f"/regions/{region_id}/activate"),
        _patch(client, f"/regions/{uuid.uuid4()}", {"name": "x"}),
    ]
    assert [response.status_code for response in denied] == [403] * len(denied)
    assert {response.json()["error"]["code"] for response in denied} == {"forbidden"}

    # Reading is allowed to every role holding trip.read.
    assert client.get("/api/v1/countries").status_code == 200
    assert client.get(f"/api/v1/countries/{ru}").json()["name"] == "Россия"
    assert client.get(f"/api/v1/regions/{region_id}").json()["name"] == "Район"
    with session_scope() as session:
        country = session.get(Country, ru)
        assert country is not None
        assert (country.name, country.active) == ("Россия", True)


def test_catalogs_require_authentication_and_csrf(client: TestClient) -> None:
    world = _world()
    assert client.get("/api/v1/countries").status_code == 401
    assert client.get("/api/v1/regions").status_code == 401
    _authenticate_as(world.admin)
    assert client.post("/api/v1/countries", json={"code": "XK", "name": "n"}).status_code == 403
    response = client.post(
        "/api/v1/regions", json={"country_id": str(_country_id("RU")), "code": "c", "name": "n"}
    )
    assert response.status_code == 403


# --- Trip Geography -------------------------------------------------------------------


def test_trip_without_geography(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    created = _post(client, "/trips", {"event_id": str(world.event_id)})
    assert created.status_code == 201
    assert (created.json()["country_id"], created.json()["region_id"]) == (None, None)
    completed = _post(client, f"/events/{world.event_id}/status", {"status": "completed"})
    assert completed.status_code == 200, completed.text
    assert _stored_geography(world.event_id) == (None, None)


def test_trip_created_with_country_and_region(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    created = _post(client, "/trips", {"event_id": str(world.event_id), **_ids(ru, region_id)})
    assert created.status_code == 201, created.text
    assert (created.json()["country_id"], created.json()["region_id"]) == (
        str(ru),
        str(region_id),
    )
    detail = client.get(f"/api/v1/trips/{world.event_id}").json()
    assert (detail["country_id"], detail["region_id"]) == (str(ru), str(region_id))
    assert client.get("/api/v1/trips").json()["items"][0]["region_id"] == str(region_id)


@pytest.mark.parametrize(
    ("body_factory", "code"),
    [
        (lambda: {"country_id": str(uuid.uuid4())}, "country_not_found"),
        (lambda: _ids(_country_id("RU"), uuid.uuid4()), "region_not_found"),
        (lambda: {"region_id": str(_region("RU"))}, "region_country_mismatch"),
        (lambda: _ids(_country_id("HR"), _region("RU")), "region_country_mismatch"),
    ],
)
def test_trip_creation_rejects_invalid_geography(
    client: TestClient,
    body_factory,
    code: str,  # type: ignore[no-untyped-def]
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    response = _post(client, "/trips", {"event_id": str(world.event_id), **body_factory()})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert not _trip_exists(world.event_id)


def test_trip_creation_rejects_inactive_entries(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _set_country_active("HR", False)
    inactive_country = _post(
        client, "/trips", {"event_id": str(world.event_id), "country_id": str(_country_id("HR"))}
    )
    assert inactive_country.json()["error"]["code"] == "country_inactive"
    inactive_region = _post(
        client,
        "/trips",
        {"event_id": str(world.event_id), **_ids(_country_id("RU"), _region(active=False))},
    )
    assert inactive_region.json()["error"]["code"] == "region_inactive"
    assert not _trip_exists(world.event_id)


@pytest.mark.parametrize("status", ["draft", "published", "in_progress"])
def test_geography_set_changed_and_cleared_while_editing_is_open(
    client: TestClient, status: str
) -> None:
    world = _world(status=status, with_trip=True)
    _authenticate_as(world.admin)
    ru, hr = _country_id("RU"), _country_id("HR")
    ru_region, ru_region_2, hr_region = _region("RU"), _region("RU"), _region("HR")

    assert _geo(client, world.event_id, {"country_id": str(ru)}).status_code == 200
    assert _stored_geography(world.event_id) == (ru, None)
    assert _geo(client, world.event_id, {"region_id": str(ru_region)}).status_code == 200
    assert _stored_geography(world.event_id) == (ru, ru_region)
    assert _geo(client, world.event_id, {"region_id": str(ru_region_2)}).status_code == 200
    assert _stored_geography(world.event_id) == (ru, ru_region_2)
    # Changing the Country together with a Region of the new Country.
    both = _geo(client, world.event_id, _ids(hr, hr_region))
    assert both.status_code == 200
    assert (both.json()["country_id"], both.json()["region_id"]) == (str(hr), str(hr_region))
    # Clear the Region, then the Country.
    assert _geo(client, world.event_id, {"region_id": None}).status_code == 200
    assert _stored_geography(world.event_id) == (hr, None)
    assert _geo(client, world.event_id, {"country_id": None}).status_code == 200
    assert _stored_geography(world.event_id) == (None, None)
    # Country without Region is valid; clearing both at once as well.
    _geo(client, world.event_id, _ids(ru, ru_region))
    assert _geo(client, world.event_id, _ids(None, None)).status_code == 200
    assert _stored_geography(world.event_id) == (None, None)


def test_editing_rejects_inconsistent_geography(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, hr = _country_id("RU"), _country_id("HR")
    ru_region, hr_region = _region("RU"), _region("HR")
    _geo(client, world.event_id, _ids(ru, ru_region))

    attempts = {
        "region of another country": {"region_id": str(hr_region)},
        "country change keeps the old region": {"country_id": str(hr)},
        "country cleared, region kept": {"country_id": None},
        "explicit mismatch": _ids(ru, hr_region),
        "region without country": _ids(None, ru_region),
    }
    for label, body in attempts.items():
        response = _geo(client, world.event_id, body)
        assert response.status_code == 422, label
        assert response.json()["error"]["code"] == "region_country_mismatch", label
    assert _stored_geography(world.event_id) == (ru, ru_region)

    missing = [
        _geo(client, world.event_id, {"country_id": str(uuid.uuid4())}),
        _geo(client, world.event_id, {"region_id": str(uuid.uuid4())}),
    ]
    assert [r.json()["error"]["code"] for r in missing] == ["country_not_found", "region_not_found"]
    assert _stored_geography(world.event_id) == (ru, ru_region)


def test_editing_rejects_inactive_entries(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    _set_country_active("HR", False)
    inactive_country = _geo(client, world.event_id, {"country_id": str(_country_id("HR"))})
    assert inactive_country.status_code == 422
    assert inactive_country.json()["error"]["code"] == "country_inactive"
    inactive_region = _geo(
        client, world.event_id, _ids(_country_id("RU"), _region("RU", active=False))
    )
    assert inactive_region.status_code == 422
    assert inactive_region.json()["error"]["code"] == "region_inactive"
    assert _stored_geography(world.event_id) == (None, None)


def test_rejected_geography_does_not_apply_other_fields_of_the_same_body(
    client: TestClient,
) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    tourism_type_id = _tourism_type()
    response = _geo(
        client,
        world.event_id,
        {"tourism_type_id": str(tourism_type_id), "region_id": str(_region("RU"))},
    )
    assert response.status_code == 422
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.tourism_type_id is None


def test_re_sending_the_stored_geography_is_idempotent(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    first = _geo(client, world.event_id, _ids(ru, region_id))
    again = _geo(client, world.event_id, _ids(ru, region_id))
    assert again.status_code == 200
    assert again.json() == first.json()


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
def test_closed_trip_cannot_change_geography(client: TestClient, status: str) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    _geo(client, world.event_id, _ids(ru, region_id))
    _set_event_status(world.event_id, status)

    attempts = [
        _geo(client, world.event_id, {"country_id": str(_country_id("HR"))}),
        _geo(client, world.event_id, {"region_id": None}),
        _geo(client, world.event_id, _ids(None, None)),
        _geo(client, world.event_id, {"region_id": str(uuid.uuid4())}),
    ]
    assert [response.status_code for response in attempts] == [409] * len(attempts)
    assert {response.json()["error"]["code"] for response in attempts} == {"trip_editing_closed"}
    assert _stored_geography(world.event_id) == (ru, region_id)


def test_deactivation_keeps_historical_trip_references(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    _geo(client, world.event_id, _ids(ru, region_id))
    _set_event_status(world.event_id, "completed")

    assert _post(client, f"/regions/{region_id}/deactivate").status_code == 200
    assert _post(client, f"/countries/{ru}/deactivate").status_code == 200
    assert _stored_geography(world.event_id) == (ru, region_id)
    detail = client.get(f"/api/v1/trips/{world.event_id}").json()
    assert (detail["country_id"], detail["region_id"]) == (str(ru), str(region_id))


def test_open_trip_keeps_deactivated_entries_it_already_holds(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)
    ru, region_id = _country_id("RU"), _region("RU")
    _geo(client, world.event_id, _ids(ru, region_id))
    _post(client, f"/regions/{region_id}/deactivate")
    _post(client, f"/countries/{ru}/deactivate")

    # Re-sending the stored pair and editing other facts never drop them.
    assert _geo(client, world.event_id, _ids(ru, region_id)).status_code == 200
    assert _geo(client, world.event_id, {"tourism_type_id": None}).status_code == 200
    assert _stored_geography(world.event_id) == (ru, region_id)
    # Clearing only the Region keeps the (inactive) Country it already holds.
    assert _geo(client, world.event_id, {"region_id": None}).status_code == 200
    assert _stored_geography(world.event_id) == (ru, None)
    # But it cannot be assigned again once removed.
    assert _geo(client, world.event_id, {"country_id": None}).status_code == 200
    reassigned = _geo(client, world.event_id, {"country_id": str(ru)})
    assert reassigned.json()["error"]["code"] == "country_inactive"


def test_geography_is_never_assigned_automatically(client: TestClient) -> None:
    world = _world(status="in_progress")
    _authenticate_as(world.admin)
    _region("RU")
    _post(
        client, "/trips", {"event_id": str(world.event_id), "tourism_type_id": str(_tourism_type())}
    )
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
    assert _stored_geography(world.event_id) == (None, None)


@pytest.mark.parametrize(
    ("country", "region_factory"),
    [
        (None, lambda: _region("RU")),
        ("HR", lambda: _region("RU")),
    ],
)
def test_database_rejects_inconsistent_trip_geography(country, region_factory) -> None:  # type: ignore[no-untyped-def]
    world = _world(with_trip=True)
    region_id = region_factory()
    country_id = _country_id(country) if country else None
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.country_id, trip.region_id = country_id, region_id
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
    assert exc_info.value.orig.diag.constraint_name in {  # type: ignore[union-attr]
        "ck_trips_region_requires_country",
        "fk_trips_region_id_country_id",
    }
    assert _stored_geography(world.event_id) == (None, None)


# --- Trip assignment authorization ----------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "instructor_events"])
def test_trip_managers_in_scope_can_assign(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    ru, region_id = _country_id("RU"), _region("RU")
    _authenticate_as(getattr(world, role))
    response = _geo(client, world.event_id, _ids(ru, region_id))
    assert response.status_code == 200, response.text
    assert _stored_geography(world.event_id) == (ru, region_id)


@pytest.mark.parametrize("role", ["instructor_unrelated", "member", "guardian"])
def test_roles_without_trip_manage_scope_cannot_assign(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    ru = _country_id("RU")
    _authenticate_as(getattr(world, role))
    assert _geo(client, world.event_id, {"country_id": str(ru)}).status_code == 404
    other = _world()
    created = _post(client, "/trips", {"event_id": str(other.event_id), "country_id": str(ru)})
    assert created.status_code == 404
    assert _stored_geography(world.event_id) == (None, None)
    assert not _trip_exists(other.event_id)


@pytest.mark.parametrize("role", ["member", "guardian"])
def test_trip_readers_see_the_geography(client: TestClient, role: str) -> None:
    world = _world(with_trip=True)
    ru, region_id = _country_id("RU"), _region("RU")
    _authenticate_as(world.admin)
    _geo(client, world.event_id, _ids(ru, region_id))
    _authenticate_as(getattr(world, role))
    detail = client.get(f"/api/v1/trips/{world.event_id}").json()
    assert (detail["country_id"], detail["region_id"]) == (str(ru), str(region_id))


# --- migration ------------------------------------------------------------------------


def test_migration_downgrade_and_upgrade_keep_existing_trips(database_url: str) -> None:
    world = _world(with_trip=True)
    tourism_type_id = _tourism_type()
    ru, region_id = _country_id("RU"), _region("RU")
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        trip.tourism_type_id = tourism_type_id
        trip.country_id, trip.region_id = ru, region_id
        session.commit()
    try:
        downgrade = run_alembic("downgrade", "2a3f572d4f32", database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        inspector = sa.inspect(get_engine())
        assert not {"countries", "regions"} & set(inspector.get_table_names())
        columns = {c["name"] for c in inspector.get_columns("trips")}
        assert not {"country_id", "region_id"} & columns
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
    get_engine().dispose()
    with session_scope() as session:
        trip = session.get(Trip, world.event_id)
        assert trip is not None
        assert trip.tourism_type_id == tourism_type_id
        assert (trip.country_id, trip.region_id) == (None, None)
        assert session.execute(select(func.count()).select_from(Country)).scalar_one() == 249
        # The RU seed is re-created; the Region created by this test is gone.
        assert session.execute(select(func.count()).select_from(Region)).scalar_one() == 89
