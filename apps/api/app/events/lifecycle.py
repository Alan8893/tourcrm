"""Event lifecycle and data-integrity validation (Issue #36).

Canonical sources: docs/03-architecture/adr/ADR-0018-event-lifecycle.md
(canonical statuses and allowed transitions), docs/02-requirements/
business-rules.md §10.2 (cancellation requires a reason), docs/04-modules/
events-and-schedule.md §5/§6/§26 (time range, timezone, coordinate
consistency).

Pure Python — no FastAPI import, no database session, no SQLAlchemy/ORM
import of any kind (not even `app.db.events`, only the equally-pure
`app.events.vocabulary`) — so this is testable and reusable in complete
isolation from persistence, not just from HTTP. The dependency direction
is deliberate: `app.db.events` (persistence) is allowed to import from
this module (it does, for `validate_timezone` — see below), but this
module and `app.events.vocabulary` must never import anything from
`app.db.*`, to keep domain code independent of the persistence
implementation it happens to be enforced through today. This mirrors
app.authorization.service/app.authentication.service's separation from
FastAPI, taken one step further to also exclude the ORM.

This module does not implement or perform authorization: it only decides
whether a *value* or *transition* is valid per the canonical documents
above, never who is allowed to apply it.

A `Event.status`/`Event.event_type` CHECK constraint (app.db.events)
already rejects an invalid value at the database boundary. A *transition*
(old status -> new status) cannot be expressed as a single-row CHECK
constraint, which is why it is validated here instead (ADR-0003: business
invariants that cannot be expressed as constraints are checked at the
application/domain layer). The same applies to an IANA timezone name,
which Postgres CHECK constraints cannot validate at all — `app.db.events`
instead calls this module's `validate_timezone` from a SQLAlchemy
`@validates` hook, so an invalid timezone is rejected on the actual
persistence path, not only when a caller remembers to invoke this
function directly.
"""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.events.vocabulary import CANONICAL_EVENT_STATUSES, CANONICAL_EVENT_TYPES

# ADR-0018's allowed transition graph. Any pair not listed here is
# rejected, including a status transitioning to itself and `planned`,
# which is not a canonical status at all.
ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"published"}),
    "published": frozenset({"in_progress", "cancelled"}),
    "in_progress": frozenset({"completed", "cancelled"}),
    "completed": frozenset({"archived"}),
    "cancelled": frozenset({"archived"}),
    "archived": frozenset(),
}


class EventDomainError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidEventTypeError(EventDomainError):
    """`value` is not one of CANONICAL_EVENT_TYPES."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical event type")
        self.value = value


class InvalidEventStatusError(EventDomainError):
    """`value` is not one of CANONICAL_EVENT_STATUSES (this also rejects
    `planned`, which ADR-0018 explicitly says is not a separate status)."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical event status")
        self.value = value


class InvalidEventStatusTransitionError(EventDomainError):
    """ADR-0018 does not allow this (from_status -> to_status) transition."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"{from_status!r} -> {to_status!r} is not an allowed transition")
        self.from_status = from_status
        self.to_status = to_status


class CancellationReasonRequiredError(EventDomainError):
    """business-rules.md §10.2: cancellation requires a reason."""


class InvalidTimeRangeError(EventDomainError):
    """events-and-schedule.md §26: `end_at` must be after `start_at`."""


class InvalidTimezoneError(EventDomainError):
    """`value` is not a resolvable IANA timezone identifier."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a valid IANA timezone")
        self.value = value


class InconsistentCoordinatesError(EventDomainError):
    """events-and-schedule.md §5: latitude/longitude must both be present
    or both be absent — never only one."""


def validate_event_type(value: str) -> None:
    if value not in CANONICAL_EVENT_TYPES:
        raise InvalidEventTypeError(value)


def validate_status(value: str) -> None:
    if value not in CANONICAL_EVENT_STATUSES:
        raise InvalidEventStatusError(value)


def validate_status_transition(
    current_status: str, new_status: str, *, cancellation_reason: str | None = None
) -> None:
    """Validate a `current_status -> new_status` transition per ADR-0018.

    Raises InvalidEventStatusError if either status is not canonical,
    InvalidEventStatusTransitionError if the transition itself is not one
    of ADR-0018's allowed edges, and CancellationReasonRequiredError if the
    transition targets `cancelled` without a reason.
    """
    validate_status(current_status)
    validate_status(new_status)
    if new_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidEventStatusTransitionError(current_status, new_status)
    if new_status == "cancelled" and not cancellation_reason:
        raise CancellationReasonRequiredError(
            "cancellation_reason is required when transitioning to 'cancelled'"
        )


def validate_time_range(start_at: datetime, end_at: datetime) -> None:
    if not end_at > start_at:
        raise InvalidTimeRangeError("end_at must be after start_at")


def validate_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise InvalidTimezoneError(value) from None


def validate_coordinates(latitude: float | None, longitude: float | None) -> None:
    if (latitude is None) != (longitude is None):
        raise InconsistentCoordinatesError(
            "location_latitude and location_longitude must both be set or both be absent"
        )


__all__ = [
    "ALLOWED_STATUS_TRANSITIONS",
    "EventDomainError",
    "InvalidEventTypeError",
    "InvalidEventStatusError",
    "InvalidEventStatusTransitionError",
    "CancellationReasonRequiredError",
    "InvalidTimeRangeError",
    "InvalidTimezoneError",
    "InconsistentCoordinatesError",
    "validate_event_type",
    "validate_status",
    "validate_status_transition",
    "validate_time_range",
    "validate_timezone",
    "validate_coordinates",
]
