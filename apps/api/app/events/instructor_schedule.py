"""Instructor Schedule projection query composition (Issue #88 / TH-0083),
`GET /api/v1/me/instructor-schedule`.

Canonical sources: `docs/05-api/group-and-instructor-schedule-api.md` §3,
ADR-0013 (scopes), ADR-0021 (GroupInstructorAssignment), ADR-0022
(cross-Club ownership integrity), ADR-0029/ADR-0030 (occurrence-level
relationships).

## This projection is relationship-based, not scope-based

Every other list-level authorization predicate in this codebase (see
`app.events.authorization.event_visibility_filter`,
`app.events.series_authorization.occurrence_visibility_filter`) resolves
`scope_matches` per `UserRoleAssignment` (`all` -> unconditional, `own_
events` -> a relationship check, etc.). Instructor Schedule is explicitly
different (group-and-instructor-schedule-api.md §3 "Scope behavior"): "An
`all` RoleAssignment does not by itself turn this contextual endpoint into
a club-wide instructor schedule" — i.e. `scope_type` itself plays no role
in *widening* the result; only the underlying relationship
(`EventStaffAssignment` / `EventGroupTarget` + `GroupInstructorAssignment`)
does. What the assignment *does* still gate is (a) whether the caller
holds `event.read` at all in a given Club (`none` contributes nothing,
matching that scope's universal "no access" meaning everywhere else in
this codebase) and (b) which Club(s) are covered at all (`club_id`
boundary) — ADR-0022 §13: "preserve independent Club/object boundaries...
no Club may be inferred from the User's global identity". A Club the
caller holds zero `event.read` assignments for (of any non-`none` scope)
never appears, even if a stray relationship row exists there.

## Effectivity is evaluated at the item's own scheduled start, not "now"

group-and-instructor-schedule-api.md §3 "Effectivity" is explicit:
"Applicability is evaluated at the Event/Occurrence scheduled start
instant" — deliberately different from `app.events.authorization`'s own
`_own_event_condition`/`_own_group_condition` (which check "active *now*",
correct for a live, current authorization decision on one Event, but
wrong for a schedule that must keep showing historical items you
genuinely staffed even after that assignment has since ended). This
module therefore defines its own instant-parameterized predicates rather
than reusing those two now()-only helpers.

`assigned_events` is never a literal `scope_type` value (it is normalized
to `own_events` at the write boundary, `app.authorization.context.
normalize_scope_type`) — this module needs no separate handling for it.
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session, aliased

from app.authorization.service import applicable_assignments
from app.db.authorization import UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceStaffAssignment,
    SeriesGroupTarget,
    SeriesStaffAssignment,
)
from app.db.events import Event, EventGroupTarget, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment
from app.events.calendar import (
    CALENDAR_EVENT_STATUSES,
    CALENDAR_OCCURRENCE_STATUSES,
    CalendarItem,
)
from app.events.materialization import ensure_materialized


def _effective_at(valid_from: Any, valid_to: Any, instant: Any) -> sa.ColumnElement[bool]:
    return sa.and_(valid_from <= instant, sa.or_(valid_to.is_(None), instant < valid_to))


def _own_event_condition_at(event_id: Any, user_id: Any, instant: Any) -> sa.ColumnElement[bool]:
    return sa.exists(
        sa.select(EventStaffAssignment.id).where(
            EventStaffAssignment.event_id == event_id,
            EventStaffAssignment.user_id == user_id,
            _effective_at(EventStaffAssignment.valid_from, EventStaffAssignment.valid_to, instant),
        )
    )


def _own_group_condition_at(
    event_id: Any, event_club_id: Any, user_id: Any, instant: Any
) -> sa.ColumnElement[bool]:
    egt = aliased(EventGroupTarget)
    grp = aliased(Group)
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(egt.id)
        .join(grp, grp.id == egt.group_id)
        .join(gia, gia.group_id == grp.id)
        .where(
            egt.event_id == event_id,
            grp.club_id == event_club_id,
            gia.user_id == user_id,
            _effective_at(egt.valid_from, egt.valid_to, instant),
            _effective_at(gia.valid_from, gia.valid_to, instant),
        )
    )


def _own_occurrence_condition_at(
    occurrence_id: Any, user_id: Any, instant: Any
) -> sa.ColumnElement[bool]:
    return sa.exists(
        sa.select(EventOccurrenceStaffAssignment.id).where(
            EventOccurrenceStaffAssignment.occurrence_id == occurrence_id,
            EventOccurrenceStaffAssignment.user_id == user_id,
            _effective_at(
                EventOccurrenceStaffAssignment.valid_from,
                EventOccurrenceStaffAssignment.valid_to,
                instant,
            ),
        )
    )


def _own_occurrence_group_condition_at(
    occurrence_id: Any, occurrence_club_id: Any, user_id: Any, instant: Any
) -> sa.ColumnElement[bool]:
    ogt = aliased(EventOccurrenceGroupTarget)
    grp = aliased(Group)
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(ogt.id)
        .join(grp, grp.id == ogt.group_id)
        .join(gia, gia.group_id == grp.id)
        .where(
            ogt.occurrence_id == occurrence_id,
            grp.club_id == occurrence_club_id,
            gia.user_id == user_id,
            _effective_at(ogt.valid_from, ogt.valid_to, instant),
            _effective_at(gia.valid_from, gia.valid_to, instant),
        )
    )


def _assignment_club_coverage(
    club_id_column: Any, non_none_assignments: list[UserRoleAssignment]
) -> sa.ColumnElement[bool]:
    """True if at least one of the caller's non-`none` `event.read`
    assignments covers `club_id_column` (`club_id IS NULL` -> global,
    covers every Club)."""
    if any(a.club_id is None for a in non_none_assignments):
        return sa.true()
    club_ids = [a.club_id for a in non_none_assignments]
    if not club_ids:
        return sa.false()
    return club_id_column.in_(club_ids)


def _extend_instructor_materialization(
    session: Session, *, user_id: uuid.UUID, until: datetime
) -> None:
    """ADR-0015 §4 / ADR-0028: extend the materialized horizon to cover
    `until` for every active, terminal EventSeries where the caller has a
    direct `SeriesStaffAssignment` or a `SeriesGroupTarget` matched by an
    active `GroupInstructorAssignment` — bounded to series actually
    relevant to this User's instructor responsibilities."""
    terminal = aliased(EventSeries)
    is_terminal = ~sa.exists(
        sa.select(terminal.id).where(terminal.supersedes_series_id == EventSeries.id)
    )
    via_staff = sa.exists(
        sa.select(SeriesStaffAssignment.id).where(
            SeriesStaffAssignment.event_series_id == EventSeries.id,
            SeriesStaffAssignment.user_id == user_id,
        )
    )
    via_group = sa.exists(
        sa.select(SeriesGroupTarget.id)
        .join(Group, Group.id == SeriesGroupTarget.group_id)
        .join(GroupInstructorAssignment, GroupInstructorAssignment.group_id == Group.id)
        .where(
            SeriesGroupTarget.event_series_id == EventSeries.id,
            GroupInstructorAssignment.user_id == user_id,
        )
    )
    stmt = (
        sa.select(EventSeries)
        .where(EventSeries.status == "active", is_terminal, sa.or_(via_staff, via_group))
        .distinct()
    )
    for series in session.execute(stmt).scalars().all():
        ensure_materialized(session, series=series, until=until)


