"""Event list query composition (Issue #40).

Canonical source: docs/05-api/events-api.md §4 (documented GET /events
filters: `from`/`to`, `event_type`, `status`, `group_id`,
`participant_id`, `instructor_id`, `search`, pagination, sorting).

Applies deterministic scope-based authorization
(app.events.authorization.event_visibility_filter) directly inside the
SQL query — never fetch-then-filter-in-Python, so an unauthorized Event
can never leak through pagination/totals/offsets. The documented filters
above are then applied as additional, narrowing-only conditions: a
filter can never expand what the authorization predicate already allows
(events-api.md §16's "Фильтры не могут расширять доступ" principle,
applied identically here even though §16 itself describes the calendar
endpoint, not this one).

`group_id`/`participant_id`/`instructor_id` match against the
EventGroupTarget/EventParticipation/EventStaffAssignment relationship
tables directly (any historical row, not only a currently-active one) —
these are search filters, not an authorization mechanism, so they are
deliberately not restricted to "currently active" the way the scope
predicates in app.events.authorization are.
"""

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.events.authorization import event_visibility_filter

_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "start_at": Event.start_at.asc(),
    "-start_at": Event.start_at.desc(),
    "created_at": Event.created_at.asc(),
    "-created_at": Event.created_at.desc(),
}
DEFAULT_SORT = "start_at"


class InvalidSortError(ValueError):
    """`sort` is not one of the whitelisted values (api-contract.md's
    "sorting (whitelist)" convention — never arbitrary/dynamic SQL).
    """


def list_events_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = DEFAULT_SORT,
    starts_from: Optional[datetime] = None,
    starts_to: Optional[datetime] = None,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    group_id: Optional[uuid.UUID] = None,
    participant_id: Optional[uuid.UUID] = None,
    instructor_id: Optional[uuid.UUID] = None,
    search: Optional[str] = None,
) -> tuple[list[Event], int]:
    if sort not in _SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        event_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if starts_from is not None:
        conditions.append(Event.start_at >= starts_from)
    if starts_to is not None:
        conditions.append(Event.start_at <= starts_to)
    if event_type is not None:
        conditions.append(Event.event_type == event_type)
    if status is not None:
        conditions.append(Event.status == status)
    if group_id is not None:
        conditions.append(
            sa.exists(
                sa.select(EventGroupTarget.id).where(
                    EventGroupTarget.event_id == Event.id,
                    EventGroupTarget.group_id == group_id,
                )
            )
        )
    if participant_id is not None:
        conditions.append(
            sa.exists(
                sa.select(EventParticipation.id).where(
                    EventParticipation.event_id == Event.id,
                    EventParticipation.person_id == participant_id,
                )
            )
        )
    if instructor_id is not None:
        conditions.append(
            sa.exists(
                sa.select(EventStaffAssignment.id).where(
                    EventStaffAssignment.event_id == Event.id,
                    EventStaffAssignment.user_id == instructor_id,
                )
            )
        )
    if search:
        conditions.append(Event.title.ilike(f"%{search}%"))

    total = session.execute(
        sa.select(sa.func.count()).select_from(Event).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(Event)
            .where(*conditions)
            .order_by(_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def get_event_targeting(
    session: Session, *, event_id: uuid.UUID
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """TH-0108 / ADR-0037 §1-§2: the Event's *currently active*
    (validity-interval sense) target Group ids and responsible-instructor
    User ids, for `EventOut.group_ids`/`.instructor_ids` — so the
    frontend can repopulate the edit form's selectors. Unlike this
    module's own `group_id`/`instructor_id` *search* filters above
    (which deliberately match any historical row), a response
    projection must reflect the Event's present targeting only; an
    ended (`valid_to` in the past) row is not currently part of it.
    """
    moment = datetime.now(dt_timezone.utc)
    group_ids = (
        session.execute(
            sa.select(EventGroupTarget.group_id)
            .where(
                EventGroupTarget.event_id == event_id,
                EventGroupTarget.valid_from <= moment,
                sa.or_(EventGroupTarget.valid_to.is_(None), EventGroupTarget.valid_to > moment),
            )
            .order_by(EventGroupTarget.valid_from.asc())
        )
        .scalars()
        .all()
    )
    instructor_ids = (
        session.execute(
            sa.select(EventStaffAssignment.user_id)
            .where(
                EventStaffAssignment.event_id == event_id,
                EventStaffAssignment.valid_from <= moment,
                sa.or_(
                    EventStaffAssignment.valid_to.is_(None), EventStaffAssignment.valid_to > moment
                ),
            )
            .order_by(EventStaffAssignment.valid_from.asc())
        )
        .scalars()
        .all()
    )
    return list(group_ids), list(instructor_ids)


__all__ = ["list_events_page", "InvalidSortError", "DEFAULT_SORT", "get_event_targeting"]
