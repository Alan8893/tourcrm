"""EventSeries/EventOccurrence/EventOccurrenceException lifecycle and
data-integrity validation (Issue #79, ADR-0028 §6/§7).

Pure Python — no FastAPI import, no database session, no SQLAlchemy/ORM
import — mirrors app.events.lifecycle's isolation from persistence
exactly. `app.db.event_recurrence` is allowed to import from this module
(for `validate_timezone`); this module never imports from `app.db.*`.

This module does not implement or perform authorization: it only decides
whether a value or transition is valid per ADR-0028, never who is allowed
to apply it.
"""

from app.events.series_vocabulary import (
    ALLOWED_OCCURRENCE_OVERRIDE_FIELDS,
    CANONICAL_EXCEPTION_TYPES,
    CANONICAL_OCCURRENCE_STATUSES,
    CANONICAL_SERIES_STATUSES,
)

# ADR-0028 §6: active <-> paused, active -> cancelled -> archived. No other
# transition — including no direct paused -> cancelled/archived, and no
# transition at all out of `archived`.
ALLOWED_SERIES_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"paused", "cancelled"}),
    "paused": frozenset({"active"}),
    "cancelled": frozenset({"archived"}),
    "archived": frozenset(),
}

# ADR-0028 §7: scheduled -> in_progress -> completed, or scheduled ->
# cancelled. `completed`/`cancelled` are terminal.
ALLOWED_OCCURRENCE_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "scheduled": frozenset({"in_progress", "cancelled"}),
    "in_progress": frozenset({"completed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


class EventSeriesDomainError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidSeriesStatusError(EventSeriesDomainError):
    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical EventSeries status")
        self.value = value


class InvalidSeriesStatusTransitionError(EventSeriesDomainError):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"{from_status!r} -> {to_status!r} is not an allowed series transition")
        self.from_status = from_status
        self.to_status = to_status


class InvalidOccurrenceStatusError(EventSeriesDomainError):
    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical EventOccurrence status")
        self.value = value


class InvalidOccurrenceStatusTransitionError(EventSeriesDomainError):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"{from_status!r} -> {to_status!r} is not an allowed occurrence transition"
        )
        self.from_status = from_status
        self.to_status = to_status


class OccurrenceCancellationReasonRequiredError(EventSeriesDomainError):
    """ADR-0028 §5/§7: cancelling an occurrence (directly or via a
    `cancelled` exception) requires a reason."""


class CancelledOccurrenceCannotBeBoundaryError(EventSeriesDomainError):
    """ADR-0028 §3: "A cancelled occurrence cannot be selected as the
    boundary; the caller must select the next scheduled occurrence."""


class InvalidBoundaryOccurrenceStatusError(EventSeriesDomainError):
    """ADR-0028 §3: a "this and following" boundary must be a future
    `scheduled` occurrence — `scheduled` is the *only* eligible status.
    `cancelled` is rejected via the more specific
    CancelledOccurrenceCannotBeBoundaryError above; this covers every
    other non-`scheduled` status (`in_progress`/`completed`), which are
    equally ineligible even though they are not terminal-by-cancellation:
    an occurrence that has already started or finished can no longer be
    the first occurrence of a new, still-`scheduled` version chain."""

    def __init__(self, *, status: str) -> None:
        super().__init__(
            f"{status!r} occurrence cannot be the boundary for a new Series version — "
            "only a 'scheduled' occurrence is eligible"
        )
        self.status = status


class OccurrenceNotEligibleForRescheduleError(EventSeriesDomainError):
    """ADR-0028 §5/§7: a reschedule exception may only be applied to a
    `scheduled` occurrence. `completed` and `cancelled` are terminal
    (ADR-0018-style terminal-state protection, carried over to the
    occurrence lifecycle) — rescheduling one would either resurrect a
    terminal occurrence's schedule with no corresponding status change,
    or silently overwrite a `cancelled` occurrence's own exception
    record while leaving `status="cancelled"`, neither of which is a
    valid state."""

    def __init__(self, *, current_status: str) -> None:
        super().__init__(
            f"{current_status!r} occurrence cannot be rescheduled — only a "
            "'scheduled' occurrence is eligible"
        )
        self.current_status = current_status


class InvalidExceptionTypeError(EventSeriesDomainError):
    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical exception_type")
        self.value = value


class UnknownOverrideFieldError(EventSeriesDomainError):
    """ADR-0028 §5: `overrides` contains a key outside the allow-list."""

    def __init__(self, field: str) -> None:
        super().__init__(f"{field!r} is not an allow-listed occurrence override field")
        self.field = field


def validate_series_status(value: str) -> None:
    if value not in CANONICAL_SERIES_STATUSES:
        raise InvalidSeriesStatusError(value)


