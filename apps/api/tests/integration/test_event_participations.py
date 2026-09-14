"""Real PostgreSQL integration tests for the Issue #51 EventParticipation
persistence foundation (ORM model + migration + DB-level constraints/FK
integrity), including a real-concurrency proof for the Event/Person
uniqueness invariant.

EventParticipation has no cross-Club ownership invariant of its own
(ADR-0023 §4 defines none — unlike EventStaffAssignment/EventGroupTarget)
and no application/service ownership boundary is added for it; every
invariant this Issue defines (Event/Person uniqueness) is expressed as
a single PostgreSQL UNIQUE constraint.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import threading
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.events import Event, EventGroupTarget, EventParticipation
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person
from app.db.session import session_scope

from ._schema_reset import run_alembic
from .conftest import requires_postgres


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
    defaults: dict[str, object] = {
        "last_name": "Ivanova",
        "first_name": f"Anna-{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Orienteering basics",
        "start_at": _utc(2026, 9, 20, 17, 0),
        "end_at": _utc(2026, 9, 20, 19, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_participation(
    event: Event, person: Person, **overrides: object
) -> EventParticipation:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "person_id": person.id,
        "registration_status": "registered",
    }
    defaults.update(overrides)
    return EventParticipation(**defaults)  # type: ignore[arg-type]


# --- model / field persistence ------------------------------------------


@requires_postgres
def test_creating_a_valid_event_participation_persists_all_fields() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person = _make_person()
        session.add_all([event, person])
        session.commit()

        participation = _make_participation(event, person, registration_status="invited")
        session.add(participation)
        session.commit()

        fetched = session.execute(
            select(EventParticipation).where(EventParticipation.id == participation.id)
        ).scalar_one()
        assert fetched.event_id == event.id
        assert fetched.person_id == person.id
        assert fetched.registration_status == "invited"
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_two_participations_receive_distinct_uuids() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person_a = _make_person()
        person_b = _make_person()
        session.add_all([event, person_a, person_b])
        session.commit()

        first = _make_participation(event, person_a)
        second = _make_participation(event, person_b)
        session.add_all([first, second])
        session.commit()

        assert first.id != second.id


# --- registration_status: no CHECK/enum exists (ADR-0020 §4) --------------


@requires_postgres
@pytest.mark.parametrize(
    "status_value",
    ["invited", "registered", "waitlisted", "declined", "removed", "some-made-up-status"],
)
def test_registration_status_accepts_any_string_no_enum_exists(status_value: str) -> None:
    """ADR-0020 §4 calls invited/registered/waitlisted/declined/removed
    "documented reference values", not a ratified vocabulary, and
    explicitly defers the full transition policy — no CHECK constraint
    is introduced, and this parametrization proves it by also accepting
    an arbitrary, non-reference value with equal success."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person = _make_person()
        session.add_all([event, person])
        session.commit()

        participation = _make_participation(event, person, registration_status=status_value)
        session.add(participation)
        session.commit()  # must not raise: no CHECK/enum constraint exists

        fetched = session.execute(
            select(EventParticipation).where(EventParticipation.id == participation.id)
        ).scalar_one()
        assert fetched.registration_status == status_value


# --- Event/Person uniqueness -----------------------------------------------


