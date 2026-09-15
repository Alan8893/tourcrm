"""Pure-Python unit tests for the Issue #79 EventSeries/EventOccurrence/
EventOccurrenceException lifecycle domain module (app.events.
series_lifecycle) — no database, no HTTP. Real PostgreSQL CHECK/unique
constraints are covered separately in tests/integration/
test_event_recurrence.py.
"""

import pytest

from app.events.series_lifecycle import (
    ALLOWED_OCCURRENCE_STATUS_TRANSITIONS,
    ALLOWED_SERIES_STATUS_TRANSITIONS,
    CancelledOccurrenceCannotBeBoundaryError,
    InvalidBoundaryOccurrenceStatusError,
    InvalidExceptionTypeError,
    InvalidOccurrenceStatusError,
    InvalidOccurrenceStatusTransitionError,
    InvalidSeriesStatusError,
    InvalidSeriesStatusTransitionError,
    OccurrenceCancellationReasonRequiredError,
    UnknownOverrideFieldError,
    validate_boundary_occurrence_status,
    validate_exception_cancellation_reason,
    validate_exception_type,
    validate_occurrence_overrides,
    validate_occurrence_status_transition,
    validate_series_status_transition,
)
from app.events.series_vocabulary import CANONICAL_OCCURRENCE_STATUSES, CANONICAL_SERIES_STATUSES

# --- Series lifecycle (ADR-0028 §6): active <-> paused, active -> cancelled -> archived


def test_active_to_paused_is_allowed() -> None:
    validate_series_status_transition("active", "paused")


def test_paused_to_active_is_allowed() -> None:
    validate_series_status_transition("paused", "active")


def test_active_to_cancelled_is_allowed() -> None:
    validate_series_status_transition("active", "cancelled")


def test_cancelled_to_archived_is_allowed() -> None:
    validate_series_status_transition("cancelled", "archived")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("paused", "cancelled"),  # must go through active first
        ("paused", "archived"),
        ("cancelled", "active"),
        ("cancelled", "paused"),
        ("archived", "active"),
        ("archived", "cancelled"),
        ("active", "archived"),  # must go through cancelled first
        ("active", "active"),
    ],
)
def test_disallowed_series_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(InvalidSeriesStatusTransitionError):
        validate_series_status_transition(current, target)


def test_invalid_series_status_is_rejected() -> None:
    with pytest.raises(InvalidSeriesStatusError):
        validate_series_status_transition("planned", "active")


def test_every_canonical_series_status_appears_in_the_transition_table() -> None:
    assert set(ALLOWED_SERIES_STATUS_TRANSITIONS) == set(CANONICAL_SERIES_STATUSES)


def test_archived_is_terminal_for_series() -> None:
    assert ALLOWED_SERIES_STATUS_TRANSITIONS["archived"] == frozenset()


# --- Occurrence lifecycle (ADR-0028 §7): scheduled -> in_progress -> completed,
# scheduled -> cancelled


def test_scheduled_to_in_progress_is_allowed() -> None:
    validate_occurrence_status_transition("scheduled", "in_progress")


def test_in_progress_to_completed_is_allowed() -> None:
    validate_occurrence_status_transition("in_progress", "completed")


def test_scheduled_to_cancelled_requires_a_reason() -> None:
    with pytest.raises(OccurrenceCancellationReasonRequiredError):
        validate_occurrence_status_transition("scheduled", "cancelled")
    validate_occurrence_status_transition("scheduled", "cancelled", cancellation_reason="weather")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("scheduled", "completed"),  # must pass through in_progress
        ("in_progress", "cancelled"),  # cancellation only from scheduled
        ("in_progress", "scheduled"),
        ("completed", "cancelled"),
        ("completed", "scheduled"),
        ("cancelled", "scheduled"),
        ("cancelled", "in_progress"),
    ],
)
def test_disallowed_occurrence_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(InvalidOccurrenceStatusTransitionError):
        validate_occurrence_status_transition(current, target, cancellation_reason="x")


def test_invalid_occurrence_status_is_rejected() -> None:
    with pytest.raises(InvalidOccurrenceStatusError):
        validate_occurrence_status_transition("pending", "scheduled")


def test_completed_and_cancelled_are_terminal_for_occurrence() -> None:
    assert ALLOWED_OCCURRENCE_STATUS_TRANSITIONS["completed"] == frozenset()
    assert ALLOWED_OCCURRENCE_STATUS_TRANSITIONS["cancelled"] == frozenset()


def test_every_canonical_occurrence_status_appears_in_the_transition_table() -> None:
    assert set(ALLOWED_OCCURRENCE_STATUS_TRANSITIONS) == set(CANONICAL_OCCURRENCE_STATUSES)


# --- Boundary occurrence validation (ADR-0028 §3) ---------------------------


def test_scheduled_occurrence_can_be_a_boundary() -> None:
    validate_boundary_occurrence_status("scheduled")


def test_cancelled_occurrence_cannot_be_a_boundary() -> None:
    with pytest.raises(CancelledOccurrenceCannotBeBoundaryError):
        validate_boundary_occurrence_status("cancelled")


def test_in_progress_occurrence_cannot_be_a_boundary() -> None:
    with pytest.raises(InvalidBoundaryOccurrenceStatusError):
        validate_boundary_occurrence_status("in_progress")


def test_completed_occurrence_cannot_be_a_boundary() -> None:
    with pytest.raises(InvalidBoundaryOccurrenceStatusError):
        validate_boundary_occurrence_status("completed")


def test_scheduled_is_the_only_status_accepted_as_a_boundary() -> None:
    for status in CANONICAL_OCCURRENCE_STATUSES:
        if status == "scheduled":
            validate_boundary_occurrence_status(status)  # must not raise
        else:
            expected = (
                CancelledOccurrenceCannotBeBoundaryError,
                InvalidBoundaryOccurrenceStatusError,
            )
            with pytest.raises(expected):
                validate_boundary_occurrence_status(status)


# --- Exceptions (ADR-0028 §5) -----------------------------------------------


@pytest.mark.parametrize("value", ["rescheduled", "cancelled"])
def test_canonical_exception_types_are_accepted(value: str) -> None:
    validate_exception_type(value)


def test_non_canonical_exception_type_is_rejected() -> None:
    with pytest.raises(InvalidExceptionTypeError):
        validate_exception_type("modified")


def test_cancelled_exception_requires_a_reason() -> None:
    with pytest.raises(OccurrenceCancellationReasonRequiredError):
        validate_exception_cancellation_reason("cancelled", None)
    validate_exception_cancellation_reason("cancelled", "weather")


def test_rescheduled_exception_does_not_require_a_reason() -> None:
    validate_exception_cancellation_reason("rescheduled", None)


def test_allow_listed_override_fields_are_accepted() -> None:
    validate_occurrence_overrides({"name": "New name", "description": "x", "event_type": "lesson"})


def test_empty_or_none_overrides_are_accepted() -> None:
    validate_occurrence_overrides(None)
    validate_occurrence_overrides({})


def test_unknown_override_field_is_rejected() -> None:
    with pytest.raises(UnknownOverrideFieldError):
        validate_occurrence_overrides({"location_name": "New hall"})


def test_unknown_override_field_is_rejected_even_alongside_valid_ones() -> None:
    with pytest.raises(UnknownOverrideFieldError):
        validate_occurrence_overrides({"name": "ok", "status": "cancelled"})
