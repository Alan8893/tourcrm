"""Pure-Python unit tests for the Issue #79 recurrence rule domain
module (app.events.rrule) — no database, no HTTP. DB-level invariants
(exclusion/unique constraints, materialization idempotency) are covered
separately in tests/integration/test_event_recurrence.py.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from app.events.rrule import (
    InconsistentRecurrenceTerminationError,
    InvalidRecurrenceByDayError,
    InvalidRecurrenceByMonthDayError,
    InvalidRecurrenceByMonthError,
    InvalidRecurrenceCountError,
    InvalidRecurrenceFrequencyError,
    InvalidRecurrenceIntervalError,
    InvalidRecurrenceTimezoneError,
    RecurrenceInput,
    build_canonical_rrule,
    expand_occurrences,
)

_MON_2026_01_05_18_00_UTC = datetime(2026, 1, 5, 18, 0, tzinfo=dt_timezone.utc)
_FAR_WINDOW_END = datetime(2027, 1, 1, tzinfo=dt_timezone.utc)


# --- build_canonical_rrule: validation --------------------------------------


@pytest.mark.parametrize("freq", ["DAILY", "WEEKLY", "MONTHLY", "YEARLY"])
def test_every_canonical_frequency_is_accepted(freq: str) -> None:
    rule, end = build_canonical_rrule(RecurrenceInput(frequency=freq))
    assert rule == f"FREQ={freq}"
    assert end is None


def test_invalid_frequency_is_rejected() -> None:
    with pytest.raises(InvalidRecurrenceFrequencyError):
        build_canonical_rrule(RecurrenceInput(frequency="HOURLY"))


def test_invalid_interval_is_rejected() -> None:
    with pytest.raises(InvalidRecurrenceIntervalError):
        build_canonical_rrule(RecurrenceInput(frequency="DAILY", interval=0))


def test_invalid_by_day_is_rejected() -> None:
    with pytest.raises(InvalidRecurrenceByDayError):
        build_canonical_rrule(RecurrenceInput(frequency="WEEKLY", by_day=("XX",)))


def test_invalid_by_month_day_is_rejected() -> None:
    with pytest.raises(InvalidRecurrenceByMonthDayError):
        build_canonical_rrule(RecurrenceInput(frequency="MONTHLY", by_month_day=(32,)))


def test_invalid_by_month_is_rejected() -> None:
    with pytest.raises(InvalidRecurrenceByMonthError):
        build_canonical_rrule(RecurrenceInput(frequency="YEARLY", by_month=(13,)))


def test_invalid_count_is_rejected() -> None:
    with pytest.raises(InvalidRecurrenceCountError):
        build_canonical_rrule(RecurrenceInput(frequency="DAILY", count=0))


# --- build_canonical_rrule: canonical string shape --------------------------


def test_interval_one_is_omitted_from_canonical_string() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY", interval=1))
    assert rule == "FREQ=DAILY"


def test_interval_greater_than_one_is_included() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY", interval=3))
    assert rule == "FREQ=DAILY;INTERVAL=3"


def test_by_day_is_included() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="WEEKLY", by_day=("MO", "WE")))
    assert rule == "FREQ=WEEKLY;BYDAY=MO,WE"


def test_by_month_day_is_included() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="MONTHLY", by_month_day=(1, 15)))
    assert rule == "FREQ=MONTHLY;BYMONTHDAY=1,15"


def test_by_month_is_included() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="YEARLY", by_month=(6,)))
    assert rule == "FREQ=YEARLY;BYMONTH=6"


def test_count_is_included() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY", count=10))
    assert rule == "FREQ=DAILY;COUNT=10"


# --- UNTIL normalization -----------------------------------------------------


def test_until_is_never_persisted_in_the_canonical_rule() -> None:
    until = datetime(2026, 6, 1, tzinfo=dt_timezone.utc)
    rule, series_end_at = build_canonical_rrule(RecurrenceInput(frequency="DAILY", until=until))
    assert "UNTIL" not in rule
    assert rule == "FREQ=DAILY"
    assert series_end_at == until


def test_explicit_series_end_at_matching_until_is_accepted() -> None:
    until = datetime(2026, 6, 1, tzinfo=dt_timezone.utc)
    rule, series_end_at = build_canonical_rrule(
        RecurrenceInput(frequency="DAILY", until=until), series_end_at=until
    )
    assert series_end_at == until


def test_explicit_series_end_at_conflicting_with_until_is_rejected() -> None:
    until = datetime(2026, 6, 1, tzinfo=dt_timezone.utc)
    other = datetime(2026, 7, 1, tzinfo=dt_timezone.utc)
    with pytest.raises(InconsistentRecurrenceTerminationError):
        build_canonical_rrule(RecurrenceInput(frequency="DAILY", until=until), series_end_at=other)


def test_series_end_at_without_until_passes_through() -> None:
    series_end_at = datetime(2026, 6, 1, tzinfo=dt_timezone.utc)
    rule, effective = build_canonical_rrule(
        RecurrenceInput(frequency="DAILY"), series_end_at=series_end_at
    )
    assert effective == series_end_at


# --- expand_occurrences: FREQ coverage --------------------------------------


def test_daily_expansion() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY", count=5))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
    )
    assert len(occs) == 5
    assert occs == sorted(occs)
    assert occs[0] == _MON_2026_01_05_18_00_UTC
    assert (occs[1] - occs[0]).days == 1


def test_weekly_expansion() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="WEEKLY", count=4))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
    )
    assert len(occs) == 4
    assert all((occs[i + 1] - occs[i]).days == 7 for i in range(len(occs) - 1))


def test_monthly_expansion() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="MONTHLY", count=3))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
    )
    assert [o.month for o in occs] == [1, 2, 3]
    assert all(o.day == 5 for o in occs)


def test_yearly_expansion() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="YEARLY", count=3))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=datetime(2030, 1, 1, tzinfo=dt_timezone.utc),
    )
    assert [o.year for o in occs] == [2026, 2027, 2028]


def test_interval_two_weekly() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="WEEKLY", interval=2, count=3))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
    )
    assert all((occs[i + 1] - occs[i]).days == 14 for i in range(len(occs) - 1))


def test_by_day_multiple_weekdays() -> None:
    rule, _ = build_canonical_rrule(
        RecurrenceInput(frequency="WEEKLY", by_day=("MO", "WE", "FR"), count=6)
    )
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
    )
    weekdays = [o.weekday() for o in occs]
    assert weekdays == [0, 2, 4, 0, 2, 4]  # Mon, Wed, Fri repeating


def test_by_month_day() -> None:
    rule, _ = build_canonical_rrule(
        RecurrenceInput(frequency="MONTHLY", by_month_day=(1,), count=3)
    )
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
    )
    assert all(o.day == 1 for o in occs)


def test_by_month_restricts_to_given_months() -> None:
    rule, _ = build_canonical_rrule(
        RecurrenceInput(frequency="MONTHLY", by_month=(3, 6, 9), count=3)
    )
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=datetime(2028, 1, 1, tzinfo=dt_timezone.utc),
    )
    assert [o.month for o in occs] == [3, 6, 9]


# --- termination: series_end_at / occurrence_limit --------------------------


def test_series_end_bounds_generation() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY"))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
        series_end_at=datetime(2026, 1, 8, 18, 0, tzinfo=dt_timezone.utc),
    )
    # RFC 5545 UNTIL is inclusive: an occurrence exactly at series_end_at
    # is the last one generated (Jan 5, 6, 7, 8).
    assert len(occs) == 4


def test_occurrence_limit_bounds_generation() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY"))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
        occurrence_limit=4,
    )
    assert len(occs) == 4


def test_both_limits_set_first_reached_wins() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY"))
    # series_end allows 10 days; occurrence_limit allows only 3 - the
    # smaller (first-reached) limit must win.
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
        series_end_at=datetime(2026, 1, 15, tzinfo=dt_timezone.utc),
        occurrence_limit=3,
    )
    assert len(occs) == 3

    # Reversed: occurrence_limit allows 100 but series_end allows only 2.
    occs2 = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
        series_end_at=datetime(2026, 1, 7, 18, 0, tzinfo=dt_timezone.utc),
        occurrence_limit=100,
    )
    assert len(occs2) == 3  # Jan 5, 6, 7 - UNTIL is inclusive (see test above)


def test_occurrence_limit_zero_remaining_after_already_generated() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY"))
    occs = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=_FAR_WINDOW_END,
        occurrence_limit=3,
        already_generated_count=3,
    )
    assert occs == []


# --- idempotent incremental extension (on-demand horizon extension) --------


def test_incremental_extension_is_deterministic_and_produces_no_overlap() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY"))
    first_window = datetime(2026, 1, 10, tzinfo=dt_timezone.utc)
    first_batch = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=first_window,
    )
    second_batch = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=datetime(2026, 1, 20, tzinfo=dt_timezone.utc),
        already_generated_count=len(first_batch),
    )
    assert set(first_batch).isdisjoint(second_batch)
    assert min(second_batch) > max(first_batch)

    # Re-running the very same call twice is itself idempotent (same args
    # -> same deterministic sequence) - the real DB-level idempotency
    # guard is the UNIQUE(series_id, recurrence_anchor_at) constraint,
    # exercised in the integration tests, but the generator itself must
    # never depend on wall-clock time to be safe to call repeatedly.
    repeat = expand_occurrences(
        canonical_rrule=rule,
        series_start_at=_MON_2026_01_05_18_00_UTC,
        timezone="UTC",
        window_end=first_window,
    )
    assert repeat == first_batch


# --- invalid timezone ---------------------------------------------------


def test_invalid_timezone_is_rejected() -> None:
    rule, _ = build_canonical_rrule(RecurrenceInput(frequency="DAILY", count=1))
    with pytest.raises(InvalidRecurrenceTimezoneError):
        expand_occurrences(
            canonical_rrule=rule,
            series_start_at=_MON_2026_01_05_18_00_UTC,
            timezone="Not/AZone",
            window_end=_FAR_WINDOW_END,
        )
