"""Canonical Club-ownership validation for Group relationship writes
(ADR-0022), plus the Group/GroupMembership/GroupInstructorAssignment
CRUD + lifecycle + audit service layer (Issue #71, implementing the
Issue #69 specification gate: docs/05-api/people-api.md §14-16).

Pure Python + SQLAlchemy — no FastAPI import, no permission/scope check
(authorization stays a separate layer; see ADR-0022 §5: a role assignment
may grant permission to *attempt* an operation, but it never substitutes
for the Club-relationship invariant enforced here). This is the single,
shared mechanism ADR-0022 §3 requires: any write path for these two
relationships — the Group API included — must call
create_group_membership()/create_group_instructor_assignment() below
rather than construct the ORM rows directly or re-implement an
equivalent check per caller. The same shape is intended to generalize to
a future Event<->Group relationship (ADR-0022 §7): resolve each side's
Club, lock the row(s) the decision depends on, compare, then write in
the same transaction.

Every mutating function here owns and commits its own transaction
(mirroring app.authentication.service / app.people.guardian_service's
existing shape) and performs its ownership/invariant check(s), audit
record, and write inside that same transaction — an audit-required
mutation never succeeds without its audit record (ADR-0024 §5 fail-closed
semantics; see app.audit.service.record_audit_event's own docstring).

The two cross-Club ownership checks read the row(s) they depend on with
`SELECT ... FOR SHARE` (`with_for_update(read=True)`) so a concurrent
change to that specific row cannot invalidate an already-passed check
before this transaction commits (ADR-0022 §6) — under PostgreSQL's
default READ COMMITTED isolation, `FOR SHARE` blocks a concurrent
writer until this transaction ends, and (if it must wait) re-reads the
now-committed row before this check proceeds, so a stale read is not
possible either.

The "does this User's Person have an active ClubMembership in this
Club?" half of the check is shared with every other relationship that
needs it (Issue #48's EventStaffAssignment included) via
app.authorization.club_ownership.user_has_active_club_membership —
defined once there rather than re-implemented per relationship.

The duplicate-active-GroupMembership and one-active-primary-
GroupInstructorAssignment invariants (people-api.md §15.2/§16.2) are
DB-level GiST exclusion constraints (app.db.groups) — the functions below
catch the resulting IntegrityError and re-raise it as a typed domain
error, mirroring app.events.service.create_event_staff_assignment's
identical pattern for EventStaffAssignment's own primary-conflict
constraint.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authorization.club_ownership import user_has_active_club_membership
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership
from app.groups.lifecycle import (
    validate_group_membership_status_transition,
    validate_group_status_transition,
)

_NO_DUPLICATE_ACTIVE_MEMBERSHIP_CONSTRAINT = "ck_group_memberships_no_duplicate_active"
_ONE_ACTIVE_PRIMARY_CONSTRAINT = "ck_group_instructor_assignments_one_active_primary"

# Fields the respective PATCH service function accepts — people-api.md
# §14 ("PATCH ... status и club_id не изменяются") / §15 ("Изменяемое
# поле: только valid_from").
UPDATABLE_GROUP_FIELDS = frozenset({"name", "description", "valid_from", "valid_to"})
UPDATABLE_GROUP_MEMBERSHIP_FIELDS = frozenset({"valid_from"})


class GroupOwnershipError(Exception):
    """Base class for this module's typed, expected failures."""


class GroupMembershipClubMismatchError(GroupOwnershipError):
    """ADR-0022 §4: `Group.club_id` must equal `ClubMembership.club_id`."""

    def __init__(self, *, group_club_id: uuid.UUID, club_membership_club_id: uuid.UUID) -> None:
        super().__init__(
            f"Group's club {group_club_id} does not match "
            f"ClubMembership's club {club_membership_club_id}"
        )
        self.group_club_id = group_club_id
        self.club_membership_club_id = club_membership_club_id


class InstructorClubMembershipMissingError(GroupOwnershipError):
    """ADR-0022 §5: the assigned User's Person has no active
    ClubMembership in the Group's Club. A global `instructor` role
    assignment never substitutes for this — this error is raised
    regardless of any role the User holds.
    """

    def __init__(self, *, user_id: uuid.UUID, club_id: uuid.UUID) -> None:
        super().__init__(f"User {user_id} has no active ClubMembership in club {club_id}")
        self.user_id = user_id
        self.club_id = club_id


