"""EventSeries/EventOccurrence authorization scope resolution (Issue #79,
ADR-0028 §11; upgraded for occurrence authorization by Issue #85 / TH-0082,
ADR-0029, ADR-0030).

ADR-0028 §11 is explicit: "Series and occurrence operations use the
canonical Event permissions and scopes. No separate recurrence permission
is introduced" — this module resolves those same permissions
(`event.read`/`event.update`/`event.manage`/etc.) and the same canonical
scope vocabulary (ADR-0013) against EventSeries/EventOccurrence resources,
reusing app.authorization.service's shared engine exactly as
app.events.authorization does for Event.

## EventSeries resource authorization — unchanged

`SeriesStaffAssignment`/`SeriesGroupTarget`/`SeriesParticipant` (ADR-0030)
are the relationship *source* for occurrence materialization; ADR-0029/
ADR-0030 define `own_events`/`own_groups`/`self`/`children` only for
recurring `EventOccurrence` resources, never for the `EventSeries`
resource itself (the Series lifecycle/update endpoints — pause/resume/
cancel/archive/PATCH). `series_visibility_filter`/
`build_series_resource_context` therefore remain exactly as Issue #79
left them: only an `all`-scope grant can satisfy a Series-
resource permission check. Extending Series-resource authorization to use
the relationship source directly is a different, not-yet-canonically-
specified decision and is not made here.

## EventOccurrence resource authorization — ADR-0029/ADR-0030

`own_events`/`own_groups`/`self`/`children` are now resolved against real,
already-materialized occurrence-level relationship data
(`EventOccurrenceStaffAssignment`/`EventOccurrenceGroupTarget`/
`EventOccurrenceParticipant` — app.db.event_recurrence_relationships),
mirroring app.events.authorization's own predicate shapes field-for-field
but keyed to `occurrence_id` instead of `event_id`. `occurrence.club_id`
alone is never sufficient for any of these four scopes (ADR-0030 —
enforced structurally here: every non-`all` predicate below requires an
EXISTS against real relationship rows, never a bare club_id comparison).

Two call shapes, exactly matching app.events.authorization's own split:

- Single-occurrence resolution: `build_occurrence_resource_context()` runs
  each predicate as its own scalar EXISTS query against one already-loaded
  EventOccurrence.
- List-level filtering (the calendar projection, app.events.calendar):
  `occurrence_visibility_filter()` builds one predicate per applicable
  UserRoleAssignment and ORs them together, correlated against
  `EventOccurrence.id`/`EventOccurrence.club_id` in the outer query — so
  authorization runs *inside* the SQL query itself, never fetch-then-
  filter-in-Python.

`children` follows ADR-0042/ADR-0043 (TH-0172 / Issue #213): an
occurrence is readable through an eligible child via EITHER an active
`EventOccurrenceParticipant` row (the original ADR-0029/ADR-0030 path) OR
the child's active `GroupMembership` in a Group with an active
`EventOccurrenceGroupTarget` for the occurrence — the same two paths, and
the same relationship/lifecycle checks, as app.events.authorization's own
`Event` `_child_condition`. ADR-0043 §2 makes the group path mandatory: a
Guardian must not have to wait for the child's direct participation to
see a recurring group activity.

`self` depends on the permission being checked (Issue #285, PO decision
2026-10-06, ADR-0029): for `event.read` it is the Member Event object
policy of ADR-0020 §2/§3 applied at occurrence level
(`_member_occurrence_read_condition` — a recurring occurrence with no
active `EventOccurrenceGroupTarget` is club-wide within the requester's
active ClubMembership; a targeted one needs an active GroupMembership in
a target Group; `EventOccurrenceParticipant` neither required nor by
itself sufficient). It applies to recurring occurrences only
(`series_id IS NOT NULL`): the occurrence backing an ordinary Event never
has occurrence-level relationship rows (ADR-0033) and is authorized
through its Event, so treating it as "untargeted" here would expose it.
Every other permission keeps the participation-based `_self_condition`.

Every nested EXISTS below that references the outer occurrence explicitly
correlates against `EventOccurrence`/the immediately-enclosing aliased
table via `.correlate(...)`, rather than relying on SQLAlchemy's default
auto-correlation: a nested EXISTS more than one SELECT deep does not
reliably auto-correlate to a table two levels up, silently degenerating
into an unrestricted cross join — see the historical Event-level
`_child_condition` regression this exact pattern caused, fixed in the
internal-calendar-projection PR. Applied proactively here rather than
reproducing that defect in the new occurrence predicates.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_grants
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceParticipant,
    EventOccurrenceStaffAssignment,
)
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, GuardianRelationship, User

_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GUARDIAN_RELATIONSHIP_STATUS = "active"
_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"
_EVENT_READ_PERMISSION = "event.read"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _own_occurrence_condition(occurrence_id: Any, user_id: Any) -> sa.ColumnElement[bool]:
    """ADR-0029/ADR-0030: `own_events` is exclusively an active
    `EventOccurrenceStaffAssignment` — never `EventOccurrence.club_id`,
    never role inference."""
    return sa.exists(
        sa.select(EventOccurrenceStaffAssignment.id).where(
            EventOccurrenceStaffAssignment.occurrence_id == occurrence_id,
            EventOccurrenceStaffAssignment.user_id == user_id,
            _active_interval(
                EventOccurrenceStaffAssignment.valid_from, EventOccurrenceStaffAssignment.valid_to
            ),
        )
    )


def _own_group_condition(
    occurrence_id: Any, occurrence_club_id: Any, user_id: Any
) -> sa.ColumnElement[bool]:
    """ADR-0029/ADR-0030 + ADR-0022: an active
    `EventOccurrenceGroupTarget` for the occurrence, targeting a Group the
    user is an active `GroupInstructorAssignment` for, with the Group's
    Club matching the occurrence's Club (mandatory, never skipped)."""
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
            _active_interval(ogt.valid_from, ogt.valid_to),
            _active_interval(gia.valid_from, gia.valid_to),
        )
    )


