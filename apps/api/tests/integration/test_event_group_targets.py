"""Real PostgreSQL integration tests for the Issue #49 EventGroupTarget
persistence foundation (ORM model + migration + DB-level constraints/FK
integrity).

These tests exercise the constraints that only PostgreSQL itself can
enforce (CHECK constraints, foreign keys, indexes) at the raw ORM/DB
layer — Club-ownership validation is a separate, application/service-
layer concern (ADR-0022) covered in
tests/integration/test_event_group_targets_service.py, not here; the
cross-Club test in this file documents that the raw persistence layer
deliberately does not enforce it (ADR-0022 §3/§8, mirroring the
equivalent GroupInstructorAssignment/EventStaffAssignment tests).

ADR-0023 §2 defines no overlap-prevention rule for simultaneous
EventGroupTarget rows on the same Event/Group pair (unlike
EventStaffAssignment's primary-assignment invariant) — this file
includes an explicit regression test that overlapping intervals are
accepted, so no such rule is ever silently (re)introduced.

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

from app.db.events import Event, EventGroupTarget
from app.db.groups import Group
from app.db.identity import Club, Person, User
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


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Test Group {uuid.uuid4().hex[:8]}",
        "status": "active",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


def _make_target(event: Event, group: Group, **overrides: object) -> EventGroupTarget:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "group_id": group.id,
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return EventGroupTarget(**defaults)  # type: ignore[arg-type]


# --- model / field persistence ------------------------------------------


@requires_postgres
def test_creating_an_event_group_target_persists_all_fields() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        target = _make_target(event, group, valid_to=_utc(2025, 1, 1))
        session.add(target)
        session.commit()

        fetched = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.id == target.id)
        ).scalar_one()
        assert fetched.event_id == event.id
        assert fetched.group_id == group.id
        assert fetched.valid_from == _utc(2024, 1, 1)
        assert fetched.valid_to == _utc(2025, 1, 1)
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_two_targets_receive_distinct_uuids() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        first = _make_target(event, group)
        second = _make_target(event, group, valid_from=_utc(2025, 1, 1))
        session.add_all([first, second])
        session.commit()

        assert first.id != second.id


# --- multiple relationships (M:N) ------------------------------------------


@requires_postgres
def test_event_can_target_multiple_groups() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([event, group_a, group_b])
        session.commit()

        session.add_all([_make_target(event, group_a), _make_target(event, group_b)])
        session.commit()  # must not raise

        rows = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == event.id)
        ).scalars().all()
        assert {row.group_id for row in rows} == {group_a.id, group_b.id}


@requires_postgres
def test_group_can_be_targeted_by_multiple_events() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event_a = _make_event(club)
        event_b = _make_event(club)
        group = _make_group(club)
        session.add_all([event_a, event_b, group])
        session.commit()

        session.add_all([_make_target(event_a, group), _make_target(event_b, group)])
        session.commit()  # must not raise

        rows = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.group_id == group.id)
        ).scalars().all()
        assert {row.event_id for row in rows} == {event_a.id, event_b.id}


# --- validity interval ----------------------------------------------------


@requires_postgres
def test_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        session.add(
            _make_target(event, group, valid_from=_utc(2024, 6, 1), valid_to=_utc(2024, 1, 1))
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_valid_to_equal_to_valid_from_is_accepted() -> None:
    """Matches the existing project convention (Group,
    GroupInstructorAssignment, GroupMembership, EventStaffAssignment all
    use `valid_to >= valid_from`, i.e. an inclusive boundary) — no new
    semantics invented here."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        same = _utc(2024, 6, 1)
        session.add(_make_target(event, group, valid_from=same, valid_to=same))
        session.commit()  # must not raise: boundary is >=, not >


@requires_postgres
def test_valid_to_is_optional_for_an_open_ended_target() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        target = _make_target(event, group)
        session.add(target)
        session.commit()  # must not raise

        fetched = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.id == target.id)
        ).scalar_one()
        assert fetched.valid_to is None


@requires_postgres
def test_closing_a_target_period_preserves_the_row() -> None:
    """History is never deleted; a closed targeting period is a fact
    recorded via `valid_to`, not a row removal (ADR-0023 §2)."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        target = _make_target(event, group)
        session.add(target)
        session.commit()

        target.valid_to = _utc(2024, 12, 31)
        session.commit()

        fetched = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.id == target.id)
        ).scalar_one()
        assert fetched.valid_to == _utc(2024, 12, 31)


@requires_postgres
def test_multiple_independent_historical_targeting_records_are_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        first = _make_target(
            event, group, valid_from=_utc(2023, 1, 1), valid_to=_utc(2023, 6, 1)
        )
        second = _make_target(
            event, group, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        third = _make_target(event, group, valid_from=_utc(2025, 1, 1))
        session.add_all([first, second, third])
        session.commit()  # must not raise: independent historical rows

        rows = session.execute(
            select(EventGroupTarget).where(
                EventGroupTarget.event_id == event.id, EventGroupTarget.group_id == group.id
            )
        ).scalars().all()
        assert len(rows) == 3


@requires_postgres
def test_overlapping_target_intervals_for_same_event_and_group_are_allowed() -> None:
    """ADR-0023 §2 defines no overlap-prevention rule for
    EventGroupTarget (unlike EventStaffAssignment's primary-assignment
    invariant) — this must not be invented here."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        overlapping_a = _make_target(
            event, group, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 12, 1)
        )
        overlapping_b = _make_target(
            event, group, valid_from=_utc(2024, 6, 1), valid_to=_utc(2025, 1, 1)
        )
        session.add_all([overlapping_a, overlapping_b])
        session.commit()  # must not raise: no overlap constraint exists


