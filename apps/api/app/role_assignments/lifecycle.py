"""RoleAssignment `club_id` and revoke-transition validation (Issue #74,
implementing ADR-0026 §1/§2; amended by Issue #99/TH-0089 for the global
administrator; AUTH-2A).

Canonical sources: docs/03-architecture/adr/ADR-0026-role-assignment-api-
decisions.md §1/§2/§5 and its TH-0089 amendment, docs/02-requirements/
roles-and-permissions.md §19.1/§19.2.

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.groups.lifecycle/app.people.lifecycle/app.events.lifecycle
exactly.

## AUTH-2A `club_id` rule (PO decision)

A RoleAssignment no longer carries an authorization scope (scopes belong
to each permission grant, `RolePermissionScope`), so ADR-0026 §2's
per-scope `club_id` table no longer applies to it. The rule is fixed and
is not derived from the Role's permission set: every RoleAssignment
requires a `club_id`, except an assignment of the canonical system
`admin` role, whose permissions all carry the canonical `all` scope — it
may be global (`club_id = NULL`, installation-wide, per the TH-0089
amendment) or club-wide. `scope_ref_id` is a legacy column and must be
NULL.
"""

import uuid
from typing import Optional


class RoleAssignmentLifecycleError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidRoleAssignmentScopeError(RoleAssignmentLifecycleError):
    """`club_id`/`scope_ref_id` violate the AUTH-2A RoleAssignment rule
    (see module docstring)."""


class InvalidRoleAssignmentTransitionError(RoleAssignmentLifecycleError):
    """ADR-0026 §1: `POST .../revoke` was called for an assignment whose
    `valid_to` is already set to a moment at or before now — this entity
    has no separate status field, so "already ended" is derived from the
    interval itself, exactly like GroupInstructorAssignment
    (app.groups.service.InvalidGroupInstructorAssignmentTransitionError)."""

    def __init__(self, *, assignment_id: uuid.UUID) -> None:
        super().__init__(f"UserRoleAssignment {assignment_id} has already ended")
        self.assignment_id = assignment_id


def validate_role_assignment_club(
    *,
    is_global_admin_role: bool,
    club_id: Optional[uuid.UUID],
    scope_ref_id: Optional[uuid.UUID],
) -> None:
    """Raises InvalidRoleAssignmentScopeError unless `scope_ref_id` is NULL
    and `club_id` is set — or the Role is the canonical system `admin`
    role (`is_global_admin_role`), for which `club_id` may also be NULL.
    """
    if scope_ref_id is not None:
        raise InvalidRoleAssignmentScopeError(
            "scope_ref_id is not used by any canonical MVP RoleAssignment"
        )
    if club_id is None and not is_global_admin_role:
        raise InvalidRoleAssignmentScopeError(
            "a club_id is required; only the canonical admin role may be assigned globally"
        )


__all__ = [
    "RoleAssignmentLifecycleError",
    "InvalidRoleAssignmentScopeError",
    "InvalidRoleAssignmentTransitionError",
    "validate_role_assignment_club",
]
