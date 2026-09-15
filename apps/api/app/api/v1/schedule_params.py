"""Shared `[from, to)` range validation for the Group Schedule and
Instructor Schedule projections (Issue #88 / TH-0083).

Mirrors `app.api.v1.events.get_calendar`'s own inline validation exactly
(missing/naive/reversed `from`/`to`, UTC normalization) — duplicated into
its own small helper rather than refactoring the existing, already-shipped
calendar route, per this Issue's own instruction not to touch code outside
its scope. Both `group-and-instructor-schedule-api.md` §1 and
`events-api.md` §16 specify the identical contract.
"""

from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Optional

from fastapi import status

from app.api.errors import APIError
from app.events.lifecycle import InvalidTimeRangeError, validate_time_range


def parse_schedule_range(
    from_: Optional[datetime], to: Optional[datetime]
) -> tuple[datetime, datetime]:
    """Validate and UTC-normalize the mandatory `from`/`to` query
    parameters. Returns `(from_utc, to_utc)`. Raises `APIError` (422) for
    a missing, naive, or reversed/equal range."""
    if from_ is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "missing_from",
            "'from' query parameter is required",
        )
    if to is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "missing_to", "'to' query parameter is required"
        )
    if from_.tzinfo is None or to.tzinfo is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "naive_timestamp",
            "'from' and 'to' must be timezone-aware RFC 3339 timestamps",
        )
    try:
        validate_time_range(from_, to)
    except InvalidTimeRangeError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_range",
            "'from' must be strictly before 'to'",
        ) from exc
    return from_.astimezone(dt_timezone.utc), to.astimezone(dt_timezone.utc)


__all__ = ["parse_schedule_range"]
