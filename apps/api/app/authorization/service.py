"""Centralized RBAC + permission scope enforcement engine (Issue #29).

Canonical sources: docs/03-architecture/adr/ADR-0005-identity-and-access.md
(`User -> Role -> Permission -> Scope`),
docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/02-requirements/roles-and-permissions.md,
docs/05-api/auth-and-authorization.md §11/§13.

`can()` is the reusable decision function: given a real database session, an
authenticated user id, a canonical permission code, and an explicit
ResourceContext already resolved by domain policy, it decides allow/deny by
querying the actual `UserRoleAssignment`/`RolePermission`/
`RolePermissionScope` data — never from an assumed/hardcoded
role-to-permission mapping.

AUTH-2A: scope is a property of each permission grant
(`UserRoleAssignment -> RolePermission -> RolePermissionScope`), never of
the assignment. A user may hold several roles; permissions are additive
(any one applicable, matching grant scope is enough) — no explicit deny
exists (roles-and-permissions.md §13).
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.db.authorization import (
    Permission,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)


def club_boundary_matches(
    assignment_club_id: uuid.UUID | None, resource_club_id: uuid.UUID | None
) -> bool:
    """Issue #29 §7: a club-scoped assignment (`club_id` set) applies only to
    that club; a global/installation assignment (`club_id IS NULL`) applies
    regardless of club. A resource whose club is unknown/unresolved never
    satisfies a club-scoped assignment (fail closed, not "assume same club").
    """
    if assignment_club_id is None:
        return True
    return assignment_club_id == resource_club_id


def scope_matches(scope_type: str, context: ResourceContext) -> bool:
    """ADR-0013's canonical scopes, evaluated only against explicit,
    backend-asserted context — never against a raw client-supplied ID.

    Each relationship field on ResourceContext is a tri-state
    (True/False/None), not a plain bool: only an explicit `True` — the
    relationship was actually checked and holds — matches. `False` (checked,
    does not hold) and `None` (never checked at all) must both deny; the
    `is True` comparisons below are deliberate rather than truthiness
    checks, so an unresolved relationship can never be mistaken for a
    confirmed one.
    """
    if scope_type == "all":
        return True
    if scope_type == "self":
        return context.is_self is True
    if scope_type == "children":
        return context.is_child is True
    if scope_type == "own_groups":
        return context.is_own_group is True
    if scope_type == "own_events":
        return context.is_own_event is True
    if scope_type == "none":
        return False
    # Unreachable for a row that passed the DB's own scope_type CHECK
    # constraint; guards against a future canonical scope being added here
    # without updating this function.
    raise ValueError(f"Unhandled scope_type: {scope_type!r}")


@dataclass(frozen=True)
class PermissionGrant:
    """One scope through which a permission reaches a user (AUTH-2A):
    a currently-effective `assignment` whose Role holds the permission,
    and one `scope_type` of *that* permission's own RolePermission grant
    (`UserRoleAssignment -> RolePermission -> RolePermissionScope`).

    `club_id` is the assignment's Club boundary; `scope_type` never comes
    from the assignment (its legacy column is not an authorization input).
    """

    assignment: UserRoleAssignment
    scope_type: str

    @property
    def club_id(self) -> uuid.UUID | None:
        return self.assignment.club_id


def applicable_grants(
    session: Session, user_id: uuid.UUID, permission_code: str
) -> list[PermissionGrant]:
    """Every (currently-effective assignment, scope) pair through which
    the user holds `permission_code`: the assignment's Role ->
    RolePermission for exactly this Permission -> each of that grant's
    RolePermissionScope rows. A grant with several scopes yields one
    PermissionGrant per scope; scopes of any *other* permission of the
    same role never appear here (AUTH-2A). A grant without scope rows
    yields nothing (fail closed).

    ADR-0026 §1 / Issue #74: "Effective authorization considers only
    assignments valid at the authorization-check time" — a revoked
    (`valid_to` in the past) assignment must never continue granting
    permissions project-wide. This is the one, central query every
    permission check in the codebase goes through (via `can()`/
    `Authorizer`), so the temporal filter lives here rather than being
    repeated per caller — matching the `valid_from <= now() AND (valid_to
    IS NULL OR now() < valid_to)` convention already used identically by
    every other temporal entity's own authorization/visibility resolution
    (app.events.authorization, app.groups.authorization,
    app.people.guardian_authorization).

    Public because domain-level query filtering (e.g. Event list scope
    filtering, Issue #40) needs the identical query to build per-grant
    SQL predicates, not just the aggregate allow/deny `can()` returns.

    Deliberately does NOT check ClubMembership status. ADR-0027 (RoleAssignment
    and ClubMembership effectivity) settled this explicitly: active
    ClubMembership is a creation-time integrity prerequisite for a club-scoped
    RoleAssignment (enforced once, in app.role_assignments.service.
    create_role_assignment), never a later, universal authorization
    prerequisite — this engine must not acquire that requirement merely
    because an assignment has a non-null `club_id`, since it would silently
    change already-shipped Event/Person/Membership/Group authorization
    semantics. Do not "fix" this by adding a ClubMembership join here.
    """
    now = sa.func.now()
    stmt = (
        select(UserRoleAssignment, RolePermissionScope.scope_type)
        .join(RolePermission, RolePermission.role_id == UserRoleAssignment.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .join(RolePermissionScope, RolePermissionScope.role_permission_id == RolePermission.id)
        .where(
            UserRoleAssignment.user_id == user_id,
            Permission.code == permission_code,
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
        .order_by(UserRoleAssignment.id, RolePermissionScope.scope_type)
    )
    return [
        PermissionGrant(assignment=assignment, scope_type=scope_type)
        for assignment, scope_type in session.execute(stmt).all()
    ]


def can(
    session: Session,
    user_id: uuid.UUID,
    permission_code: str,
    context: ResourceContext | None = None,
) -> bool:
    """Allow iff at least one PermissionGrant of `permission_code` (see
    `applicable_grants`) matches `context` on both its assignment's club
    boundary and its own permission scope.
    """
    resolved_context = context if context is not None else ResourceContext()
    return any(
        club_boundary_matches(grant.club_id, resolved_context.club_id)
        and scope_matches(grant.scope_type, resolved_context)
        for grant in applicable_grants(session, user_id, permission_code)
    )


class AuthorizationDenied(Exception):
    """Raised by Authorizer.check() on deny; API-layer code maps this to
    HTTP 403 (see app.api.deps.require_permission) — this module itself has
    no HTTP dependency, so the engine stays testable without one.
    """


@dataclass(frozen=True)
class Authorizer:
    """Bound to one (session, authenticated user, required permission).

    Deliberately two-phase: the FastAPI dependency that constructs this
    (app.api.deps.require_permission) only knows the caller is
    authenticated and which permission the endpoint declared. The endpoint
    itself resolves the real resource relationship (ownership, group
    responsibility, guardian link, club) into a ResourceContext and calls
    `check()` — the resource ID a client sent is never trusted directly by
    this mechanism (Issue #29 §6).
    """

    session: Session
    user_id: uuid.UUID
    permission_code: str

    def is_allowed(self, context: ResourceContext | None = None) -> bool:
        return can(self.session, self.user_id, self.permission_code, context)

    def check(self, context: ResourceContext | None = None) -> None:
        if not self.is_allowed(context):
            raise AuthorizationDenied(self.permission_code)
