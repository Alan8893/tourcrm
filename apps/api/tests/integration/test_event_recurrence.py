"""Real PostgreSQL integration tests for the Issue #79 EventSeries/
EventOccurrence/EventOccurrenceException recurrence domain (ADR-0028):
versioning (app.events.versioning/app.events.series_service), lifecycle
transitions, exceptions, audit, authorization, and real concurrency.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_role_assignments_service.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v

Materialization itself (app.events.rrule.expand_occurrences ->
EventOccurrence rows via app.events.materialization) is covered
separately in tests/integration/test_event_recurrence_materialization.py.
`EventSeries.duration_minutes` is a required, positive-integer field of
the Series version snapshot; materialization derives each occurrence's
`ends_at = starts_at + duration_minutes` from the governing Series
version (ADR-0028 §2 duration amendment). Every test below still
constructs EventOccurrence rows directly with explicit starts_at/ends_at
(rather than via materialization) since this file's own focus is
versioning/lifecycle/exception/audit/authorization invariants that don't
depend on how an occurrence's timestamps were produced.
"""

import threading
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.vocabulary import CANONICAL_AUDIT_ACTIONS
from app.authorization.service import Authorizer
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventOccurrenceException, EventSeries
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.events.materialization import materialize_occurrences
from app.events.series_authorization import (
    build_occurrence_resource_context,
    build_series_resource_context,
)
from app.events.series_lifecycle import (
    CancelledOccurrenceCannotBeBoundaryError,
    InvalidBoundaryOccurrenceStatusError,
    InvalidOccurrenceStatusTransitionError,
    InvalidSeriesStatusTransitionError,
    OccurrenceCancellationReasonRequiredError,
    OccurrenceNotEligibleForRescheduleError,
    UnknownOverrideFieldError,
)
from app.events.series_service import (
    OccurrenceCancellationRequiresExceptionError,
    create_series,
    create_successor_version,
    set_occurrence_exception,
    transition_occurrence_status,
    transition_series_status,
)
from app.events.versioning import (
    BoundaryOccurrenceNotInCurrentVersionError,
    StaleSeriesVersionError,
    get_current_series_version,
)

from .conftest import requires_postgres

_START = datetime(2026, 1, 5, 18, 0, tzinfo=dt_timezone.utc)


def _make_club_and_user(session, *, club_name: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    club = Club(name=club_name or f"Club-{uuid.uuid4().hex[:8]}", status="active")
    person = Person(last_name="A", first_name="B")
    session.add_all([club, person])
    session.commit()
    user = User(
        person_id=person.id, login_identifier=f"u-{uuid.uuid4().hex[:8]}@x.example", status="active"
    )
    session.add(user)
    session.commit()
    return club.id, user.id


def _grant_all_scope(session, *, user_id: uuid.UUID, permission_code: str, club_id=None) -> None:
    role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role", is_system=False)
    permission = session.execute(
        select(Permission).where(Permission.code == permission_code)
    ).scalar_one()
    session.add(role)
    session.commit()
    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.add(
        UserRoleAssignment(
            user_id=user_id,
            role_id=role.id,
            club_id=club_id,
            scope_type="all",
            valid_from=datetime.now(dt_timezone.utc),
        )
    )
    session.commit()


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


def _make_occurrence(
    session, *, series_id: uuid.UUID, club_id: uuid.UUID, anchor: datetime, **overrides
) -> EventOccurrence:
    defaults = dict(
        series_id=series_id,
        club_id=club_id,
        name="Occurrence",
        event_type="lesson",
        recurrence_anchor_at=anchor,
        starts_at=anchor,
        ends_at=anchor + timedelta(hours=2),
        timezone="Europe/Moscow",
        status="scheduled",
    )
    defaults.update(overrides)
    occ = EventOccurrence(**defaults)
    session.add(occ)
    session.commit()
    return occ


# --- Versioning: v1/v2/v3 chain, root/version/predecessor invariants -------


@requires_postgres
def test_v1_is_self_rooted_with_no_predecessor() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = create_series(
            s,
            club_id=club_id,
            name="Weekly lesson",
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
        assert series.version == 1
        assert series.root_series_id == series.id
        assert series.supersedes_series_id is None


@requires_postgres
def test_v1_v2_v3_chain_has_correct_root_version_and_predecessor() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ1 = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)

        v2, rebound1 = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=occ1.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=30),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY;INTERVAL=2",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        occ2 = _make_occurrence(
            s, series_id=v2.id, club_id=club_id, anchor=_START + timedelta(days=44)
        )
        v3, rebound2 = create_successor_version(
            s,
            source_series_id=v2.id,
            boundary_occurrence_id=occ2.id,
            name="v3",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=90),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=MONTHLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )

        assert v2.root_series_id == v1.root_series_id == v1.id
        assert v3.root_series_id == v1.id
        assert v2.version == 2
        assert v3.version == 3
        assert v2.supersedes_series_id == v1.id
        assert v3.supersedes_series_id == v2.id


