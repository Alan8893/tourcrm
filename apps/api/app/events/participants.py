"""Read-only Event participant list (events-api.md §18,
`GET /api/v1/events/{event_id}/participants`).

Participant set: Persons whose `EventParticipation` for this Event has
`registration_status == registered` (ADR-0037) — exactly the roster
`app.documents.package` resolves for the competition document package.
`cancelled` (and any other non-`registered`) rows are excluded in SQL.

Authorization (ADR-0020 §1-§3: "participant read" is `event.read`):
the caller has already passed the Event object-level `event.read` check;
which participant rows are then visible follows the same per-scope row
rule ADR-0032 already established for the attendance roster —
`all`/`own_events`/`own_groups` see the full set, `self` only the
requester's own row, `children` only their active children's rows —
reused verbatim from `app.events.attendance` with
`permission_code="event.read"`. Applied inside the SQL query before
count/pagination, never fetch-then-filter.

Order: `last_name`, `first_name`, `id` — the same deterministic Person
roster order as `app.documents.package` and the attendance roster.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.db.events import Event, EventParticipation
from app.db.identity import Person
from app.events.attendance import _attendance_row_visibility as _participant_row_visibility
from app.events.participation import REGISTERED_STATUS

_READ_PERMISSION = "event.read"


@dataclass(frozen=True)
class EventParticipant:
    person_id: uuid.UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]


def list_event_participants(
    session: Session,
    *,
    event: Event,
    resource_context: ResourceContext,
    user_id: uuid.UUID,
    page: int,
    page_size: int,
) -> tuple[list[EventParticipant], int]:
    visibility = _participant_row_visibility(
        session,
        club_id=event.club_id,
        resource_context=resource_context,
        user_id=user_id,
        permission_code=_READ_PERMISSION,
        participant_person_id_column=EventParticipation.person_id,
    )
    base_query = (
        sa.select(
            Person.id.label("person_id"),
            Person.first_name.label("first_name"),
            Person.last_name.label("last_name"),
            Person.middle_name.label("middle_name"),
        )
        .select_from(EventParticipation)
        .join(Person, Person.id == EventParticipation.person_id)
        .where(
            EventParticipation.event_id == event.id,
            EventParticipation.registration_status == REGISTERED_STATUS,
            visibility,
        )
    )

    total = session.execute(
        sa.select(sa.func.count()).select_from(base_query.subquery())
    ).scalar_one()
    rows = session.execute(
        base_query.order_by(Person.last_name, Person.first_name, Person.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return [
        EventParticipant(
            person_id=row.person_id,
            first_name=row.first_name,
            last_name=row.last_name,
            middle_name=row.middle_name,
        )
        for row in rows
    ], total
