"""RoleAssignment scope-combination and revoke-transition validation
(Issue #74, implementing ADR-0026 §1/§2; amended by Issue #99/TH-0089 for
the `all + club_id=NULL` combination — see ADR-0026's own "Amendment
(TH-0089 / Issue #99)" section).

Canonical sources: docs/03-architecture/adr/ADR-0026-role-assignment-api-
decisions.md §1/§2/§5 and its TH-0089 amendment, docs/02-requirements/
roles-and-permissions.md §19.1/§19.2.

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.groups.lifecycle/app.people.lifecycle/app.events.lifecycle
exactly.

This module validates the (`scope_type`, `club_id`, `scope_ref_id`)
*combination* ADR-0026 §2 requires. It does NOT validate that
`scope_type` itself is a canonical ADR-0013 scope, or resolve the
`assigned_events` alias — that is
`app.authorization.context.normalize_scope_type`'s job and must be
called first (see app.role_assignments.service.create_role_assignment).

## `all`'s club_id is optional, not merely "required" (TH-0089 amendment)

ADR-0026 §2's original table marked `club_id` "required" for every scope
except `none`, including `all` — but §5 (`role.manage` authorization),
in the very same accepted ADR, already described `all + club_id=NULL`
as a valid, meaningful combination ("the holder may manage
RoleAssignments in any Club"). Nothing in this codebase could actually
*create* that combination until this amendment: `app.authorization.
service`'s read-side `scope_matches`/`club_boundary_matches` and
`app.role_assignments.authorization.role_assignment_visibility_filter`
already treat `all + club_id=NULL` as "every Club" wherever they
encounter it — only this creation-time validator was out of step with
its own ADR. `all` is therefore the one scope_type whose club_id may be
either NULL (installation-wide) or a specific Club (club-wide); every
other scope keeps its original, unchanged rule.
"""

import uuid
from typing import Optional

# ADR-0026 §2 / roles-and-permissions.md §19.2 (as amended for TH-0089 /
# Issue #99 — see module docstring): "required" means club_id must be
# set; "forbidden" means club_id must be NULL; "optional" (currently only
# `all`) means either is a valid combination. `scope_ref_id` is
# unconditionally required to be NULL for every one of these in this MVP
# slice.
_CLUB_ID_RULE_BY_SCOPE: dict[str, str] = {
    "all": "optional",
    "self": "required",
    "children": "required",
    "own_groups": "required",
    "own_events": "required",
    "none": "forbidden",
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
    if scope_type not in _CLUB_ID_RULE_BY_SCOPE:
        raise InvalidRoleAssignmentScopeError(
            f"{scope_type!r} is not a canonical RoleAssignment scope_type"
        )
    if scope_ref_id is not None:
        raise InvalidRoleAssignmentScopeError(
            "scope_ref_id is not used by any canonical MVP RoleAssignment scope combination"
        )
    club_id_rule = _CLUB_ID_RULE_BY_SCOPE[scope_type]
    if club_id_rule == "required" and club_id is None:
        raise InvalidRoleAssignmentScopeError(f"scope_type {scope_type!r} requires a club_id")
    if club_id_rule == "forbidden" and club_id is not None:
        raise InvalidRoleAssignmentScopeError(f"scope_type {scope_type!r} must not have a club_id")


__all__ = [
    "RoleAssignmentLifecycleError",
    "InvalidRoleAssignmentScopeError",
    "InvalidRoleAssignmentTransitionError",
    "validate_role_assignment_scope",
]
