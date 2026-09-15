"""Canonical vocabularies for the EventSeries/EventOccurrence recurrence
domain (Issue #79, ADR-0028).

Pure Python — no ORM/FastAPI import — mirrors app.events.vocabulary's role
for the base Event domain: the persistence layer (app.db.event_recurrence)
and the domain layer (app.events.series_lifecycle) both import from here
rather than duplicating the same literal sets.

Canonical source: docs/03-architecture/adr/ADR-0028-event-recurrence-
persistence-and-versioning.md.
"""

CANONICAL_SERIES_STATUSES = (
    "active",
    "paused",
    "cancelled",
    "archived",
)

CANONICAL_OCCURRENCE_STATUSES = (
    "scheduled",
    "in_progress",
    "completed",
    "cancelled",
)

CANONICAL_EXCEPTION_TYPES = (
    "rescheduled",
    "cancelled",
)

# ADR-0028 §5 / database-schema-recurrence.md §3: `overrides` is a strictly
# allow-listed subset of Event fields, validated by the same domain rules as
# ordinary Event updates. EventOccurrence's own canonical physical field
# list (database-schema-recurrence.md §2) has no location columns at all,
# so only the fields EventOccurrence actually persists as a snapshot can
# ever be overridden here — there is nowhere to store anything else.
ALLOWED_OCCURRENCE_OVERRIDE_FIELDS = frozenset({"name", "description", "event_type"})

# ADR-0028 §8 (RFC 5545-compatible MVP vocabulary).
CANONICAL_RECURRENCE_FREQUENCIES = (
    "DAILY",
    "WEEKLY",
    "MONTHLY",
    "YEARLY",
)

# RFC 5545 two-letter weekday codes accepted in BYDAY.
CANONICAL_BYDAY_CODES = (
    "MO",
    "TU",
    "WE",
    "TH",
    "FR",
    "SA",
    "SU",
)

__all__ = [
    "CANONICAL_SERIES_STATUSES",
    "CANONICAL_OCCURRENCE_STATUSES",
    "CANONICAL_EXCEPTION_TYPES",
    "ALLOWED_OCCURRENCE_OVERRIDE_FIELDS",
    "CANONICAL_RECURRENCE_FREQUENCIES",
    "CANONICAL_BYDAY_CODES",
]
