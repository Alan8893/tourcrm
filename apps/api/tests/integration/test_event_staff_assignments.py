"""Real PostgreSQL integration tests for the Issue #48 EventStaffAssignment
persistence foundation (ORM model + migration + DB-level constraints/FK
integrity).

These tests exercise the constraints that only PostgreSQL itself can
enforce (CHECK/exclusion constraints, foreign keys, indexes) at the raw
ORM/DB layer — Club-ownership validation is a separate, application/
service-layer concern (ADR-0022) covered in
tests/integration/test_event_staff_assignments_service.py, not here; the
cross-Club test in this file documents that the raw persistence layer
deliberately does not enforce it (ADR-0022 §3/§8, mirroring
tests/integration/test_groups.py's equivalent GroupInstructorAssignment
test).

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

from app.db.events import Event, EventStaffAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope

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


def _make_club_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "person_id": person.id,
        "membership_type": "regular",
        "status": "active",
        "joined_at": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


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


def _make_assignment(
    event: Event, user: User, **overrides: object
) -> EventStaffAssignment:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "user_id": user.id,
        "role_in_event": "instructor",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return EventStaffAssignment(**defaults)  # type: ignore[arg-type]


# --- model / field persistence ------------------------------------------


@requires_postgres
def test_creating_an_event_staff_assignment_persists_all_fields() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        assignment = _make_assignment(
            event, user, role_in_event="leader", is_primary=True, valid_to=_utc(2025, 1, 1)
        )
        session.add(assignment)
        session.commit()

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.event_id == event.id
        assert fetched.user_id == user.id
        assert fetched.role_in_event == "leader"
        assert fetched.is_primary is True
        assert fetched.valid_from == _utc(2024, 1, 1)
        assert fetched.valid_to == _utc(2025, 1, 1)
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_is_primary_defaults_to_false_when_not_specified() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        assignment = EventStaffAssignment(
            event_id=event.id,
            user_id=user.id,
            role_in_event="assistant",
            valid_from=_utc(2024, 1, 1),
        )
        session.add(assignment)
        session.commit()

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.is_primary is False


@requires_postgres
@pytest.mark.parametrize(
    "role_value", ["instructor", "leader", "assistant", "some-made-up-role", "anything at all"]
)
def test_role_in_event_accepts_any_string_no_enum_exists(role_value: str) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        assignment = _make_assignment(event, user, role_in_event=role_value)
        session.add(assignment)
        session.commit()  # must not raise: no CHECK/enum constraint exists

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.role_in_event == role_value


@requires_postgres
def test_two_assignments_receive_distinct_uuids() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        first = _make_assignment(event, user)
        second = _make_assignment(event, user, valid_from=_utc(2025, 1, 1))
        session.add_all([first, second])
        session.commit()

        assert first.id != second.id


# --- validity interval ----------------------------------------------------


@requires_postgres
def test_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        session.add(
            _make_assignment(
                event, user, valid_from=_utc(2024, 6, 1), valid_to=_utc(2024, 1, 1)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_valid_to_equal_to_valid_from_is_accepted() -> None:
    """Matches the existing project convention (Group, GroupInstructorAssignment,
    GroupMembership all use `valid_to >= valid_from`, i.e. boundary is
    inclusive) — no new semantics invented here."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        same = _utc(2024, 6, 1)
        session.add(_make_assignment(event, user, valid_from=same, valid_to=same))
        session.commit()  # must not raise: boundary is >=, not >


@requires_postgres
def test_valid_to_is_optional_for_an_open_ended_assignment() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        assignment = _make_assignment(event, user)
        session.add(assignment)
        session.commit()  # must not raise

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.valid_to is None


