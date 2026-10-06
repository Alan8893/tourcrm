"""Event authorization scope resolution (Issue #40).

Canonical sources: docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/03-architecture/adr/ADR-0022-cross-club-ownership-integrity.md,
docs/03-architecture/adr/ADR-0023-event-relationships-and-guardian-
persistence.md §1/§2/§3/§4, docs/02-requirements/roles-and-permissions.md
§10/§11/§12.

This module resolves the 4 relationship-based canonical scopes
(`own_events`, `own_groups`, `self`, `children`) against real,
already-persisted relationship data — never via `Event.created_by`, never
via role inference, never via a temporary/shortcut mapping. `all`/`none`
need no resolution here (see app.authorization.service.scope_matches).

Two call shapes share the same SQL-predicate builders below:

- Single-Event resolution (detail/update/status/archive endpoints):
  `build_event_resource_context()` runs each predicate as its own scalar
  EXISTS query against one already-loaded Event.
- List-level filtering (the list endpoint): `event_visibility_filter()`
  builds one predicate per applicable UserRoleAssignment (via
  app.authorization.service.applicable_grants) and ORs them
  together, correlated against `Event.id`/`Event.club_id` in the outer
  query — so authorization is applied *inside* the SQL query itself,
  never by fetching rows first and filtering in Python (required so an
  unauthorized row can never leak through pagination/totals/offsets).

"Active"/"currently valid" semantics used throughout, per the interval-
containment convention already established for EventStaffAssignment/
EventGroupTarget/GroupInstructorAssignment/GroupMembership/
GuardianRelationship: `valid_from <= now() AND (valid_to IS NULL OR
now() < valid_to)`. ClubMembership is the one exception (`status ==
'active'` alone, no interval math) — see
app.authorization.club_ownership, the same precedent reused here.

`own_groups` bakes in the mandatory `Group.club_id == Event.club_id`
check (ADR-0022) directly into the join/where clause; `children` bakes
in the guardian's own active ClubMembership in the Event's Club and the
child's active ClubMembership in that same Club (ADR-0023 §3/§4) — never
inferred, never skipped.

`self` depends on the permission being checked (Issue #285, PO decision
2026-10-06): for `event.read` it is the Member Event object policy of
ADR-0020 §2/§3 (`_member_event_read_condition` — club-wide Events within
the requester's active ClubMembership, Group-targeted Events through an
active GroupMembership in a target Group; EventParticipation neither
required nor by itself sufficient; `draft` excluded per ADR-0018). Every
other permission that reuses these Event relationships with `self`
(`trip.read`/`trip.manage`, `attendance.*`) keeps the participation-based
`_self_condition`, unchanged.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_grants
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, GuardianRelationship, User

_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GUARDIAN_RELATIONSHIP_STATUS = "active"
_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"
_EVENT_READ_PERMISSION = "event.read"
_DRAFT_STATUS = "draft"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _effective_at(valid_from: Any, valid_to: Any, instant: Any) -> sa.ColumnElement[bool]:
    return sa.and_(valid_from <= instant, sa.or_(valid_to.is_(None), instant < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _own_event_condition(event_id, user_id) -> sa.ColumnElement[bool]:
    """ADR-0023 §1: `own_events` is exclusively an active
    EventStaffAssignment — never `Event.created_by`, never role
    inference.
    """
    return sa.exists(
        sa.select(EventStaffAssignment.id).where(
            EventStaffAssignment.event_id == event_id,
            EventStaffAssignment.user_id == user_id,
            _active_interval(EventStaffAssignment.valid_from, EventStaffAssignment.valid_to),
        )
    )


def _own_group_condition(event_id, event_club_id, user_id) -> sa.ColumnElement[bool]:
    """ADR-0023 §2 + ADR-0022: an active EventGroupTarget for the Event,
    targeting a Group the user is an active GroupInstructorAssignment
    for, with the Group's Club matching the Event's Club (mandatory,
    never skipped).
    """
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
            _active_interval(egt.valid_from, egt.valid_to),
            _active_interval(gia.valid_from, gia.valid_to),
        )
    )


def _self_condition(event_id, person_id) -> sa.ColumnElement[bool]:
    """ADR-0023 §4: `self` is resolved via an existing EventParticipation
    row — no self-registration API is implemented or assumed here.
    """
    return sa.exists(
        sa.select(EventParticipation.id).where(
            EventParticipation.event_id == event_id,
            EventParticipation.person_id == person_id,
        )
    )


def _member_event_read_condition(
    event_id, event_club_id, event_status, event_start_at, person_id
) -> sa.ColumnElement[bool]:
    """ADR-0020 §2/§3 Member Event object policy (`event.read` + `self`,
    Issue #285): the requester's Person has an active ClubMembership in
    the Event's Club AND either the Event has no active EventGroupTarget
    (club-wide Event) or that Person has an active GroupMembership —
    through that same Club's active ClubMembership — in a Group of the
    Event's Club with an active EventGroupTarget for the Event. Never
    EventParticipation (neither required nor sufficient), never an
    ended/historical membership, never another Club. A `draft` Event is
    not available before publication (ADR-0018).

    Whether the Event is targeted is decided by each EventGroupTarget's
    `[valid_from, valid_to)` at the Event's own `start_at` — the same
    effectivity rule as the Group Schedule (app.events.group_schedule,
    ADR-0029/ADR-0030) — so a target that starts later than "now" still
    makes the Event targeted and never lets it fall back to club-wide
    (fail closed). The requester's GroupMembership/ClubMembership are
    evaluated now: only a current membership authorizes.

    Every EXISTS below is one level deep and explicitly correlated to the
    outer Event (a no-op on the single-Event path, where the Event's
    values are literals).
    """
    club_membership = aliased(ClubMembership)
    has_active_club_membership = sa.exists(
        sa.select(club_membership.id)
        .where(
            club_membership.person_id == person_id,
            club_membership.club_id == event_club_id,
            club_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
        .correlate(Event)
    )

    any_target = aliased(EventGroupTarget)
    has_active_group_target = sa.exists(
        sa.select(any_target.id)
        .where(
            any_target.event_id == event_id,
            _effective_at(any_target.valid_from, any_target.valid_to, event_start_at),
        )
        .correlate(Event)
    )

    target = aliased(EventGroupTarget)
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
            target.event_id == event_id,
            _effective_at(target.valid_from, target.valid_to, event_start_at),
            grp.club_id == event_club_id,
            group_membership.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(group_membership.valid_from, group_membership.valid_to),
            member_club_membership.person_id == person_id,
            member_club_membership.club_id == event_club_id,
            member_club_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
        .correlate(Event)
    )

    # `event_status` is the outer `Event.status` column on the list path
    # and an already-loaded Python value on the single-Event path.
    not_draft: sa.ColumnElement[bool]
    if isinstance(event_status, str):
        not_draft = sa.true() if event_status != _DRAFT_STATUS else sa.false()
    else:
        not_draft = event_status != _DRAFT_STATUS

    return sa.and_(
        not_draft,
        has_active_club_membership,
        sa.or_(sa.not_(has_active_group_target), targets_member_group),
    )


def _self_scope_condition(
    permission_code: str, event_id, event_club_id, event_status, event_start_at, person_id
) -> sa.ColumnElement[bool]:
    """`self` for `permission_code` — see module docstring."""
    if permission_code == _EVENT_READ_PERMISSION:
        return _member_event_read_condition(
            event_id, event_club_id, event_status, event_start_at, person_id
        )
    return _self_condition(event_id, person_id)


def _child_condition(event_id, event_club_id, guardian_person_id) -> sa.ColumnElement[bool]:
    """ADR-0023 §3/§4: guardian access requires an active
    GuardianRelationship AND the child's active ClubMembership in the
    Event's Club AND the guardian's own active ClubMembership in that
    same Club, AND (the child has an EventParticipation for this Event
    OR the child has an active GroupMembership in a Group with an active
    EventGroupTarget for this Event). Never unrestricted guardian access,
    never plain `guardian_person_id` filtering alone.
    """
    guardian_has_membership = sa.exists(
        sa.select(ClubMembership.id).where(
            ClubMembership.person_id == guardian_person_id,
            ClubMembership.club_id == event_club_id,
            ClubMembership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )

    gr = aliased(GuardianRelationship)
    child_membership = aliased(ClubMembership)
    participation = aliased(EventParticipation)
    group_membership = aliased(GroupMembership)
    group_target = aliased(EventGroupTarget)

    # Explicit `.correlate(...)` below (rather than relying on
    # SQLAlchemy's automatic correlation) because these two EXISTS
    # subqueries are nested *two* levels deep inside `eligible_child_exists`
    # (itself an EXISTS wrapping the guardian/child-membership join): left
    # to automatic correlation, SQLAlchemy only correlates one enclosing
    # SELECT out and re-includes the true outer `events`/middle-level `gr`
    # table in *this* subquery's own FROM clause instead — turning it into
    # an unrestricted cross join that is satisfied by any row and defeats
    # the whole per-Event check (list-level `event_visibility_filter`
    # embeds `event_id`/`event_club_id` as real correlated columns of the
    # outer Event being tested, unlike the single-object call path in
    # `build_event_resource_context`, where they are literal values and no
    # correlation is needed at all — this explicit `.correlate()` is a
    # no-op there, but load-bearing here).
    child_via_participation = sa.exists(
        sa.select(participation.id)
        .where(
            participation.event_id == event_id,
            participation.person_id == gr.child_person_id,
        )
        .correlate(Event, gr)
    )
    child_via_group_target = sa.exists(
        sa.select(group_membership.id)
        .join(group_target, group_target.group_id == group_membership.group_id)
        .where(
            group_membership.club_membership_id == child_membership.id,
            group_membership.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(group_membership.valid_from, group_membership.valid_to),
            group_target.event_id == event_id,
            _active_interval(group_target.valid_from, group_target.valid_to),
        )
        .correlate(Event, child_membership)
    )

    eligible_child_exists = sa.exists(
        sa.select(gr.id)
        .join(
            child_membership,
            sa.and_(
                child_membership.person_id == gr.child_person_id,
                child_membership.club_id == event_club_id,
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


def build_event_resource_context(
    session: Session, *, event: Event, user_id: uuid.UUID, permission_code: str
) -> ResourceContext:
    """Resolve the full ResourceContext for one already-loaded Event
    against the acting user, running each relationship predicate as its
    own scalar EXISTS query. Used by the detail/update/status/archive
    endpoints, each of which operates on exactly one Event.

    `permission_code` is the permission the context will be checked
    against: it selects what `self` means (see module docstring).
    """
    person_id = _person_id_for_user(session, user_id)
    is_own_event = session.execute(sa.select(_own_event_condition(event.id, user_id))).scalar()
    is_own_group = session.execute(
        sa.select(_own_group_condition(event.id, event.club_id, user_id))
    ).scalar()
    is_self = session.execute(
        sa.select(
            _self_scope_condition(
                permission_code,
                event.id,
                event.club_id,
                event.status,
                event.start_at,
                person_id,
            )
        )
    ).scalar()
    is_child = session.execute(
        sa.select(_child_condition(event.id, event.club_id, person_id))
    ).scalar()
    return ResourceContext(
        club_id=event.club_id,
        is_self=bool(is_self),
        is_child=bool(is_child),
        is_own_group=bool(is_own_group),
        is_own_event=bool(is_own_event),
    )


def event_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate to apply directly to an Event list query (a
    `.where(...)` clause referencing `Event.id`/`Event.club_id`) that is
    true only for Events the acting user is authorized to see under
    `permission_code`, per every UserRoleAssignment applicable to that
    permission. Never fetch-then-filter-in-Python — required so
    pagination/totals/offsets never leak an unauthorized row.
    """
    grants = applicable_grants(session, user_id, permission_code)
    if not grants:
        return sa.false()

    needs_person = any(a.scope_type in ("self", "children") for a in grants)
    person_id = _person_id_for_user(session, user_id) if needs_person else None

    clauses: list[sa.ColumnElement[bool]] = []
    for grant in grants:
        club_boundary: sa.ColumnElement[bool] = (
            sa.true() if grant.club_id is None else Event.club_id == grant.club_id
        )
        if grant.scope_type == "all":
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif grant.scope_type == "own_events":
            scope_predicate = _own_event_condition(Event.id, user_id)
        elif grant.scope_type == "own_groups":
            scope_predicate = _own_group_condition(Event.id, Event.club_id, user_id)
        elif grant.scope_type == "self":
            scope_predicate = _self_scope_condition(
                permission_code, Event.id, Event.club_id, Event.status, Event.start_at, person_id
            )
        elif grant.scope_type == "children":
            scope_predicate = _child_condition(Event.id, Event.club_id, person_id)
        elif grant.scope_type == "none":
            scope_predicate = sa.false()
        else:  # pragma: no cover - unreachable, DB CHECK constraint guards this
            raise ValueError(f"Unhandled scope_type: {grant.scope_type!r}")
        clauses.append(sa.and_(club_boundary, scope_predicate))

    return sa.or_(*clauses)


__all__ = ["build_event_resource_context", "event_visibility_filter"]
