"""Group Schedule (`GET /groups/{group_id}/schedule`) authorization
resolution (Issue #88 / TH-0083, resolved by ODR-0002).

Canonical sources: `docs/03-architecture/adr/ODR-0002-group-schedule-
visibility-and-group-lifecycle.md`, `docs/05-api/group-and-instructor-
schedule-api.md` §2, ADR-0013 (scopes), ADR-0021 (Group persistence),
ADR-0022 (cross-Club ownership integrity).

This is deliberately a *separate* module from `app.groups.authorization`:
that module's `own_groups`-only resolution (`build_group_resource_context`)
backs `group.read`/`group.manage` access to the *Group entity itself* and
explicitly treats `self`/`children` as inapplicable/fail-closed (see its
own module docstring). ODR-0002 defines a *different* policy for the
Group's *schedule* under `event.read`: `self`/`children` ARE applicable
here, resolved via active `GroupMembership` (never `GroupInstructorAssignment`,
never `Event.created_by`).

ODR-0002 also defines two access *tiers*, not a single yes/no gate:
`all`/`own_groups` may read historical AND future schedule items;
`self`/`children` may read future items ONLY. A single `ResourceContext`
boolean cannot express that distinction (it answers only "is this
scope/resource combination allowed", not "with which temporal
restriction"), so this module resolves both tiers directly as two
booleans (`historical_allowed`/`future_only_allowed`) rather than routing
through `app.authorization.service.Authorizer`/`ResourceContext`. The two
booleans are provably equivalent to what the generic engine would decide
per scope (same underlying relationship facts, same club-boundary rule),
just computed directly since the generic engine has no room for a second,
scope-dependent temporal tier.

`own_events` is never checked anywhere in this module (ADR/ODR-0002:
"`own_events` does not grant standalone Group Schedule access") — an
`own_events`-scoped assignment simply contributes nothing to either tier.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.service import applicable_assignments
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, GuardianRelationship, User

_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"
_ACTIVE_GUARDIAN_RELATIONSHIP_STATUS = "active"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    # Duplicated per this codebase's existing convention (see
    # app.groups.authorization/app.people.authorization) rather than
    # imported from a shared module.
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _own_group_instructor_condition(group_id: Any, user_id: Any) -> sa.ColumnElement[bool]:
    """`own_groups`: an active `GroupInstructorAssignment` for this exact
    Group — the same relationship `app.groups.authorization` uses for
    `group.read`/`group.manage`, evaluated *now* (current responsibility
    for the Group as a whole, not a per-item historical fact)."""
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(gia.id).where(
            gia.group_id == group_id,
            gia.user_id == user_id,
            _active_interval(gia.valid_from, gia.valid_to),
        )
    )


def _self_membership_condition(
    group_id: Any, group_club_id: Any, person_id: Any
) -> sa.ColumnElement[bool]:
    """`self`: the requester's own Person has an active `GroupMembership`
    in this Group, via an active `ClubMembership` in the Group's own Club
    (ODR-0002: membership answers "am I a member of this Group", evaluated
    *now* — never an EventGroupTarget, that is a separate item-level
    check)."""
    gm = aliased(GroupMembership)
    cm = aliased(ClubMembership)
    return sa.exists(
        sa.select(gm.id)
        .join(cm, cm.id == gm.club_membership_id)
        .where(
            gm.group_id == group_id,
            gm.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(gm.valid_from, gm.valid_to),
            cm.person_id == person_id,
            cm.club_id == group_club_id,
            cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )


def _child_membership_condition(
    group_id: Any, group_club_id: Any, guardian_person_id: Any
) -> sa.ColumnElement[bool]:
    """`children`: an active `GuardianRelationship` to a child who
    themselves has an active `ClubMembership` in the Group's Club and an
    active `GroupMembership` in this exact Group — mirrors
    `app.events.authorization._child_condition`'s guardian/child-membership
    chain, applied to Group membership instead of Event participation."""
    guardian_has_membership = sa.exists(
        sa.select(ClubMembership.id).where(
            ClubMembership.person_id == guardian_person_id,
            ClubMembership.club_id == group_club_id,
            ClubMembership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )

    gr = aliased(GuardianRelationship)
    child_membership = aliased(ClubMembership)
    child_group_membership = aliased(GroupMembership)

    eligible_child_exists = sa.exists(
        sa.select(gr.id)
        .join(
            child_membership,
            sa.and_(
                child_membership.person_id == gr.child_person_id,
                child_membership.club_id == group_club_id,
                child_membership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
            ),
        )
        .join(
            child_group_membership,
            sa.and_(
                child_group_membership.club_membership_id == child_membership.id,
                child_group_membership.group_id == group_id,
                child_group_membership.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            ),
        )
        .where(
            gr.guardian_person_id == guardian_person_id,
            gr.status == _ACTIVE_GUARDIAN_RELATIONSHIP_STATUS,
            _active_interval(gr.valid_from, gr.valid_to),
            _active_interval(child_group_membership.valid_from, child_group_membership.valid_to),
        )
    )

    return sa.and_(guardian_has_membership, eligible_child_exists)


@dataclass(frozen=True)
class GroupScheduleAccess:
    """The two ODR-0002 access tiers resolved for one (Group, requester)
    pair. `historical_allowed` implies `future_only_allowed` is moot (a
    superset); `allowed` is the existence-hiding gate."""

    historical_allowed: bool
    future_only_allowed: bool

    @property
    def allowed(self) -> bool:
        return self.historical_allowed or self.future_only_allowed


def build_group_schedule_access(
    session: Session, *, group: Group, requester_user_id: uuid.UUID, permission_code: str
) -> GroupScheduleAccess:
    """Resolve ODR-0002's access policy for one already-loaded Group
    against the acting user under `permission_code` (`event.read`)."""
    person_id = _person_id_for_user(session, requester_user_id)
    is_own_group = bool(
        session.execute(
            sa.select(_own_group_instructor_condition(group.id, requester_user_id))
        ).scalar()
    )
    is_self = bool(
        session.execute(
            sa.select(_self_membership_condition(group.id, group.club_id, person_id))
        ).scalar()
    )
    is_child = bool(
        session.execute(
            sa.select(_child_membership_condition(group.id, group.club_id, person_id))
        ).scalar()
    )

    assignments = applicable_assignments(session, requester_user_id, permission_code)
    matching = [a for a in assignments if a.club_id is None or a.club_id == group.club_id]
    has_all = any(a.scope_type == "all" for a in matching)
    has_own_groups = any(a.scope_type == "own_groups" for a in matching)
    has_self = any(a.scope_type == "self" for a in matching)
    has_children = any(a.scope_type == "children" for a in matching)

    return GroupScheduleAccess(
        historical_allowed=has_all or (has_own_groups and is_own_group),
        future_only_allowed=(has_self and is_self) or (has_children and is_child),
    )


__all__ = ["GroupScheduleAccess", "build_group_schedule_access"]
