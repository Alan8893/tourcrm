"""Centralized RBAC + permission scope enforcement engine (Issue #29).

Canonical sources: docs/03-architecture/adr/ADR-0005-identity-and-access.md
(`User -> Role -> Permission -> Scope`),
docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/02-requirements/roles-and-permissions.md,
docs/05-api/auth-and-authorization.md §11/§13.

`can()` is the reusable decision function: given a real database session, an
authenticated user id, a canonical permission code, and an explicit
ResourceContext already resolved by domain policy, it decides allow/deny by
querying the actual `UserRoleAssignment`/`RolePermission` data — never from
an assumed/hardcoded role-to-permission mapping (Issue #19 deliberately
seeded no such grants; Issue #29 must not invent any either).

A user may hold several roles/assignments; permissions are additive (any
one applicable, matching assignment is enough) — no explicit deny exists
(roles-and-permissions.md §13).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.db.authorization import Permission, RolePermission, UserRoleAssignment


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


def applicable_assignments(
    session: Session, user_id: uuid.UUID, permission_code: str
) -> list[UserRoleAssignment]:
    """All of the user's UserRoleAssignment rows that grant `permission_code`
    (via the assignment's Role -> RolePermission -> Permission chain).

    Public because domain-level query filtering (e.g. Event list scope
    filtering, Issue #40) needs the identical query to build per-assignment
    SQL predicates, not just the aggregate allow/deny `can()` returns.
    """
    stmt = (
        select(UserRoleAssignment)
        .join(RolePermission, RolePermission.role_id == UserRoleAssignment.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(UserRoleAssignment.user_id == user_id, Permission.code == permission_code)
    )
    return list(session.execute(stmt).scalars().all())


def can(
    session: Session,
    user_id: uuid.UUID,
    permission_code: str,
    context: ResourceContext | None = None,
) -> bool:
    """Allow iff at least one of the user's UserRoleAssignments grants
    `permission_code` (via its Role's RolePermission rows) and that
    assignment's club boundary and scope both match `context`.
    """
    resolved_context = context if context is not None else ResourceContext()
    assignments = applicable_assignments(session, user_id, permission_code)
    return any(
        club_boundary_matches(assignment.club_id, resolved_context.club_id)
        and scope_matches(assignment.scope_type, resolved_context)
        for assignment in assignments
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
