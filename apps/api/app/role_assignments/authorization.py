"""RoleAssignment authorization scope resolution for `role.manage`
(Issue #74, implementing ADR-0026 §5).

Canonical sources: docs/03-architecture/adr/ADR-0026-role-assignment-api-
decisions.md §5, docs/02-requirements/roles-and-permissions.md §19.5.

Unlike every other domain's own authorization module in this codebase
(app.groups.authorization, app.people.authorization,
app.people.guardian_authorization, app.events.authorization), this one
resolves no relationship-based scope at all: ADR-0026 §5 states plainly
that `role.manage` is effective only with scope `all` —
`self`/`children`/`own_groups`/`own_events`/`none` are invalid scopes
for it. Since AUTH-2A that scope is the `role.manage` grant's own
`RolePermissionScope`, never the assignment's legacy `scope_type`.
`app.authorization.service.scope_matches` already returns `True`
unconditionally for `all` and denies every other scope unless the
corresponding `ResourceContext` relationship field was explicitly
resolved to `True` — which nothing here ever does. A `role.manage` grant
scoped anything other than `all` is therefore never effective through
the generic engine; no bespoke rejection logic is needed.

What remains is exactly the club boundary `role.manage`'s own scope
establishes (ADR-0026 §5: "the caller's role.manage scope determines the
target Club boundary") — resolved via the existing generic
`app.authorization.service.club_boundary_matches`/`Authorizer`, given
only a `club_id`.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_grants
from app.db.authorization import UserRoleAssignment


def build_role_assignment_resource_context(assignment: UserRoleAssignment) -> ResourceContext:
    """The ResourceContext for one already-loaded UserRoleAssignment —
    just its own `club_id`; no relationship fields are ever set (see
    module docstring for why none are needed for `role.manage`)."""
    return ResourceContext(club_id=assignment.club_id)


def role_assignment_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate for a RoleAssignment list query (`.where(...)`
    referencing `UserRoleAssignment.club_id`), true only for rows the
    acting user is authorized to see under `permission_code`
    (`role.manage`). Used by `GET /api/v1/role-assignments` so a caller
    can never enumerate assignments outside their administrative
    boundary.

    Every `applicable_grants` grant whose own scope is not `all` is
    deliberately skipped (`continue`) — it grants no `role.manage`
    visibility, per module docstring — matching the explicit
    per-scope-type allow-list shape already used by
    app.groups.authorization.group_visibility_filter/
    app.people.authorization.person_visibility_filter for their own
    inapplicable scopes.
    """
    grants = applicable_grants(session, user_id, permission_code)
    if not grants:
        return sa.false()

    clauses: list[sa.ColumnElement[bool]] = []
    for grant in grants:
        if grant.scope_type != "all":
            continue
        if grant.club_id is None:
            clauses.append(sa.true())
        else:
            clauses.append(UserRoleAssignment.club_id == grant.club_id)

    return sa.or_(*clauses) if clauses else sa.false()


__all__ = ["build_role_assignment_resource_context", "role_assignment_visibility_filter"]
