"""Pure-Python unit tests for the Issue #36 Event lifecycle/data-integrity
domain module — no database, no HTTP. Real PostgreSQL CHECK constraints
(event_type/status validity, end_at > start_at, cancellation reason,
coordinate consistency) and FK integrity are covered separately in
tests/integration/test_events.py.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.events import CANONICAL_EVENT_STATUSES, CANONICAL_EVENT_TYPES
from app.events.lifecycle import (
    CancellationReasonRequiredError,
    InconsistentCoordinatesError,
    InvalidEventStatusError,
    InvalidEventStatusTransitionError,
    InvalidEventTypeError,
    InvalidTimeRangeError,
    InvalidTimezoneError,
    validate_coordinates,
    validate_event_type,
    validate_status,
    validate_status_transition,
    validate_time_range,
    validate_timezone,
)

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