@requires_postgres
def test_current_version_is_the_terminal_node() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ1 = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        v2, _ = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=occ1.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=30),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        current = get_current_series_version(s, root_series_id=v1.root_series_id)
        assert current is not None
        assert current.id == v2.id


@requires_postgres
def test_past_occurrence_remains_on_its_historical_version_and_is_immutable_in_series_id() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        past_occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="completed"
        )
        boundary_occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=7)
        )
        v2, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary_occ.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=7),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY;INTERVAL=2",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        s.refresh(past_occ)
        assert past_occ.series_id == v1.id  # untouched
        assert [r.id for r in rebound] == [boundary_occ.id]
        assert rebound[0].series_id == v2.id


@requires_postgres
def test_boundary_occurrence_rebinding_keeps_the_same_id_and_creates_no_new_row() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        occ_id = occ.id
        before_count = s.execute(select(EventOccurrence)).scalars().all()

        v2, rebound = create_successor_version(
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
        after_count = s.execute(select(EventOccurrence)).scalars().all()
        assert [r.id for r in rebound] == [occ_id]
        assert len(after_count) == len(before_count)  # no new occurrence created


@requires_postgres
def test_cancelled_boundary_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s,
            series_id=v1.id,
            club_id=club_id,
            anchor=_START,
            status="cancelled",
            cancellation_reason="weather",
        )
        with pytest.raises(CancelledOccurrenceCannotBeBoundaryError):
            create_successor_version(
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


@requires_postgres
def test_in_progress_boundary_is_rejected_with_no_mutation() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="in_progress"
        )
        occ_id, v1_id = occ.id, v1.id
        series_count_before = len(s.execute(select(EventSeries)).scalars().all())

        with pytest.raises(InvalidBoundaryOccurrenceStatusError):
            create_successor_version(
                s,
                source_series_id=v1_id,
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
        # In-memory object must not carry a rejected rebind either.
        assert occ.series_id == v1_id
        assert len(s.execute(select(EventSeries)).scalars().all()) == series_count_before

    # Fresh read after the transaction ended: the rollback actually
    # reached the database — no successor row, boundary not rebound.
    with session_scope() as verify:
        successors = (
            verify.execute(select(EventSeries).where(EventSeries.supersedes_series_id == v1_id))
            .scalars()
            .all()
        )
        assert successors == []
        fresh_occ = verify.get(EventOccurrence, occ_id)
        assert fresh_occ is not None
        assert fresh_occ.series_id == v1_id
        assert fresh_occ.status == "in_progress"


@requires_postgres
def test_completed_boundary_is_rejected_with_no_mutation() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="completed"
        )
        occ_id, v1_id = occ.id, v1.id
        series_count_before = len(s.execute(select(EventSeries)).scalars().all())

        with pytest.raises(InvalidBoundaryOccurrenceStatusError):
            create_successor_version(
                s,
                source_series_id=v1_id,
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
        assert occ.series_id == v1_id
        assert len(s.execute(select(EventSeries)).scalars().all()) == series_count_before

    with session_scope() as verify:
        successors = (
            verify.execute(select(EventSeries).where(EventSeries.supersedes_series_id == v1_id))
            .scalars()
            .all()
        )
        assert successors == []
        fresh_occ = verify.get(EventOccurrence, occ_id)
        assert fresh_occ is not None
        assert fresh_occ.series_id == v1_id
        assert fresh_occ.status == "completed"


@requires_postgres
def test_cancelled_boundary_is_rejected_with_no_mutation() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s,
            series_id=v1.id,
            club_id=club_id,
            anchor=_START,
            status="cancelled",
            cancellation_reason="weather",
        )
        occ_id, v1_id = occ.id, v1.id
        series_count_before = len(s.execute(select(EventSeries)).scalars().all())

        with pytest.raises(CancelledOccurrenceCannotBeBoundaryError):
            create_successor_version(
                s,
                source_series_id=v1_id,
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
        assert occ.series_id == v1_id
        assert len(s.execute(select(EventSeries)).scalars().all()) == series_count_before

    with session_scope() as verify:
        successors = (
            verify.execute(select(EventSeries).where(EventSeries.supersedes_series_id == v1_id))
            .scalars()
            .all()
        )
        assert successors == []
        fresh_occ = verify.get(EventOccurrence, occ_id)
        assert fresh_occ is not None
        assert fresh_occ.series_id == v1_id
        assert fresh_occ.status == "cancelled"


@requires_postgres
def test_scheduled_boundary_is_accepted() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="scheduled"
        )
        v1_id, occ_id = v1.id, occ.id

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1_id,
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
        assert successor.version == 2
        assert [r.id for r in rebound] == [occ_id]
        assert rebound[0].series_id == successor.id