def _self_condition(occurrence_id: Any, person_id: Any) -> sa.ColumnElement[bool]:
    """ADR-0029/ADR-0030: `self` is resolved via an active
    `EventOccurrenceParticipant` row."""
    return sa.exists(
        sa.select(EventOccurrenceParticipant.id).where(
            EventOccurrenceParticipant.occurrence_id == occurrence_id,
            EventOccurrenceParticipant.person_id == person_id,
            _active_interval(
                EventOccurrenceParticipant.valid_from, EventOccurrenceParticipant.valid_to
            ),
        )
    )


def _member_occurrence_read_condition(
    occurrence_id: Any, occurrence_club_id: Any, occurrence_series_id: Any, person_id: Any
) -> sa.ColumnElement[bool]:
    """ADR-0029 `self` for `event.read` (Issue #285): the Member Event
    object policy of ADR-0020 §2/§3 at occurrence level — a recurring
    occurrence, the requester's Person with an active ClubMembership in
    the occurrence's Club, AND either no active `EventOccurrenceGroupTarget`
    (club-wide) or an active GroupMembership — through that same Club's
    active ClubMembership — in a Group of that Club with an active
    `EventOccurrenceGroupTarget` for the occurrence. See module docstring
    for the recurring-only restriction. Each EXISTS is one level deep and
    explicitly correlated to the outer occurrence."""
    if occurrence_series_id is None:
        return sa.false()
    is_recurring: sa.ColumnElement[bool] = (
        sa.true()
        if isinstance(occurrence_series_id, uuid.UUID)
        else occurrence_series_id.is_not(None)
    )

    club_membership = aliased(ClubMembership)
    has_active_club_membership = sa.exists(
        sa.select(club_membership.id)
        .where(
            club_membership.person_id == person_id,
            club_membership.club_id == occurrence_club_id,
            club_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
        .correlate(EventOccurrence)
    )

    any_target = aliased(EventOccurrenceGroupTarget)
    has_active_group_target = sa.exists(
        sa.select(any_target.id)
        .where(
            any_target.occurrence_id == occurrence_id,
            _active_interval(any_target.valid_from, any_target.valid_to),
        )
        .correlate(EventOccurrence)
    )

    target = aliased(EventOccurrenceGroupTarget)
    grp = aliased(Group)
    group_membership = aliased(GroupMembership)
    member_club_membership = aliased(ClubMembership)
    targets_member_group = sa.exists(
        sa.select(target.id)
        .join(grp, grp.id == target.group_id)
        .join(group_membership, group_membership.group_id == grp.id)
        .join(
            member_club_membership, member_club_membership.id == group_membership.club_membership_id
        )
        .where(
            target.occurrence_id == occurrence_id,
            _active_interval(target.valid_from, target.valid_to),
            grp.club_id == occurrence_club_id,
            group_membership.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(group_membership.valid_from, group_membership.valid_to),
            member_club_membership.person_id == person_id,
            member_club_membership.club_id == occurrence_club_id,
            member_club_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
        .correlate(EventOccurrence)
    )

    return sa.and_(
        is_recurring,
        has_active_club_membership,
        sa.or_(sa.not_(has_active_group_target), targets_member_group),
    )


def _self_scope_condition(
    permission_code: str,
    occurrence_id: Any,
    occurrence_club_id: Any,
    occurrence_series_id: Any,
    person_id: Any,
) -> sa.ColumnElement[bool]:
    """`self` for `permission_code` — see module docstring."""
    if permission_code == _EVENT_READ_PERMISSION:
        return _member_occurrence_read_condition(
            occurrence_id, occurrence_club_id, occurrence_series_id, person_id
        )
    return _self_condition(occurrence_id, person_id)


def _child_condition(
    occurrence_id: Any, occurrence_club_id: Any, guardian_person_id: Any
) -> sa.ColumnElement[bool]:
    """ADR-0029/ADR-0030 + ADR-0042/ADR-0043: guardian access requires an
    active GuardianRelationship AND the child's active ClubMembership in
    the occurrence's Club AND the guardian's own active ClubMembership in
    that same Club, AND (an active `EventOccurrenceParticipant` row for
    the child on this occurrence OR the child's active GroupMembership in
    a Group with an active `EventOccurrenceGroupTarget` for this
    occurrence). Mirrors app.events.authorization's `Event`
    `_child_condition` path-for-path. Never unrestricted guardian access,
    never plain `guardian_person_id` filtering alone.
    """
    guardian_has_membership = sa.exists(
        sa.select(ClubMembership.id).where(
            ClubMembership.person_id == guardian_person_id,
            ClubMembership.club_id == occurrence_club_id,
            ClubMembership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )

    gr = aliased(GuardianRelationship)
    child_membership = aliased(ClubMembership)
    participation = aliased(EventOccurrenceParticipant)
    group_membership = aliased(GroupMembership)
    group_target = aliased(EventOccurrenceGroupTarget)

    # Correlated two levels deep (past `eligible_child_exists` up to the
    # true outer occurrence/club_id) — explicit `.correlate(...)` rather
    # than relying on automatic correlation. See module docstring.
    child_via_participation = sa.exists(
        sa.select(participation.id)
        .where(
            participation.occurrence_id == occurrence_id,
            participation.person_id == gr.child_person_id,
            _active_interval(participation.valid_from, participation.valid_to),
        )
        .correlate(EventOccurrence, gr)
    )
    child_via_group_target = sa.exists(
        sa.select(group_membership.id)
        .join(group_target, group_target.group_id == group_membership.group_id)
        .where(
            group_membership.club_membership_id == child_membership.id,
            group_membership.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(group_membership.valid_from, group_membership.valid_to),
            group_target.occurrence_id == occurrence_id,
            _active_interval(group_target.valid_from, group_target.valid_to),
        )
        .correlate(EventOccurrence, child_membership)
    )

    eligible_child_exists = sa.exists(
        sa.select(gr.id)
        .join(
            child_membership,
            sa.and_(
                child_membership.person_id == gr.child_person_id,
                child_membership.club_id == occurrence_club_id,
                child_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
            ),
        )
        .where(
            gr.guardian_person_id == guardian_person_id,
            gr.status == _ACTIVE_GUARDIAN_RELATIONSHIP_STATUS,
            _active_interval(gr.valid_from, gr.valid_to),
            sa.or_(child_via_participation, child_via_group_target),
        )
    )

    return sa.and_(guardian_has_membership, eligible_child_exists)


def build_series_resource_context(*, series: EventSeries) -> ResourceContext:
    """See module docstring "EventSeries resource authorization —
    unchanged": only the Club boundary is resolvable for an EventSeries
    resource."""
    return ResourceContext(club_id=series.club_id)


def build_occurrence_resource_context(
    session: Session, *, occurrence: EventOccurrence, user_id: uuid.UUID, permission_code: str
) -> ResourceContext:
    """Resolve the full ResourceContext for one already-loaded
    EventOccurrence against the acting user (ADR-0029/ADR-0030), running
    each relationship predicate as its own scalar EXISTS query — mirrors
    app.events.authorization.build_event_resource_context exactly,
    including the `permission_code`-dependent meaning of `self`."""
    person_id = _person_id_for_user(session, user_id)
    is_own_event = session.execute(
        sa.select(_own_occurrence_condition(occurrence.id, user_id))
    ).scalar()
    is_own_group = session.execute(
        sa.select(_own_group_condition(occurrence.id, occurrence.club_id, user_id))
    ).scalar()
    is_self = session.execute(
        sa.select(
            _self_scope_condition(
                permission_code,
                occurrence.id,
                occurrence.club_id,
                occurrence.series_id,
                person_id,
            )
        )
    ).scalar()
    is_child = session.execute(
        sa.select(_child_condition(occurrence.id, occurrence.club_id, person_id))
    ).scalar()
    return ResourceContext(
        club_id=occurrence.club_id,
        is_self=bool(is_self),
        is_child=bool(is_child),
        is_own_group=bool(is_own_group),
        is_own_event=bool(is_own_event),
    )


def _all_scope_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str, club_id_column: Any
) -> sa.ColumnElement[bool]:
    """`all`-scope-only predicate — still used as-is by
    `series_visibility_filter` (see module docstring)."""
    grants = applicable_grants(session, user_id, permission_code)
    all_scope_club_ids = [a.club_id for a in grants if a.scope_type == "all"]
    if not all_scope_club_ids:
        return sa.false()
    if any(club_id is None for club_id in all_scope_club_ids):
        return sa.true()
    return club_id_column.in_(all_scope_club_ids)


def series_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    return _all_scope_visibility_filter(
        session,
        user_id=user_id,
        permission_code=permission_code,
        club_id_column=EventSeries.club_id,
    )


def occurrence_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate to apply directly to an EventOccurrence list
    query (a `.where(...)` clause referencing `EventOccurrence.id`/
    `EventOccurrence.club_id`), true only for occurrences the acting user
    is authorized to see under `permission_code`, per every applicable
    UserRoleAssignment (ADR-0029/ADR-0030). Never fetch-then-filter-in-
    Python — required so pagination/totals/offsets never leak an
    unauthorized row (mirrors app.events.authorization.
    event_visibility_filter exactly)."""
    grants = applicable_grants(session, user_id, permission_code)
    if not grants:
        return sa.false()

    needs_person = any(a.scope_type in ("self", "children") for a in grants)
    person_id = _person_id_for_user(session, user_id) if needs_person else None

    clauses: list[sa.ColumnElement[bool]] = []
    for grant in grants:
        club_boundary: sa.ColumnElement[bool] = (
            sa.true() if grant.club_id is None else EventOccurrence.club_id == grant.club_id
        )
        if grant.scope_type == "all":
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif grant.scope_type == "own_events":
            scope_predicate = _own_occurrence_condition(EventOccurrence.id, user_id)
        elif grant.scope_type == "own_groups":
            scope_predicate = _own_group_condition(
                EventOccurrence.id, EventOccurrence.club_id, user_id
            )
        elif grant.scope_type == "self":
            scope_predicate = _self_scope_condition(
                permission_code,
                EventOccurrence.id,
                EventOccurrence.club_id,
                EventOccurrence.series_id,
                person_id,
            )
        elif grant.scope_type == "children":
            scope_predicate = _child_condition(
                EventOccurrence.id, EventOccurrence.club_id, person_id
            )
        elif grant.scope_type == "none":
            scope_predicate = sa.false()
        else:  # pragma: no cover - unreachable, DB CHECK constraint guards this
            raise ValueError(f"Unhandled scope_type: {grant.scope_type!r}")
        clauses.append(sa.and_(club_boundary, scope_predicate))

    return sa.or_(*clauses)


__all__ = [
    "build_series_resource_context",
    "build_occurrence_resource_context",
    "series_visibility_filter",
    "occurrence_visibility_filter",
]
