"""Canonical Participant Export filter vocabularies (TH-0118.5 / PR #226,
PO decision on the `participation_status` GAP).

`participation_status` filters `EventParticipation.registration_status`.
That column is an unconstrained string (ADR-0020 §4 keeps its older values
as reference only), so the export filter gets its own closed, canonical
list for the current MVP: exactly the statuses the implemented
self-registration workflow writes (ADR-0037 §6) — `registered` and
`cancelled`, taken from app.events.participation so the two can never
drift apart. Extending the list (e.g. once admin status management,
events-api.md §21, is implemented) requires a new PO decision.

The backend is the only source of these values and of their display
labels: `GET /memberships/exports/filters` publishes them for the frontend,
and `POST /memberships/exports` rejects any other value with 422
`invalid_participation_status`.
"""

from dataclasses import dataclass
from typing import Final

from app.events.participation import CANCELLED_STATUS, REGISTERED_STATUS


@dataclass(frozen=True)
class FilterOption:
    value: str
    label: str


PARTICIPATION_STATUS_OPTIONS: Final[tuple[FilterOption, ...]] = (
    FilterOption(REGISTERED_STATUS, "Зарегистрирован"),
    FilterOption(CANCELLED_STATUS, "Регистрация отменена"),
)

CANONICAL_PARTICIPATION_STATUSES: Final[frozenset[str]] = frozenset(
    option.value for option in PARTICIPATION_STATUS_OPTIONS
)

__all__ = [
    "CANONICAL_PARTICIPATION_STATUSES",
    "PARTICIPATION_STATUS_OPTIONS",
    "FilterOption",
]