class GroupArchivedError(GroupOwnershipError):
    """people-api.md §15/§16: a new GroupMembership/GroupInstructorAssignment
    cannot be created for a Group whose `status = 'archived'` — ending an
    already-existing one remains allowed (see end_group_membership/
    end_group_instructor_assignment, which perform no such check)."""

    def __init__(self, *, group_id: uuid.UUID) -> None:
        super().__init__(f"Group {group_id} is archived; new relationships cannot be created")
        self.group_id = group_id


class DuplicateActiveGroupMembershipError(GroupOwnershipError):
    """people-api.md §15.2: an active GroupMembership already exists for
    this (group_id, club_membership_id) pair with an overlapping
    `[valid_from, valid_to)` interval — enforced by the
    `ck_group_memberships_no_duplicate_active` GiST exclusion constraint.
    Multiple simultaneous/overlapping memberships across *different*
    Groups remain unaffected."""

    def __init__(self, *, group_id: uuid.UUID, club_membership_id: uuid.UUID) -> None:
        super().__init__(
            f"An active GroupMembership already exists for group {group_id} / "
            f"club_membership {club_membership_id} with an overlapping interval"
        )
        self.group_id = group_id
        self.club_membership_id = club_membership_id


class GroupInstructorPrimaryConflictError(GroupOwnershipError):
    """people-api.md §16.2: an `is_primary=true` GroupInstructorAssignment
    already exists for this group_id whose `[valid_from, valid_to)`
    interval overlaps the one being created — enforced by the
    `ck_group_instructor_assignments_one_active_primary` GiST exclusion
    constraint. A touching boundary (no actual overlap) is not a
    conflict; sequential historical primaries are unaffected. No
    automatic demotion is performed — the caller must end the
    conflicting assignment first."""

    def __init__(self, *, group_id: uuid.UUID) -> None:
        super().__init__(
            f"An overlapping active primary GroupInstructorAssignment already exists for "
            f"group {group_id}"
        )
        self.group_id = group_id


class InvalidGroupInstructorAssignmentTransitionError(GroupOwnershipError):
    """people-api.md §16.1: `POST .../end` was called for an assignment
    whose `valid_to` is already set to a moment at or before now — this
    entity has no status field, so "already ended" is derived from the
    interval itself rather than a stored vocabulary value."""

    def __init__(self, *, assignment_id: uuid.UUID) -> None:
        super().__init__(f"GroupInstructorAssignment {assignment_id} has already ended")
        self.assignment_id = assignment_id


def _lock_group_club_id(session: Session, group_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        select(Group.club_id).where(Group.id == group_id).with_for_update(read=True)
    ).scalar_one()


def _lock_club_membership_club_id(session: Session, club_membership_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        select(ClubMembership.club_id)
        .where(ClubMembership.id == club_membership_id)
        .with_for_update(read=True)
    ).scalar_one()


def _group_status(session: Session, group_id: uuid.UUID) -> str:
    return session.execute(select(Group.status).where(Group.id == group_id)).scalar_one()


