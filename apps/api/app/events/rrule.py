"""RFC 5545-compatible recurrence rule building, validation and expansion
for EventSeries (Issue #79, ADR-0028 §8/§9).

Pure Python — no ORM/FastAPI import, no database session — mirrors
app.events.lifecycle's separation from persistence. Uses `dateutil.rrule`
for calendar arithmetic (ADR-0028 §14 leaves the exact parser library as an
implementation detail).

Two responsibilities, kept in this one small module since they share the
same structured-input vocabulary:

1. `build_canonical_rrule()` — turns validated, structured recurrence
   input (never raw client-supplied RRULE text — ADR-0028 §8: "Arbitrary
   raw RRULE entry is not an MVP UI capability") into the canonical
   persisted RRULE string. `UNTIL` is deliberately never written into that
   string; an input `until` is normalized into a returned `series_end_at`
   instead (ADR-0028 §8).
2. `expand_occurrences()` — given a series version's persisted
   `recurrence_rule` + `series_start_at` + `timezone` (+ optional
   `series_end_at`/`occurrence_limit` caps), deterministically generates
   the ordered sequence of occurrence anchor instants for a bounded
   window. Never uses wall-clock execution time as an input to identity —
   the same (rule, start, timezone, window) always yields the same
   sequence (ADR-0028 §9/§10; database-schema-recurrence.md "Materialization
   idempotency key").

Recurrence math runs in the series' own IANA timezone using naive
wall-clock arithmetic (so "every Monday at 18:00" stays 18:00 local
across a DST transition, matching ordinary human scheduling intent), then
each generated instant is localized back to that timezone before being
returned as a timezone-aware `datetime`. Ambiguous/nonexistent local times
at a DST fold/gap use Python's default `fold=0` disambiguation — exact
DST-fold resolution is deliberately left simple, matching ADR-0028 §14's
"exact locking statement and transaction isolation level ... are
implementation details" latitude for other MVP internals.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY
from dateutil.rrule import rrule as dateutil_rrule
from dateutil.rrule import weekday as dateutil_weekday

from app.events.series_vocabulary import CANONICAL_BYDAY_CODES, CANONICAL_RECURRENCE_FREQUENCIES

_FREQ_MAP = {
    "DAILY": DAILY,
    "WEEKLY": WEEKLY,
    "MONTHLY": MONTHLY,
    "YEARLY": YEARLY,
}

# RFC 5545 weekday code -> dateutil weekday constant (Monday=0).
_BYDAY_MAP = {
    "MO": dateutil_weekday(0),
    "TU": dateutil_weekday(1),
    "WE": dateutil_weekday(2),
    "TH": dateutil_weekday(3),
    "FR": dateutil_weekday(4),
    "SA": dateutil_weekday(5),
    "SU": dateutil_weekday(6),
}


class RecurrenceError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidRecurrenceFrequencyError(RecurrenceError):
    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a supported FREQ value")
        self.value = value


class InvalidRecurrenceIntervalError(RecurrenceError):
    """INTERVAL must be a positive integer."""


class InvalidRecurrenceByDayError(RecurrenceError):
    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a supported BYDAY code")
        self.value = value


class InvalidRecurrenceByMonthDayError(RecurrenceError):
    """BYMONTHDAY entries must be integers in 1..31."""


class InvalidRecurrenceByMonthError(RecurrenceError):
    """BYMONTH entries must be integers in 1..12."""


class InvalidRecurrenceCountError(RecurrenceError):
    """COUNT must be a positive integer."""


class InconsistentRecurrenceTerminationError(RecurrenceError):
    """`series_end_at` was supplied directly and also implied by `until`,
    and the two values disagree."""


class InvalidRecurrenceTimezoneError(RecurrenceError):
    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a valid IANA timezone")
        self.value = value


@dataclass(frozen=True)
class RecurrenceInput:
    """Structured recurrence input (ADR-0028 §8/§6 "Series creation").

    Never a raw RRULE string. `until`, if given, is input-only and is
    normalized into `series_end_at` by `build_canonical_rrule` — it is
    never itself persisted.
    """

    frequency: str
    interval: int = 1
    by_day: tuple[str, ...] = ()
    by_month_day: tuple[int, ...] = ()
    by_month: tuple[int, ...] = ()
    count: int | None = None
    until: datetime | None = None


def _validate_recurrence_input(recurrence: RecurrenceInput) -> None:
    if recurrence.frequency not in CANONICAL_RECURRENCE_FREQUENCIES:
        raise InvalidRecurrenceFrequencyError(recurrence.frequency)
    if recurrence.interval < 1:
        raise InvalidRecurrenceIntervalError("interval must be a positive integer")
    for code in recurrence.by_day:
        if code not in CANONICAL_BYDAY_CODES:
            raise InvalidRecurrenceByDayError(code)
    for day in recurrence.by_month_day:
        if not (1 <= day <= 31):
            raise InvalidRecurrenceByMonthDayError("by_month_day entries must be in 1..31")
    for month in recurrence.by_month:
        if not (1 <= month <= 12):
            raise InvalidRecurrenceByMonthError("by_month entries must be in 1..12")
    if recurrence.count is not None and recurrence.count < 1:
        raise InvalidRecurrenceCountError("count must be a positive integer")


def build_canonical_rrule(
    recurrence: RecurrenceInput, *, series_end_at: datetime | None = None
) -> tuple[str, datetime | None]:
    """Validate structured recurrence input and build the canonical
    persisted RRULE string (never containing `UNTIL`).

    Returns `(canonical_rrule, effective_series_end_at)`. `until`, if
    given, is normalized into the returned `series_end_at`; if the caller
    also passed an explicit `series_end_at` that disagrees, raises
    InconsistentRecurrenceTerminationError and persists nothing.
    """
    _validate_recurrence_input(recurrence)

    effective_series_end_at = series_end_at
    if recurrence.until is not None:
        if series_end_at is not None and series_end_at != recurrence.until:
            raise InconsistentRecurrenceTerminationError(
                "series_end_at and recurrence.until were both supplied and disagree"
            )
        effective_series_end_at = recurrence.until

    parts = [f"FREQ={recurrence.frequency}"]
    if recurrence.interval != 1:
        parts.append(f"INTERVAL={recurrence.interval}")
    if recurrence.by_day:
        parts.append(f"BYDAY={','.join(recurrence.by_day)}")
    if recurrence.by_month_day:
        parts.append(f"BYMONTHDAY={','.join(str(d) for d in recurrence.by_month_day)}")
    if recurrence.by_month:
        parts.append(f"BYMONTH={','.join(str(m) for m in recurrence.by_month)}")
    if recurrence.count is not None:
        parts.append(f"COUNT={recurrence.count}")
    return ";".join(parts), effective_series_end_at


def _parse_canonical_rrule(canonical_rrule: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in canonical_rrule.split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        fields[key] = value
    return fields


def expand_occurrences(
    *,
    canonical_rrule: str,
    series_start_at: datetime,
    timezone: str,
    window_end: datetime,
    series_end_at: datetime | None = None,
    occurrence_limit: int | None = None,
    already_generated_count: int = 0,
) -> list[datetime]:
    """Deterministically expand `canonical_rrule` into the ordered list of
    occurrence anchor instants in `(series_start_at, window_end]` — i.e.
    the *next* occurrences after whatever has already been materialized,
    bounded by whichever of `series_end_at`/`occurrence_limit` is reached
    first (ADR-0028 §8: "The first reached limit terminates generation").

    `already_generated_count` is the count of occurrences already
    materialized for this series version (so a persisted `occurrence_limit`
    is enforced across repeated/incremental calls, not just within one
    call) — callers doing on-demand horizon extension must pass the
    correct running total.

    Never uses the current wall-clock time as an input: the same
    arguments always produce the same sequence.
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InvalidRecurrenceTimezoneError(timezone) from None

    fields = _parse_canonical_rrule(canonical_rrule)
    freq = _FREQ_MAP.get(fields.get("FREQ", ""))
    if freq is None:
        raise InvalidRecurrenceFrequencyError(fields.get("FREQ", ""))
    interval = int(fields.get("INTERVAL", "1"))
    byweekday = (
        [_BYDAY_MAP[code] for code in fields["BYDAY"].split(",")] if "BYDAY" in fields else None
    )
    bymonthday = (
        [int(d) for d in fields["BYMONTHDAY"].split(",")] if "BYMONTHDAY" in fields else None
    )
    bymonth = [int(m) for m in fields["BYMONTH"].split(",")] if "BYMONTH" in fields else None
    rule_count = int(fields["COUNT"]) if "COUNT" in fields else None

    remaining_by_limit: int | None = None
    if occurrence_limit is not None:
        remaining_by_limit = max(occurrence_limit - already_generated_count, 0)
        if remaining_by_limit == 0:
            return []

    # Wall-clock/naive arithmetic in the series' own timezone (see module
    # docstring), then localize each generated instant.
    dtstart_local = series_start_at.astimezone(zone).replace(tzinfo=None)
    until_local = None
    if series_end_at is not None:
        until_local = series_end_at.astimezone(zone).replace(tzinfo=None)
    window_end_local = window_end.astimezone(zone).replace(tzinfo=None)
    effective_until_local = (
        min(until_local, window_end_local) if until_local is not None else window_end_local
    )

    # rule_count is the RRULE's OWN termination count from the series
    # start, so it must be applied to the full generated sequence, not
    # just the remaining window — request the rule's full COUNT (or
    # unbounded, letting `until`/window bound it) and slice afterward.
    generator = dateutil_rrule(
        freq,
        dtstart=dtstart_local,
        interval=interval,
        byweekday=byweekday,
        bymonthday=bymonthday,
        bymonth=bymonth,
        count=rule_count,
        until=None if rule_count is not None else effective_until_local,
    )

    results: list[datetime] = []
    for index, naive_local in enumerate(generator):
        if rule_count is not None and naive_local > effective_until_local:
            break
        if index < already_generated_count:
            continue
        aware = naive_local.replace(tzinfo=zone)
        results.append(aware.astimezone(series_start_at.tzinfo or aware.tzinfo))
        if remaining_by_limit is not None and len(results) >= remaining_by_limit:
            break

    return results


__all__ = [
    "RecurrenceError",
    "InvalidRecurrenceFrequencyError",
    "InvalidRecurrenceIntervalError",
    "InvalidRecurrenceByDayError",
    "InvalidRecurrenceByMonthDayError",
    "InvalidRecurrenceByMonthError",
    "InvalidRecurrenceCountError",
    "InconsistentRecurrenceTerminationError",
    "InvalidRecurrenceTimezoneError",
    "RecurrenceInput",
    "build_canonical_rrule",
    "expand_occurrences",
]
