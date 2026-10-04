"""Achievement metric catalog (Issue #220, A6/A8).

Canonical source: docs/04-modules/achievements-and-norms.md §21, §23.

The Engine evaluates Rules only against metrics registered here. Each
metric is computed from canonical domain facts at evaluation time — the
Achievement Domain keeps no tourism history of its own (§11, §21).

Approved catalog (A8): exactly one metric,

- `completed_trips` — the number of Trips that are completed and in
  which the Person has `TripParticipant.actual_participation = true`.
  Sources: `Trip` (Event(type=trip) -> Trip), the Event's lifecycle
  `Event.status = 'completed'` (a Trip has no lifecycle of its own,
  trips-and-tourist-profile.md §6), and the Person's `TripParticipant`
  through their `EventParticipation`.

Every other metric named in the canonical docs (overnights, one-day /
multi-day hikes, degree / category hikes, distinct tourism types or
regions) is deliberately absent: A8 makes them non-executable until their
canonical sources are approved, so a Rule referencing one is rejected by
app.achievements.conditions.

Adding a future approved metric means registering one more
`MetricDefinition` here — no change to the condition model or the Engine.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.events import Event, EventParticipation
from app.db.trips import Trip, TripParticipant

COMPLETED_TRIPS = "completed_trips"
_COMPLETED_EVENT_STATUS = "completed"

# Values per Person; `None` means the canonical fact is missing for that
# Person (never satisfies a condition, §20).
MetricValues = dict[uuid.UUID, Optional[int]]


@dataclass(frozen=True)
class MetricDefinition:
    code: str
    label: str
    description: str
    compute: Callable[[Session, Sequence[uuid.UUID]], MetricValues]


def _completed_trips(session: Session, person_ids: Sequence[uuid.UUID]) -> MetricValues:
    """Distinct completed Trips with confirmed actual participation, per
    Person. A Person with none has the (known) value 0."""
    if not person_ids:
        return {}
    rows = session.execute(
        sa.select(EventParticipation.person_id, sa.func.count(sa.distinct(Trip.event_id)))
        .select_from(Trip)
        .join(Event, Event.id == Trip.event_id)
        .join(TripParticipant, TripParticipant.event_id == Trip.event_id)
        .join(
            EventParticipation,
            EventParticipation.id == TripParticipant.event_participation_id,
        )
        .where(
            Event.status == _COMPLETED_EVENT_STATUS,
            TripParticipant.actual_participation.is_(True),
            EventParticipation.person_id.in_(list(person_ids)),
        )
        .group_by(EventParticipation.person_id)
    ).all()
    values: MetricValues = {person_id: 0 for person_id in person_ids}
    values.update({person_id: count for person_id, count in rows})
    return values


APPROVED_METRICS: dict[str, MetricDefinition] = {
    COMPLETED_TRIPS: MetricDefinition(
        code=COMPLETED_TRIPS,
        label="Завершённые походы",
        description=(
            "Количество завершённых походов (Event.status = completed), "
            "в которых участник фактически участвовал "
            "(TripParticipant.actual_participation = true)."
        ),
        compute=_completed_trips,
    ),
}
APPROVED_METRIC_CODES: frozenset[str] = frozenset(APPROVED_METRICS)


def compute_metrics(
    session: Session, *, metric_codes: frozenset[str], person_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Optional[int]]]:
    """Facts per Person for exactly the requested approved metrics."""
    facts: dict[uuid.UUID, dict[str, Optional[int]]] = {pid: {} for pid in person_ids}
    for code in sorted(metric_codes):
        values = APPROVED_METRICS[code].compute(session, person_ids)
        for person_id in person_ids:
            facts[person_id][code] = values.get(person_id)
    return facts


__all__ = [
    "COMPLETED_TRIPS",
    "MetricDefinition",
    "APPROVED_METRICS",
    "APPROVED_METRIC_CODES",
    "compute_metrics",
]