def validate_series_status_transition(current_status: str, new_status: str) -> None:
    """ADR-0028 §6. Raises InvalidSeriesStatusError if either value is not
    canonical, InvalidSeriesStatusTransitionError if the transition itself
    is not one of the allowed edges.
    """
    validate_series_status(current_status)
    validate_series_status(new_status)
    if new_status not in ALLOWED_SERIES_STATUS_TRANSITIONS[current_status]:
        raise InvalidSeriesStatusTransitionError(current_status, new_status)


def validate_occurrence_status(value: str) -> None:
    if value not in CANONICAL_OCCURRENCE_STATUSES:
        raise InvalidOccurrenceStatusError(value)


def validate_occurrence_status_transition(
    current_status: str, new_status: str, *, cancellation_reason: str | None = None
) -> None:
    """ADR-0028 §7. Raises InvalidOccurrenceStatusError,
    InvalidOccurrenceStatusTransitionError, or
    OccurrenceCancellationReasonRequiredError (persisting nothing in every
    case)."""
    validate_occurrence_status(current_status)
    validate_occurrence_status(new_status)
    if new_status not in ALLOWED_OCCURRENCE_STATUS_TRANSITIONS[current_status]:
        raise InvalidOccurrenceStatusTransitionError(current_status, new_status)
    if new_status == "cancelled" and not cancellation_reason:
        raise OccurrenceCancellationReasonRequiredError(
            "cancellation_reason is required when cancelling an occurrence"
        )


def validate_boundary_occurrence_status(status: str) -> None:
    """ADR-0028 §3: only a `scheduled` occurrence may be selected as a
    "this and following" boundary — `in_progress` and `completed` are
    rejected exactly like `cancelled`, not merely "not cancelled"."""
    validate_occurrence_status(status)
    if status == "cancelled":
        raise CancelledOccurrenceCannotBeBoundaryError(
            "A cancelled occurrence cannot be the boundary for a new Series version"
        )
    if status != "scheduled":
        raise InvalidBoundaryOccurrenceStatusError(status=status)


def validate_occurrence_reschedule_eligibility(status: str) -> None:
    """ADR-0028 §5/§7: only a `scheduled` occurrence may receive a
    `rescheduled` exception. Raises OccurrenceNotEligibleForRescheduleError
    (persisting nothing) for `completed`/`cancelled` — including an
    occurrence that already carries a `cancelled` exception, since that
    always implies `status == "cancelled"` here (the two are kept in
    sync by app.events.series_service.set_occurrence_exception)."""
    validate_occurrence_status(status)
    if status != "scheduled":
        raise OccurrenceNotEligibleForRescheduleError(current_status=status)


def validate_exception_type(value: str) -> None:
    if value not in CANONICAL_EXCEPTION_TYPES:
        raise InvalidExceptionTypeError(value)


def validate_exception_cancellation_reason(
    exception_type: str, cancellation_reason: str | None
) -> None:
    if exception_type == "cancelled" and not cancellation_reason:
        raise OccurrenceCancellationReasonRequiredError(
            "cancellation_reason is required when exception_type is 'cancelled'"
        )


def validate_occurrence_overrides(overrides: dict | None) -> None:
    """ADR-0028 §5: `overrides` contains only an explicitly allow-listed
    subset of fields. Raises UnknownOverrideFieldError (persisting
    nothing) for any other key. Value-level domain validation (event_type
    vocabulary, non-empty name, etc.) is the caller's responsibility,
    reusing the same functions app.events.lifecycle already provides for
    ordinary Event updates — never re-implemented or relaxed here.
    """
    if not overrides:
        return
    unknown_fields = set(overrides) - ALLOWED_OCCURRENCE_OVERRIDE_FIELDS
    if unknown_fields:
        raise UnknownOverrideFieldError(sorted(unknown_fields)[0])


__all__ = [
    "ALLOWED_SERIES_STATUS_TRANSITIONS",
    "ALLOWED_OCCURRENCE_STATUS_TRANSITIONS",
    "EventSeriesDomainError",
    "InvalidSeriesStatusError",
    "InvalidSeriesStatusTransitionError",
    "InvalidOccurrenceStatusError",
    "InvalidOccurrenceStatusTransitionError",
    "OccurrenceCancellationReasonRequiredError",
    "CancelledOccurrenceCannotBeBoundaryError",
    "InvalidBoundaryOccurrenceStatusError",
    "OccurrenceNotEligibleForRescheduleError",
    "InvalidExceptionTypeError",
    "UnknownOverrideFieldError",
    "validate_series_status",
    "validate_series_status_transition",
    "validate_occurrence_status",
    "validate_occurrence_status_transition",
    "validate_boundary_occurrence_status",
    "validate_occurrence_reschedule_eligibility",
    "validate_exception_type",
    "validate_exception_cancellation_reason",
    "validate_occurrence_overrides",
]
