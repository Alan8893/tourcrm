"""Event-driven Achievement Engine triggers (Issue #220, A7).

Canonical source: docs/04-modules/achievements-and-norms.md §22.

The canonical tourism domain calls these hooks *after* it has committed a
change to a fact the approved metrics read (`completed_trips`):

- `TripParticipant.actual_participation` recorded or changed
  (app.trips.service.record_actual_participation);
- an Event transitioning to `completed` (app.events.crud.
  transition_event_status) — every Person with confirmed actual
  participation in its Trip is affected.

Achievement evaluation never blocks or rolls back the canonical change
that triggered it: a failure here is logged and swallowed (the session is
rolled back to a clean state), and periodic reconciliation
(app.achievements.engine.reconcile) later creates any Award the missed
event would have produced.
"""

import logging
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.achievements import engine
from app.achievements.metrics import COMPLETED_TRIPS
from app.db.events import EventParticipation
from app.db.trips import TripParticipant

logger = logging.getLogger("tourcrm.achievements.triggers")

_TRIP_FACT_METRICS = frozenset({COMPLETED_TRIPS})


def _dispatch(session: Session, *, person_ids: Sequence[uuid.UUID], source: str) -> None:
    try:
        engine.handle_tourism_facts_changed(
            session, person_ids=person_ids, changed_metrics=_TRIP_FACT_METRICS
        )
    except Exception:
        session.rollback()
        logger.exception("achievements.trigger.failed source=%s", source)


def trip_participation_changed(session: Session, *, person_id: uuid.UUID) -> None:
    _dispatch(session, person_ids=[person_id], source="trip_participant")


def event_completed(session: Session, *, event_id: uuid.UUID) -> None:
    person_ids = (
        session.execute(
            sa.select(EventParticipation.person_id)
            .join(
                TripParticipant,
                TripParticipant.event_participation_id == EventParticipation.id,
            )
            .where(
                TripParticipant.event_id == event_id,
                TripParticipant.actual_participation.is_(True),
            )
        )
        .scalars()
        .all()
    )
    if person_ids:
        _dispatch(session, person_ids=list(person_ids), source="event_completed")


__all__ = ["trip_participation_changed", "event_completed"]
