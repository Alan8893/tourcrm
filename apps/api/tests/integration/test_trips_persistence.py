"""Persistence-level integration tests for the Trip / TripParticipant
tourism-fact foundation (Issue #245, migrations f6527395ba00 /
3c1e9a7d5b20).

Covers the database-enforced invariants independently of the API:
Trip only for an ordinary `Event(type=trip)` (composite FK + CHECK), at
most one Trip per Event, TripParticipant only as the extension of an
EventParticipation of the Trip's own Event (composite FK), at most one
TripParticipant per EventParticipation, RESTRICT (never cascade) on every
reference, no duplicated registration/person columns, and the migration
round trip.

Run against a real PostgreSQL instance, matching the rest of
tests/integration. Self-contained factories, per this codebase's
convention of not importing helpers across test files.
"""

import datetime
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation
from app.db.identity import Club, Person
from app.db.session import get_engine, session_scope
from app.db.trips import Trip, TripParticipant

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.timezone.utc)
_TRIPS_MIGRATION_PARENT = "ceae7e0687cb"


def _club(session) -> Club:  # type: ignore[no-untyped-def]
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _person(session) -> Person:  # type: ignore[no-untyped-def]
    person = Person(last_name="Petrova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _event(session, club: Club, event_type: str = "trip") -> Event:  # type: ignore[no-untyped-def]
    """ADR-0033: an ordinary Event always has its one linked occurrence."""
    event = Event(
        id=uuid.uuid4(),
        club_id=club.id,
        event_type=event_type,
        title="Поход",
        start_at=_START,
        end_at=_START + datetime.timedelta(days=2),
        timezone="UTC",
        status="published",
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
            status="scheduled",
        )
    )
    session.flush()
    return event


def _participation(session, event: Event, person: Person) -> EventParticipation:  # type: ignore[no-untyped-def]
    participation = EventParticipation(
        event_id=event.id, person_id=person.id, registration_status="registered"
    )
    session.add(participation)
    session.flush()
    return participation


def _assert_rejected(session, constraint_name: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError) as exc_info:
        session.flush()
    assert exc_info.value.orig.diag.constraint_name == constraint_name  # type: ignore[union-attr]
    session.rollback()


# --- Trip <-> Event ------------------------------------------------------------


@requires_postgres
def test_trip_extends_a_trip_event_one_to_one() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        session.add(Trip(event_id=event.id))
        session.commit()

        trip = session.get(Trip, event.id)
        assert trip is not None
        assert trip.event_type == "trip"


@requires_postgres
def test_trip_for_an_event_of_another_type_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        event = _event(session, _club(session), event_type="lesson")
        session.commit()
        session.add(Trip(event_id=event.id))
        _assert_rejected(session, "fk_trips_event_id_event_type")


@requires_postgres
def test_trip_event_type_column_only_accepts_trip() -> None:
    with session_scope() as session:
        event = _event(session, _club(session), event_type="lesson")
        session.commit()
        session.add(Trip(event_id=event.id, event_type="lesson"))
        _assert_rejected(session, "ck_trips_event_type_trip")


@requires_postgres
def test_trip_for_a_nonexistent_event_is_rejected() -> None:
    with session_scope() as session:
        session.add(Trip(event_id=uuid.uuid4()))
        _assert_rejected(session, "fk_trips_event_id_event_type")


@requires_postgres
def test_second_trip_for_the_same_event_is_rejected() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        session.add(Trip(event_id=event.id))
        session.commit()
    with pytest.raises(IntegrityError) as exc_info:
        with session_scope() as session:
            session.execute(sa.insert(Trip).values(event_id=event.id, event_type="trip"))
            session.commit()
    assert exc_info.value.orig.diag.constraint_name == "pk_trips"  # type: ignore[union-attr]


@requires_postgres
def test_event_type_of_an_event_with_a_trip_cannot_change() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        session.add(Trip(event_id=event.id))
        session.commit()

        event.event_type = "lesson"
        _assert_rejected(session, "fk_trips_event_id_event_type")


@requires_postgres
def test_event_type_of_a_trip_event_without_a_trip_can_still_change() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        session.commit()
        event.event_type = "lesson"
        session.commit()
        assert session.get(Event, event.id).event_type == "lesson"  # type: ignore[union-attr]


@requires_postgres
def test_event_with_a_trip_cannot_be_deleted() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        session.add(Trip(event_id=event.id))
        session.commit()
        event_id = event.id
    with pytest.raises(IntegrityError) as exc_info:
        with session_scope() as session:
            # Remove the linked occurrence first so only the Trip's own FK
            # can be what blocks the delete.
            session.execute(sa.delete(EventOccurrence).where(EventOccurrence.event_id == event_id))
            session.execute(sa.delete(Event).where(Event.id == event_id))
    assert exc_info.value.orig.diag.constraint_name == "fk_trips_event_id_event_type"  # type: ignore[union-attr]


# --- TripParticipant <-> EventParticipation -------------------------------------


@requires_postgres
def test_trip_participant_extends_a_participation_of_the_trips_event() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        participation = _participation(session, event, _person(session))
        session.add(Trip(event_id=event.id))
        session.flush()
        session.add(
            TripParticipant(
                event_participation_id=participation.id,
                event_id=event.id,
                actual_participation=True,
            )
        )
        session.commit()
        assert session.get(TripParticipant, participation.id) is not None