@requires_postgres
def test_stale_source_version_is_rejected_with_no_mutation() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ1 = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=occ1.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=7),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        series_count_before = len(s.execute(select(EventSeries)).scalars().all())
        with pytest.raises(StaleSeriesVersionError):
            create_successor_version(
                s,
                source_series_id=v1.id,  # stale - v2 already exists
                boundary_occurrence_id=occ1.id,
                name="v3-stale",
                description=None,
                event_type="lesson",
                series_start_at=_START + timedelta(days=14),
                series_end_at=None,
                occurrence_limit=None,
                duration_minutes=90,
                recurrence_rule="FREQ=WEEKLY",
                timezone="Europe/Moscow",
                actor_user_id=user_id,
            )
        series_count_after = len(s.execute(select(EventSeries)).scalars().all())
        assert series_count_after == series_count_before


@requires_postgres
def test_boundary_from_a_different_series_version_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        other_root = uuid.uuid4()
        other_series = EventSeries(
            id=other_root,
            root_series_id=other_root,
            version=1,
            club_id=club_id,
            name="Other",
            event_type="lesson",
            series_start_at=_START,
            duration_minutes=90,
            recurrence_rule="FREQ=DAILY",
            timezone="Europe/Moscow",
            status="active",
        )
        s.add(other_series)
        s.commit()
        foreign_occ = _make_occurrence(s, series_id=other_series.id, club_id=club_id, anchor=_START)
        with pytest.raises(BoundaryOccurrenceNotInCurrentVersionError):
            create_successor_version(
                s,
                source_series_id=v1.id,
                boundary_occurrence_id=foreign_occ.id,
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


@requires_postgres
def test_db_rejects_a_second_successor_for_the_same_predecessor_even_bypassing_the_service() -> (
    None
):
    """Defense in depth: the DB-level `uq_event_series_one_successor_per_predecessor`
    constraint independently rejects a duplicate successor even for a
    direct ORM insert that bypasses app.events.versioning entirely."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        successor_a = EventSeries(
            root_series_id=v1.root_series_id,
            supersedes_series_id=v1.id,
            version=2,
            club_id=club_id,
            name="a",
            event_type="lesson",
            series_start_at=_START,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            status="active",
        )
        s.add(successor_a)
        s.commit()

        successor_b = EventSeries(
            root_series_id=v1.root_series_id,
            supersedes_series_id=v1.id,
            version=3,
            club_id=club_id,
            name="b-rival",
            event_type="lesson",
            series_start_at=_START,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            status="active",
        )
        s.add(successor_b)
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


# --- Concurrency: two concurrent successor-creation attempts ---------------


@requires_postgres
def test_concurrent_successor_creation_yields_exactly_one_winner() -> None:
    trial_count = 15
    for _trial in range(trial_count):
        with session_scope() as setup:
            club_id, user_id = _make_club_and_user(setup)
            v1 = _make_series_v1(setup, club_id=club_id, user_id=user_id)
            occ = _make_occurrence(setup, series_id=v1.id, club_id=club_id, anchor=_START)
            v1_id, occ_id = v1.id, occ.id

        start_gate = threading.Barrier(2, timeout=10)
        results: dict[str, str] = {}

        def attempt(name: str) -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_successor_version(
                        session,
                        source_series_id=v1_id,
                        boundary_occurrence_id=occ_id,
                        name=f"v2-{name}",
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
                    results[name] = "succeeded"
                except StaleSeriesVersionError:
                    results[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a",))
        thread_b = threading.Thread(target=attempt, args=("b",))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        with session_scope() as check:
            successor_count = (
                check.execute(
                    select(EventSeries).where(EventSeries.supersedes_series_id == v1_id)
                )
                .scalars()
                .all()
            )
        assert len(successor_count) == 1, f"trial: outcomes={results}"
        assert sorted(results.values()) == ["rejected", "succeeded"]


# --- Series lifecycle ---------------------------------------------------


@requires_postgres
def test_series_active_paused_active_roundtrip() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        transition_series_status(s, series=v1, new_status="paused", actor_user_id=user_id)
        assert v1.status == "paused"
        transition_series_status(s, series=v1, new_status="active", actor_user_id=user_id)
        assert v1.status == "active"


@requires_postgres
def test_series_active_cancelled_archived() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        transition_series_status(s, series=v1, new_status="cancelled", actor_user_id=user_id)
        transition_series_status(s, series=v1, new_status="archived", actor_user_id=user_id)
        assert v1.status == "archived"


@requires_postgres
def test_series_invalid_transition_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, status="paused")
        with pytest.raises(InvalidSeriesStatusTransitionError):
            transition_series_status(s, series=v1, new_status="cancelled", actor_user_id=user_id)


@requires_postgres
def test_pausing_a_series_does_not_touch_existing_occurrences() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        transition_series_status(s, series=v1, new_status="paused", actor_user_id=user_id)
        s.refresh(occ)
        assert occ.status == "scheduled"


# --- Occurrence lifecycle -------------------------------------------------


@requires_postgres
def test_occurrence_scheduled_in_progress_completed() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        transition_occurrence_status(
            s, occurrence=occ, new_status="in_progress", actor_user_id=user_id
        )
        assert occ.status == "in_progress"
        transition_occurrence_status(
            s, occurrence=occ, new_status="completed", actor_user_id=user_id
        )
        assert occ.status == "completed"


@requires_postgres
def test_occurrence_invalid_transition_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        with pytest.raises(InvalidOccurrenceStatusTransitionError):
            transition_occurrence_status(
                s, occurrence=occ, new_status="completed", actor_user_id=user_id
            )


@requires_postgres
def test_occurrence_cancellation_via_direct_transition_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        with pytest.raises(OccurrenceCancellationRequiresExceptionError):
            transition_occurrence_status(
                s, occurrence=occ, new_status="cancelled", actor_user_id=user_id
            )


# --- Exceptions -----------------------------------------------------------


@requires_postgres
def test_create_reschedule_exception_keeps_status_scheduled_and_updates_effective_time() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        new_start = _START + timedelta(hours=3)
        new_end = new_start + timedelta(hours=2)
        exc = set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=new_start,
            effective_end_at=new_end,
            actor_user_id=user_id,
        )
        assert occ.status == "scheduled"
        assert occ.starts_at == new_start
        assert occ.ends_at == new_end
        assert exc.exception_type == "rescheduled"
        assert exc.original_start_at == _START


@requires_postgres
def test_modify_reschedule_exception_updates_in_place_not_duplicated() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=_START + timedelta(hours=1),
            effective_end_at=_START + timedelta(hours=3),
            actor_user_id=user_id,
        )
        second_start = _START + timedelta(hours=5)
        second_end = second_start + timedelta(hours=2)
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=second_start,
            effective_end_at=second_end,
            actor_user_id=user_id,
        )
        rows = (
            s.execute(
                select(EventOccurrenceException).where(
                    EventOccurrenceException.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].effective_start_at == second_start


@requires_postgres
def test_cancel_exception_transitions_occurrence_and_requires_a_reason() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        with pytest.raises(OccurrenceCancellationReasonRequiredError):
            set_occurrence_exception(
                s, occurrence=occ, exception_type="cancelled", actor_user_id=user_id
            )

        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="cancelled",
            cancellation_reason="Instructor unavailable",
            actor_user_id=user_id,
        )
        assert occ.status == "cancelled"
        assert occ.cancellation_reason == "Instructor unavailable"


@requires_postgres
def test_exception_occurrence_id_is_unique_at_the_db_level() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        s.add(
            EventOccurrenceException(
                occurrence_id=occ.id,
                exception_type="rescheduled",
                original_start_at=_START,
                created_by=user_id,
            )
        )
        s.commit()
        s.add(
            EventOccurrenceException(
                occurrence_id=occ.id,
                exception_type="rescheduled",
                original_start_at=_START,
                created_by=user_id,
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


@requires_postgres
def test_override_unknown_field_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        with pytest.raises(UnknownOverrideFieldError):
            set_occurrence_exception(
                s,
                occurrence=occ,
                exception_type="rescheduled",
                overrides={"location_name": "New hall"},
                actor_user_id=user_id,
            )


@requires_postgres
def test_override_invalid_event_type_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        with pytest.raises(Exception):  # InvalidEventTypeError (app.events.lifecycle)
            set_occurrence_exception(
                s,
                occurrence=occ,
                exception_type="rescheduled",
                overrides={"event_type": "not-a-real-type"},
                actor_user_id=user_id,
            )


@requires_postgres
def test_allow_listed_override_applies_to_the_occurrence_snapshot() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            overrides={"name": "Special guest lesson"},
            actor_user_id=user_id,
        )
        assert occ.name == "Special guest lesson"


@requires_postgres
def test_occurrence_id_remains_stable_through_reschedule_and_cancel() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        original_id = occ.id
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=_START + timedelta(hours=1),
            effective_end_at=_START + timedelta(hours=3),
            actor_user_id=user_id,
        )
        assert occ.id == original_id
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="cancelled",
            cancellation_reason="x",
            actor_user_id=user_id,
        )
        assert occ.id == original_id


# --- Terminal-state protection for reschedule (review blocker #1) ---------


@requires_postgres
def test_reschedule_of_a_completed_occurrence_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="completed"
        )
        original_starts_at = occ.starts_at
        original_ends_at = occ.ends_at

        with pytest.raises(OccurrenceNotEligibleForRescheduleError):
            set_occurrence_exception(
                s,
                occurrence=occ,
                exception_type="rescheduled",
                effective_start_at=_START + timedelta(hours=3),
                actor_user_id=user_id,
            )

        # In-memory object must not carry the rejected mutation either.
        assert occ.status == "completed"
        assert occ.starts_at == original_starts_at
        assert occ.ends_at == original_ends_at

    # And nothing was persisted: a fresh read after the transaction ended
    # confirms the rollback actually reached the database.
    with session_scope() as verify:
        fresh = verify.get(EventOccurrence, occ.id)
        assert fresh is not None
        assert fresh.status == "completed"
        assert fresh.starts_at == original_starts_at
        assert fresh.ends_at == original_ends_at
        exceptions = (
            verify.execute(
                select(EventOccurrenceException).where(
                    EventOccurrenceException.occurrence_id == occ.id
                )
            )
            .scalars()
            .all()
        )
        assert exceptions == []


@requires_postgres
def test_reschedule_of_a_cancelled_occurrence_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(
            s,
            series_id=v1.id,
            club_id=club_id,
            anchor=_START,
            status="cancelled",
            cancellation_reason="weather",
        )
        original_starts_at = occ.starts_at

        with pytest.raises(OccurrenceNotEligibleForRescheduleError):
            set_occurrence_exception(
                s,
                occurrence=occ,
                exception_type="rescheduled",
                effective_start_at=_START + timedelta(hours=3),
                actor_user_id=user_id,
            )

        assert occ.status == "cancelled"
        assert occ.starts_at == original_starts_at

    with session_scope() as verify:
        fresh = verify.get(EventOccurrence, occ.id)
        assert fresh is not None
        assert fresh.status == "cancelled"
        assert fresh.starts_at == original_starts_at


@requires_postgres
def test_occurrence_with_existing_cancelled_exception_cannot_be_rescheduled() -> None:
    """scheduled -> cancelled exception -> attempted reschedule must be
    rejected, and the occurrence must never end up
    status="cancelled"/exception_type="rescheduled" (the exact
    inconsistent state this guard exists to prevent)."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)

        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="cancelled",
            cancellation_reason="Instructor unavailable",
            actor_user_id=user_id,
        )
        assert occ.status == "cancelled"

        with pytest.raises(OccurrenceNotEligibleForRescheduleError):
            set_occurrence_exception(
                s,
                occurrence=occ,
                exception_type="rescheduled",
                effective_start_at=_START + timedelta(hours=3),
                actor_user_id=user_id,
            )

        # Never status="cancelled" with a "rescheduled" exception on top.
        assert occ.status == "cancelled"

    with session_scope() as verify:
        fresh = verify.get(EventOccurrence, occ.id)
        assert fresh is not None
        assert fresh.status == "cancelled"
        exception = verify.execute(
            select(EventOccurrenceException).where(
                EventOccurrenceException.occurrence_id == occ.id
            )
        ).scalar_one()
        assert exception.exception_type == "cancelled"