# --- EventParticipation separation ------------------------------------------


@requires_postgres
def test_creating_an_event_group_target_has_no_participation_side_effect() -> None:
    """ADR-0023 §2: targeting is audience selection only and must never
    create EventParticipation, registration or attendance.
    EventParticipation is not yet part of the persistence layer at all
    (a later, separate Issue) — confirmed directly here, together with
    the fact that the only observable effect of creating an
    EventGroupTarget is the one new row itself.
    """
    with session_scope() as session:
        tables = session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalars().all()
        assert "event_participations" not in tables

        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        target = _make_target(event, group)
        session.add(target)
        session.commit()

        rows = session.execute(select(EventGroupTarget)).scalars().all()
        assert [row.id for row in rows] == [target.id]


# --- foreign key integrity -------------------------------------------------


@requires_postgres
def test_event_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group = _make_group(club)
        session.add(group)
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
        session.add(_make_target(bogus_event, group))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()

        bogus_group = Group(
            id=uuid.uuid4(),
            club_id=club.id,
            name="unused",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        session.add(_make_target(event, bogus_group))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_an_event_with_a_group_target_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()
        session.add(_make_target(event, group))
        session.commit()

        session.delete(event)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_group_with_an_event_target_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()
        session.add(_make_target(event, group))
        session.commit()

        session.delete(group)
        with pytest.raises(IntegrityError):
            session.commit()


# --- cross-Club: not enforced at this raw persistence layer ---------------


@requires_postgres
def test_cross_club_event_group_target_is_persistable_but_detectable_via_join() -> None:
    """Same documented ADR-0022 §3/§8 boundary as
    tests/integration/test_event_staff_assignments.py's equivalent test:
    constructing the ORM row directly (bypassing
    app.events.service.create_event_group_target) does not validate
    that Event and Group belong to the same Club — by design, at this
    layer. See tests/integration/test_event_group_targets_service.py
    for the actual enforcement. A requester's role/User is irrelevant
    to this relationship's own persistence — no User/Person is even
    involved in constructing this row.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        event_in_a = _make_event(club_a)
        group_in_b = _make_group(club_b)
        session.add_all([event_in_a, group_in_b])
        session.commit()

        cross_club_target = _make_target(event_in_a, group_in_b)
        session.add(cross_club_target)
        session.commit()  # not rejected at this layer, by design — see docstring above

        joined = session.execute(
            select(Event.club_id, Group.club_id)
            .join(EventGroupTarget, EventGroupTarget.event_id == Event.id)
            .join(Group, Group.id == EventGroupTarget.group_id)
            .where(EventGroupTarget.id == cross_club_target.id)
        ).one()
        event_club_id, group_club_id = joined
        assert event_club_id == club_a.id
        assert group_club_id == club_b.id
        assert event_club_id != group_club_id


# --- Event.created_by is not an ownership substitute ------------------------


@requires_postgres
def test_event_created_by_is_irrelevant_to_group_targeting() -> None:
    """Regression guard: EventGroupTarget carries no reference to
    Event.created_by, and an Event's creator having no relationship to
    a Group at all does not prevent (or otherwise affect) targeting at
    this persistence layer."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        creator = _make_user(person)
        session.add(creator)
        session.commit()

        event = _make_event(club, created_by=creator.id, updated_by=creator.id)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        target = _make_target(event, group)
        session.add(target)
        session.commit()  # must not raise

        fetched = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.id == target.id)
        ).scalar_one()
        assert fetched.event_id == event.id
        assert event.created_by == creator.id


# --- migration --------------------------------------------------------


@requires_postgres
def test_event_group_target_migration_creates_the_expected_table(database_url: str) -> None:
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

    assert "event_group_targets" in tables


@requires_postgres
def test_event_group_target_downgrade_then_upgrade_preserves_a_working_schema(
    database_url: str,
) -> None:
    # Pinned to the absolute pre-EventGroupTarget revision rather than a
    # relative "-1", so this test keeps targeting the right migration
    # once a later migration becomes the new head.
    downgrade = _run_alembic("downgrade", "e5859c9b98a6", database_url=database_url)
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
    assert "event_group_targets" not in tables

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()
        session.add(_make_target(event, group))
        session.commit()  # must not raise: the re-created table works
