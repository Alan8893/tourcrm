"""RoleAssignment create/revoke service layer (Issue #74, implementing
ADR-0026 and ADR-0027).

Canonical sources: docs/03-architecture/adr/ADR-0026-role-assignment-api-
decisions.md, docs/03-architecture/adr/ADR-0027-role-assignment-and-club-
membership-effectivity.md, docs/02-requirements/roles-and-permissions.md
§19, docs/03-architecture/adr/ADR-0022-cross-club-ownership-integrity.md,
docs/03-architecture/adr/ADR-0024-audit-infrastructure.md.

ADR-0027: the active-ClubMembership check below is a creation-time
integrity prerequisite only (this module's own concern). It is never
re-checked later, and ending the target's ClubMembership afterward never
touches the resulting RoleAssignment row — see
app.authorization.service.applicable_assignments's own docstring for why
the shared authorization engine has no corresponding check.

This module performs no authorization: the caller (the API router) must
have already resolved and checked `role.manage` (via
app.role_assignments.authorization / the generic
app.authorization.service.Authorizer) before invoking any function here
— the same division of responsibility as app.groups.service and
app.people.guardian_service.

Every mutating function here owns and commits its own transaction and
performs its cross-Club check (create only), audit record, and write
inside that same transaction — an audit-required mutation never succeeds
without its audit record (ADR-0024 §5 fail-closed semantics).

The temporal duplicate/overlap invariant (ADR-0026 §1) is a DB-level GiST
exclusion constraint (app.db.authorization.UserRoleAssignment) — create
catches the resulting IntegrityError and re-raises it as a typed domain
error, mirroring app.groups.service.create_group_membership's identical
pattern for GroupMembership's own duplicate-active constraint.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authorization.club_ownership import user_has_active_club_membership
from app.db.authorization import UserRoleAssignment
from app.role_assignments.lifecycle import (
    InvalidRoleAssignmentTransitionError,
    validate_role_assignment_scope,
)

_NO_OVERLAPPING_ACTIVE_CONSTRAINT = "ck_user_role_assignments_no_overlapping_active"


class RoleAssignmentError(Exception):
    """Base class for this module's typed, expected failures."""


class RoleAssignmentClubMembershipMissingError(RoleAssignmentError):
    """ADR-0026 §3/ADR-0027 §1: a club-scoped RoleAssignment may be
    created only when the target User has an active ClubMembership in
    the target Club at creation time — regardless of any role/permission
    the User may already hold. This is a creation-time integrity check
    only; it is never re-evaluated after the assignment exists."""

    def __init__(self, *, user_id: uuid.UUID, club_id: uuid.UUID) -> None:
        super().__init__(f"User {user_id} has no active ClubMembership in club {club_id}")
        self.user_id = user_id
        self.club_id = club_id


class DuplicateRoleAssignmentError(RoleAssignmentError):
    """ADR-0026 §1: an assignment already exists for this (user_id,
    role_id, club_id, scope_type, scope_ref_id) tuple with an overlapping
    `[valid_from, valid_to)` interval — enforced by the
    `ck_user_role_assignments_no_overlapping_active` GiST exclusion
    constraint. Sequential historical assignments (no overlap) remain
    unaffected."""

    def __init__(
        self,
        *,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        club_id: Optional[uuid.UUID],
        scope_type: str,
    ) -> None:
        super().__init__(
            f"An overlapping RoleAssignment already exists for user {user_id} / "
            f"role {role_id} / club {club_id} / scope {scope_type!r}"
        )
        self.user_id = user_id
        self.role_id = role_id
        self.club_id = club_id
        self.scope_type = scope_type


_DEADLOCK_DETECTED_SQLSTATE = "40P01"


def _is_no_overlapping_active_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _NO_OVERLAPPING_ACTIVE_CONSTRAINT


def _is_deadlock(exc: OperationalError) -> bool:
    # Two concurrent INSERTs each checking the other's not-yet-committed
    # row against `ck_user_role_assignments_no_overlapping_active` (a
    # GiST exclusion constraint) can resolve either as an
    # ExclusionViolation (IntegrityError, handled above) or, if both
    # sides are mid-check at once, as a genuine PostgreSQL deadlock
    # (DeadlockDetected, SQLSTATE 40P01) — surfaced by SQLAlchemy as
    # OperationalError, not IntegrityError. Checked by SQLSTATE (driver-
    # agnostic, matching `_is_no_overlapping_active_violation`'s own
    # `.orig` inspection style) rather than an isinstance check against a
    # psycopg-specific exception class, so an unrelated OperationalError
    # (e.g. a lost connection) is never misclassified as a duplicate.
    return getattr(exc.orig, "sqlstate", None) == _DEADLOCK_DETECTED_SQLSTATE


