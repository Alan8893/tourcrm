"""Real PostgreSQL integration tests for app.events.materialization
(Issue #79, ADR-0015, ADR-0028 §9): the 180-day default horizon,
on-demand extension, deterministic occurrence identity, DB-level
idempotency, concurrent-materializer safety, and — since a "this and
following" version boundary exists — that only the terminal/current
EventSeries version is ever materialized (see the "superseded version"
section below).

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
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.events.materialization import (
    DEFAULT_MATERIALIZATION_HORIZON_DAYS,
    ensure_materialized,
    materialize_occurrences,
)
from app.events.series_service import create_successor_version

from .conftest import requires_postgres

_START = datetime(2026, 1, 5, 18, 0, tzinfo=dt_timezone.utc)


def _make_club(session) -> uuid.UUID:
    club = Club(name=f"Club-{uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.commit()
    return club.id


def _make_user(session) -> uuid.UUID:
    person = Person(last_name="A", first_name="B")
    session.add(person)
    session.commit()
    user = User(
        person_id=person.id, login_identifier=f"u-{uuid.uuid4().hex[:8]}@x.example", status="active"
    )
    session.add(user)
    session.commit()
    return user.id


def _make_series(session, *, club_id: uuid.UUID, **overrides) -> EventSeries:
    series_id = uuid.uuid4()
    defaults = dict(
        id=series_id,
        root_series_id=series_id,
        version=1,
        club_id=club_id,
        name="Weekly lesson",
        event_type="lesson",
        series_start_at=_START,
        recurrence_rule="FREQ=DAILY",
        duration_minutes=60,
        timezone="UTC",
        status="active",
    )
    defaults.update(overrides)
    series = EventSeries(**defaults)
    session.add(series)
    session.commit()
    return series


@requires_postgres
def test_default_horizon_is_180_days_forward_from_now() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id, series_start_at=_START)

        before = datetime.now(dt_tz := dt_timezone.utc)
        created = ensure_materialized(s, series=series)
        after = datetime.now(dt_tz)

        assert created  # DAILY from a past start date always yields rows
        latest_anchor = max(o.recurrence_anchor_at for o in created)
        expected_min = before + timedelta(days=DEFAULT_MATERIALIZATION_HORIZON_DAYS)
        expected_max = after + timedelta(days=DEFAULT_MATERIALIZATION_HORIZON_DAYS)
        # DAILY means the latest anchor lands within one day of the
        # horizon boundary, not exactly on it.
        assert expected_min - timedelta(days=1) <= latest_anchor <= expected_max


@requires_postgres
def test_on_demand_extension_creates_only_the_new_slice() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id)

        first_batch = materialize_occurrences(
            s, series=series, horizon_end=_START + timedelta(days=10)
        )
        assert len(first_batch) == 11  # day 0..10 inclusive (DAILY, UNTIL inclusive semantics)

        second_batch = materialize_occurrences(
            s, series=series, horizon_end=_START + timedelta(days=20)
        )
        assert len(second_batch) == 10  # day 11..20, no overlap with first_batch
        first_ids = {o.id for o in first_batch}
        second_ids = {o.id for o in second_batch}
        assert first_ids.isdisjoint(second_ids)

        all_rows = s.execute(
            select(EventOccurrence).where(EventOccurrence.series_id == series.id)
        ).scalars().all()
        assert len(all_rows) == 21


@requires_postgres
def test_repeated_materialization_of_the_same_window_is_idempotent() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id)

        first = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=14))
        assert len(first) > 0

        second = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=14))
        assert second == []

        rows = s.execute(
            select(EventOccurrence).where(EventOccurrence.series_id == series.id)
        ).scalars().all()
        assert len(rows) == len(first)


@requires_postgres
def test_occurrence_ids_are_stable_across_repeated_materialization_calls() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id)

        first = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=5))
        first_ids_by_anchor = {o.recurrence_anchor_at: o.id for o in first}

        # Extend far beyond, then re-fetch the original rows directly —
        # their ids must be untouched.
        materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=30))

        rows = s.execute(
            select(EventOccurrence)
            .where(EventOccurrence.series_id == series.id)
            .where(EventOccurrence.recurrence_anchor_at.in_(first_ids_by_anchor.keys()))
        ).scalars().all()
        for row in rows:
            assert row.id == first_ids_by_anchor[row.recurrence_anchor_at]


@requires_postgres
def test_materialization_never_deletes_existing_occurrences() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id)
        materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=10))

        # An occurrence with operational history (moved to in_progress)
        # must survive a further materialization call untouched.
        occ = s.execute(
            select(EventOccurrence).where(EventOccurrence.series_id == series.id).limit(1)
        ).scalar_one()
        occ.status = "in_progress"
        s.commit()
        occ_id = occ.id

        materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=30))

        still_there = s.get(EventOccurrence, occ_id)
        assert still_there is not None
        assert still_there.status == "in_progress"


@requires_postgres
def test_ends_at_equals_starts_at_plus_duration_minutes() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id, duration_minutes=45)
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=3))
        assert created
        for occ in created:
            assert occ.ends_at - occ.starts_at == timedelta(minutes=45)


@requires_postgres
def test_paused_series_materializes_nothing() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id, status="paused")
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=10))
        assert created == []


@requires_postgres
def test_cancelled_series_materializes_nothing() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id, status="cancelled")
        created = materialize_occurrences(s, series=series, horizon_end=_START + timedelta(days=10))
        assert created == []


@requires_postgres
def test_occurrence_limit_and_series_end_at_both_respected() -> None:
    with session_scope() as s:
        club_id = _make_club(s)
        series = _make_series(s, club_id=club_id, occurrence_limit=5)
        created = materialize_occurrences(
            s, series=series, horizon_end=_START + timedelta(days=100)
        )
        assert len(created) == 5


# --- Concurrency: two real concurrent materializers, same series/window ---


@requires_postgres
def test_concurrent_materialization_produces_no_duplicate_anchors() -> None:
    trial_count = 10
    for _trial in range(trial_count):
        with session_scope() as setup:
            club_id = _make_club(setup)
            series = _make_series(setup, club_id=club_id)
            series_id = series.id

        start_gate = threading.Barrier(2, timeout=10)
        counts: dict[str, int] = {}

        def attempt(name: str) -> None:
            with session_scope() as session:
                series_row = session.get(EventSeries, series_id)
                start_gate.wait()
                created = materialize_occurrences(
                    session, series=series_row, horizon_end=_START + timedelta(days=59)
                )
                counts[name] = len(created)

        thread_a = threading.Thread(target=attempt, args=("a",))
        thread_b = threading.Thread(target=attempt, args=("b",))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        with session_scope() as check:
            rows = check.execute(
                select(EventOccurrence).where(EventOccurrence.series_id == series_id)
            ).scalars().all()
            distinct_anchors = {row.recurrence_anchor_at for row in rows}

        assert len(rows) == len(distinct_anchors), f"duplicate anchors: {counts}"
        assert len(rows) == 60  # day 0..59
        assert counts["a"] + counts["b"] == len(rows)


# --- Materialization only for the terminal/current EventSeries version -----
#
# Regression coverage for the defect where a historical version — whose
# own `status` column a "this and following" change deliberately never
# touches (that would be a separate, unrequested business decision) —
# stayed materializable, so re-calling materialize_occurrences() on it
# after versioning could resurrect future slots that ADR-0028 §3 says
# now belong to the successor, silently duplicating logical occurrence
# slots across versions and miscounting occurrence_limit/COUNT. Scenario
# numbers below match the checklist this round of tests was requested
# against:
#   1. materialize(v1) before successor -> works
#   2. create v2 via this_and_following
#   3. materialize(v1) after versioning -> [] / no new rows
#   4. materialize(v2) -> creates only new slots
#   5. repeated materialize(v2) -> idempotent
#   6/7. occurrence_limit / COUNT / series_end_at still respected
#   8. past occurrences remain on v1
#   9. rebound occurrences keep their ids
#   10. concurrent materialization vs. versioning never duplicates a slot


@requires_postgres
def test_materialize_v1_before_this_and_following_works() -> None:
    """Scenario 1: ordinary materialization of a not-yet-superseded
    version behaves exactly as before this round's fix."""
    with session_scope() as s:
        club_id = _make_club(s)
        v1 = _make_series(s, club_id=club_id)
        created = materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(days=10))
        assert len(created) == 11  # day 0..10 inclusive


