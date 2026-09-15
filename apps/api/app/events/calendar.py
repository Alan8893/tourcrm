"""Internal calendar projection query composition (Issue #82 / TH-0080).

Canonical sources: docs/05-api/events-api.md §16 (query contract, status
visibility, materialization, response identity), ADR-0015 (materialization
strategy), ADR-0028 (recurrence persistence/versioning), ADR-0029
(EventOccurrence authorization relationships), docs/03-architecture/
database-schema-recurrence.md §5 (occurrence authorization relationship
persistence).

Merges ordinary `Event` rows and recurring `EventOccurrence` rows into one
sorted, paginated calendar projection using a single `UNION ALL` SQL
statement — authorization (app.events.authorization.event_visibility_filter
/ app.events.series_authorization.occurrence_visibility_filter), the
documented narrowing filters, and pagination/ordering are all applied
inside that one query, never fetched-then-filtered/paginated in Python
(events-api.md §16 / api-conventions.md §10 whitelist-filtering
requirement).

## GAP/ODR — recurring occurrence relationship materialization (ADR-0029)

ADR-0029 ("Consequence for TH-0079") directs this calendar implementation
Issue to include occurrence-level staff/group-target/participation
relationship persistence, materialized from "the governing Series
version['s] source relationship definition" so that `own_events`/
`own_groups`/`self`/`children` can be evaluated for recurring
`EventOccurrence` rows exactly as `app.events.authorization` already does
for ordinary `Event` rows.

That Series-version-level *source* does not exist anywhere in this
codebase or in any canonical document: `EventSeries` has no staff/group/
participant fields or child tables, `docs/05-api/event-recurrence-api.md`
and `app.api.v1.events_series_schemas.EventSeriesCreateRequest` define no
such input, and no API anywhere can create one. `docs/03-architecture/
database-schema-recurrence.md` §5 explicitly leaves "exact physical table
names for occurrence relationship records" as an implementation detail,
but never specifies the Series-level source's own field list — and
`app.events.series_authorization`'s existing (already-reviewed, already-
shipped) module docstring independently confirms this exact gap for
Series/Occurrence permission checks in general.

Per this task's own instruction ("если occurrence-level authorization
relationships ещё не существуют физически и без них невозможно корректно
реализовать calendar authorization: не придумывай структуру сам,
остановись и сообщи GAP/ODR"), this module does not invent that Series-
level source schema or a corresponding occurrence-relationship persistence
layer. Instead it reuses the existing, already-accepted
`app.events.series_authorization.occurrence_visibility_filter` exactly as
shipped: `all`/`none` resolve correctly and safely; `own_events`/
`own_groups`/`self`/`children` correctly and safely resolve to "no
access" for every recurring occurrence, identical to that module's
already-reviewed behavior for the Series/Occurrence `event.update`/
`event.manage` endpoints — this is not a regression introduced by the
calendar endpoint. Ordinary (non-recurring) `Event` calendar entries get
the full, already-correct `own_events`/`own_groups`/`self`/`children`
support via `app.events.authorization.event_visibility_filter`. A
follow-up ODR is required to define the EventSeries-level relationship
source persistence (and its write API) before recurring-occurrence
`own_events`/`own_groups`/`self`/`children` can ever return true. See the
final implementation report for the same note.

A structural consequence of this same gap: the `user_id`/`group_id`
narrowing filters (resolved against `EventStaffAssignment`/
`EventGroupTarget`, the only relationship tables that exist) can never
match a recurring occurrence, so supplying either filter correctly
excludes every occurrence from the result — never a leak (it excludes,
never includes), and never a guess at a relationship that cannot be
verified.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session, aliased

from app.authorization.service import applicable_assignments
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.events import Event, EventGroupTarget, EventStaffAssignment
from app.events.authorization import event_visibility_filter
from app.events.materialization import ensure_materialized
from app.events.series_authorization import occurrence_visibility_filter
from app.events.series_vocabulary import CANONICAL_OCCURRENCE_STATUSES

# events-api.md §16: "draft и archived в обычную calendar projection не
# входят" — the calendar-visible subset of the ordinary Event lifecycle.
CALENDAR_EVENT_STATUSES: tuple[str, ...] = ("published", "in_progress", "completed", "cancelled")

# The occurrence lifecycle (ADR-0028 §7) has no draft/archived-equivalent
# state, so every canonical occurrence status is calendar-visible — kept
# as an explicit, documented set (not "no filter at all") so a future
# change to the occurrence status vocabulary cannot silently widen calendar
# visibility without a corresponding review here.
CALENDAR_OCCURRENCE_STATUSES: tuple[str, ...] = CANONICAL_OCCURRENCE_STATUSES

# The whitelist for the `status` narrowing filter (api-conventions.md §10:
# "whitelist sortable/filterable fields"): every status value that could
# ever appear on a calendar-visible row of either kind.
CALENDAR_STATUS_FILTER_VALUES: frozenset[str] = frozenset(CALENDAR_EVENT_STATUSES) | frozenset(
    CALENDAR_OCCURRENCE_STATUSES
)


@dataclass(frozen=True)
class CalendarItem:
    """One calendar row — either an ordinary Event or a materialized
    EventOccurrence, already fully authorized/filtered/paginated by the
    SQL query that produced it. `id` is the stable, opaque id of the
    originating Event/EventOccurrence itself (events-api.md §16: "не
    создаётся второй календарный identity").
    """

    id: uuid.UUID
    kind: str
    club_id: uuid.UUID
    event_type: str
    title: str
    description: Optional[str]
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    cancellation_reason: Optional[str]
    series_id: Optional[uuid.UUID]
    series_version: Optional[int]


def _extend_recurring_materialization(
    session: Session, *, user_id: uuid.UUID, permission_code: str, until: datetime
) -> None:
    """ADR-0015 §4 / events-api.md §16: automatically extend materialization
    to cover `until` (and at least the default 180-day horizon —
    app.events.materialization.ensure_materialized's own contract) for
    every active, terminal EventSeries the requester could possibly see.

    Bounded to Clubs the requester has a `scope_type="all"` assignment for
    `permission_code` — per this module's GAP/ODR note, that is the *only*
    scope under which any recurring occurrence can currently become
    calendar-visible, so extending materialization for any other Club
    would be pure wasted work, never a security boundary (materialization
    itself returns no data and leaks nothing).
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    all_scope_club_ids = [a.club_id for a in assignments if a.scope_type == "all"]
    if not all_scope_club_ids:
        return

    terminal = aliased(EventSeries)
    is_terminal = ~sa.exists(
        sa.select(terminal.id).where(terminal.supersedes_series_id == EventSeries.id)
    )
    stmt = sa.select(EventSeries).where(EventSeries.status == "active", is_terminal)
    if not any(club_id is None for club_id in all_scope_club_ids):
        stmt = stmt.where(EventSeries.club_id.in_(all_scope_club_ids))

    for series in session.execute(stmt).scalars().all():
        ensure_materialized(session, series=series, until=until)


def list_calendar_items_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    from_at: datetime,
    to_at: datetime,
    page: int,
    page_size: int,
    user_id_filter: Optional[uuid.UUID] = None,
    group_id_filter: Optional[uuid.UUID] = None,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[CalendarItem], int]:
    """Return `(items, total)` for `[from_at, to_at)` (both already
    normalized to UTC by the caller), fully authorized, filtered, sorted
    (`start_at ASC, id ASC`) and paginated inside one SQL statement.

    `user_id_filter`/`group_id_filter`/`event_type`/`status` only ever
    narrow what `permission_code`'s scope/object policy already allows —
    never expand it (events-api.md §16 / api-conventions.md).
    """
    _extend_recurring_materialization(
        session, user_id=user_id, permission_code=permission_code, until=to_at
    )

    event_conditions: list[sa.ColumnElement[bool]] = [
        event_visibility_filter(session, user_id=user_id, permission_code=permission_code),
        Event.start_at >= from_at,
        Event.start_at < to_at,
        Event.status.in_(CALENDAR_EVENT_STATUSES),
    ]
    if event_type is not None:
        event_conditions.append(Event.event_type == event_type)
    if status is not None:
        event_conditions.append(Event.status == status)
    if user_id_filter is not None:
        event_conditions.append(
            sa.exists(
                sa.select(EventStaffAssignment.id).where(
                    EventStaffAssignment.event_id == Event.id,
                    EventStaffAssignment.user_id == user_id_filter,
                )
            )
        )
    if group_id_filter is not None:
        event_conditions.append(
            sa.exists(
                sa.select(EventGroupTarget.id).where(
                    EventGroupTarget.event_id == Event.id,
                    EventGroupTarget.group_id == group_id_filter,
                )
            )
        )

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

    branches = [event_branch]

    # See module GAP/ODR note: no occurrence-level staff/group-target
    # relationship exists yet, so an occurrence can never satisfy a
    # user_id/group_id filter — the occurrence branch is simply omitted
    # (equivalent to, but cheaper than, adding a permanently-false clause).
    if user_id_filter is None and group_id_filter is None:
        occurrence_conditions: list[sa.ColumnElement[bool]] = [
            occurrence_visibility_filter(
                session, user_id=user_id, permission_code=permission_code
            ),
            EventOccurrence.starts_at >= from_at,
            EventOccurrence.starts_at < to_at,
            EventOccurrence.status.in_(CALENDAR_OCCURRENCE_STATUSES),
        ]
        if event_type is not None:
            occurrence_conditions.append(EventOccurrence.event_type == event_type)
        if status is not None:
            occurrence_conditions.append(EventOccurrence.status == status)

        occurrence_branch = (
            sa.select(
                EventOccurrence.id.label("id"),
                sa.literal("occurrence").label("kind"),
                EventOccurrence.club_id.label("club_id"),
                EventOccurrence.event_type.label("event_type"),
                EventOccurrence.name.label("title"),
                EventOccurrence.description.label("description"),
                # ADR-0028 §4 / database-schema-recurrence.md §2:
                # starts_at/ends_at are the *current effective* schedule —
                # already updated in place by a reschedule exception, so no
                # extra join/derivation is needed to satisfy events-api.md
                # §16's "эффективные start_at/end_at после применения
                # exceptions/overrides".
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
        branches.append(occurrence_branch)

    calendar_items = sa.union_all(*branches).subquery("calendar_items")

    total = session.execute(sa.select(sa.func.count()).select_from(calendar_items)).scalar_one()
    rows = session.execute(
        sa.select(calendar_items)
        .order_by(calendar_items.c.start_at.asc(), calendar_items.c.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [CalendarItem(**row._mapping) for row in rows]
    return items, total


__all__ = [
    "CALENDAR_EVENT_STATUSES",
    "CALENDAR_OCCURRENCE_STATUSES",
    "CALENDAR_STATUS_FILTER_VALUES",
    "CalendarItem",
    "list_calendar_items_page",
]