# --- Reschedule duration derivation (review blocker #2) --------------------


@requires_postgres
def test_reschedule_without_effective_end_at_derives_it_from_series_duration() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=90)
        old_start = _START  # 18:00
        old_end = old_start + timedelta(minutes=90)  # 19:30
        occ = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, starts_at=old_start, ends_at=old_end
        )

        new_start = old_start.replace(hour=20, minute=0)  # 20:00, same day
        exc = set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=new_start,
            actor_user_id=user_id,
        )

        expected_end = new_start + timedelta(minutes=90)  # 21:30
        assert occ.starts_at == new_start
        assert occ.ends_at == expected_end
        assert occ.ends_at != old_end  # the stale old ends_at must not survive
        assert exc.effective_start_at == new_start
        assert exc.effective_end_at == expected_end

    with session_scope() as verify:
        fresh = verify.get(EventOccurrence, occ.id)
        assert fresh is not None
        assert fresh.starts_at == new_start
        assert fresh.ends_at == expected_end


@requires_postgres
def test_reschedule_with_explicit_effective_end_at_is_used_as_is() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=90)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)

        new_start = _START + timedelta(hours=3)
        explicit_end = new_start + timedelta(hours=5)  # deliberately NOT +90min
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=new_start,
            effective_end_at=explicit_end,
            actor_user_id=user_id,
        )
        assert occ.ends_at == explicit_end


