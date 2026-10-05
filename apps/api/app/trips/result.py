"""Result of a Trip (Issue #276).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §8
(Issue #259 TD3, PO decision TH-276).

Result is the actual result of the Trip itself — 0..1, one of:

- `COMPLETED` — the route/trip was completed;
- `PARTIALLY_COMPLETED` — completed partially;
- `NOT_COMPLETED` — not completed.

It is not the Trip/Event lifecycle: `Event.status = completed` does not
set it, `Event.status = cancelled` does not determine it, and its absence
never blocks creating or completing a Trip. It is not a participant's
result nor an official/sporting result, and it is never derived from the
route, GPX, participation, Duration Classification, Geography,
TourismType, Difficulty or any other fact (§12).

Who may set it follows the Trip's ordinary `trip.manage` scope
(Administrator and Instructor, §8); callers check it — this module
never does.
"""

from typing import Optional

COMPLETED = "COMPLETED"
PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
NOT_COMPLETED = "NOT_COMPLETED"
TRIP_RESULTS: tuple[str, ...] = (COMPLETED, PARTIALLY_COMPLETED, NOT_COMPLETED)


class InvalidTripResultError(Exception):
    """Not one of the approved Result values."""


def validate_trip_result(value: Optional[str]) -> Optional[str]:
    """Return `value` if it is an approved Result or `None` (no Result)."""
    if value is not None and value not in TRIP_RESULTS:
        raise InvalidTripResultError(f"Unknown trip result {value!r}")
    return value


__all__ = [
    "COMPLETED",
    "PARTIALLY_COMPLETED",
    "NOT_COMPLETED",
    "TRIP_RESULTS",
    "InvalidTripResultError",
    "validate_trip_result",
]
