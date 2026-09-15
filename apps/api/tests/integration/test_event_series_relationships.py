"""Real PostgreSQL integration tests for the Issue #85 / TH-0082
EventSeries relationship-source persistence and service layer (ADR-0030):
`SeriesStaffAssignment`/`SeriesGroupTarget`/`SeriesParticipant`
persistence, cross-Club integrity, effectivity, primary-assignment
concurrency, snapshot-copy on versioning, and audit.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import threading
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.audit import AuditLog
from app.db.event_recurrence import EventSeries
from app.db.event_recurrence_relationships import (
    SeriesStaffAssignment,
)
from app.db.groups import Group
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.events.series_relationships import (
    InvalidSeriesRelationshipTransitionError,
    SeriesGroupTargetClubMismatchError,
    SeriesStaffAssignmentPrimaryConflictError,
    SeriesStaffClubMembershipMissingError,
    create_series_group_target,
    create_series_participant,
    create_series_staff_assignment,
    end_series_group_target,
    end_series_participant,
    end_series_staff_assignment,
    snapshot_copy_series_relationships,
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
        person_id=person.id,
        login_identifier=f"u-{uuid.uuid4().hex[:8]}@x.example",
        status="active",
    )
    session.add(user)
    session.commit()
    return club.id, user.id


def _make_club_membership(session, *, club_id: uuid.UUID, person_id: uuid.UUID, status="active"):
    membership = ClubMembership(
        club_id=club_id,
        person_id=person_id,
        membership_type="student",
        status=status,
        joined_at=_START - timedelta(days=365),
    )
    session.add(membership)
    session.commit()
    return membership


def _make_group(session, *, club_id: uuid.UUID) -> Group:
    group = Group(
        club_id=club_id, name=f"Group-{uuid.uuid4().hex[:8]}", status="active", valid_from=_START
    )
    session.add(group)
    session.commit()
    return group


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


# --- SeriesStaffAssignment persistence/cross-club/effectivity --------------


@requires_postgres
def test_create_series_staff_assignment_persists_all_fields() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"staff-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)

        assignment = create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            is_primary=True,
            actor_user_id=user_id,
        )
        assert assignment.event_series_id == series.id
        assert assignment.user_id == staff_user.id
        assert assignment.role_in_event == "instructor"
        assert assignment.is_primary is True
        assert assignment.valid_to is None


@requires_postgres
def test_create_series_staff_assignment_without_club_membership_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        outsider_person = Person(last_name="Out", first_name="Sider")
        s.add(outsider_person)
        s.commit()
        outsider = User(
            person_id=outsider_person.id,
            login_identifier=f"out-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(outsider)
        s.commit()
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        series_id = series.id

        with pytest.raises(SeriesStaffClubMembershipMissingError):
            create_series_staff_assignment(
                s,
                event_series_id=series_id,
                user_id=outsider.id,
                role_in_event="instructor",
                valid_from=_START,
                actor_user_id=user_id,
            )

    with session_scope() as verify:
        rows = (
            verify.execute(
                select(SeriesStaffAssignment).where(
                    SeriesStaffAssignment.event_series_id == series_id
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_second_overlapping_active_primary_series_staff_assignment_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)

        person_a = Person(last_name="A", first_name="Staff")
        person_b = Person(last_name="B", first_name="Staff")
        s.add_all([person_a, person_b])
        s.commit()
        user_a = User(
            person_id=person_a.id,
            login_identifier=f"a-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        user_b = User(
            person_id=person_b.id,
            login_identifier=f"b-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add_all([user_a, user_b])
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person_a.id)
        _make_club_membership(s, club_id=club_id, person_id=person_b.id)

        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=user_a.id,
            role_in_event="instructor",
            valid_from=_START,
            is_primary=True,
            actor_user_id=user_id,
        )
        with pytest.raises(SeriesStaffAssignmentPrimaryConflictError):
            create_series_staff_assignment(
                s,
                event_series_id=series.id,
                user_id=user_b.id,
                role_in_event="instructor",
                valid_from=_START,
                is_primary=True,
                actor_user_id=user_id,
            )


@requires_postgres
def test_second_primary_series_staff_assignment_allowed_after_first_ends() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        person_a = Person(last_name="A", first_name="Staff")
        s.add(person_a)
        s.commit()
        user_a = User(
            person_id=person_a.id,
            login_identifier=f"a-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(user_a)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person_a.id)

        first = create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=user_a.id,
            role_in_event="instructor",
            valid_from=_START,
            valid_to=_START + timedelta(days=30),
            is_primary=True,
            actor_user_id=user_id,
        )
        second = create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=user_a.id,
            role_in_event="instructor",
            valid_from=_START + timedelta(days=30),
            is_primary=True,
            actor_user_id=user_id,
        )
        assert first.id != second.id


@requires_postgres
def test_end_series_staff_assignment_sets_valid_to_and_is_idempotent_against_reend() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"s-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)

        assignment = create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )
        ended = end_series_staff_assignment(s, assignment=assignment, actor_user_id=user_id)
        assert ended.valid_to is not None

        with pytest.raises(InvalidSeriesRelationshipTransitionError):
            end_series_staff_assignment(s, assignment=assignment, actor_user_id=user_id)


# --- SeriesGroupTarget cross-club/effectivity -------------------------------


@requires_postgres
def test_create_series_group_target_persists_and_requires_matching_club() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        other_club_id, _ = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        same_club_group = _make_group(s, club_id=club_id)
        other_club_group = _make_group(s, club_id=other_club_id)

        target = create_series_group_target(
            s,
            event_series_id=series.id,
            group_id=same_club_group.id,
            valid_from=_START,
            actor_user_id=user_id,
        )
        assert target.group_id == same_club_group.id

        with pytest.raises(SeriesGroupTargetClubMismatchError):
            create_series_group_target(
                s,
                event_series_id=series.id,
                group_id=other_club_group.id,
                valid_from=_START,
                actor_user_id=user_id,
            )


@requires_postgres
def test_end_series_group_target() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        group = _make_group(s, club_id=club_id)

        target = create_series_group_target(
            s,
            event_series_id=series.id,
            group_id=group.id,
            valid_from=_START,
            actor_user_id=user_id,
        )
        ended = end_series_group_target(s, target=target, actor_user_id=user_id)
        assert ended.valid_to is not None


# --- SeriesParticipant -------------------------------------------------------


@requires_postgres
def test_create_and_end_series_participant() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        participant_person = Person(last_name="Kid", first_name="Y")
        s.add(participant_person)
        s.commit()

        participant = create_series_participant(
            s,
            event_series_id=series.id,
            person_id=participant_person.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=user_id,
        )
        assert participant.person_id == participant_person.id
        ended = end_series_participant(s, participant=participant, actor_user_id=user_id)
        assert ended.valid_to is not None


# --- Effectivity: [valid_from, valid_to) boundary ---------------------------


@requires_postgres
def test_effectivity_boundary_valid_to_is_exclusive() -> None:
    """A relationship's own [valid_from, valid_to) is checked at
    materialization time against the occurrence start instant — verified
    directly here via the raw interval query semantics used throughout
    this domain (valid_from <= instant AND (valid_to IS NULL OR instant <
    valid_to))."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"e-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)

        boundary = _START + timedelta(days=10)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            valid_to=boundary,
            actor_user_id=user_id,
        )

        from app.events.occurrence_relationships import _effective_at

        just_before = (
            s.execute(
                select(SeriesStaffAssignment.id).where(
                    SeriesStaffAssignment.event_series_id == series.id,
                    _effective_at(
                        SeriesStaffAssignment.valid_from,
                        SeriesStaffAssignment.valid_to,
                        boundary - timedelta(seconds=1),
                    ),
                )
            )
            .scalars()
            .all()
        )
        at_boundary = (
            s.execute(
                select(SeriesStaffAssignment.id).where(
                    SeriesStaffAssignment.event_series_id == series.id,
                    _effective_at(
                        SeriesStaffAssignment.valid_from, SeriesStaffAssignment.valid_to, boundary
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert len(just_before) == 1
        assert at_boundary == []  # valid_to is exclusive


@requires_postgres
def test_future_relationship_does_not_apply_before_valid_from() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"f-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)

        future_start = _START + timedelta(days=30)
        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=future_start,
            actor_user_id=user_id,
        )

        from app.events.occurrence_relationships import _effective_at

        before = (
            s.execute(
                select(SeriesStaffAssignment.id).where(
                    SeriesStaffAssignment.event_series_id == series.id,
                    _effective_at(
                        SeriesStaffAssignment.valid_from,
                        SeriesStaffAssignment.valid_to,
                        future_start - timedelta(seconds=1),
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert before == []


@requires_postgres
def test_open_ended_relationship_applies_indefinitely() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"o-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)

        create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            valid_to=None,
            actor_user_id=user_id,
        )

        from app.events.occurrence_relationships import _effective_at

        far_future = (
            s.execute(
                select(SeriesStaffAssignment.id).where(
                    SeriesStaffAssignment.event_series_id == series.id,
                    _effective_at(
                        SeriesStaffAssignment.valid_from,
                        SeriesStaffAssignment.valid_to,
                        _START + timedelta(days=3650),
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert len(far_future) == 1


# --- Snapshot copy on versioning ---------------------------------------------


@requires_postgres
def test_snapshot_copy_series_relationships_creates_independent_rows() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        v2_id = uuid.uuid4()
        v2 = EventSeries(
            id=v2_id,
            root_series_id=v1.root_series_id,
            supersedes_series_id=v1.id,
            version=2,
            club_id=club_id,
            name="v2",
            event_type="lesson",
            series_start_at=_START,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            status="active",
        )
        s.add(v2)
        s.commit()

        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"sc-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)

        create_series_staff_assignment(
            s,
            event_series_id=v1.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )

        snapshot_copy_series_relationships(s, source_series_id=v1.id, target_series_id=v2.id)

        v1_rows = (
            s.execute(
                select(SeriesStaffAssignment).where(SeriesStaffAssignment.event_series_id == v1.id)
            )
            .scalars()
            .all()
        )
        v2_rows = (
            s.execute(
                select(SeriesStaffAssignment).where(SeriesStaffAssignment.event_series_id == v2_id)
            )
            .scalars()
            .all()
        )
        assert len(v1_rows) == 1
        assert len(v2_rows) == 1
        assert v1_rows[0].id != v2_rows[0].id  # independent row, not shared
        assert v2_rows[0].user_id == staff_user.id
        assert v2_rows[0].role_in_event == "instructor"

        # Ending the copy must never touch the predecessor's own row.
        end_series_staff_assignment(s, assignment=v2_rows[0], actor_user_id=user_id)
        s.refresh(v1_rows[0])
        assert v1_rows[0].valid_to is None


@requires_postgres
def test_this_and_following_snapshot_copies_relationships_onto_successor() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        from tests.integration.test_event_recurrence import _make_occurrence

        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)

        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"tf-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)
        create_series_staff_assignment(
            s,
            event_series_id=v1.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )

        v2, _rebound = create_successor_version(
            s,
            source_series_id=v1.id,
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

        v2_staff = (
            s.execute(
                select(SeriesStaffAssignment).where(SeriesStaffAssignment.event_series_id == v2.id)
            )
            .scalars()
            .all()
        )
        assert len(v2_staff) == 1
        assert v2_staff[0].user_id == staff_user.id


@requires_postgres
def test_audit_actions_recorded_for_series_relationship_mutations() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        person = Person(last_name="Staff", first_name="X")
        s.add(person)
        s.commit()
        staff_user = User(
            person_id=person.id,
            login_identifier=f"au-{uuid.uuid4().hex[:6]}@x.example",
            status="active",
        )
        s.add(staff_user)
        s.commit()
        _make_club_membership(s, club_id=club_id, person_id=person.id)

        assignment = create_series_staff_assignment(
            s,
            event_series_id=series.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=user_id,
        )
        end_series_staff_assignment(s, assignment=assignment, actor_user_id=user_id)

        actions = {
            row.action
            for row in s.execute(
                select(AuditLog).where(AuditLog.resource_id == assignment.id)
            ).scalars()
        }
        assert actions == {
            "event_series_staff_assignment.created",
            "event_series_staff_assignment.ended",
        }


# --- Concurrency: two concurrent primary staff-assignment creations --------


@requires_postgres
def test_concurrent_primary_series_staff_assignment_creation_yields_exactly_one_winner() -> None:
    trial_count = 10
    for _trial in range(trial_count):
        with session_scope() as setup:
            club_id, user_id = _make_club_and_user(setup)
            series = _make_series_v1(setup, club_id=club_id, user_id=user_id)
            person_a = Person(last_name="A", first_name="Staff")
            person_b = Person(last_name="B", first_name="Staff")
            setup.add_all([person_a, person_b])
            setup.commit()
            user_a = User(
                person_id=person_a.id,
                login_identifier=f"ca-{uuid.uuid4().hex[:6]}@x.example",
                status="active",
            )
            user_b = User(
                person_id=person_b.id,
                login_identifier=f"cb-{uuid.uuid4().hex[:6]}@x.example",
                status="active",
            )
            setup.add_all([user_a, user_b])
            setup.commit()
            _make_club_membership(setup, club_id=club_id, person_id=person_a.id)
            _make_club_membership(setup, club_id=club_id, person_id=person_b.id)
            series_id, user_a_id, user_b_id = series.id, user_a.id, user_b.id

        start_gate = threading.Barrier(2, timeout=10)
        results: dict[str, str] = {}

        def attempt(name: str, staff_user_id: uuid.UUID) -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_series_staff_assignment(
                        session,
                        event_series_id=series_id,
                        user_id=staff_user_id,
                        role_in_event="instructor",
                        valid_from=_START,
                        is_primary=True,
                        actor_user_id=user_id,
                    )
                    results[name] = "succeeded"
                except SeriesStaffAssignmentPrimaryConflictError:
                    results[name] = "rejected"
                except IntegrityError:
                    results[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a", user_a_id))
        thread_b = threading.Thread(target=attempt, args=("b", user_b_id))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        assert sorted(results.values()) == ["rejected", "succeeded"], results