@requires_postgres
def test_duplicate_event_person_participation_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person = _make_person()
        session.add_all([event, person])
        session.commit()
        session.add(_make_participation(event, person))
        session.commit()

        session.add(_make_participation(event, person, registration_status="waitlisted"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_same_event_different_persons_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person_a = _make_person()
        person_b = _make_person()
        session.add_all([event, person_a, person_b])
        session.commit()

        session.add_all(
            [_make_participation(event, person_a), _make_participation(event, person_b)]
        )
        session.commit()  # must not raise

        rows = session.execute(
            select(EventParticipation).where(EventParticipation.event_id == event.id)
        ).scalars().all()
        assert {row.person_id for row in rows} == {person_a.id, person_b.id}


@requires_postgres
def test_different_events_same_person_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event_a = _make_event(club)
        event_b = _make_event(club)
        person = _make_person()
        session.add_all([event_a, event_b, person])
        session.commit()

        session.add_all(
            [_make_participation(event_a, person), _make_participation(event_b, person)]
        )
        session.commit()  # must not raise

        rows = session.execute(
            select(EventParticipation).where(EventParticipation.person_id == person.id)
        ).scalars().all()
        assert {row.event_id for row in rows} == {event_a.id, event_b.id}


# --- foreign key integrity -------------------------------------------------


@requires_postgres
def test_event_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()

        bogus_event = Event(
            id=uuid.uuid4(),
            club_id=club.id,
            event_type="lesson",
            title="unused",
            start_at=_utc(2026, 1, 1),
            end_at=_utc(2026, 1, 2),
            timezone="Europe/Moscow",
            status="draft",
        )
        session.add(_make_participation(bogus_event, person))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_person_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()

        bogus_person = Person(id=uuid.uuid4(), last_name="Nobody", first_name="Nobody")
        session.add(_make_participation(event, bogus_person))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_an_event_with_a_participation_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person = _make_person()
        session.add_all([event, person])
        session.commit()
        session.add(_make_participation(event, person))
        session.commit()

        session.delete(event)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_person_with_a_participation_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person = _make_person()
        session.add_all([event, person])
        session.commit()
        session.add(_make_participation(event, person))
        session.commit()

        session.delete(person)
        with pytest.raises(IntegrityError):
            session.commit()


# --- separation from other Event relationships -----------------------------


@requires_postgres
def test_other_event_relationships_do_not_create_a_participation() -> None:
    """ADR-0023 §2/§4: EventGroupTarget/GroupMembership/GuardianRelationship
    are all explicitly documented as NOT creating EventParticipation.
    There is no code path that could do so (none of those models or
    their services import EventParticipation, and this module imports
    none of them) — this test confirms the absence of that side effect
    directly, rather than standing in for a nonexistent regression."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = Group(
            club_id=club.id,
            name=f"Test Group {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        guardian = _make_person()
        child = _make_person()
        member_person = _make_person()
        session.add_all([event, group, guardian, child, member_person])
        session.commit()

        club_membership = ClubMembership(
            club_id=club.id,
            person_id=member_person.id,
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        session.add(club_membership)
        session.commit()

        session.add_all(
            [
                EventGroupTarget(event_id=event.id, group_id=group.id, valid_from=_utc(2024, 1, 1)),
                GroupMembership(
                    group_id=group.id,
                    club_membership_id=club_membership.id,
                    valid_from=_utc(2024, 1, 1),
                    membership_status="active",
                ),
                GuardianRelationship(
                    guardian_person_id=guardian.id,
                    child_person_id=child.id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_utc(2024, 1, 1),
                ),
            ]
        )
        session.commit()

        participations = session.execute(select(EventParticipation)).scalars().all()
        assert participations == []


# --- concurrency: Event/Person uniqueness ----------------------------------


@requires_postgres
def test_concurrent_duplicate_event_person_participation_leaves_exactly_one() -> None:
    """Real-concurrency proof for the UNIQUE(event_id, person_id)
    constraint backing ADR-0023 §4's "must prevent duplicate
    participation for the same Event and Person": two transactions
    racing to create a participation for the same Event/Person must
    never both succeed.

    Catches OperationalError alongside IntegrityError defensively (the
    same lesson learned writing the GuardianRelationship GiST-exclusion
    concurrency tests in Issue #50): a plain single-index UNIQUE
    violation is a one-directional wait (the second inserter waits for
    the first to finish, then re-checks), not the symmetric two-sided
    wait that can deadlock, but this is verified empirically below
    across many trials rather than assumed.
    """
    trial_count = 30
    for trial in range(trial_count):
        with session_scope() as setup:
            club = _make_club()
            session_person_a = _make_person()
            setup.add_all([club, session_person_a])
            setup.commit()
            event = _make_event(club)
            setup.add(event)
            setup.commit()
            event_id, person_id = event.id, session_person_a.id

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, str] = {}

        def attempt(name: str, registration_status: str) -> None:
            with session_scope() as session:
                event_ref = Event(id=event_id)
                person_ref = Person(id=person_id)
                start_gate.wait()
                try:
                    session.add(
                        _make_participation(
                            event_ref, person_ref, registration_status=registration_status
                        )
                    )
                    session.commit()
                    result[name] = "succeeded"
                except (IntegrityError, OperationalError):
                    session.rollback()
                    result[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a", "registered"))
        thread_b = threading.Thread(target=attempt, args=("b", "waitlisted"))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        with session_scope() as check:
            participation_count = check.execute(
                text(
                    "SELECT count(*) FROM event_participations "
                    "WHERE event_id = :e AND person_id = :p"
                ),
                {"e": str(event_id), "p": str(person_id)},
            ).scalar_one()

        assert participation_count == 1, (
            f"trial {trial}: expected exactly one surviving participation, "
            f"got {participation_count}; outcomes={result}"
        )
        assert sorted(result.values()) == ["rejected", "succeeded"], (
            f"trial {trial}: expected exactly one success and one rejection, "
            f"got outcomes={result}"
        )


# --- migration --------------------------------------------------------


@requires_postgres
def test_event_participation_migration_creates_the_expected_table(database_url: str) -> None:
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

    assert "event_participations" in tables


@requires_postgres
def test_event_participation_downgrade_then_upgrade_preserves_a_working_schema(
    database_url: str,
) -> None:
    # Pinned to the absolute pre-EventParticipation revision rather
    # than a relative "-1", so this test keeps targeting the right
    # migration once a later migration becomes the new head.
    downgrade = run_alembic("downgrade", "d0023b757548", database_url=database_url)
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
    assert "event_participations" not in tables

    upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        person = _make_person()
        session.add_all([event, person])
        session.commit()
        session.add(_make_participation(event, person))
        session.commit()  # must not raise: the re-created table works