@requires_postgres
def test_reschedule_with_effective_end_at_before_start_is_rejected() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=90)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        original_ends_at = occ.ends_at

        new_start = _START + timedelta(hours=3)
        invalid_end = new_start - timedelta(minutes=10)
        with pytest.raises(Exception):  # InvalidTimeRangeError (app.events.lifecycle)
            set_occurrence_exception(
                s,
                occurrence=occ,
                exception_type="rescheduled",
                effective_start_at=new_start,
                effective_end_at=invalid_end,
                actor_user_id=user_id,
            )
        assert occ.ends_at == original_ends_at


# --- Audit ------------------------------------------------------------


@requires_postgres
def test_series_created_emits_correct_audit_action() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = create_series(
            s,
            club_id=club_id,
            name="Weekly lesson",
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
        row = s.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "event_series", AuditLog.resource_id == series.id
            )
        ).scalar_one()
        assert row.action == "event_series.created"
        assert row.outcome == "success"
        assert row.actor_user_id == user_id


@requires_postgres
def test_this_and_following_emits_both_version_created_and_series_rebound() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        v2, rebound = create_successor_version(
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
        actions = {
            row.action
            for row in s.execute(
                select(AuditLog).where(
                    AuditLog.resource_id.in_([v2.id, *[r.id for r in rebound]]),
                )
            ).scalars()
        }
        assert actions == {"event_series.version_created", "event_occurrence.series_rebound"}


@requires_postgres
def test_exception_created_then_changed_emit_distinct_actions() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=_START + timedelta(hours=1),
            effective_end_at=_START + timedelta(hours=3),
            actor_user_id=user_id,
        )
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            effective_start_at=_START + timedelta(hours=2),
            effective_end_at=_START + timedelta(hours=4),
            actor_user_id=user_id,
        )
        rows = (
            s.execute(
                select(AuditLog)
                .where(AuditLog.resource_type == "event_occurrence", AuditLog.resource_id == occ.id)
                .order_by(AuditLog.occurred_at)
            )
            .scalars()
            .all()
        )
        assert [r.action for r in rows] == [
            "event_occurrence.exception_created",
            "event_occurrence.exception_changed",
        ]


