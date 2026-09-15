"""Internal calendar projection query composition (Issue #82 / TH-0080).

Canonical sources: docs/05-api/events-api.md §16 (query contract, status
visibility, materialization, response identity), ADR-0015 (materialization
strategy), ADR-0028 (recurrence persistence/versioning), ADR-0029
(EventOccurrence authorization relationships), ADR-0030 (EventSeries
relationship source), docs/03-architecture/database-schema-recurrence.md
§5 (occurrence authorization relationship persistence).

Merges ordinary `Event` rows and recurring `EventOccurrence` rows into one
sorted, paginated calendar projection using a single `UNION ALL` SQL
statement — authorization (app.events.authorization.event_visibility_filter
/ app.events.series_authorization.occurrence_visibility_filter), the
documented narrowing filters, and pagination/ordering are all applied
inside that one query, never fetched-then-filtered/paginated in Python
(events-api.md §16 / api-conventions.md §10 whitelist-filtering
requirement).

## Recurring occurrence authorization (ADR-0030 / TH-0082 / PR #86)

`app.events.series_authorization.occurrence_visibility_filter` now
resolves every canonical scope (`all`, `own_events`, `own_groups`, `self`,
`children`, `none`; `assigned_events` aliases `own_events`) against real,
direct-FK occurrence-level relationship rows (`EventOccurrenceStaff
Assignment`/`GroupTarget`/`Participant`), materialized atomically from the
governing `EventSeries` version's own relationship source
(`SeriesStaffAssignment`/`SeriesGroupTarget`/`SeriesParticipant`) at
occurrence-creation time. This module calls that filter with the exact
same signature it always has (`session, *, user_id, permission_code`), so
no code change was needed here to pick up the fix — recurring occurrences
are now visible under `own_events`/`own_groups`/`self`/`children` exactly
when the acting user holds the corresponding occurrence-level
relationship, never from `occurrence.club_id` alone. `all`/`none` are
unchanged. See `app.events.series_authorization`'s own module docstring
and ADR-0030 for the full relationship-source/materialization model.

One residual, narrower limitation (not a security gap — it only affects
result completeness, never over-authorization): `_extend_recurring_
materialization` below only proactively extends the materialization
horizon for series the requester holds `all` scope on. A user who only
holds `own_events`/`own_groups`/`self`/`children` on a series has no
existing path (here or via the per-series `GET /series/{id}/occurrences`
endpoint, which is itself `all`-scope-only per
`app.events.series_authorization`'s deliberate, documented decision to
scope Series-resource access to `all` only) to proactively extend that
series' horizon beyond the default 180-day window from the calendar
endpoint. Occurrences within the already-materialized range are fully,
correctly authorized for every scope; only on-demand extension beyond that
range remains bounded to `all`-scope series, matching the existing,
unchanged scoping of Series-resource access itself.

The `user_id`/`group_id` narrowing filters are still resolved only against
`EventStaffAssignment`/`EventGroupTarget` (ordinary `Event` relationships),
so they still only ever narrow the `Event` branch, never the `Occurrence`
branch — matching the documented "a filter only narrows, never expands,
already-authorized access" contract; this is a deliberate scope-limit
carried over unchanged from the original implementation, not a new gap.
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
    `permission_code` — see the module docstring's "residual, narrower
    limitation" note: this mirrors the existing, unchanged, all-scope-only
    Series-resource access scoping, so extending materialization for any
    other Club would be pure wasted work, never a security boundary
    (materialization itself returns no data and leaks nothing).
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

    # See module docstring's final paragraph: user_id/group_id filters only
    # ever resolve against the ordinary EventStaffAssignment/EventGroupTarget
    # tables, never the separate occurrence-level relationship tables, so an
    # occurrence can never satisfy either filter — the occurrence branch is
    # simply omitted (equivalent to, but cheaper than, a permanently-false
    # clause).
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
