"""Event conflict detection (Issue #91 / TH-0085), `GET /api/v1/events/
conflicts`.

Canonical sources: `docs/03-architecture/adr/ADR-0031-event-conflict-
detection.md`, `docs/03-architecture/adr/ODR-0003-event-conflict-
semantics.md`, `docs/05-api/events-api.md` §28, ADR-0015/ADR-0028
(materialization), ADR-0029/ADR-0030 (occurrence-level relationships).

A conflict is a **derived** result, never a persisted entity: two
concrete, operational Event/EventOccurrence records whose effective
`[start_at, end_at)` intervals overlap (`max(a,b).start < min(a,b).end`;
touching boundaries never conflict) and which share one of exactly three
MVP domains (ADR-0031 §2):

- `instructor` — same User via an applicable staffing/responsibility
  relationship (`EventStaffAssignment`/`EventOccurrenceStaffAssignment`);
- `group` — same Group via an explicit targeting relationship
  (`EventGroupTarget`/`EventOccurrenceGroupTarget`);
- `participant` — same Person via `EventParticipation`/
  `EventOccurrenceParticipant`.

`club_id`, `ClubMembership`, `GroupMembership` alone, `GuardianRelationship`
alone, role names, and `created_by` never establish a conflict domain
(ADR-0031 §2/§5) — none of those tables or fields are referenced below.

## Relationship effectivity: evaluated at each object's own scheduled start

ADR-0031 does not itself state an instant for relationship effectivity
(unlike `docs/05-api/group-and-instructor-schedule-api.md` §3, which is
explicit for the Instructor Schedule). This module evaluates every
`[valid_from, valid_to)` relationship interval (`EventStaffAssignment`/
`EventGroupTarget`/`EventOccurrenceStaffAssignment`/
`EventOccurrenceGroupTarget`/`EventOccurrenceParticipant`) at the *owning*
object's own scheduled start instant, not "now" — the same canonical
effectivity philosophy ADR-0030 already establishes project-wide for
relationship applicability ("applied at the occurrence's scheduled start
time"), and the same choice already made for the Instructor/Group
Schedule projections (`app.events.instructor_schedule`/`app.events.
group_schedule`). This matters concretely for *future* conflicts: an
instructor assignment whose `valid_from` starts after "now" but before the
conflicting event's own start must still be able to produce a conflict
for that future event; a bare "active right now" check would miss it.
`EventParticipation` has no interval at all (ADR-0023 §4: "at most one
row, ever"), so the participant domain's ordinary-Event branch is a plain
existence check, matching `app.events.authorization._self_condition`'s
own shape exactly.

## Authorization is the existing Event/Occurrence visibility policy, verbatim

ADR-0031 §8: "a caller may receive only conflicts whose participating
concrete objects are within the caller's existing Event visibility/object
policy." This is the *same* policy `GET /api/v1/events`/`GET /api/v1/
events/calendar` already enforce — this module reuses `app.events.
authorization.event_visibility_filter`/`app.events.series_authorization.
occurrence_visibility_filter` directly, unmodified, as the sole
authorization boundary for which candidate objects even enter the
self-join below (see "Query shape"). This is deliberately different from
`app.events.group_schedule`/`app.events.instructor_schedule`, which
implement their *own*, narrower relationship-based visibility — conflict
detection is explicitly scoped by ADR-0031 to the *generic* Event
visibility policy (all/own_events/own_groups/self/children/none), not a
bespoke one.

## Query shape — one indexed self-join per domain, never brute force

For each domain: build a `(object_kind, object_id, shared_key)`
relationship subquery (`UNION ALL` of an Event-level and an Occurrence-
level branch, each joined once to its owning Event/EventOccurrence for the
effectivity check above), then self-join it against itself on
`shared_key`, joining both sides back to one shared "authorized,
in-range, operational" candidate-item subquery (`_candidate_items_
subquery` — the same `UNION ALL` shape as `app.events.calendar.
CalendarItem`, minus the display fields conflicts don't need). A
canonical `(kind, id) < (kind, id)` tuple ordering on the self-join
eliminates self-pairs, reversed duplicates, and non-overlapping rows in
one pass — never a fetch-then-compare-in-Python cross product, and every
join key (`event_id`/`occurrence_id`/`user_id`/`group_id`/`person_id`) is
already indexed by the existing relationship tables. The three domains'
results are `UNION ALL`-ed, given a deterministic string `id` (`domain:
kind:id:kind:id`, using the same canonical pair ordering — same unordered
pair and domain always produce the same id, regardless of which side of
the join happened to be picked first), then ordered/paginated in one final
query — authorization and domain-matching both happen before counting or
pagination, never after.

## `user_id`/`group_id` narrowing

Mirrors `app.events.calendar`'s own `user_id_filter`/`group_id_filter`
precedent: each filter narrows to the one domain it can possibly apply to
(`user_id` -> `instructor` domain rows where the shared User matches;
`group_id` -> `group` domain rows where the shared Group matches) and
excludes the other domains entirely, rather than attempting an
underspecified cross-domain match — `participant` is Person-keyed, not
User-keyed, and neither filter is defined against it anywhere in the
canonical docs. Never widens authorization (ADR-0031 §9).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session, aliased

from app.authorization.service import applicable_assignments
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceParticipant,
    EventOccurrenceStaffAssignment,
    SeriesGroupTarget,
    SeriesStaffAssignment,
)
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment
from app.events.authorization import event_visibility_filter
from app.events.materialization import ensure_materialized
from app.events.series_authorization import occurrence_visibility_filter

# ADR-0031 §4 / events-api.md §28: operational conflict candidates —
# deliberately narrower than app.events.calendar's own calendar-visibility
# status sets (which also include completed/cancelled for historical
# calendar reading).
CONFLICT_EVENT_STATUSES: tuple[str, ...] = ("published", "in_progress")
CONFLICT_OCCURRENCE_STATUSES: tuple[str, ...] = ("scheduled", "in_progress")

_DOMAINS: tuple[str, ...] = ("instructor", "group", "participant")


@dataclass(frozen=True)
class ConflictItem:
    """One derived conflict row, already fully authorized/filtered/
    paginated by the SQL query that produced it."""

    id: str
    domain: str
    first_object_type: str
    first_object_id: uuid.UUID
    first_series_id: Optional[uuid.UUID]
    first_series_version: Optional[int]
    second_object_type: str
    second_object_id: uuid.UUID
    second_series_id: Optional[uuid.UUID]
    second_series_version: Optional[int]
    overlap_start_at: datetime
    overlap_end_at: datetime


def _effective_at(valid_from: Any, valid_to: Any, instant: Any) -> sa.ColumnElement[bool]:
    return sa.and_(valid_from <= instant, sa.or_(valid_to.is_(None), instant < valid_to))


def _candidate_items_subquery(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    from_at: datetime,
    to_at: datetime,
) -> Any:
    """Authorized, in-range, operational-status candidate Event/
    EventOccurrence rows — the universe both sides of every domain
    self-join are drawn from. Range bound is genuine interval overlap
    against `[from_at, to_at)` (`start_at < to_at AND end_at > from_at`),
    not a start-time window: an already-ongoing object that started
    before `from_at` can still be a live conflict candidate."""
    event_conditions: list[sa.ColumnElement[bool]] = [
        Event.start_at < to_at,
        Event.end_at > from_at,
        Event.status.in_(CONFLICT_EVENT_STATUSES),
        event_visibility_filter(session, user_id=user_id, permission_code=permission_code),
    ]
    event_items = sa.select(
        Event.id.label("id"),
        sa.literal("event").label("kind"),
        Event.club_id.label("club_id"),
        Event.start_at.label("start_at"),
        Event.end_at.label("end_at"),
        sa.cast(sa.null(), PG_UUID(as_uuid=True)).label("series_id"),
        sa.cast(sa.null(), sa.Integer).label("series_version"),
    ).where(*event_conditions)

    occurrence_conditions: list[sa.ColumnElement[bool]] = [
        EventOccurrence.starts_at < to_at,
        EventOccurrence.ends_at > from_at,
        EventOccurrence.status.in_(CONFLICT_OCCURRENCE_STATUSES),
        occurrence_visibility_filter(session, user_id=user_id, permission_code=permission_code),
    ]
    occurrence_items = (
        sa.select(
            EventOccurrence.id.label("id"),
            sa.literal("occurrence").label("kind"),
            EventOccurrence.club_id.label("club_id"),
            EventOccurrence.starts_at.label("start_at"),
            EventOccurrence.ends_at.label("end_at"),
            EventOccurrence.series_id.label("series_id"),
            EventSeries.version.label("series_version"),
        )
        .join(EventSeries, EventSeries.id == EventOccurrence.series_id)
        .where(*occurrence_conditions)
    )

    return sa.union_all(event_items, occurrence_items).subquery("candidate_items")


def _instructor_relationship_subquery() -> Any:
    event_branch = (
        sa.select(
            sa.literal("event").label("object_kind"),
            EventStaffAssignment.event_id.label("object_id"),
            EventStaffAssignment.user_id.label("shared_key"),
        )
        .join(Event, Event.id == EventStaffAssignment.event_id)
        .where(
            _effective_at(
                EventStaffAssignment.valid_from, EventStaffAssignment.valid_to, Event.start_at
            )
        )
    )
    occurrence_branch = (
        sa.select(
            sa.literal("occurrence").label("object_kind"),
            EventOccurrenceStaffAssignment.occurrence_id.label("object_id"),
            EventOccurrenceStaffAssignment.user_id.label("shared_key"),
        )
        .join(EventOccurrence, EventOccurrence.id == EventOccurrenceStaffAssignment.occurrence_id)
        .where(
            _effective_at(
                EventOccurrenceStaffAssignment.valid_from,
                EventOccurrenceStaffAssignment.valid_to,
                EventOccurrence.starts_at,
            )
        )
    )
    return sa.union_all(event_branch, occurrence_branch).subquery("instructor_relationships")


def _group_relationship_subquery() -> Any:
    event_branch = (
        sa.select(
            sa.literal("event").label("object_kind"),
            EventGroupTarget.event_id.label("object_id"),
            EventGroupTarget.group_id.label("shared_key"),
        )
        .join(Event, Event.id == EventGroupTarget.event_id)
        .where(
            _effective_at(EventGroupTarget.valid_from, EventGroupTarget.valid_to, Event.start_at)
        )
    )
    occurrence_branch = (
        sa.select(
            sa.literal("occurrence").label("object_kind"),
            EventOccurrenceGroupTarget.occurrence_id.label("object_id"),
            EventOccurrenceGroupTarget.group_id.label("shared_key"),
        )
        .join(EventOccurrence, EventOccurrence.id == EventOccurrenceGroupTarget.occurrence_id)
        .where(
            _effective_at(
                EventOccurrenceGroupTarget.valid_from,
                EventOccurrenceGroupTarget.valid_to,
                EventOccurrence.starts_at,
            )
        )
    )
    return sa.union_all(event_branch, occurrence_branch).subquery("group_relationships")


def _participant_relationship_subquery() -> Any:
    # EventParticipation has no [valid_from, valid_to) at all (ADR-0023
    # §4) -- plain existence, mirroring app.events.authorization.
    # _self_condition exactly.
    event_branch = sa.select(
        sa.literal("event").label("object_kind"),
        EventParticipation.event_id.label("object_id"),
        EventParticipation.person_id.label("shared_key"),
    )
    occurrence_branch = (
        sa.select(
            sa.literal("occurrence").label("object_kind"),
            EventOccurrenceParticipant.occurrence_id.label("object_id"),
            EventOccurrenceParticipant.person_id.label("shared_key"),
        )
        .join(EventOccurrence, EventOccurrence.id == EventOccurrenceParticipant.occurrence_id)
        .where(
            _effective_at(
                EventOccurrenceParticipant.valid_from,
                EventOccurrenceParticipant.valid_to,
                EventOccurrence.starts_at,
            )
        )
    )
    return sa.union_all(event_branch, occurrence_branch).subquery("participant_relationships")


def _domain_conflicts_query(
    domain: str,
    relationship_subquery: Any,
    candidate_items: Any,
    *,
    shared_key_filter: Optional[uuid.UUID],
) -> sa.Select[Any]:
    """One indexed self-join producing every conflicting pair for
    `domain`, covering all four Event/EventOccurrence combinations at
    once via the canonical `(kind, id)` tuple ordering (see module
    docstring)."""
    left_rel = relationship_subquery.alias(f"{domain}_left_rel")
    right_rel = relationship_subquery.alias(f"{domain}_right_rel")
    left_items = candidate_items.alias(f"{domain}_left_items")
    right_items = candidate_items.alias(f"{domain}_right_items")

    overlap_start_at = sa.func.greatest(left_items.c.start_at, right_items.c.start_at)
    overlap_end_at = sa.func.least(left_items.c.end_at, right_items.c.end_at)

    conditions: list[sa.ColumnElement[bool]] = [
        sa.tuple_(left_rel.c.object_kind, left_rel.c.object_id)
        < sa.tuple_(right_rel.c.object_kind, right_rel.c.object_id),
        overlap_start_at < overlap_end_at,
    ]
    if shared_key_filter is not None:
        conditions.append(left_rel.c.shared_key == shared_key_filter)

    return (
        sa.select(
            sa.literal(domain).label("domain"),
            left_items.c.kind.label("a_kind"),
            left_items.c.id.label("a_id"),
            left_items.c.series_id.label("a_series_id"),
            left_items.c.series_version.label("a_series_version"),
            right_items.c.kind.label("b_kind"),
            right_items.c.id.label("b_id"),
            right_items.c.series_id.label("b_series_id"),
            right_items.c.series_version.label("b_series_version"),
            overlap_start_at.label("overlap_start_at"),
            overlap_end_at.label("overlap_end_at"),
        )
        .select_from(left_rel)
        .join(right_rel, right_rel.c.shared_key == left_rel.c.shared_key)
        .join(
            left_items,
            sa.and_(
                left_items.c.kind == left_rel.c.object_kind, left_items.c.id == left_rel.c.object_id
            ),
        )
        .join(
            right_items,
            sa.and_(
                right_items.c.kind == right_rel.c.object_kind,
                right_items.c.id == right_rel.c.object_id,
            ),
        )
        .where(*conditions)
    )


def _extend_conflict_materialization(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    user_id_filter: Optional[uuid.UUID],
    group_id_filter: Optional[uuid.UUID],
    until: datetime,
) -> None:
    """ADR-0015 §4 / ADR-0028: extend the materialized horizon to cover
    `until`, bounded exactly like the equally-scoped extension in
    `app.events.group_schedule`/`app.events.instructor_schedule` — never a
    blanket "every series in the system" pass."""
    terminal = aliased(EventSeries)
    is_terminal = ~sa.exists(
        sa.select(terminal.id).where(terminal.supersedes_series_id == EventSeries.id)
    )

    if group_id_filter is not None:
        stmt = (
            sa.select(EventSeries)
            .join(SeriesGroupTarget, SeriesGroupTarget.event_series_id == EventSeries.id)
            .where(
                EventSeries.status == "active",
                is_terminal,
                SeriesGroupTarget.group_id == group_id_filter,
            )
            .distinct()
        )
    elif user_id_filter is not None:
        via_staff = sa.exists(
            sa.select(SeriesStaffAssignment.id).where(
                SeriesStaffAssignment.event_series_id == EventSeries.id,
                SeriesStaffAssignment.user_id == user_id_filter,
            )
        )
        via_group = sa.exists(
            sa.select(SeriesGroupTarget.id)
            .join(Group, Group.id == SeriesGroupTarget.group_id)
            .join(GroupInstructorAssignment, GroupInstructorAssignment.group_id == Group.id)
            .where(
                SeriesGroupTarget.event_series_id == EventSeries.id,
                GroupInstructorAssignment.user_id == user_id_filter,
            )
        )
        stmt = sa.select(EventSeries).where(
            EventSeries.status == "active", is_terminal, sa.or_(via_staff, via_group)
        )
    else:
        # No narrowing filter: bounded to Clubs the requester holds
        # `all`-scope event.read access for -- the same bound
        # app.events.calendar._extend_recurring_materialization uses.
        assignments = applicable_assignments(session, user_id, permission_code)
        all_scope_club_ids = [a.club_id for a in assignments if a.scope_type == "all"]
        if not all_scope_club_ids:
            return
        stmt = sa.select(EventSeries).where(EventSeries.status == "active", is_terminal)
        if not any(club_id is None for club_id in all_scope_club_ids):
            stmt = stmt.where(EventSeries.club_id.in_(all_scope_club_ids))

    for series in session.execute(stmt).scalars().all():
        ensure_materialized(session, series=series, until=until)


def list_conflicts_page(
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
) -> tuple[list[ConflictItem], int]:
    """Return `(items, total)` of derived conflicts for `[from_at,
    to_at)`, fully authorized, filtered, sorted (`overlap_start_at ASC,
    id ASC`) and paginated inside SQL. Both objects of every returned
    conflict have already independently passed the caller's own Event/
    Occurrence visibility policy (see module docstring) -- an
    inaccessible opposing object never reaches this far."""
    _extend_conflict_materialization(
        session,
        user_id=user_id,
        permission_code=permission_code,
        user_id_filter=user_id_filter,
        group_id_filter=group_id_filter,
        until=to_at,
    )

    candidate_items = _candidate_items_subquery(
        session, user_id=user_id, permission_code=permission_code, from_at=from_at, to_at=to_at
    )

    domain_queries = {
        "instructor": _domain_conflicts_query(
            "instructor",
            _instructor_relationship_subquery(),
            candidate_items,
            shared_key_filter=user_id_filter,
        ),
        "group": _domain_conflicts_query(
            "group",
            _group_relationship_subquery(),
            candidate_items,
            shared_key_filter=group_id_filter,
        ),
        "participant": _domain_conflicts_query(
            "participant",
            _participant_relationship_subquery(),
            candidate_items,
            shared_key_filter=None,
        ),
    }
    # user_id/group_id each narrow to the one domain they apply to and
    # exclude the others entirely -- see module docstring.
    if user_id_filter is not None:
        del domain_queries["group"]
        del domain_queries["participant"]
    elif group_id_filter is not None:
        del domain_queries["instructor"]
        del domain_queries["participant"]

    conflicts = sa.union_all(*domain_queries.values()).subquery("conflicts")

    # Deterministic id: domain + canonically-ordered (kind, id) pair --
    # same unordered pair + domain always yields the same id.
    id_column = sa.func.concat(
        conflicts.c.domain,
        sa.literal(":"),
        conflicts.c.a_kind,
        sa.literal(":"),
        sa.cast(conflicts.c.a_id, sa.String),
        sa.literal(":"),
        conflicts.c.b_kind,
        sa.literal(":"),
        sa.cast(conflicts.c.b_id, sa.String),
    ).label("id")
    with_id = sa.select(*conflicts.c, id_column).subquery("conflicts_with_id")

    total = session.execute(sa.select(sa.func.count()).select_from(with_id)).scalar_one()
    rows = session.execute(
        sa.select(with_id)
        .order_by(with_id.c.overlap_start_at.asc(), with_id.c.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        ConflictItem(
            id=row.id,
            domain=row.domain,
            first_object_type=row.a_kind,
            first_object_id=row.a_id,
            first_series_id=row.a_series_id,
            first_series_version=row.a_series_version,
            second_object_type=row.b_kind,
            second_object_id=row.b_id,
            second_series_id=row.b_series_id,
            second_series_version=row.b_series_version,
            overlap_start_at=row.overlap_start_at,
            overlap_end_at=row.overlap_end_at,
        )
        for row in rows
    ]
    return items, total


__all__ = [
    "CONFLICT_EVENT_STATUSES",
    "CONFLICT_OCCURRENCE_STATUSES",
    "ConflictItem",
    "list_conflicts_page",
]
