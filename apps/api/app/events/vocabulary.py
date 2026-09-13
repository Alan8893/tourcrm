"""Canonical Event type/status vocabulary (Issue #36).

Pure Python — no ORM/persistence import (no `app.db.*`) and no FastAPI
import. This is the single source of truth for the canonical
vocabularies: app.db.events (persistence) depends on this module to
build its CHECK constraints; app.events.lifecycle (domain validation)
depends on it too. Neither app.events.lifecycle nor this module may ever
import from app.db.* — persistence depends on domain here, never the
reverse.

Canonical sources: docs/04-modules/events-and-schedule.md §3 (event
types), docs/03-architecture/adr/ADR-0018-event-lifecycle.md (statuses).
`planned` is neither a type nor a status.
"""

CANONICAL_EVENT_TYPES = (
    "lesson",
    "training",
    "trip",
    "competition",
    "tour_slet",
    "excursion",
    "meeting",
    "other",
)

CANONICAL_EVENT_STATUSES = (
    "draft",
    "published",
    "in_progress",
    "completed",
    "cancelled",
    "archived",
)

__all__ = ["CANONICAL_EVENT_TYPES", "CANONICAL_EVENT_STATUSES"]
