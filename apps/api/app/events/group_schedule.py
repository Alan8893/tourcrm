"""Group Schedule projection query composition (Issue #88 / TH-0083),
`GET /api/v1/groups/{group_id}/schedule`.

Canonical sources: `docs/05-api/group-and-instructor-schedule-api.md` §2,
`docs/03-architecture/adr/ODR-0002-group-schedule-visibility-and-group-
lifecycle.md`, ADR-0015/ADR-0028 (materialization), ADR-0029/ADR-0030
(occurrence-level relationships).

Reuses `app.events.calendar.CalendarItem`/`CALENDAR_EVENT_STATUSES`/
`CALENDAR_OCCURRENCE_STATUSES` verbatim — this is the same underlying
Event/EventOccurrence identity and status-visibility contract as the
internal calendar projection, scoped down to one Group's explicit
`EventGroupTarget`/occurrence-level GroupTarget relationships instead of
the caller's full authorized Event/Occurrence set. No second calendar/
schedule identity is introduced (group-and-instructor-schedule-api.md §1).

## Relationship qualification vs. collection access — two different checks

`app.groups.schedule_authorization.build_group_schedule_access` answers
"may the caller see this Group's schedule at all, and with which temporal
restriction" (a per-request fact, resolved once via `GroupMembership`/
`GroupInstructorAssignment`, evaluated *now*). This module additionally
requires, per item, an explicit `EventGroupTarget`/occurrence-level
GroupTarget row for the requested Group — `GroupMembership` is never a
substitute (ODR-0002: "Membership never exposes an unrelated Event").

Unlike the collection-access booleans above (evaluated *now*), each
GroupTarget row's own `[valid_from, valid_to)` is evaluated at the item's
*own* scheduled start instant (`_effective_at` below) — the same
effectivity philosophy ADR-0029/ADR-0030 already establish for occurrence-
level relationships, applied here uniformly to ordinary Events too, per
this Issue's own temporal-semantics requirement: an Event/Occurrence
authoritatively belongs to the Group's schedule if the targeting
relationship was in force when it happened/will happen, not only if that
relationship happens to still be open-ended today.
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session, aliased

from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import EventOccurrenceGroupTarget, SeriesGroupTarget
from app.db.events import Event, EventGroupTarget
from app.db.groups import Group
from app.events.calendar import (
    CALENDAR_EVENT_STATUSES,
    CALENDAR_OCCURRENCE_STATUSES,
    CalendarItem,
)
from app.events.materialization import ensure_materialized
from app.groups.schedule_authorization import GroupScheduleAccess


def _effective_at(valid_from: Any, valid_to: Any, instant: Any) -> sa.ColumnElement[bool]:
    return sa.and_(valid_from <= instant, sa.or_(valid_to.is_(None), instant < valid_to))


def _extend_group_materialization(
    session: Session, *, group_id: uuid.UUID, until: datetime
) -> None:
    """ADR-0015 §4 / ADR-0028: extend the materialized horizon to cover
    `until` for every active, terminal EventSeries that has a
    `SeriesGroupTarget` for this Group — bounded to series actually
    relevant to this Group's schedule, never a blanket "every series the
    caller can see" pass (unlike the calendar endpoint's own, differently-
    bounded, all-scope extension)."""
    terminal = aliased(EventSeries)
    is_terminal = ~sa.exists(
        sa.select(terminal.id).where(terminal.supersedes_series_id == EventSeries.id)
    )
    stmt = (
        sa.select(EventSeries)
        .join(SeriesGroupTarget, SeriesGroupTarget.event_series_id == EventSeries.id)
        .where(EventSeries.status == "active", is_terminal, SeriesGroupTarget.group_id == group_id)
        .distinct()
    )
    for series in session.execute(stmt).scalars().all():
        ensure_materialized(session, series=series, until=until)


def list_group_schedule_items_page(
    session: Session,
    *,
    group: Group,
    access: GroupScheduleAccess,
    from_at: datetime,
    to_at: datetime,
    page: int,
    page_size: int,
) -> tuple[list[CalendarItem], int]:
    """Return `(items, total)` for `[from_at, to_at)`, fully filtered,
    sorted (`start_at ASC, id ASC`) and paginated inside one SQL
    statement. `access` must already have been resolved (and its
    `.allowed` checked by the caller for the existence-hiding 404 gate)
    — this function only shapes the query's temporal restriction from it.
    """
    _extend_group_materialization(session, group_id=group.id, until=to_at)

    def _temporal_restriction(start_at_column: Any) -> sa.ColumnElement[bool]:
        if access.historical_allowed:
            return sa.true()
        if access.future_only_allowed:
            return start_at_column >= sa.func.now()
        return sa.false()  # pragma: no cover - caller's 404 gate already excludes this

    event_conditions: list[sa.ColumnElement[bool]] = [
        Event.club_id == group.club_id,
        Event.start_at >= from_at,
        Event.start_at < to_at,
        Event.status.in_(CALENDAR_EVENT_STATUSES),
        _temporal_restriction(Event.start_at),
        sa.exists(
            sa.select(EventGroupTarget.id).where(
                EventGroupTarget.event_id == Event.id,
                EventGroupTarget.group_id == group.id,
                _effective_at(
                    EventGroupTarget.valid_from, EventGroupTarget.valid_to, Event.start_at
                ),
            )
        ),
    ]
    event_branch = sa.select(
        Event.id.label("id"),
        sa.literal("event").label("kind"),
        Event.club_id.label("club_id"),
        Event.event_type.label("event_type"),
        Event.title.label("title"),
        Event.description.label("description"),
        Event.start_at.label("start_at"),
        Event.end_at.label("end_at"),
        Event.timezone.label("timezone"),
        Event.status.label("status"),
        Event.cancellation_reason.label("cancellation_reason"),
        sa.cast(sa.null(), PG_UUID(as_uuid=True)).label("series_id"),
        sa.cast(sa.null(), sa.Integer).label("series_version"),
    ).where(*event_conditions)

    occurrence_conditions: list[sa.ColumnElement[bool]] = [
        EventOccurrence.club_id == group.club_id,
        EventOccurrence.starts_at >= from_at,
        EventOccurrence.starts_at < to_at,
        EventOccurrence.status.in_(CALENDAR_OCCURRENCE_STATUSES),
        _temporal_restriction(EventOccurrence.starts_at),
        sa.exists(
            sa.select(EventOccurrenceGroupTarget.id).where(
                EventOccurrenceGroupTarget.occurrence_id == EventOccurrence.id,
                EventOccurrenceGroupTarget.group_id == group.id,
                _effective_at(
                    EventOccurrenceGroupTarget.valid_from,
                    EventOccurrenceGroupTarget.valid_to,
                    EventOccurrence.starts_at,
                ),
            )
        ),
    ]
    occurrence_branch = (
        sa.select(
            EventOccurrence.id.label("id"),
            sa.literal("occurrence").label("kind"),
            EventOccurrence.club_id.label("club_id"),
            EventOccurrence.event_type.label("event_type"),
            EventOccurrence.name.label("title"),
            EventOccurrence.description.label("description"),
            EventOccurrence.starts_at.label("start_at"),
            EventOccurrence.ends_at.label("end_at"),
            EventOccurrence.timezone.label("timezone"),
            EventOccurrence.status.label("status"),
            EventOccurrence.cancellation_reason.label("cancellation_reason"),
            EventOccurrence.series_id.label("series_id"),
            EventSeries.version.label("series_version"),
        )
        .join(EventSeries, EventSeries.id == EventOccurrence.series_id)
        .where(*occurrence_conditions)
    )

    schedule_items = sa.union_all(event_branch, occurrence_branch).subquery("group_schedule_items")

    total = session.execute(sa.select(sa.func.count()).select_from(schedule_items)).scalar_one()
    rows = session.execute(
        sa.select(schedule_items)
        .order_by(schedule_items.c.start_at.asc(), schedule_items.c.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [CalendarItem(**row._mapping) for row in rows]
    return items, total


__all__ = ["list_group_schedule_items_page"]