def list_instructor_schedule_items_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    from_at: datetime,
    to_at: datetime,
    page: int,
    page_size: int,
) -> tuple[list[CalendarItem], int]:
    """Return `(items, total)` for `[from_at, to_at)` for the authenticated
    User's own instructor responsibilities — relationship-based, not
    scope-based (see module docstring)."""
    assignments = applicable_assignments(session, user_id, permission_code)
    non_none_assignments = [a for a in assignments if a.scope_type != "none"]
    if not non_none_assignments:
        return [], 0

    _extend_instructor_materialization(session, user_id=user_id, until=to_at)

    event_conditions: list[sa.ColumnElement[bool]] = [
        _assignment_club_coverage(Event.club_id, non_none_assignments),
        Event.start_at >= from_at,
        Event.start_at < to_at,
        Event.status.in_(CALENDAR_EVENT_STATUSES),
        sa.or_(
            _own_event_condition_at(Event.id, user_id, Event.start_at),
            _own_group_condition_at(Event.id, Event.club_id, user_id, Event.start_at),
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
        _assignment_club_coverage(EventOccurrence.club_id, non_none_assignments),
        EventOccurrence.starts_at >= from_at,
        EventOccurrence.starts_at < to_at,
        EventOccurrence.status.in_(CALENDAR_OCCURRENCE_STATUSES),
        sa.or_(
            _own_occurrence_condition_at(EventOccurrence.id, user_id, EventOccurrence.starts_at),
            _own_occurrence_group_condition_at(
                EventOccurrence.id, EventOccurrence.club_id, user_id, EventOccurrence.starts_at
            ),
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

    schedule_items = sa.union_all(event_branch, occurrence_branch).subquery(
        "instructor_schedule_items"
    )

    total = session.execute(sa.select(sa.func.count()).select_from(schedule_items)).scalar_one()
    rows = session.execute(
        sa.select(schedule_items)
        .order_by(schedule_items.c.start_at.asc(), schedule_items.c.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [CalendarItem(**row._mapping) for row in rows]
    return items, total


__all__ = ["list_instructor_schedule_items_page"]
