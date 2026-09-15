"""Real PostgreSQL integration tests for the Issue #85 / TH-0082
EventOccurrence authorization-relationship materialization/propagation
(ADR-0029, ADR-0030): atomic materialization from the governing
EventSeries version, idempotency, protected occurrence-level overrides,
and `this_and_following` propagation to already-materialized future
occurrences.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import threading
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from sqlalchemy import select

from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceParticipant,
    EventOccurrenceStaffAssignment,
    SeriesStaffAssignment,
)
from app.db.groups import Group
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.events.materialization import materialize_occurrences
from app.events.occurrence_relationships import (
    create_occurrence_staff_assignment,
    end_occurrence_staff_assignment,
    propagate_occurrence_relationships,
)
from app.events.series_relationships import (
    create_series_group_target,
    create_series_participant,
    create_series_staff_assignment,
    end_series_staff_assignment,
)
from app.events.series_service import create_successor_version

from .conftest import requires_postgres

_START = datetime(2026, 1, 5, 18, 0, tzinfo=dt_timezone.utc)


def _make_club_and_user(session) -> tuple[uuid.UUID, uuid.UUID]:
    club = Club(name=f"Club-{uuid.uuid4().hex[:8]}", status="active")
    person = Person(last_name="A", first_name="B")
    session.add_all([club, person])
    session.commit()
    user = User(
        person_id=person.id, login_identifier=f"u-{uuid.uuid4().hex[:8]}@x.example", status="active"
    )
    session.add(user)
    session.commit()
    return club.id, user.id


def _make_person_user(session, *, club_id: uuid.UUID | None = None) -> tuple[Person, User]:
    person = Person(last_name="P", first_name=f"X-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.commit()
    user = User(
        person_id=person.id,
        login_identifier=f"pu-{uuid.uuid4().hex[:8]}@x.example",
        status="active",
    )
    session.add(user)
    session.commit()
    if club_id is not None:
        session.add(
            ClubMembership(
                club_id=club_id,
                person_id=person.id,
                membership_type="student",
                status="active",
                joined_at=_START - timedelta(days=365),
            )
        )
        session.commit()
    return person, user


def _make_series_v1(session, *, club_id: uuid.UUID, user_id: uuid.UUID, **overrides) -> EventSeries:
    series_id = uuid.uuid4()
    defaults = dict(
        id=series_id,
        root_series_id=series_id,
        supersedes_series_id=None,
        version=1,
        club_id=club_id,
        name="Weekly lesson",
        event_type="lesson",
        series_start_at=_START,
        duration_minutes=90,
        recurrence_rule="FREQ=WEEKLY",
        timezone="Europe/Moscow",
        status="active",
        created_by=user_id,
        updated_by=user_id,
    )
    defaults.update(overrides)
    series = EventSeries(**defaults)
    session.add(series)
    session.commit()
    return series


# --- Materialization: staff/group/participant copied atomically ------------


@requires_postgres
def test_materialization_copies_effective_staff_group_and_participant_relationships() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        group = Group(
            club_id=club_id, name=f"G-{uuid.uuid4().hex[:6]}", status="active", valid_from=_START
        )
        s.add(group)
        s.commit()
        staff_person, staff_user = _make_person_user(s, club_id=club_id)
        participant_person, _ = _make_person_user(s, club_id=club_id)

        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            is_primary=True,
            actor_user_id=user_id,
        )
        create_series_group_target(
            s,
            event_series_id=series.id,
            group_id=group.id,
            valid_from=_START,
            actor_user_id=user_id,
        )
        create_series_participant(
            s,
            event_series_id=series.id,
            person_id=participant_person.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=user_id,
        )

        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        assert len(created) == 1
        occ = created[0]

        staff_rows = (
            s.execute(
                select(EventOccurrenceStaffAssignment).where(
                    EventOccurrenceStaffAssignment.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )
        group_rows = (
            s.execute(
                select(EventOccurrenceGroupTarget).where(
                    EventOccurrenceGroupTarget.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )
        participant_rows = (
            s.execute(
                select(EventOccurrenceParticipant).where(
                    EventOccurrenceParticipant.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )

        assert len(staff_rows) == 1
        assert staff_rows[0].user_id == staff_user.id
        assert staff_rows[0].is_override is False
        assert len(group_rows) == 1
        assert group_rows[0].group_id == group.id
        assert group_rows[0].is_override is False
        assert len(participant_rows) == 1
        assert participant_rows[0].person_id == participant_person.id
        assert participant_rows[0].is_override is False


@requires_postgres
def test_materialization_does_not_copy_a_definition_not_yet_effective() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, staff_user = _make_person_user(s, club_id=club_id)

        # Effective only starting well after the first occurrence's start.
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START + timedelta(days=30),
            actor_user_id=user_id,
        )

        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        assert len(created) == 1
        occ = created[0]
        staff_rows = (
            s.execute(
                select(EventOccurrenceStaffAssignment).where(
                    EventOccurrenceStaffAssignment.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )
        assert staff_rows == []


@requires_postgres
def test_materialization_does_not_copy_an_expired_definition() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, staff_user = _make_person_user(s, club_id=club_id)

        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START - timedelta(days=60),
            valid_to=_START - timedelta(days=30),
            actor_user_id=user_id,
        )

        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        assert len(created) == 1
        occ = created[0]
        staff_rows = (
            s.execute(
                select(EventOccurrenceStaffAssignment).where(
                    EventOccurrenceStaffAssignment.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )
        assert staff_rows == []


@requires_postgres
def test_repeated_materialization_does_not_duplicate_relationship_rows() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, staff_user = _make_person_user(s, club_id=club_id)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )

        materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=7))
        materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=7))

        occurrence_ids = (
            s.execute(select(EventOccurrence.id).where(EventOccurrence.series_id == series.id))
            .scalars()
            .all()
        )
        staff_rows = (
            s.execute(
                select(EventOccurrenceStaffAssignment).where(
                    EventOccurrenceStaffAssignment.occurrence_id.in_(occurrence_ids)
                )
            )
            .scalars()
            .all()
        )
        assert len(occurrence_ids) == 2  # days 0 and 7
        assert len(staff_rows) == 2  # one per occurrence, no duplicates


@requires_postgres
def test_concurrent_materialization_does_not_duplicate_relationship_rows() -> None:
    trial_count = 8
    for _trial in range(trial_count):
        with session_scope() as setup:
            club_id, user_id = _make_club_and_user(setup)
            series = _make_series_v1(setup, club_id=club_id, user_id=user_id)
            _, staff_user = _make_person_user(setup, club_id=club_id)
            create_series_staff_assignment(
                setup,
                event_series_id=series.id,
                user_id=staff_user.id,
                role_in_event="instructor",
                valid_from=_START,
                actor_user_id=user_id,
            )
            series_id = series.id

        start_gate = threading.Barrier(2, timeout=10)

        def attempt() -> None:
            with session_scope() as session:
                series_row = session.get(EventSeries, series_id)
                start_gate.wait()
                materialize_occurrences(
                    session, series=series_row, horizon_end=_START + timedelta(days=5)
                )

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        with session_scope() as check:
            occurrence_ids = (
                check.execute(
                    select(EventOccurrence.id).where(EventOccurrence.series_id == series_id)
                )
                .scalars()
                .all()
            )
            distinct_occurrence_ids = set(occurrence_ids)
            assert len(occurrence_ids) == len(distinct_occurrence_ids)

            staff_rows = (
                check.execute(
                    select(EventOccurrenceStaffAssignment).where(
                        EventOccurrenceStaffAssignment.occurrence_id.in_(occurrence_ids)
                    )
                )
                .scalars()
                .all()
            )
            # exactly one staff row per occurrence — never duplicated by
            # the two racing materializers.
            assert len(staff_rows) == len(occurrence_ids)


# --- Protected occurrence-level overrides ------------------------------------


@requires_postgres
def test_explicit_occurrence_override_is_protected_from_series_propagation() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, series_staff_user = _make_person_user(s, club_id=club_id)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=series_staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        occ = created[0]

        _, override_user = _make_person_user(s, club_id=club_id)
        create_occurrence_staff_assignment(
            s,
            occurrence_id=occ.id,
            user_id=override_user.id,
            role_in_event="substitute",
            valid_from=_START,
            actor_user_id=user_id,
        )

        # This and following with a *different* staff assignment on the
        # successor — the override must not be touched.
        _, successor_staff_user = _make_person_user(s, club_id=club_id)
        successor, _rebound = create_successor_version(
            s,
            source_series_id=series.id,
            boundary_occurrence_id=occ.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        create_series_staff_assignment(
            s,
            event_series_id=successor.id,
            user_id=successor_staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )

        staff_rows = (
            s.execute(
                select(EventOccurrenceStaffAssignment).where(
                    EventOccurrenceStaffAssignment.occurrence_id == occ.id,
                    EventOccurrenceStaffAssignment.valid_to.is_(None),
                )
            )
            .scalars()
            .all()
        )
        # Only the protected override remains active — the original
        # series-sourced row was never re-synced onto this occurrence
        # because a protected override exists for this category.
        active_user_ids = {row.user_id for row in staff_rows}
        assert override_user.id in active_user_ids
        assert successor_staff_user.id not in active_user_ids


@requires_postgres
def test_ending_an_occurrence_staff_assignment_marks_it_protected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, staff_user = _make_person_user(s, club_id=club_id)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        occ = created[0]

        row = s.execute(
            select(EventOccurrenceStaffAssignment).where(
                EventOccurrenceStaffAssignment.occurrence_id == occ.id
            )
        ).scalar_one()
        assert row.is_override is False

        end_occurrence_staff_assignment(s, assignment=row, actor_user_id=user_id)
        s.refresh(row)
        assert row.is_override is True
        assert row.valid_to is not None


# --- Propagation for unprotected categories on this_and_following ----------


@requires_postgres
def test_this_and_following_propagates_unprotected_relationships_to_successor_definitions() -> None:
    """Realistic "change staff for this and following" flow, each step
    its own committed transaction (matching how separate API calls would
    actually be issued): this_and_following snapshot-copies the
    predecessor's staff definition onto the successor and immediately
    propagates it to the rebound occurrence; the caller then ends that
    copied definition and creates a new one on the successor; a follow-up
    propagation call re-syncs the (still-unprotected) occurrence category
    to reflect only the successor's now-current definition.
    """
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, old_staff_user = _make_person_user(s, club_id=club_id)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=old_staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        occ_id = created[0].id
        series_id = series.id

    with session_scope() as s2:
        _, new_staff_user = _make_person_user(s2, club_id=club_id)
        successor, rebound = create_successor_version(
            s2,
            source_series_id=series_id,
            boundary_occurrence_id=occ_id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        successor_id = successor.id
        new_staff_user_id = new_staff_user.id
        assert occ_id in {o.id for o in rebound}

        # Change staff on the successor: end the copied predecessor
        # definition, add the new one.
        copied_def = s2.execute(
            select(SeriesStaffAssignment).where(
                SeriesStaffAssignment.event_series_id == successor_id
            )
        ).scalar_one()
        end_series_staff_assignment(s2, assignment=copied_def, actor_user_id=user_id)
        create_series_staff_assignment(
            s2,
            event_series_id=successor_id,
            user_id=new_staff_user_id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )

    with session_scope() as s3:
        occ = s3.get(EventOccurrence, occ_id)
        successor_row = s3.get(EventSeries, successor_id)
        propagate_occurrence_relationships(s3, occurrence=occ, series=successor_row)
        s3.commit()

    with session_scope() as verify:
        active_staff = (
            verify.execute(
                select(EventOccurrenceStaffAssignment).where(
                    EventOccurrenceStaffAssignment.occurrence_id == occ_id,
                    EventOccurrenceStaffAssignment.valid_to.is_(None),
                )
            )
            .scalars()
            .all()
        )
        assert len(active_staff) == 1
        assert active_staff[0].user_id == new_staff_user_id


@requires_postgres
def test_stable_occurrence_id_and_no_duplicate_relationship_identity_across_versioning() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _, staff_user = _make_person_user(s, club_id=club_id)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=1))
        occ = created[0]
        occ_id_before = occ.id

        successor, rebound = create_successor_version(
            s,
            source_series_id=series.id,
            boundary_occurrence_id=occ.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )

        assert rebound[0].id == occ_id_before  # stable ID across rebind

        with session_scope() as verify:
            all_occurrences = (
                verify.execute(select(EventOccurrence.id).where(EventOccurrence.club_id == club_id))
                .scalars()
                .all()
            )
            assert sorted(all_occurrences) == sorted({occ_id_before})