@requires_postgres
def test_materialize_of_a_superseded_version_creates_nothing() -> None:
    """Scenarios 2, 3, 4, 5, 8, 9: once a "this and following" successor
    exists, the historical version — even though its own `status` stays
    `active` (this fix never changes lifecycle status; that is a separate
    business decision) — is never re-materialized, while the successor
    correctly continues generating only new slots, idempotently, and the
    rebound rows keep their original ids."""
    with session_scope() as s:
        club_id = _make_club(s)
        user_id = _make_user(s)
        v1 = _make_series(s, club_id=club_id)
        materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(days=20))
        v1_occurrences = (
            s.execute(
                select(EventOccurrence)
                .where(EventOccurrence.series_id == v1.id)
                .order_by(EventOccurrence.recurrence_anchor_at)
            )
            .scalars()
            .all()
        )
        assert len(v1_occurrences) == 21  # day 0..20 inclusive
        boundary = v1_occurrences[10]  # day 10 -> 11 rows (days 10..20) rebind to v2
        pre_boundary_ids = {o.id for o in v1_occurrences[:10]}
        rebindable_ids_by_anchor = {o.recurrence_anchor_at: o.id for o in v1_occurrences[10:]}

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
            duration_minutes=v1.duration_minutes,
            recurrence_rule=v1.recurrence_rule,
            timezone=v1.timezone,
            actor_user_id=user_id,
        )
        assert len(rebound) == 11
        assert v1.status == "active"  # lifecycle status is never auto-changed

        # Scenario 9: rebound rows keep their original ids.
        for occ in rebound:
            assert occ.id == rebindable_ids_by_anchor[occ.recurrence_anchor_at]

        # Scenario 3: v1 is no longer the terminal version — materializing
        # it again, even with a horizon far beyond anything already
        # generated, creates nothing.
        assert materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(days=200)) == []

        # Scenario 8: only the pre-boundary (past, relative to the
        # boundary) rows remain on v1 — nothing was added or removed.
        total_under_v1 = (
            s.execute(select(EventOccurrence).where(EventOccurrence.series_id == v1.id))
            .scalars()
            .all()
        )
        assert {o.id for o in total_under_v1} == pre_boundary_ids

        # Scenario 4: the successor materializes only genuinely new slots
        # beyond the 11 rows it already inherited via the rebind.
        newly_created = materialize_occurrences(
            s, series=successor, horizon_end=boundary.recurrence_anchor_at + timedelta(days=20)
        )
        assert len(newly_created) == 10  # days 21..30 relative to _START

        # Scenario 5: repeating that same call is idempotent.
        assert (
            materialize_occurrences(
                s,
                series=successor,
                horizon_end=boundary.recurrence_anchor_at + timedelta(days=20),
            )
            == []
        )

        total_under_successor = (
            s.execute(select(EventOccurrence).where(EventOccurrence.series_id == successor.id))
            .scalars()
            .all()
        )
        assert len(total_under_successor) == 21  # 11 rebound + 10 freshly materialized