@requires_postgres
def test_audit_failure_rolls_back_the_business_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.events.series_service as series_service_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(series_service_module, "record_audit_event", _boom)

    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        with pytest.raises(RuntimeError):
            create_series(
                s,
                club_id=club_id,
                name="Should not persist",
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

    with session_scope() as verify:
        rows = (
            verify.execute(select(EventSeries).where(EventSeries.name == "Should not persist"))
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_every_recurrence_audit_action_is_in_the_canonical_vocabulary() -> None:
    recurrence_actions = {
        "event_series.created",
        "event_series.updated",
        "event_series.version_created",
        "event_series.status_changed",
        "event_occurrence.exception_created",
        "event_occurrence.exception_changed",
        "event_occurrence.status_changed",
        "event_occurrence.series_rebound",
    }
    assert recurrence_actions <= CANONICAL_AUDIT_ACTIONS


@requires_postgres
def test_override_name_does_not_leak_into_audit_details() -> None:
    """Empirical proof that arbitrary caller-controlled override text
    (here, deliberately shaped like a leaked secret) becomes ordinary
    EventOccurrence business data but is never echoed into the audit
    `details` blob (ADR-0024: no raw request serialization)."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        sentinel = "password=hunter2 api_key=sk-should-not-leak"
        set_occurrence_exception(
            s,
            occurrence=occ,
            exception_type="rescheduled",
            overrides={"name": sentinel},
            actor_user_id=user_id,
        )
        row = s.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "event_occurrence", AuditLog.resource_id == occ.id
            )
        ).scalar_one()
        assert sentinel not in str(row.details)


# --- "This and following" following-occurrence rebind fan-out --------------
#
# Regression coverage for the defect where create_successor_version() only
# rebound the single selected boundary occurrence and left every other
# already-materialized occurrence chronologically at/after it stranded on
# the historical (source) version — contradicting ADR-0028 §3's "Following
# occurrences belong to the new version". Scenario numbers below match the
# checklist this round of tests was requested against:
#   1. boundary without exception
#   2. following materialized occurrences without exception
#   3. following occurrence with reschedule/override (protected)
#   4. past occurrence
#   5. occurrence_limit
#   6. COUNT/series_end_at
#   7. repeated materialization call after versioning — no duplicates
#   8. duration change in successor
#   9. occurrence IDs remain stable


@requires_postgres
def test_this_and_following_rebinds_boundary_without_exception_and_syncs_snapshot() -> None:
    """Scenarios 1 + 8: the boundary occurrence itself (no protected
    exception) has its snapshot and duration resynced to the successor."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        boundary = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, name="v1 name", event_type="lesson"
        )
        boundary_id = boundary.id

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary_id,
            name="v2 name",
            description="v2 description",
            event_type="training",
            series_start_at=_START,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=120,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )

        assert [r.id for r in rebound] == [boundary_id]
        rebound_boundary = rebound[0]
        assert rebound_boundary.series_id == successor.id
        assert rebound_boundary.name == "v2 name"
        assert rebound_boundary.description == "v2 description"
        assert rebound_boundary.event_type == "training"
        assert rebound_boundary.ends_at == _START + timedelta(minutes=120)  # duration resynced