def _json_safe(value: Any) -> Any:
    """ADR-0024 §6 / app.audit.security: audit `details` must be plain
    JSON-safe data — a `datetime` is not — so convert to ISO 8601 the
    same way app.people.service._person_field_diff already does, rather
    than leaving it to fail at the audit boundary."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _is_duplicate_active_membership_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _NO_DUPLICATE_ACTIVE_MEMBERSHIP_CONSTRAINT


def _is_one_active_primary_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _ONE_ACTIVE_PRIMARY_CONSTRAINT


def create_group_membership(
    session: Session,
    *,
    group_id: uuid.UUID,
    club_membership_id: uuid.UUID,
    valid_from: datetime,
    actor_user_id: uuid.UUID,
    membership_status: str = "active",
    valid_to: Optional[datetime] = None,
    request_id: Optional[str] = None,
) -> GroupMembership:
    """ADR-0022 §4: create a GroupMembership only when
    `Group.club_id == ClubMembership.club_id`. people-api.md §15: a new
    GroupMembership always starts `active` (the `membership_status`
    default) and cannot be created against an archived Group.

    Raises GroupMembershipClubMismatchError on a cross-Club mismatch,
    GroupArchivedError when the target Group is archived, and
    DuplicateActiveGroupMembershipError when the
    `ck_group_memberships_no_duplicate_active` invariant would be
    violated — persisting nothing in each case. Records
    `group_membership.created` in the same transaction as the write
    (ADR-0024 fail-closed semantics). The ownership check and the write
    happen in one transaction — see the module docstring for the
    concurrency rationale.
    """
    group_club_id = _lock_group_club_id(session, group_id)
    club_membership_club_id = _lock_club_membership_club_id(session, club_membership_id)
    if group_club_id != club_membership_club_id:
        session.rollback()
        raise GroupMembershipClubMismatchError(
            group_club_id=group_club_id, club_membership_club_id=club_membership_club_id
        )
    if _group_status(session, group_id) == "archived":
        session.rollback()
        raise GroupArchivedError(group_id=group_id)

    membership = GroupMembership(
        group_id=group_id,
        club_membership_id=club_membership_id,
        valid_from=valid_from,
        valid_to=valid_to,
        membership_status=membership_status,
    )
    session.add(membership)
    try:
        session.flush()
        record_audit_event(
            session,
            action="group_membership.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group_membership",
            resource_id=membership.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_duplicate_active_membership_violation(exc):
            raise DuplicateActiveGroupMembershipError(
                group_id=group_id, club_membership_id=club_membership_id
            ) from exc
        raise
    return membership


def update_group_membership(
    session: Session,
    *,
    membership: GroupMembership,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
    **fields: Any,
) -> GroupMembership:
    """people-api.md §15: PATCH accepts only `valid_from` (a data-entry
    correction, never a lifecycle transition) — `group_id`,
    `club_membership_id` and `membership_status` are immutable here (see
    app.api.v1.groups for the immutable-field rejection). No-ops (no
    mutation, no audit record) when nothing actually changes.
    """
    unknown_fields = set(fields) - UPDATABLE_GROUP_MEMBERSHIP_FIELDS
    if unknown_fields:
        raise ValueError(
            f"Fields not updatable via update_group_membership: {sorted(unknown_fields)}"
        )

    changes: dict[str, Any] = {}
    for field_name, new_value in fields.items():
        old_value = getattr(membership, field_name)
        if old_value == new_value:
            continue
        changes[field_name] = {"from": _json_safe(old_value), "to": _json_safe(new_value)}
        setattr(membership, field_name, new_value)

    if not changes:
        return membership

    try:
        session.flush()
        record_audit_event(
            session,
            action="group_membership.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group_membership",
            resource_id=membership.id,
            outcome="success",
            request_id=request_id,
            details={"changes": changes},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return membership


def end_group_membership(
    session: Session,
    *,
    membership: GroupMembership,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> GroupMembership:
    """people-api.md §15.1: the only allowed `membership_status`
    transition, `active -> ended`; sets `valid_to = now()` (mirroring
    app.people.service.transition_membership_status's identical
    `left_at`-on-ending pattern). Raises
    InvalidGroupMembershipStatusTransitionError, and persists nothing,
    when `membership` is already `ended` (terminal).
    """
    validate_group_membership_status_transition(membership.membership_status, "ended")

    old_status = membership.membership_status
    membership.membership_status = "ended"
    membership.valid_to = datetime.now(timezone.utc)

    try:
        session.flush()
        record_audit_event(
            session,
            action="group_membership.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group_membership",
            resource_id=membership.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"membership_status": {"from": old_status, "to": "ended"}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return membership


def create_group_instructor_assignment(
    session: Session,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    role_in_group: str,
    valid_from: datetime,
    actor_user_id: uuid.UUID,
    valid_to: Optional[datetime] = None,
    is_primary: bool = False,
    request_id: Optional[str] = None,
) -> GroupInstructorAssignment:
    """ADR-0022 §5: create a GroupInstructorAssignment only when the
    User's Person has an active ClubMembership in the target Group's
    Club. people-api.md §16: cannot be created against an archived
    Group.

    Raises InstructorClubMembershipMissingError when that relationship is
    absent (regardless of any global `instructor` role the User may
    hold), GroupArchivedError when the target Group is archived, and
    GroupInstructorPrimaryConflictError when `is_primary=True` and its
    interval overlaps an existing active primary assignment for the same
    Group — persisting nothing in each case. Records
    `group_instructor_assignment.created` in the same transaction as the
    write. The ownership check and the write happen in one transaction —
    see the module docstring for the concurrency rationale.
    """
    group_club_id = _lock_group_club_id(session, group_id)
    if not user_has_active_club_membership(session, user_id=user_id, club_id=group_club_id):
        session.rollback()
        raise InstructorClubMembershipMissingError(user_id=user_id, club_id=group_club_id)
    if _group_status(session, group_id) == "archived":
        session.rollback()
        raise GroupArchivedError(group_id=group_id)

    assignment = GroupInstructorAssignment(
        group_id=group_id,
        user_id=user_id,
        role_in_group=role_in_group,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(assignment)
    try:
        session.flush()
        record_audit_event(
            session,
            action="group_instructor_assignment.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group_instructor_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_one_active_primary_violation(exc):
            raise GroupInstructorPrimaryConflictError(group_id=group_id) from exc
        raise
    return assignment


def end_group_instructor_assignment(
    session: Session,
    *,
    assignment: GroupInstructorAssignment,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> GroupInstructorAssignment:
    """people-api.md §16.1: sets `valid_to = now()`, ending the
    assignment's period of effect. Raises
    InvalidGroupInstructorAssignmentTransitionError, and persists
    nothing, when `assignment` has already ended (`valid_to` already set
    to a moment at or before now).
    """
    now = datetime.now(timezone.utc)
    if assignment.valid_to is not None and assignment.valid_to <= now:
        raise InvalidGroupInstructorAssignmentTransitionError(assignment_id=assignment.id)

    old_valid_to = assignment.valid_to
    assignment.valid_to = now

    try:
        session.flush()
        record_audit_event(
            session,
            action="group_instructor_assignment.ended",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group_instructor_assignment",
            resource_id=assignment.id,
            outcome="success",
            request_id=request_id,
            details={
                "changes": {"valid_to": {"from": _json_safe(old_valid_to), "to": _json_safe(now)}}
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return assignment


def create_group(
    session: Session,
    *,
    club_id: uuid.UUID,
    name: str,
    actor_user_id: uuid.UUID,
    description: Optional[str] = None,
    valid_from: Optional[datetime] = None,
    valid_to: Optional[datetime] = None,
    request_id: Optional[str] = None,
) -> Group:
    """people-api.md §14: a created Group always gets `status = 'active'`
    (Issue #69 PO decision, §14.1) — the caller cannot pass a different
    status. Records `group.created` in the same transaction as the
    write.
    """
    group = Group(
        club_id=club_id,
        name=name,
        description=description,
        status="active",
        valid_from=valid_from if valid_from is not None else datetime.now(timezone.utc),
        valid_to=valid_to,
    )
    session.add(group)
    try:
        session.flush()
        record_audit_event(
            session,
            action="group.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group",
            resource_id=group.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return group


def update_group(
    session: Session,
    *,
    group: Group,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
    **fields: Any,
) -> Group:
    """people-api.md §14: PATCH accepts only `name`/`description`/
    `valid_from`/`valid_to` — `status` and `club_id` are immutable here
    (see app.api.v1.groups for the immutable-field rejection; `status`
    changes only through archive_group). No-ops (no mutation, no audit
    record) when nothing actually changes.
    """
    unknown_fields = set(fields) - UPDATABLE_GROUP_FIELDS
    if unknown_fields:
        raise ValueError(f"Fields not updatable via update_group: {sorted(unknown_fields)}")

    changes: dict[str, Any] = {}
    for field_name, new_value in fields.items():
        old_value = getattr(group, field_name)
        if old_value == new_value:
            continue
        changes[field_name] = {"from": _json_safe(old_value), "to": _json_safe(new_value)}
        setattr(group, field_name, new_value)

    if not changes:
        return group

    try:
        session.flush()
        record_audit_event(
            session,
            action="group.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group",
            resource_id=group.id,
            outcome="success",
            request_id=request_id,
            details={"changes": changes},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return group


def archive_group(
    session: Session,
    *,
    group: Group,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> Group:
    """people-api.md §14.1: the only allowed `status` transition,
    `active -> archived` (terminal — no restore/unarchive exists).
    Recorded as `group.updated` (ADR-0024's closed vocabulary has no
    dedicated `group.archived`/`group.status_changed` action — this
    mapping is itself the Issue #69 PO decision, not a substitution
    invented here). Raises InvalidGroupStatusTransitionError, and
    persists nothing, when `group` is already `archived`.
    """
    validate_group_status_transition(group.status, "archived")

    old_status = group.status
    group.status = "archived"

    try:
        session.flush()
        record_audit_event(
            session,
            action="group.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="group",
            resource_id=group.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"status": {"from": old_status, "to": "archived"}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return group


__all__ = [
    "GroupOwnershipError",
    "GroupMembershipClubMismatchError",
    "InstructorClubMembershipMissingError",
    "GroupArchivedError",
    "DuplicateActiveGroupMembershipError",
    "GroupInstructorPrimaryConflictError",
    "InvalidGroupInstructorAssignmentTransitionError",
    "UPDATABLE_GROUP_FIELDS",
    "UPDATABLE_GROUP_MEMBERSHIP_FIELDS",
    "create_group_membership",
    "update_group_membership",
    "end_group_membership",
    "create_group_instructor_assignment",
    "end_group_instructor_assignment",
    "create_group",
    "update_group",
    "archive_group",
]
