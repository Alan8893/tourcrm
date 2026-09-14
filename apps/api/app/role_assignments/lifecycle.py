"""RoleAssignment scope-combination and revoke-transition validation
(Issue #74, implementing ADR-0026 §1/§2).

Canonical sources: docs/03-architecture/adr/ADR-0026-role-assignment-api-
decisions.md §1/§2, docs/02-requirements/roles-and-permissions.md §19.1/
§19.2.

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.groups.lifecycle/app.people.lifecycle/app.events.lifecycle
exactly.

This module validates the (`scope_type`, `club_id`, `scope_ref_id`)
*combination* ADR-0026 §2 requires. It does NOT validate that
`scope_type` itself is a canonical ADR-0013 scope, or resolve the
`assigned_events` alias — that is
`app.authorization.context.normalize_scope_type`'s job and must be
called first (see app.role_assignments.service.create_role_assignment).
"""

import uuid
from typing import Optional

# ADR-0026 §2 / roles-and-permissions.md §19.2: the six canonical
# combinations. `True` means club_id is required (must not be NULL);
# `False` means club_id must be NULL. `scope_ref_id` is unconditionally
# required to be NULL for every one of them in this MVP slice.
_CLUB_ID_REQUIRED_BY_SCOPE: dict[str, bool] = {
    "all": True,
    "self": True,
    "children": True,
    "own_groups": True,
    "own_events": True,
    "none": False,
}


class RoleAssignmentLifecycleError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidRoleAssignmentScopeError(RoleAssignmentLifecycleError):
    """ADR-0026 §2: the (`scope_type`, `club_id`, `scope_ref_id`)
    combination is not one of the six canonical MVP combinations."""


class InvalidRoleAssignmentTransitionError(RoleAssignmentLifecycleError):
    """ADR-0026 §1: `POST .../revoke` was called for an assignment whose
    `valid_to` is already set to a moment at or before now — this entity
    has no separate status field, so "already ended" is derived from the
    interval itself, exactly like GroupInstructorAssignment
    (app.groups.service.InvalidGroupInstructorAssignmentTransitionError)."""

    def __init__(self, *, assignment_id: uuid.UUID) -> None:
        super().__init__(f"UserRoleAssignment {assignment_id} has already ended")
        self.assignment_id = assignment_id


def validate_role_assignment_scope(
    *,
    scope_type: str,
    club_id: Optional[uuid.UUID],
    scope_ref_id: Optional[uuid.UUID],
) -> None:
    """Raises InvalidRoleAssignmentScopeError, and validates nothing else,
    unless `scope_type` is already one of ADR-0013's canonical scopes
    (the caller must have already normalized it via
    `app.authorization.context.normalize_scope_type`) and the
    (`club_id`, `scope_ref_id`) pairing matches ADR-0026 §2's table for
    that scope exactly.
    """
    if scope_type not in _CLUB_ID_REQUIRED_BY_SCOPE:
        raise InvalidRoleAssignmentScopeError(
            f"{scope_type!r} is not a canonical RoleAssignment scope_type"
        )
    if scope_ref_id is not None:
        raise InvalidRoleAssignmentScopeError(
            "scope_ref_id is not used by any canonical MVP RoleAssignment scope combination"
        )
    club_id_required = _CLUB_ID_REQUIRED_BY_SCOPE[scope_type]
    if club_id_required and club_id is None:
        raise InvalidRoleAssignmentScopeError(
            f"scope_type {scope_type!r} requires a club_id"
        )
    if not club_id_required and club_id is not None:
        raise InvalidRoleAssignmentScopeError(
            f"scope_type {scope_type!r} must not have a club_id"
        )


__all__ = [
    "RoleAssignmentLifecycleError",
    "InvalidRoleAssignmentScopeError",
    "InvalidRoleAssignmentTransitionError",
    "validate_role_assignment_scope",
]
