"""Group / GroupMembership / GroupInstructorAssignment authorization
scope resolution (Issue #71, implementing the Issue #69 specification
gate).

Canonical sources: docs/03-architecture/adr/ADR-0021-group-persistence-model.md
§4 (`own_groups` is based on explicit active `GroupInstructorAssignment`
relationships — never inferred from a global/club-level `instructor`
role), docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/05-api/people-api.md §14-16.

Two canonical scopes apply to all three entities: `all` and `own_groups`.
`children`/`own_events` are not applicable (no such relationship exists
for Group/GroupMembership/GroupInstructorAssignment) and fail closed,
matching app.people.guardian_authorization's treatment of inapplicable
scopes.

Member self visibility (Issue #282, PO decision 2026-10-05;
role-permission-scope-matrix.md §6 "member: own membership"): under
`group.read`, `self` resolves to the Groups in which the requester's own
Person currently participates — authenticated User -> Person -> active
`ClubMembership` in the Group's own Club -> active `GroupMembership`
(`membership_status='active'` and a current `[valid_from, valid_to)`
interval) -> Group. An ended/historical `GroupMembership`, a membership in
another Club, and Club co-membership alone never match; an archived Group
never matches `self` (same rule as `own_groups`). `self` is resolved ONLY
for `GET /groups` (`group_visibility_filter`) and `GET /groups/{id}`
(`build_group_resource_context(..., resolve_self_membership=True)`); the
nested `members`/`instructors` reads, `group.manage` and the Group
Schedule keep their own contracts and leave `is_self` unresolved (`None`,
fail closed), so this rule widens none of them.

`own_groups` here answers a simpler question than
app.people.authorization's own `own_groups` resolution for Person/
ClubMembership (which chains Person -> active ClubMembership -> active
GroupMembership -> Group -> active GroupInstructorAssignment, because it
authorizes access to a *co-member*, not to the Group itself): does the
requesting User hold an explicit *active* `GroupInstructorAssignment` for
*this exact* `group_id`? That is the entire relationship ADR-0021 §4
defines for accessing Group/GroupMembership/GroupInstructorAssignment
data.

Archived Group read visibility (people-api.md §14-16, PO decision Q1/Q2
= A): under `group.read`, an archived Group is satisfied *only* by a
`scope_type='all'` assignment — `own_groups` never grants access to an
archived Group, even with an active `GroupInstructorAssignment`. This is
expressed through the existing permission/scope model (no role-code
check): `build_group_resource_context(..., archived_requires_all_scope=
True)` resolves `is_own_group=False` for an archived Group, and
`group_visibility_filter` restricts its `own_groups` clause to active
Groups. It applies to `GET /groups`, `GET /groups/{id}`, `GET
/groups/{id}/members` and `GET /groups/{id}/instructors` only; the Group
Schedule keeps its own ODR-0002 policy (app.groups.schedule_authorization).

`GroupMembership`/`GroupInstructorAssignment` have no `club_id` column of
their own — both resolve their Club through `group_id -> Group.club_id`,
exactly like the write-side cross-Club checks in app.groups.service.

There is no separate top-level list endpoint for `GroupMembership`/
`GroupInstructorAssignment` (only nested under
`/groups/{group_id}/members` and `/groups/{group_id}/instructors` —
people-api.md §15/§16): authorizing the nested list/create endpoints is
therefore done by authorizing the *parent Group* object
(`build_group_resource_context` below), not by a separate per-row
visibility filter — see app.api.v1.groups.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_grants
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, User

ARCHIVED_GROUP_STATUS = "archived"
_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    # Duplicated from app.people.authorization/app.people.guardian_authorization
    # rather than imported: matches this codebase's existing convention of
    # duplicating this specific small helper per module.
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _own_group_condition(group_id: Any, requester_user_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """True if `requester_user_id` has an explicit *active*
    `GroupInstructorAssignment` for `group_id` (ADR-0021 §4)."""
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(gia.id).where(
            gia.group_id == group_id,
            gia.user_id == requester_user_id,
            _active_interval(gia.valid_from, gia.valid_to),
        )
    )


def _self_membership_condition(
    group_id: Any, group_club_id: Any, requester_user_id: uuid.UUID
) -> sa.ColumnElement[bool]:
    """`self` (Issue #282): the requesting User's own Person has an active
    `GroupMembership` in `group_id`, held through an active
    `ClubMembership` in the Group's own Club (`group_club_id`) — the same
    relationship app.groups.schedule_authorization uses for its `self`
    tier, evaluated *now*."""
    gm = aliased(GroupMembership)
    cm = aliased(ClubMembership)
    requester_person_id = (
        sa.select(User.person_id).where(User.id == requester_user_id).scalar_subquery()
    )
    return sa.exists(
        sa.select(gm.id)
        .join(cm, cm.id == gm.club_membership_id)
        .where(
            gm.group_id == group_id,
            gm.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(gm.valid_from, gm.valid_to),
            cm.person_id == requester_person_id,
            cm.club_id == group_club_id,
            cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )


def build_group_resource_context(
    session: Session,
    *,
    group: Group,
    requester_user_id: uuid.UUID,
    archived_requires_all_scope: bool = False,
    resolve_self_membership: bool = False,
) -> ResourceContext:
    """`archived_requires_all_scope=True` (the `group.read` item/nested-read
    endpoints) makes an archived Group reachable only through a
    `scope_type='all'` assignment — see the module docstring.

    `resolve_self_membership=True` (only `GET /groups/{id}`) additionally
    resolves `is_self` from the requester's own active GroupMembership;
    otherwise `is_self` stays `None` and `self` never matches."""
    if archived_requires_all_scope and group.status == ARCHIVED_GROUP_STATUS:
        return ResourceContext(
            club_id=group.club_id,
            is_own_group=False,
            is_self=False if resolve_self_membership else None,
        )
    is_own_group = session.execute(
        sa.select(_own_group_condition(group.id, requester_user_id))
    ).scalar()
    is_self: bool | None = None
    if resolve_self_membership:
        is_self = bool(
            session.execute(
                sa.select(_self_membership_condition(group.id, group.club_id, requester_user_id))
            ).scalar()
        )
    return ResourceContext(
        club_id=group.club_id, is_own_group=bool(is_own_group), is_self=is_self
    )


def build_group_create_context(club_id: uuid.UUID) -> ResourceContext:
    """Resolve the ResourceContext used to authorize *creating* a new
    Group in `club_id` — no Group row exists yet, so `is_own_group` stays
    unresolved (`None`, the fail-closed default): only a scope_type='all'
    assignment matching the target Club can authorize creation, mirroring
    app.api.v1.memberships.create_membership's identical precedent for
    ClubMembership creation.
    """
    return ResourceContext(club_id=club_id)


def build_group_membership_resource_context(
    session: Session, *, membership: GroupMembership, requester_user_id: uuid.UUID
) -> ResourceContext:
    group_club_id = session.execute(
        sa.select(Group.club_id).where(Group.id == membership.group_id)
    ).scalar_one()
    is_own_group = session.execute(
        sa.select(_own_group_condition(membership.group_id, requester_user_id))
    ).scalar()
    return ResourceContext(club_id=group_club_id, is_own_group=bool(is_own_group))


def build_group_instructor_assignment_resource_context(
    session: Session, *, assignment: GroupInstructorAssignment, requester_user_id: uuid.UUID
) -> ResourceContext:
    group_club_id = session.execute(
        sa.select(Group.club_id).where(Group.id == assignment.group_id)
    ).scalar_one()
    is_own_group = session.execute(
        sa.select(_own_group_condition(assignment.group_id, requester_user_id))
    ).scalar()
    return ResourceContext(club_id=group_club_id, is_own_group=bool(is_own_group))


def group_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate for a Group list query (`.where(...)`
    referencing `Group.id`/`Group.club_id`), true only for Groups the
    acting user is authorized to see under `permission_code`. Used by
    `GET /api/v1/groups`. An `own_groups` or `self` assignment only ever
    matches active Groups — archived Groups are listed through `all` alone
    (see the module docstring).
    """
    grants = applicable_grants(session, user_id, permission_code)
    if not grants:
        return sa.false()

    clauses: list[sa.ColumnElement[bool]] = []
    for grant in grants:
        club_boundary: sa.ColumnElement[bool] = (
            sa.true() if grant.club_id is None else Group.club_id == grant.club_id
        )
        if grant.scope_type == "all":
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif grant.scope_type == "own_groups":
            scope_predicate = sa.and_(
                Group.status != ARCHIVED_GROUP_STATUS, _own_group_condition(Group.id, user_id)
            )
        elif grant.scope_type == "self":
            scope_predicate = sa.and_(
                Group.status != ARCHIVED_GROUP_STATUS,
                _self_membership_condition(Group.id, Group.club_id, user_id),
            )
        elif grant.scope_type == "none":
            scope_predicate = sa.false()
        else:
            # children/own_events: not applicable to Group.
            continue
        clauses.append(sa.and_(club_boundary, scope_predicate))

    return sa.or_(*clauses) if clauses else sa.false()


__all__ = [
    "build_group_resource_context",
    "build_group_create_context",
    "build_group_membership_resource_context",
    "build_group_instructor_assignment_resource_context",
    "group_visibility_filter",
]
