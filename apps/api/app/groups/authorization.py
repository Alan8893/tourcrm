"""Group / GroupMembership / GroupInstructorAssignment authorization
scope resolution (Issue #71, implementing the Issue #69 specification
gate).

Canonical sources: docs/03-architecture/adr/ADR-0021-group-persistence-model.md
§4 (`own_groups` is based on explicit active `GroupInstructorAssignment`
relationships — never inferred from a global/club-level `instructor`
role), docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/05-api/people-api.md §14-16.

Two canonical scopes apply to all three entities: `all` and `own_groups`.
`self`/`children`/`own_events` are not applicable (no such relationship
exists for Group/GroupMembership/GroupInstructorAssignment) and fail
closed, matching app.people.guardian_authorization's treatment of
inapplicable scopes.

`own_groups` here answers a simpler question than
app.people.authorization's own `own_groups` resolution for Person/
ClubMembership (which chains Person -> active ClubMembership -> active
GroupMembership -> Group -> active GroupInstructorAssignment, because it
authorizes access to a *co-member*, not to the Group itself): does the
requesting User hold an explicit *active* `GroupInstructorAssignment` for
*this exact* `group_id`? That is the entire relationship ADR-0021 §4
defines for accessing Group/GroupMembership/GroupInstructorAssignment
data.

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
from app.authorization.service import applicable_assignments
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership


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


def build_group_resource_context(
    session: Session, *, group: Group, requester_user_id: uuid.UUID
) -> ResourceContext:
    is_own_group = session.execute(
        sa.select(_own_group_condition(group.id, requester_user_id))
    ).scalar()
    return ResourceContext(club_id=group.club_id, is_own_group=bool(is_own_group))


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
    `GET /api/v1/groups`.
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    if not assignments:
        return sa.false()

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        club_boundary: sa.ColumnElement[bool] = (
            sa.true() if assignment.club_id is None else Group.club_id == assignment.club_id
        )
        if assignment.scope_type == "all":
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif assignment.scope_type == "own_groups":
            scope_predicate = _own_group_condition(Group.id, user_id)
        elif assignment.scope_type == "none":
            scope_predicate = sa.false()
        else:
            # self/children/own_events: not applicable to Group.
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