@requires_postgres
def test_materialize_of_a_superseded_version_respects_occurrence_limit_and_count_on_successor() -> (
    None
):
    """Scenarios 6/7: occurrence_limit, COUNT, and series_end_at continue
    to be honored on the successor across the version boundary — this
    round's terminal-version guard doesn't change that pre-existing
    (already regression-tested in app.events.versioning) behavior."""
    with session_scope() as s:
        club_id = _make_club(s)
        user_id = _make_user(s)
        v1 = _make_series(s, club_id=club_id)
        materialize_occurrences(s, series=v1, horizon_end=_START + timedelta(days=4))
        v1_occurrences = (
            s.execute(
                select(EventOccurrence)
                .where(EventOccurrence.series_id == v1.id)
                .order_by(EventOccurrence.recurrence_anchor_at)
            )
            .scalars()
            .all()
        )
        assert len(v1_occurrences) == 5  # day 0..4 inclusive
        boundary = v1_occurrences[2]  # day 2 -> 3 rows (days 2..4) rebind to v2

        # occurrence_limit
        successor_limit, rebound_limit = create_successor_version(
            s,
            source_series_id=v1.id,
            boundary_occurrence_id=boundary.id,
            name="v2-limit",
            description=None,
            event_type="lesson",
            series_start_at=boundary.recurrence_anchor_at,
            series_end_at=None,
            occurrence_limit=4,
            duration_minutes=v1.duration_minutes,
            recurrence_rule=v1.recurrence_rule,
            timezone=v1.timezone,
            actor_user_id=user_id,
        )
        assert len(rebound_limit) == 3
        limit_horizon = boundary.recurrence_anchor_at + timedelta(days=100)
        materialize_occurrences(s, series=successor_limit, horizon_end=limit_horizon)
        total_limit = (
            s.execute(
                select(EventOccurrence).where(EventOccurrence.series_id == successor_limit.id)
            )
            .scalars()
            .all()
        )
        assert len(total_limit) == 4  # occurrence_limit honored across the boundary

        # A second, independent v1 -> COUNT scenario.
        v1b = _make_series(s, club_id=club_id)
        materialize_occurrences(s, series=v1b, horizon_end=_START + timedelta(days=4))
        v1b_occurrences = (
            s.execute(
                select(EventOccurrence)
                .where(EventOccurrence.series_id == v1b.id)
                .order_by(EventOccurrence.recurrence_anchor_at)
            )
            .scalars()
            .all()
        )
        boundary_b = v1b_occurrences[2]

        successor_count, rebound_count = create_successor_version(
            s,
            source_series_id=v1b.id,
            boundary_occurrence_id=boundary_b.id,
            name="v2-count",
            description=None,
            event_type="lesson",
            series_start_at=boundary_b.recurrence_anchor_at,
            series_end_at=None,
            occurrence_limit=None,
            duration_minutes=v1b.duration_minutes,
            recurrence_rule="FREQ=DAILY;COUNT=4",
            timezone=v1b.timezone,
            actor_user_id=user_id,
        )
        assert len(rebound_count) == 3
        count_horizon = boundary_b.recurrence_anchor_at + timedelta(days=100)
        materialize_occurrences(s, series=successor_count, horizon_end=count_horizon)
        total_count = (
            s.execute(
                select(EventOccurrence).where(EventOccurrence.series_id == successor_count.id)
            )
            .scalars()
            .all()
        )
        assert len(total_count) == 4  # COUNT=4 honored across the boundary


