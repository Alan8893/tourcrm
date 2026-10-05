"""Pure-Python unit tests for the Issue #36 Event lifecycle/data-integrity
domain module — no database, no HTTP. Real PostgreSQL CHECK constraints
(event_type/status validity, end_at > start_at, cancellation reason,
coordinate consistency) and FK integrity are covered separately in
tests/integration/test_events.py.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.events.lifecycle import (
    ALLOWED_STATUS_TRANSITIONS,
    CancellationReasonRequiredError,
    InconsistentCoordinatesError,
    InvalidEventStatusError,
    InvalidEventStatusTransitionError,
    InvalidEventTypeError,
    InvalidTimeRangeError,
    InvalidTimezoneError,
    time_based_status_transitions,
    validate_coordinates,
    validate_event_type,
    validate_status,
    validate_status_transition,
    validate_time_range,
    validate_timezone,
)
from app.events.vocabulary import CANONICAL_EVENT_STATUSES, CANONICAL_EVENT_TYPES

# --- canonical event type / status vocabulary -----------------------------


@pytest.mark.parametrize("event_type", CANONICAL_EVENT_TYPES)
def test_every_canonical_event_type_is_accepted(event_type: str) -> None:
    validate_event_type(event_type)  # must not raise


@pytest.mark.parametrize("event_type", ["planned", "class", "", "LESSON", "workshop"])
def test_non_canonical_event_type_is_rejected(event_type: str) -> None:
    with pytest.raises(InvalidEventTypeError):
        validate_event_type(event_type)


@pytest.mark.parametrize("status", CANONICAL_EVENT_STATUSES)
def test_every_canonical_status_is_accepted(status: str) -> None:
    validate_status(status)  # must not raise


def test_planned_is_not_a_canonical_status() -> None:
    assert "planned" not in CANONICAL_EVENT_STATUSES
    with pytest.raises(InvalidEventStatusError):
        validate_status("planned")


@pytest.mark.parametrize("status", ["", "PUBLISHED", "pending", "deleted"])
def test_non_canonical_status_is_rejected(status: str) -> None:
    with pytest.raises(InvalidEventStatusError):
        validate_status(status)


# --- ADR-0018 allowed transitions ------------------------------------------

_ALLOWED_TRANSITIONS = [
    ("draft", "published"),
    ("published", "in_progress"),
    ("published", "cancelled"),
    ("in_progress", "completed"),
    ("in_progress", "cancelled"),
    ("completed", "archived"),
    ("cancelled", "archived"),
]


@pytest.mark.parametrize("from_status,to_status", _ALLOWED_TRANSITIONS)
def test_every_allowed_transition_is_accepted(from_status: str, to_status: str) -> None:
    reason = "cancelled by organizer" if to_status == "cancelled" else None
    validate_status_transition(from_status, to_status, cancellation_reason=reason)  # must not raise


_FORBIDDEN_TRANSITIONS = [
    ("draft", "in_progress"),
    ("draft", "completed"),
    ("draft", "cancelled"),
    ("draft", "archived"),
    ("published", "draft"),
    ("published", "completed"),
    ("published", "archived"),
    ("in_progress", "draft"),
    ("in_progress", "published"),
    ("in_progress", "archived"),
    ("completed", "in_progress"),
    ("completed", "cancelled"),
    ("cancelled", "published"),
    ("cancelled", "in_progress"),
    ("archived", "draft"),
    ("archived", "published"),
    # A status transitioning to itself is not one of ADR-0018's edges either.
    ("draft", "draft"),
    ("published", "published"),
]


@pytest.mark.parametrize("from_status,to_status", _FORBIDDEN_TRANSITIONS)
def test_forbidden_transition_is_rejected(from_status: str, to_status: str) -> None:
    with pytest.raises(InvalidEventStatusTransitionError):
        validate_status_transition(
            from_status,
            to_status,
            cancellation_reason="reason" if to_status == "cancelled" else None,
        )


def test_planned_is_rejected_as_a_transition_target() -> None:
    with pytest.raises(InvalidEventStatusError):
        validate_status_transition("draft", "planned")


def test_planned_is_rejected_as_a_transition_source() -> None:
    with pytest.raises(InvalidEventStatusError):
        validate_status_transition("planned", "published")


# --- cancellation requires a reason ----------------------------------------


@pytest.mark.parametrize("from_status", ["published", "in_progress"])
def test_cancelling_without_a_reason_is_rejected(from_status: str) -> None:
    with pytest.raises(CancellationReasonRequiredError):
        validate_status_transition(from_status, "cancelled", cancellation_reason=None)


@pytest.mark.parametrize("from_status", ["published", "in_progress"])
def test_cancelling_with_an_empty_reason_is_rejected(from_status: str) -> None:
    with pytest.raises(CancellationReasonRequiredError):
        validate_status_transition(from_status, "cancelled", cancellation_reason="")


@pytest.mark.parametrize("from_status", ["published", "in_progress"])
def test_cancelling_with_a_reason_is_accepted(from_status: str) -> None:
    validate_status_transition(from_status, "cancelled", cancellation_reason="weather")


def test_cancellation_reason_is_irrelevant_for_a_non_cancelling_transition() -> None:
    # completed -> archived never touches cancellation_reason at all.
    validate_status_transition("completed", "archived", cancellation_reason=None)


# --- time range --------------------------------------------------------


def test_end_after_start_is_accepted() -> None:
    start = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    validate_time_range(start, end)  # must not raise


def test_end_before_start_is_rejected() -> None:
    start = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    end = start - timedelta(hours=1)
    with pytest.raises(InvalidTimeRangeError):
        validate_time_range(start, end)


def test_end_equal_to_start_is_rejected() -> None:
    start = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    with pytest.raises(InvalidTimeRangeError):
        validate_time_range(start, start)


# --- timezone ------------------------------------------------------------


@pytest.mark.parametrize("tz", ["Europe/Moscow", "UTC", "America/New_York", "Asia/Tokyo"])
def test_valid_iana_timezone_is_accepted(tz: str) -> None:
    validate_timezone(tz)  # must not raise


@pytest.mark.parametrize("tz", ["", "Not/AZone", "GMT+3", "Moscow", "UTC+3"])
def test_invalid_timezone_is_rejected(tz: str) -> None:
    with pytest.raises(InvalidTimezoneError):
        validate_timezone(tz)


# --- coordinate consistency -------------------------------------------------


def test_both_coordinates_present_is_accepted() -> None:
    validate_coordinates(55.751244, 37.618423)  # must not raise


def test_both_coordinates_absent_is_accepted() -> None:
    validate_coordinates(None, None)  # must not raise


def test_latitude_without_longitude_is_rejected() -> None:
    with pytest.raises(InconsistentCoordinatesError):
        validate_coordinates(55.751244, None)


def test_longitude_without_latitude_is_rejected() -> None:
    with pytest.raises(InconsistentCoordinatesError):
        validate_coordinates(None, 37.618423)


# --- ADR-0018 time-based lifecycle synchronization (Issue #281) -------------

_START = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
_END = datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc)


def _due(status: str, now: datetime) -> tuple[str, ...]:
    return time_based_status_transitions(status, start_at=_START, end_at=_END, now=now)


def test_published_before_start_has_nothing_due() -> None:
    assert _due("published", _START - timedelta(seconds=1)) == ()


def test_published_becomes_in_progress_when_start_at_is_reached() -> None:
    assert _due("published", _START) == ("in_progress",)
    assert _due("published", _START + timedelta(minutes=30)) == ("in_progress",)


def test_in_progress_before_end_has_nothing_due() -> None:
    assert _due("in_progress", _END - timedelta(seconds=1)) == ()


def test_in_progress_becomes_completed_when_end_at_is_reached() -> None:
    assert _due("in_progress", _END) == ("completed",)
    assert _due("in_progress", _END + timedelta(days=3)) == ("completed",)


def test_missed_published_converges_through_in_progress_to_completed() -> None:
    # Reconciliation first runs at 11:30 for a 10:00-11:00 Event.
    assert _due("published", _END + timedelta(minutes=30)) == ("in_progress", "completed")


@pytest.mark.parametrize("status", ["draft", "cancelled", "completed", "archived"])
def test_statuses_outside_time_driven_edges_are_never_changed(status: str) -> None:
    for now in (_START - timedelta(hours=1), _START, _END, _END + timedelta(days=30)):
        assert _due(status, now) == ()


def test_reapplying_after_convergence_is_idempotent() -> None:
    now = _END + timedelta(minutes=30)
    status = "published"
    for step in _due(status, now):
        validate_status_transition(status, step)
        status = step
    assert status == "completed"
    assert _due(status, now) == ()


def test_every_time_based_step_is_an_allowed_adr_0018_edge() -> None:
    now = _END + timedelta(minutes=30)
    for status in CANONICAL_EVENT_STATUSES:
        previous = status
        for step in _due(status, now):
            assert step in ALLOWED_STATUS_TRANSITIONS[previous]
            previous = step


def test_manual_early_completion_remains_an_allowed_transition() -> None:
    # Issue #281 G: in_progress -> completed is allowed before end_at.
    validate_status_transition("in_progress", "completed")
    # ...and once completed, time never moves it again.
    assert _due("completed", _START + timedelta(minutes=5)) == ()


@pytest.mark.parametrize(
    ("tz_name", "local_start", "local_end"),
    [
        ("Asia/Tokyo", datetime(2026, 10, 5, 10, 0), datetime(2026, 10, 5, 11, 0)),
        ("Europe/Moscow", datetime(2026, 10, 5, 10, 0), datetime(2026, 10, 5, 11, 0)),
        ("America/Los_Angeles", datetime(2026, 10, 5, 10, 0), datetime(2026, 10, 5, 11, 0)),
        # Spans the US DST fall-back hour (01:00-02:00 occurs twice).
        ("America/New_York", datetime(2026, 11, 1, 0, 30), datetime(2026, 11, 1, 1, 30, fold=1)),
    ],
)
def test_time_based_transitions_respect_the_event_timezone(
    tz_name: str, local_start: datetime, local_end: datetime
) -> None:
    zone = ZoneInfo(tz_name)
    start_at = local_start.replace(tzinfo=zone)
    end_at = local_end.replace(tzinfo=zone)

    def due(status: str, now: datetime) -> tuple[str, ...]:
        return time_based_status_transitions(status, start_at=start_at, end_at=end_at, now=now)

    # The same instants expressed in UTC: the Event's local wall-clock is
    # never compared against UTC "by eye".
    utc_start = start_at.astimezone(timezone.utc)
    utc_end = end_at.astimezone(timezone.utc)
    assert due("published", utc_start - timedelta(seconds=1)) == ()
    assert due("published", utc_start) == ("in_progress",)
    assert due("in_progress", utc_end - timedelta(seconds=1)) == ()
    assert due("in_progress", utc_end) == ("completed",)
    # Local wall-clock 10:30 in the Event's zone (not UTC 10:30).
    midway = start_at + (end_at - start_at) / 2
    assert due("published", midway.astimezone(timezone.utc)) == ("in_progress",)


def test_new_york_dst_fold_end_is_two_hours_after_start() -> None:
    zone = ZoneInfo("America/New_York")
    start_at = datetime(2026, 11, 1, 0, 30, tzinfo=zone)
    end_at = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=zone)
    one_hour_in = start_at.astimezone(timezone.utc) + timedelta(hours=1, minutes=30)
    # Wall-clock says 01:00 (first pass), but only 1.5h of the 2h elapsed.
    assert (
        time_based_status_transitions(
            "in_progress", start_at=start_at, end_at=end_at, now=one_hour_in
        )
        == ()
    )


@pytest.mark.parametrize("naive", ["start_at", "end_at", "now"])
def test_naive_datetimes_are_rejected(naive: str) -> None:
    kwargs = {"start_at": _START, "end_at": _END, "now": _START}
    kwargs[naive] = kwargs[naive].replace(tzinfo=None)
    with pytest.raises(ValueError):
        time_based_status_transitions("published", **kwargs)


def test_non_canonical_status_is_rejected_by_time_based_transitions() -> None:
    with pytest.raises(InvalidEventStatusError):
        time_based_status_transitions("planned", start_at=_START, end_at=_END, now=_END)
