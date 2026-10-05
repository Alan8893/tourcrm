"""Duration Classification (Issue #274, trips-and-tourist-profile.md §10):
the planned-interval midnight rule and value validation."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.trips.duration_classification import (
    DURATION_CLASSIFICATIONS,
    DurationClassificationMismatchError,
    InvalidDurationClassificationError,
    crosses_midnight,
    validate_duration_classification,
)

_MOSCOW = "Europe/Moscow"
_ZONE = ZoneInfo(_MOSCOW)


def _local(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=_ZONE)


def _check(value: str, start: datetime, end: datetime, tz: str = _MOSCOW) -> str:
    return validate_duration_classification(value, start_at=start, end_at=end, timezone=tz)


def test_the_three_approved_values() -> None:
    assert DURATION_CLASSIFICATIONS == ("ONE_DAY", "MULTI_DAY", "UNCLASSIFIED")


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # §10 examples.
        (_local(20, 10), _local(20, 20), False),
        (_local(20, 10), _local(21, 15), True),
        # No hour threshold: a short overnight interval crosses midnight,
        # a long same-day one does not.
        (_local(20, 23), _local(21, 1), True),
        (_local(20, 0, 30), _local(20, 23, 59), False),
        (_local(20, 22), _local(22, 22), True),
        # Strict: touching a midnight only at an end is not crossing it.
        (_local(20, 10), _local(21, 0), False),
        (_local(20, 0), _local(20, 10), False),
        (_local(20, 0), _local(21, 0), False),
        (_local(20, 10), _local(21, 0, 1), True),
    ],
)
def test_crosses_midnight(start: datetime, end: datetime, expected: bool) -> None:
    assert crosses_midnight(start, end, _MOSCOW) is expected


def test_midnight_is_local_to_the_event_timezone() -> None:
    # 10:00–22:00 in Moscow (UTC+3) is 07:00–19:00 UTC: same day either way.
    start, end = _local(20, 10), _local(20, 22)
    assert crosses_midnight(start, end, _MOSCOW) is False
    # 01:00–04:00 Moscow is 22:00–01:00 UTC: crosses a UTC midnight but not
    # a local one.
    start, end = _local(20, 1), _local(20, 4)
    assert crosses_midnight(start, end, _MOSCOW) is False
    assert crosses_midnight(start, end, "UTC") is True
    # The same instant pair seen from another zone.
    utc_start = datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc)
    utc_end = utc_start + timedelta(hours=3)
    assert crosses_midnight(utc_start, utc_end, "UTC") is False
    assert crosses_midnight(utc_start, utc_end, _MOSCOW) is True


def test_midnight_rule_across_a_dst_change() -> None:
    zone = "Europe/Berlin"
    start = datetime(2026, 3, 28, 20, 0, tzinfo=ZoneInfo(zone))
    end = datetime(2026, 3, 29, 10, 0, tzinfo=ZoneInfo(zone))
    assert crosses_midnight(start, end, zone) is True
    same_day = datetime(2026, 3, 29, 1, 0, tzinfo=ZoneInfo(zone))
    later = datetime(2026, 3, 29, 23, 0, tzinfo=ZoneInfo(zone))
    assert crosses_midnight(same_day, later, zone) is False


def test_one_day_and_multi_day_must_match_the_planned_interval() -> None:
    same_day = (_local(20, 10), _local(20, 20))
    overnight = (_local(20, 10), _local(21, 15))
    assert _check("ONE_DAY", *same_day) == "ONE_DAY"
    assert _check("MULTI_DAY", *overnight) == "MULTI_DAY"
    with pytest.raises(DurationClassificationMismatchError):
        _check("MULTI_DAY", *same_day)
    with pytest.raises(DurationClassificationMismatchError):
        _check("ONE_DAY", *overnight)


def test_unclassified_is_always_consistent() -> None:
    assert _check("UNCLASSIFIED", _local(20, 10), _local(20, 20)) == "UNCLASSIFIED"
    assert _check("UNCLASSIFIED", _local(20, 10), _local(25, 20)) == "UNCLASSIFIED"


def test_an_18_to_02_plan_is_not_a_special_value() -> None:
    # §10: an invalid children's plan, not a separate classification; by
    # the midnight rule only MULTI_DAY or UNCLASSIFIED is consistent.
    start, end = _local(20, 18), _local(21, 2)
    assert _check("MULTI_DAY", start, end) == "MULTI_DAY"
    assert _check("UNCLASSIFIED", start, end) == "UNCLASSIFIED"
    with pytest.raises(DurationClassificationMismatchError):
        _check("ONE_DAY", start, end)


@pytest.mark.parametrize(
    "value", ["", "one_day", "WEEKEND", "MULTIDAY", "NONE", "TWO_DAY", " ONE_DAY"]
)
def test_other_values_are_rejected(value: str) -> None:
    with pytest.raises(InvalidDurationClassificationError):
        _check(value, _local(20, 10), _local(20, 20))