@requires_postgres
def test_this_and_following_rebinds_every_following_materialized_occurrence() -> None:
    """Scenario 2: not just the boundary — every already-materialized
    occurrence chronologically at/after it also rebinds and resyncs."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        past = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="completed"
        )
        boundary = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=7)
        )
        follower1 = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=14)
        )
        follower2 = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=21)
        )

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=7),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )

        rebound_ids = {r.id for r in rebound}
        assert rebound_ids == {boundary.id, follower1.id, follower2.id}
        assert all(r.series_id == successor.id for r in rebound)
        assert all(r.name == "v2" for r in rebound)  # snapshot resynced

        s.refresh(past)
        assert past.series_id == v1.id  # untouched


@requires_postgres
def test_this_and_following_preserves_a_protected_following_occurrences_exception() -> None:
    """Scenario 3: a following occurrence that already carries its own
    reschedule exception rebinds (series_id changes) but keeps its
    customized schedule/snapshot untouched — never resynced to the
    successor's duration/snapshot."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        boundary = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        protected = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=7)
        )
        set_occurrence_exception(
            s,
            occurrence=protected,
            exception_type="rescheduled",
            effective_start_at=_START + timedelta(days=7, hours=3),
            effective_end_at=_START + timedelta(days=7, hours=5),
            overrides={"name": "Custom name"},
            actor_user_id=user_id,
        )
        protected_starts_at, protected_ends_at = protected.starts_at, protected.ends_at
        protected_id = protected.id

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=180,  # deliberately different — must not apply to the protected row
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )

        rebound_by_id = {r.id: r for r in rebound}
        assert set(rebound_by_id) == {boundary.id, protected_id}
        rebound_protected = rebound_by_id[protected_id]
        assert rebound_protected.series_id == successor.id  # rebinds
        assert rebound_protected.starts_at == protected_starts_at  # untouched
        assert rebound_protected.ends_at == protected_ends_at  # untouched, not resynced
        assert rebound_protected.name == "Custom name"  # override preserved


@requires_postgres
def test_this_and_following_never_touches_a_still_scheduled_past_occurrence() -> None:
    """Scenario 4: exclusion from the rebind is by chronological position
    (recurrence_anchor_at) relative to the boundary, not by status — a
    still-`scheduled` occurrence before the boundary is equally untouched."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        past = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, name="v1", status="scheduled"
        )
        boundary = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=7)
        )
        past_id, past_ends_at = past.id, past.ends_at

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=7),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=999,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )

        assert past_id not in {r.id for r in rebound}
        s.refresh(past)
        assert past.series_id == v1.id
        assert past.name == "v1"
        assert past.ends_at == past_ends_at  # not resynced to the 999-minute duration


@requires_postgres
def test_this_and_following_respects_occurrence_limit_across_rebound_and_materialization() -> None:
    """Scenario 5: `already_generated_count` for the successor's own
    materialization must count the rebound rows too, so occurrence_limit
    is enforced across the version boundary, not reset by it."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(weeks=4))
        v1_occurrences = (
            s.execute(
                select(EventOccurrence)
                .where(EventOccurrence.series_id == v1.id)
                .order_by(EventOccurrence.recurrence_anchor_at)
            )
            .scalars()
            .all()
        )
        assert len(v1_occurrences) == 5  # weeks 0..4 inclusive
        boundary = v1_occurrences[2]  # week 2 -> 3 already-materialized rows at/after it

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=boundary.recurrence_anchor_at,
            series_end_at=None,
            occurrence_limit=4,
            duration_minutes=60,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        assert len(rebound) == 3

        newly_created = materialize_occurrences(
            s, series=successor, horizon_end=boundary.recurrence_anchor_at + timedelta(weeks=50)
        )
        assert len(newly_created) == 1  # only 1 more needed to reach occurrence_limit=4

        total_under_successor = (
            s.execute(select(EventOccurrence).where(EventOccurrence.series_id == successor.id))
            .scalars()
            .all()
        )
        assert len(total_under_successor) == 4

        far_horizon = boundary.recurrence_anchor_at + timedelta(weeks=100)
        assert materialize_occurrences(s, series=successor, horizon_end=far_horizon) == []