@requires_postgres
def test_concurrent_materialization_and_versioning_never_duplicates_a_slot() -> None:
    """Scenario 10: a materializer racing a concurrent "this and
    following" versioning call for the same logical series can never win
    and insert a future slot for a version the versioning call is about
    to (or just did) supersede — the two serialize on the shared root-row
    lock (see app.events.materialization module docstring)."""
    trial_count = 8
    for _trial in range(trial_count):
        with session_scope() as setup:
            club_id = _make_club(setup)
            user_id = _make_user(setup)
            v1 = _make_series(setup, club_id=club_id)
            materialize_occurrences(setup, series=v1, horizon_end=_START + timedelta(days=5))
            boundary = (
                setup.execute(
                    select(EventOccurrence)
                    .where(EventOccurrence.series_id == v1.id)
                    .order_by(EventOccurrence.recurrence_anchor_at)
                )
                .scalars()
                .all()[3]
            )
            v1_id, boundary_id, boundary_anchor = v1.id, boundary.id, boundary.recurrence_anchor_at

        start_gate = threading.Barrier(2, timeout=10)
        errors: list[BaseException] = []

        def materialize_attempt() -> None:
            with session_scope() as session:
                v1_row = session.get(EventSeries, v1_id)
                start_gate.wait()
                try:
                    materialize_occurrences(
                        session, series=v1_row, horizon_end=_START + timedelta(days=40)
                    )
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

        def version_attempt() -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_successor_version(
                        session,
                        source_series_id=v1_id,
                        boundary_occurrence_id=boundary_id,
                        name="v2",
                        description=None,
                        event_type="lesson",
                        series_start_at=boundary_anchor,
                        series_end_at=None,
                        occurrence_limit=None,
                        duration_minutes=60,
                        recurrence_rule="FREQ=DAILY",
                        timezone="UTC",
                        actor_user_id=user_id,
                    )
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

        thread_materialize = threading.Thread(target=materialize_attempt)
        thread_version = threading.Thread(target=version_attempt)
        thread_materialize.start()
        thread_version.start()
        thread_materialize.join(timeout=15)
        thread_version.join(timeout=15)

        assert not errors, errors

        with session_scope() as check:
            rows = (
                check.execute(select(EventOccurrence).where(EventOccurrence.club_id == club_id))
                .scalars()
                .all()
            )
            anchors = [row.recurrence_anchor_at for row in rows]
            # No anchor is ever materialized under two different series
            # versions at once — whichever of the two operations won the
            # root-row lock race, the other saw a consistent, up-to-date
            # picture of the terminal version.
            assert len(anchors) == len(set(anchors))
