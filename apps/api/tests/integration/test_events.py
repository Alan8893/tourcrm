"""Real PostgreSQL integration tests for the Issue #36 Event persistence
foundation (Event ORM + migration + DB-level constraints/FK integrity).

These tests exercise the constraints that only PostgreSQL itself can
enforce (CHECK constraints, foreign keys, indexes) — see
tests/unit/test_event_lifecycle.py for the pure-Python lifecycle/data-
integrity domain tests that need no database.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db.events import Event
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.events.lifecycle import InvalidTimezoneError
from app.events.vocabulary import CANONICAL_EVENT_STATUSES, CANONICAL_EVENT_TYPES

from .conftest import requires_postgres

API_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _migrated_schema(database_url: str) -> None:
    result = _run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {
        "name": f"Test Club {uuid.uuid4().hex[:8]}",
        "status": "active",
    }
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {"last_name": "Ivanova", "first_name": "Anna"}
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


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Orienteering basics",
        "description": "Intro session",
        "start_at": _utc(2026, 9, 20, 17, 0),
        "end_at": _utc(2026, 9, 20, 19, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


# --- creation / canonical field persistence --------------------------------


@requires_postgres
def test_creating_a_draft_event_persists_all_fields() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()

        event = _make_event(
            club,
            created_by=user.id,
            updated_by=user.id,
            location_type="address",
            location_name="City Sports Hall",
            location_address="1 Main St",
            location_latitude=55.751244,
            location_longitude=37.618423,
        )
        session.add(event)
        session.commit()

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.club_id == club.id
        assert fetched.event_type == "lesson"
        assert fetched.title == "Orienteering basics"
        assert fetched.description == "Intro session"
        assert fetched.start_at == _utc(2026, 9, 20, 17, 0)
        assert fetched.end_at == _utc(2026, 9, 20, 19, 0)
        assert fetched.timezone == "Europe/Moscow"
        assert fetched.location_type == "address"
        assert fetched.location_name == "City Sports Hall"
        assert fetched.location_address == "1 Main St"
        assert float(fetched.location_latitude) == pytest.approx(55.751244)
        assert float(fetched.location_longitude) == pytest.approx(37.618423)
        assert fetched.status == "draft"
        assert fetched.cancellation_reason is None
        assert fetched.created_by == user.id
        assert fetched.updated_by == user.id
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_created_by_and_updated_by_are_optional() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club)
        session.add(event)
        session.commit()

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.created_by is None
        assert fetched.updated_by is None


@requires_postgres
def test_location_fields_are_optional() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club)
        session.add(event)
        session.commit()

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.location_type is None
        assert fetched.location_name is None
        assert fetched.location_address is None
        assert fetched.location_latitude is None
        assert fetched.location_longitude is None


# --- timezone validation on the actual ORM/persistence path ----------------
#
# There is no PostgreSQL CHECK constraint for IANA timezone validity (it
# would require querying pg_timezone_names, which CHECK constraints
# cannot do) — app.db.events.Event enforces this instead via a
# SQLAlchemy @validates hook that calls app.events.lifecycle.validate_timezone.
# These tests exercise that real persistence path end-to-end, not just the
# pure-function unit tests in tests/unit/test_event_lifecycle.py.


@requires_postgres
def test_invalid_iana_timezone_is_rejected_via_the_orm() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        with pytest.raises(InvalidTimezoneError):
            event = _make_event(club, timezone="Not/AZone")
            session.add(event)
            session.commit()


@requires_postgres
def test_valid_iana_timezone_is_accepted_via_the_orm() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club, timezone="Europe/Moscow")
        session.add(event)
        session.commit()  # must not raise

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.timezone == "Europe/Moscow"


# --- canonical event types --------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("event_type", CANONICAL_EVENT_TYPES)
def test_every_canonical_event_type_can_be_persisted(event_type: str) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club, event_type=event_type)
        session.add(event)
        session.commit()

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.event_type == event_type


@requires_postgres
def test_invalid_event_type_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, event_type="workshop"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_planned_is_not_a_valid_event_type() -> None:
    assert "planned" not in CANONICAL_EVENT_TYPES
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, event_type="planned"))
        with pytest.raises(IntegrityError):
            session.commit()


# --- canonical statuses ------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("status", CANONICAL_EVENT_STATUSES)
def test_every_canonical_status_can_be_persisted(status: str) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        overrides: dict[str, object] = {"status": status}
        if status == "cancelled":
            overrides["cancellation_reason"] = "weather"
        event = _make_event(club, **overrides)
        session.add(event)
        session.commit()

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.status == status


@requires_postgres
def test_planned_is_not_a_valid_status() -> None:
    assert "planned" not in CANONICAL_EVENT_STATUSES
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, status="planned"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_invalid_status_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, status="pending"))
        with pytest.raises(IntegrityError):
            session.commit()


# --- cancellation reason -----------------------------------------------------


@requires_postgres
def test_cancelled_without_a_reason_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, status="cancelled", cancellation_reason=None))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_cancelled_with_a_reason_is_accepted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club, status="cancelled", cancellation_reason="weather")
        session.add(event)
        session.commit()

        fetched = session.execute(select(Event).where(Event.id == event.id)).scalar_one()
        assert fetched.status == "cancelled"
        assert fetched.cancellation_reason == "weather"


@requires_postgres
def test_non_cancelled_event_does_not_require_a_reason() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club, status="draft", cancellation_reason=None)
        session.add(event)
        session.commit()  # must not raise


# --- time range ---------------------------------------------------------


@requires_postgres
def test_end_at_before_start_at_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(
            _make_event(
                club,
                start_at=_utc(2026, 9, 20, 19, 0),
                end_at=_utc(2026, 9, 20, 17, 0),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_end_at_equal_to_start_at_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        same = _utc(2026, 9, 20, 17, 0)
        session.add(_make_event(club, start_at=same, end_at=same))
        with pytest.raises(IntegrityError):
            session.commit()


# --- coordinate consistency ---------------------------------------------


@requires_postgres
def test_latitude_without_longitude_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, location_latitude=55.75, location_longitude=None))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_longitude_without_latitude_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, location_latitude=None, location_longitude=37.62))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_both_coordinates_present_is_accepted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club, location_latitude=55.75, location_longitude=37.62)
        session.add(event)
        session.commit()  # must not raise


# --- FK integrity ------------------------------------------------------


@requires_postgres
def test_club_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        fake_club = Club(id=uuid.uuid4(), name="unused", status="active")
        session.add(_make_event(fake_club))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_created_by_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, created_by=uuid.uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_updated_by_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(_make_event(club, updated_by=uuid.uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_club_with_an_event_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        session.add(_make_event(club))
        session.commit()

        session.delete(club)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_user_who_created_an_event_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()

        session.add(_make_event(club, created_by=user.id))
        session.commit()

        session.delete(user)
        with pytest.raises(IntegrityError):
            session.commit()


# --- UUID identity -------------------------------------------------------


@requires_postgres
def test_two_events_receive_distinct_uuids() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        first = _make_event(club)
        second = _make_event(club)
        session.add_all([first, second])
        session.commit()

        assert first.id != second.id


@requires_postgres
def test_event_identity_is_stable_across_reload() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club)
        session.add(event)
        session.commit()
        original_id = event.id

    with session_scope() as session:
        fetched = session.execute(select(Event).where(Event.id == original_id)).scalar_one()
        assert fetched.id == original_id


# --- migration --------------------------------------------------------


@requires_postgres
def test_events_migration_creates_the_expected_table(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars().all()
    finally:
        engine.dispose()

    assert "events" in tables


@requires_postgres
def test_events_downgrade_then_upgrade_preserves_a_working_schema(database_url: str) -> None:
    downgrade = _run_alembic("downgrade", "-1", database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars().all()
    finally:
        engine.dispose()
    assert "events" not in tables

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        session.add(_make_event(club))
        session.commit()  # must not raise: the re-created table works