@requires_postgres
def test_this_and_following_respects_recurrence_count_across_rebound_and_materialization() -> None:
    """Scenario 6: a successor whose own RRULE carries COUNT must also
    honor the rebound rows already materialized under it."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(weeks=4))
        v1_occurrences = (
            s.execute(
                select(EventOccurrence)
                .where(EventOccurrence.series_id == v1.id)
                .order_by(EventOccurrence.recurrence_anchor_at)
            )
            .scalars()
            .all()
        )
        boundary = v1_occurrences[2]

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=boundary.recurrence_anchor_at,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=60,
            recurrence_rule="FREQ=WEEKLY;COUNT=4",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        assert len(rebound) == 3

        materialize_occurrences(
            s, series=successor, horizon_end=boundary.recurrence_anchor_at + timedelta(weeks=50)
        )
        total = (
            s.execute(select(EventOccurrence).where(EventOccurrence.series_id == successor.id))
            .scalars()
            .all()
        )
        assert len(total) == 4  # COUNT=4 honored even though 3 were already materialized

        far_horizon = boundary.recurrence_anchor_at + timedelta(weeks=200)
        assert materialize_occurrences(s, series=successor, horizon_end=far_horizon) == []


@requires_postgres
def test_materialize_after_this_and_following_creates_no_duplicate_anchors() -> None:
    """Scenario 7: repeated materialization of the successor after a
    rebind never re-creates the already-rebound anchors and is idempotent."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(weeks=4))
        v1_occurrences = (
            s.execute(
                select(EventOccurrence)
                .where(EventOccurrence.series_id == v1.id)
                .order_by(EventOccurrence.recurrence_anchor_at)
            )
            .scalars()
            .all()
        )
        boundary = v1_occurrences[2]

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=boundary.recurrence_anchor_at,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=60,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        rebound_anchors = {r.recurrence_anchor_at for r in rebound}

        first_call = materialize_occurrences(
            s, series=successor, horizon_end=boundary.recurrence_anchor_at + timedelta(weeks=10)
        )
        assert first_call
        assert rebound_anchors.isdisjoint({o.recurrence_anchor_at for o in first_call})

        second_call = materialize_occurrences(
            s, series=successor, horizon_end=boundary.recurrence_anchor_at + timedelta(weeks=10)
        )
        assert second_call == []  # idempotent — no duplicates on repeat

        all_anchors = [
            o.recurrence_anchor_at
            for o in s.execute(
                select(EventOccurrence).where(EventOccurrence.series_id == successor.id)
            ).scalars()
        ]
        assert len(all_anchors) == len(set(all_anchors))  # uq_..._series_id_recurrence_anchor_at


@requires_postgres
def test_this_and_following_keeps_occurrence_ids_stable_and_creates_no_new_rows() -> None:
    """Scenario 9: across a mix of past/boundary/follower/protected
    occurrences, the rebind never creates a new EventOccurrence row and
    every rebound row keeps exactly its pre-existing id — verified via a
    fresh session read, not just in-memory state."""
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id, duration_minutes=60)
        past = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START, status="completed"
        )
        boundary = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=7)
        )
        follower = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=14)
        )
        protected = _make_occurrence(
            s, series_id=v1.id, club_id=club_id, anchor=_START + timedelta(days=21)
        )
        set_occurrence_exception(
            s,
            occurrence=protected,
            exception_type="rescheduled",
            effective_start_at=_START + timedelta(days=21, hours=1),
            actor_user_id=user_id,
        )
        all_ids_before = {o.id for o in (past, boundary, follower, protected)}
        occurrence_count_before = len(s.execute(select(EventOccurrence)).scalars().all())

        successor, rebound = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2",
            description=None,
            event_type="lesson",
            series_start_at=_START + timedelta(days=7),
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=90,
            recurrence_rule="FREQ=WEEKLY",
            timezone="Europe/Moscow",
            actor_user_id=user_id,
        )
        occurrence_count_after = len(s.execute(select(EventOccurrence)).scalars().all())
        assert occurrence_count_after == occurrence_count_before  # no new rows anywhere

        assert {r.id for r in rebound} == {boundary.id, follower.id, protected.id}
        assert {r.id for r in rebound} <= all_ids_before

    with session_scope() as verify:
        stored_ids = {
            o.id
            for o in verify.execute(
                select(EventOccurrence).where(EventOccurrence.club_id == club_id)
            ).scalars()
        }
        assert stored_ids == all_ids_before


# --- Authorization ----------------------------------------------------


@requires_postgres
def test_all_scope_assignment_for_the_matching_club_grants_access() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _grant_all_scope(s, user_id=user_id, permission_code="event.read", club_id=club_id)
        context = build_series_resource_context(series=v1)
        authorizer = Authorizer(session=s, user_id=user_id, permission_code="event.read")
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_all_scope_assignment_for_a_different_club_denies_access() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        other_club_id, _ = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _grant_all_scope(s, user_id=user_id, permission_code="event.read", club_id=other_club_id)
        context = build_series_resource_context(series=v1)
        authorizer = Authorizer(session=s, user_id=user_id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_unauthorized_user_with_no_assignment_is_denied() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        context = build_series_resource_context(series=v1)
        authorizer = Authorizer(session=s, user_id=user_id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_occurrence_authorization_follows_the_same_club_boundary() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=v1.id, club_id=club_id, anchor=_START)
        _grant_all_scope(s, user_id=user_id, permission_code="event.read", club_id=club_id)
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=user_id)
        authorizer = Authorizer(session=s, user_id=user_id, permission_code="event.read")
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_global_all_scope_assignment_grants_cross_club_access() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        v1 = _make_series_v1(s, club_id=club_id, user_id=user_id)
        _grant_all_scope(s, user_id=user_id, permission_code="event.manage", club_id=None)
        context = build_series_resource_context(series=v1)
        authorizer = Authorizer(session=s, user_id=user_id, permission_code="event.manage")
        assert authorizer.is_allowed(context) is True
