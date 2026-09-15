"""Real PostgreSQL integration tests for app.events.materialization
(Issue #79, ADR-0015, ADR-0028 §9): the 180-day default horizon,
on-demand extension, deterministic occurrence identity, DB-level
idempotency, and concurrent-materializer safety.

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
from app.db.identity import Club
from app.db.session import session_scope
from app.events.materialization import (
    DEFAULT_MATERIALIZATION_HORIZON_DAYS,
    ensure_materialized,
    materialize_occurrences,
)

from .conftest import requires_postgres

_START = datetime(2026, 1, 5, 18, 0, tzinfo=dt_timezone.utc)


def _make_club(session) -> uuid.UUID:
    club = Club(name=f"Club-{uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.commit()
    return club.id


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