@requires_postgres
def test_trip_participant_of_another_events_participation_is_rejected() -> None:
    with session_scope() as session:
        club = _club(session)
        trip_event = _event(session, club)
        other_event = _event(session, club)
        foreign_participation = _participation(session, other_event, _person(session))
        session.add_all([Trip(event_id=trip_event.id), Trip(event_id=other_event.id)])
        session.commit()

        session.add(
            TripParticipant(
                event_participation_id=foreign_participation.id,
                event_id=trip_event.id,
                actual_participation=True,
            )
        )
        _assert_rejected(session, "fk_trip_participants_event_participation")


@requires_postgres
def test_trip_participant_without_a_participation_is_rejected() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        session.add(Trip(event_id=event.id))
        session.commit()
        session.add(
            TripParticipant(
                event_participation_id=uuid.uuid4(),
                event_id=event.id,
                actual_participation=True,
            )
        )
        _assert_rejected(session, "fk_trip_participants_event_participation")


@requires_postgres
def test_trip_participant_without_a_trip_is_rejected() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        participation = _participation(session, event, _person(session))
        session.commit()
        session.add(
            TripParticipant(
                event_participation_id=participation.id,
                event_id=event.id,
                actual_participation=True,
            )
        )
        _assert_rejected(session, "fk_trip_participants_event_id")


@requires_postgres
def test_second_trip_participant_for_one_participation_is_rejected() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        participation = _participation(session, event, _person(session))
        session.add(Trip(event_id=event.id))
        session.flush()
        session.add(
            TripParticipant(
                event_participation_id=participation.id,
                event_id=event.id,
                actual_participation=True,
            )
        )
        session.commit()
        participation_id, event_id = participation.id, event.id
    with pytest.raises(IntegrityError) as exc_info:
        with session_scope() as session:
            session.execute(
                sa.insert(TripParticipant).values(
                    event_participation_id=participation_id,
                    event_id=event_id,
                    actual_participation=False,
                )
            )
    assert exc_info.value.orig.diag.constraint_name == "pk_trip_participants"  # type: ignore[union-attr]


@requires_postgres
def test_actual_participation_is_required() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        participation = _participation(session, event, _person(session))
        session.add(Trip(event_id=event.id))
        session.commit()
        participation_id, event_id = participation.id, event.id
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.execute(
                sa.insert(TripParticipant).values(
                    event_participation_id=participation_id, event_id=event_id
                )
            )


@requires_postgres
def test_participation_with_a_trip_participant_cannot_be_deleted_no_cascade() -> None:
    with session_scope() as session:
        event = _event(session, _club(session))
        participation = _participation(session, event, _person(session))
        session.add(Trip(event_id=event.id))
        session.flush()
        session.add(
            TripParticipant(
                event_participation_id=participation.id,
                event_id=event.id,
                actual_participation=True,
            )
        )
        session.commit()
        participation_id = participation.id
    with pytest.raises(IntegrityError) as exc_info:
        with session_scope() as session:
            session.execute(
                sa.delete(EventParticipation).where(EventParticipation.id == participation_id)
            )
    assert (
        exc_info.value.orig.diag.constraint_name  # type: ignore[union-attr]
        == "fk_trip_participants_event_participation"
    )
    with session_scope() as session:
        assert session.get(TripParticipant, participation_id) is not None


@requires_postgres
def test_trip_tables_carry_no_duplicated_registration_or_tourism_columns() -> None:
    inspector = sa.inspect(get_engine())
    assert {c["name"] for c in inspector.get_columns("trips")} == {
        "event_id",
        "event_type",
        "created_at",
        "updated_at",
    }
    assert {c["name"] for c in inspector.get_columns("trip_participants")} == {
        "event_participation_id",
        "event_id",
        "actual_participation",
        "created_at",
        "updated_at",
    }

    trip_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("trips")}
    assert trip_fks["fk_trips_event_id_event_type"]["referred_columns"] == ["id", "event_type"]
    assert trip_fks["fk_trips_event_id_event_type"]["options"] == {
        "onupdate": "RESTRICT",
        "ondelete": "RESTRICT",
    }
    participant_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("trip_participants")}
    assert participant_fks["fk_trip_participants_event_participation"]["referred_table"] == (
        "event_participations"
    )
    assert participant_fks["fk_trip_participants_event_participation"]["referred_columns"] == [
        "event_id",
        "id",
    ]
    assert participant_fks["fk_trip_participants_event_id"]["referred_table"] == "trips"
    for fk in participant_fks.values():
        assert fk["options"] == {"onupdate": "RESTRICT", "ondelete": "RESTRICT"}


# --- Migration -------------------------------------------------------------------


@requires_postgres
def test_trip_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    try:
        downgrade = run_alembic("downgrade", _TRIPS_MIGRATION_PARENT, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        inspector = sa.inspect(get_engine())
        tables = set(inspector.get_table_names())
        assert "trips" not in tables
        assert "trip_participants" not in tables
        assert "uq_events_id_event_type" not in {
            c["name"] for c in inspector.get_unique_constraints("events")
        }
        assert "uq_event_participations_event_id_id" not in {
            c["name"] for c in inspector.get_unique_constraints("event_participations")
        }
        with session_scope() as session:
            trip_grants = session.execute(
                sa.text(
                    "SELECT r.code FROM role_permissions rp "
                    "JOIN roles r ON r.id = rp.role_id "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE p.code LIKE 'trip.%'"
                )
            ).scalars()
            assert set(trip_grants) == {"admin"}
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    inspector = sa.inspect(get_engine())
    assert {"trips", "trip_participants"} <= set(inspector.get_table_names())