@requires_postgres
def test_closing_an_assignment_period_preserves_the_row() -> None:
    """History is never deleted; a closed period is a fact recorded via
    `valid_to`, not a row removal (ADR-0023 §1)."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        assignment = _make_assignment(event, user)
        session.add(assignment)
        session.commit()

        assignment.valid_to = _utc(2024, 12, 31)
        session.commit()

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.valid_to == _utc(2024, 12, 31)


@requires_postgres
def test_multiple_non_overlapping_assignments_for_same_user_and_event_are_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        first = _make_assignment(
            event, user, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        second = _make_assignment(event, user, valid_from=_utc(2024, 6, 1))
        session.add_all([first, second])
        session.commit()  # must not raise: not primary, no overlap constraint applies


# --- multiple staff -------------------------------------------------------


@requires_postgres
def test_multiple_active_staff_assignments_for_one_event_are_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Anna")
        person_b = _make_person(first_name="Boris")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a = _make_user(person_a)
        user_b = _make_user(person_b)
        event = _make_event(club)
        session.add_all([user_a, user_b, event])
        session.commit()

        assignment_a = _make_assignment(event, user_a, role_in_event="instructor")
        assignment_b = _make_assignment(event, user_b, role_in_event="assistant")
        session.add_all([assignment_a, assignment_b])
        session.commit()  # must not raise: multiple active non-primary assignments allowed


# --- primary invariant -----------------------------------------------------


@requires_postgres
def test_one_active_primary_assignment_is_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        session.add(_make_assignment(event, user, is_primary=True))
        session.commit()  # must not raise


@requires_postgres
def test_second_overlapping_active_primary_assignment_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Anna")
        person_b = _make_person(first_name="Boris")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a = _make_user(person_a)
        user_b = _make_user(person_b)
        event = _make_event(club)
        session.add_all([user_a, user_b, event])
        session.commit()

        session.add(
            _make_assignment(
                event, user_a, is_primary=True, valid_from=_utc(2024, 1, 1)
            )
        )
        session.commit()

        session.add(
            _make_assignment(
                event, user_b, is_primary=True, valid_from=_utc(2024, 6, 1)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_second_primary_assignment_allowed_after_first_ones_validity_ends() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Anna")
        person_b = _make_person(first_name="Boris")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a = _make_user(person_a)
        user_b = _make_user(person_b)
        event = _make_event(club)
        session.add_all([user_a, user_b, event])
        session.commit()

        first_primary = _make_assignment(
            event,
            user_a,
            is_primary=True,
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
        )
        session.add(first_primary)
        session.commit()

        second_primary = _make_assignment(
            event, user_b, is_primary=True, valid_from=_utc(2024, 6, 1)
        )
        session.add(second_primary)
        session.commit()  # must not raise: validity periods do not overlap

        # The historical (closed) primary assignment is preserved, not deleted.
        fetched_first = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == first_primary.id)
        ).scalar_one()
        assert fetched_first.valid_to == _utc(2024, 6, 1)


@requires_postgres
def test_non_primary_assignments_never_conflict_with_a_primary_assignment() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Anna")
        person_b = _make_person(first_name="Boris")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a = _make_user(person_a)
        user_b = _make_user(person_b)
        event = _make_event(club)
        session.add_all([user_a, user_b, event])
        session.commit()

        session.add(_make_assignment(event, user_a, is_primary=True))
        session.commit()
        session.add(_make_assignment(event, user_b, is_primary=False))
        session.commit()  # must not raise: exclusion constraint only applies to is_primary=true


# --- created_by is not a substitute for responsibility ---------------------


@requires_postgres
def test_event_created_by_is_not_used_as_a_substitute_for_staff_responsibility() -> None:
    """Regression guard for ADR-0023 §1 / data-model.md: `Event.created_by`
    must never be treated as an EventStaffAssignment. An Event can be
    created by a User who has no EventStaffAssignment row at all, and
    EventStaffAssignment carries no reference to Event.created_by."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        creator = _make_user(person)
        session.add(creator)
        session.commit()

        event = _make_event(club, created_by=creator.id, updated_by=creator.id)
        session.add(event)
        session.commit()

        # No EventStaffAssignment was ever created for this Event, yet
        # created_by is populated — the two are independent facts.
        assignments = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.event_id == event.id)
        ).scalars().all()
        assert assignments == []
        assert event.created_by == creator.id


# --- foreign key integrity -------------------------------------------------


@requires_postgres
def test_event_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        session.add(user)
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
        session.add(_make_assignment(bogus_event, user))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_user_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()

        bogus_user = User(
            id=uuid.uuid4(),
            person_id=uuid.uuid4(),
            login_identifier="unused@example.com",
            status="active",
        )
        session.add(_make_assignment(event, bogus_user))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_an_event_with_a_staff_assignment_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()
        session.add(_make_assignment(event, user))
        session.commit()

        session.delete(event)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_user_with_a_staff_assignment_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()
        session.add(_make_assignment(event, user))
        session.commit()

        session.delete(user)
        with pytest.raises(IntegrityError):
            session.commit()


# --- cross-Club: not enforced at this raw persistence layer ---------------


@requires_postgres
def test_cross_club_staff_assignment_is_persistable_but_detectable_via_join() -> None:
    """Same documented ADR-0022 §3/§8 boundary as
    tests/integration/test_groups.py's equivalent GroupInstructorAssignment
    test: constructing the ORM row directly (bypassing
    app.events.service.create_event_staff_assignment) does not validate
    that the assigned User has an active ClubMembership in the Event's
    Club — by design, at this layer. See
    tests/integration/test_event_staff_assignments_service.py for the
    actual enforcement.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user_in_club_b = _make_user(person)
        club_membership_in_club_b = _make_club_membership(club_b, person)
        event_in_club_a = _make_event(club_a)
        session.add_all([user_in_club_b, club_membership_in_club_b, event_in_club_a])
        session.commit()

        cross_club_assignment = _make_assignment(event_in_club_a, user_in_club_b)
        session.add(cross_club_assignment)
        session.commit()  # not rejected at this layer, by design — see docstring above

        joined = session.execute(
            select(Event.club_id, ClubMembership.club_id)
            .join(
                EventStaffAssignment,
                EventStaffAssignment.event_id == Event.id,
            )
            .join(
                ClubMembership,
                ClubMembership.person_id == person.id,
            )
            .where(EventStaffAssignment.id == cross_club_assignment.id)
        ).one()
        event_club_id, staff_club_membership_club_id = joined
        assert event_club_id == club_a.id
        assert staff_club_membership_club_id == club_b.id
        assert event_club_id != staff_club_membership_club_id


# --- migration --------------------------------------------------------


@requires_postgres
def test_event_staff_assignment_migration_creates_the_expected_table(
    database_url: str,
) -> None:
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

    assert "event_staff_assignments" in tables


@requires_postgres
def test_event_staff_assignment_downgrade_then_upgrade_preserves_a_working_schema(
    database_url: str,
) -> None:
    # Pinned to the absolute pre-EventStaffAssignment revision rather
    # than a relative "-1", so this test keeps targeting the right
    # migration once a later migration becomes the new head.
    downgrade = _run_alembic("downgrade", "b8792b2e655f", database_url=database_url)
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
    assert "event_staff_assignments" not in tables

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()
        session.add(_make_assignment(event, user))
        session.commit()  # must not raise: the re-created table works
