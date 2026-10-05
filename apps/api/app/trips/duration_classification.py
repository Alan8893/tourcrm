"""Duration Classification of a Trip (Issue #274).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §10
(Issue #259, TD1–TD2) and the Issue #274 PO decisions.

A Trip carries exactly one Duration Classification:

- `UNCLASSIFIED` — not set (every Trip starts with it);
- `ONE_DAY` — the planned interval does not cross midnight;
- `MULTI_DAY` — the planned interval crosses midnight (a planned
  overnight stay).

No 24-hour or other hour threshold exists. The planned interval is the
Trip's Event `start_at`/`end_at` — the only source of planned times; the
classification is a separate semantic fact, never a second duration
source and never computed or overwritten automatically.

`validate_duration_classification` is the explicit Trip-level check
(§10) run when the value is set (PO decision GAP-1): `ONE_DAY` and
`MULTI_DAY` must agree with the Event's current planned interval;
`UNCLASSIFIED` always does. A later Event re-planning neither is blocked
nor changes the stored classification.

"Crosses midnight" (PO decision GAP-2): a local midnight in the Event's
`timezone` lies strictly between `start_at` and `end_at` — an interval
that only starts or ends exactly at 00:00 does not cross it.

A plan such as 18:00 → 02:00 is not a valid children's hike (§10), but
no approved planning rule rejects it (PO decision GAP-3): the existing
Event validation (`end_at > start_at`) applies, and by the midnight rule
only `MULTI_DAY` or `UNCLASSIFIED` is consistent with it.

Callers have already checked authorization
(app.trips.official_difficulty_authorization — §10 and the roles matrix:
the Administrator sets and changes it); this module never does.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ONE_DAY = "ONE_DAY"
MULTI_DAY = "MULTI_DAY"
UNCLASSIFIED = "UNCLASSIFIED"
DURATION_CLASSIFICATIONS: tuple[str, ...] = (ONE_DAY, MULTI_DAY, UNCLASSIFIED)


class InvalidDurationClassificationError(Exception):
    """Not one of the approved values."""


class DurationClassificationMismatchError(Exception):
    """`ONE_DAY`/`MULTI_DAY` contradicts the Event's planned interval."""


def crosses_midnight(start_at: datetime, end_at: datetime, timezone: str) -> bool:
    """True iff a local midnight in `timezone` lies strictly between
    `start_at` and `end_at` (both timezone-aware)."""
    zone = ZoneInfo(timezone)
    local_start = start_at.astimezone(zone)
    next_midnight_date = local_start.date() + timedelta(days=1)
    next_midnight = datetime(
        next_midnight_date.year, next_midnight_date.month, next_midnight_date.day, tzinfo=zone
    )
    return next_midnight < end_at.astimezone(zone)


def validate_duration_classification(
    value: str, *, start_at: datetime, end_at: datetime, timezone: str
) -> str:
    """Return `value` if it is approved and consistent with the planned
    interval; raise otherwise."""
    if value not in DURATION_CLASSIFICATIONS:
        raise InvalidDurationClassificationError(f"Unknown duration classification {value!r}")
    if value == UNCLASSIFIED:
        return value
    crosses = crosses_midnight(start_at, end_at, timezone)
    if value == ONE_DAY and crosses:
        raise DurationClassificationMismatchError(
            "ONE_DAY requires a planned interval that does not cross midnight"
        )
    if value == MULTI_DAY and not crosses:
        raise DurationClassificationMismatchError(
            "MULTI_DAY requires a planned interval that crosses midnight"
        )
    return value


__all__ = [
    "ONE_DAY",
    "MULTI_DAY",
    "UNCLASSIFIED",
    "DURATION_CLASSIFICATIONS",
    "InvalidDurationClassificationError",
    "DurationClassificationMismatchError",
    "crosses_midnight",
    "validate_duration_classification",
]