def create_role_assignment(
    session: Session,
    *,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    scope_type: str,
    actor_user_id: uuid.UUID,
    club_id: Optional[uuid.UUID] = None,
    scope_ref_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
) -> UserRoleAssignment:
    """ADR-0026 §1-§3: create a RoleAssignment with `valid_from = now()`
    (server-authoritative — see app.db.authorization.UserRoleAssignment's
    own docstring for why this is never client-suppliable, unlike
    Group/GroupMembership's `valid_from`).

    Raises InvalidRoleAssignmentScopeError (from
    app.role_assignments.lifecycle) when the (`scope_type`, `club_id`,
    `scope_ref_id`) combination is not canonical,
    RoleAssignmentClubMembershipMissingError when a club-scoped
    assignment's target User has no active ClubMembership in that Club,
    and DuplicateRoleAssignmentError when the temporal exclusion
    invariant would be violated — either as an ExclusionViolation
    (IntegrityError) or, under a genuine concurrent-insert deadlock
    (OperationalError, SQLSTATE 40P01 — see `_is_deadlock`), as the
    deadlock-victim outcome; persisting nothing in either case.
    Records `role_assignment.created` in the same transaction as the
    write.
    """
    validate_role_assignment_scope(
        scope_type=scope_type, club_id=club_id, scope_ref_id=scope_ref_id
    )

    if club_id is not None:
        # ADR-0022 §6-shaped transaction boundary: the same shared,
        # already-concurrency-proven mechanism used by
        # app.groups.service/app.events.service for their own "does this
        # User have an active ClubMembership in this Club?" check —
        # locks the relevant ClubMembership row with `SELECT ... FOR
        # SHARE` so a concurrent deactivation cannot race past this
        # check before the write commits.
        if not user_has_active_club_membership(session, user_id=user_id, club_id=club_id):
            session.rollback()
            raise RoleAssignmentClubMembershipMissingError(user_id=user_id, club_id=club_id)

    assignment = UserRoleAssignment(
        user_id=user_id,
        role_id=role_id,
        club_id=club_id,
        scope_type=scope_type,
        scope_ref_id=scope_ref_id,
        valid_from=datetime.now(timezone.utc),
    )
    session.add(assignment)
    try:
        session.flush()
        record_audit_event(
            session,
            action="role_assignment.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type="role_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_no_overlapping_active_violation(exc):
            raise DuplicateRoleAssignmentError(
                user_id=user_id, role_id=role_id, club_id=club_id, scope_type=scope_type
            ) from exc
        raise
    except OperationalError as exc:
        session.rollback()
        if _is_deadlock(exc):
            raise DuplicateRoleAssignmentError(
                user_id=user_id, role_id=role_id, club_id=club_id, scope_type=scope_type
            ) from exc
        raise
    return assignment


def revoke_role_assignment(
    session: Session,
    *,
    assignment: UserRoleAssignment,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> UserRoleAssignment:
    """ADR-0026 §1: closes the assignment's validity interval by setting
    `valid_to = now()` (server UTC). Never deletes or reopens the row.

    Raises InvalidRoleAssignmentTransitionError, and persists nothing,
    when `assignment` has already ended (`valid_to` already set to a
    moment at or before now) — mirroring
    app.groups.service.end_group_instructor_assignment's identical
    "no separate status field, derive from the interval" shape.
    """
    now = datetime.now(timezone.utc)
    if assignment.valid_to is not None and assignment.valid_to <= now:
        raise InvalidRoleAssignmentTransitionError(assignment_id=assignment.id)

    old_valid_to = assignment.valid_to
    assignment.valid_to = now

    try:
        session.flush()
        record_audit_event(
            session,
            action="role_assignment.revoked",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=assignment.club_id,
            resource_type="role_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
            details={
                "changes": {
                    "valid_to": {
                        "from": old_valid_to.isoformat() if old_valid_to else None,
                        "to": now.isoformat(),
                    }
                }
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return assignment


__all__ = [
    "RoleAssignmentError",
    "RoleAssignmentClubMembershipMissingError",
    "DuplicateRoleAssignmentError",
    "create_role_assignment",
    "revoke_role_assignment",
]
