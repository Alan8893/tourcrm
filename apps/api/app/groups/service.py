"""Canonical Club-ownership validation for Group relationship writes
(ADR-0022).

Pure Python + SQLAlchemy — no FastAPI import, no permission/scope check
(authorization stays a separate layer; see ADR-0022 §5: a role assignment
may grant permission to *attempt* an operation, but it never substitutes
for the Club-relationship invariant enforced here). This is the single,
shared mechanism ADR-0022 §3 requires: any write path for these two
relationships — a future Group API included — must call
create_group_membership()/create_group_instructor_assignment() below
rather than construct the ORM rows directly or re-implement an
equivalent check per caller. The same shape is intended to generalize to
a future Event<->Group relationship (ADR-0022 §7): resolve each side's
Club, lock the row(s) the decision depends on, compare, then write in
the same transaction.

Each function owns and commits its own transaction (mirroring
app.authentication.service / app.authorization.service's existing
shape) and performs its ownership check and its write inside that same
transaction. The row(s) the check depends on are read with
`SELECT ... FOR SHARE` (`with_for_update(read=True)`) so a concurrent
change to that specific row cannot invalidate an already-passed check
before this transaction commits (ADR-0022 §6) — under PostgreSQL's
default READ COMMITTED isolation, `FOR SHARE` blocks a concurrent
writer until this transaction ends, and (if it must wait) re-reads the
now-committed row before this check proceeds, so a stale read is not
possible either.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, User

# identity.py's documented ClubMembership.status vocabulary (Issue #17):
# pending, active, suspended, inactive, archived. "active" is used here
# exactly as it is everywhere else in this codebase — no new value.
ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"


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


def _user_has_active_club_membership(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> bool:
    person_id = session.execute(select(User.person_id).where(User.id == user_id)).scalar_one()
    row = session.execute(
        select(ClubMembership.id)
        .where(
            ClubMembership.person_id == person_id,
            ClubMembership.club_id == club_id,
            ClubMembership.status == ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
        .with_for_update(read=True)
        .limit(1)
    ).first()
    return row is not None


def create_group_membership(
    session: Session,
    *,
    group_id: uuid.UUID,
    club_membership_id: uuid.UUID,
    valid_from: datetime,
    membership_status: str,
    valid_to: Optional[datetime] = None,
) -> GroupMembership:
    """ADR-0022 §4: create a GroupMembership only when
    `Group.club_id == ClubMembership.club_id`.

    Raises GroupMembershipClubMismatchError, and persists nothing, on a
    mismatch. The ownership check and the write happen in one
    transaction — see the module docstring for the concurrency rationale.
    """
    group_club_id = _lock_group_club_id(session, group_id)
    club_membership_club_id = _lock_club_membership_club_id(session, club_membership_id)
    if group_club_id != club_membership_club_id:
        session.rollback()
        raise GroupMembershipClubMismatchError(
            group_club_id=group_club_id, club_membership_club_id=club_membership_club_id
        )

    membership = GroupMembership(
        group_id=group_id,
        club_membership_id=club_membership_id,
        valid_from=valid_from,
        valid_to=valid_to,
        membership_status=membership_status,
    )
    session.add(membership)
    session.commit()
    return membership


def create_group_instructor_assignment(
    session: Session,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    role_in_group: str,
    valid_from: datetime,
    valid_to: Optional[datetime] = None,
    is_primary: bool = False,
) -> GroupInstructorAssignment:
    """ADR-0022 §5: create a GroupInstructorAssignment only when the
    User's Person has an active ClubMembership in the target Group's
    Club.

    Raises InstructorClubMembershipMissingError, and persists nothing,
    when that relationship is absent — regardless of any global
    `instructor` role the User may hold. The ownership check and the
    write happen in one transaction — see the module docstring for the
    concurrency rationale.
    """
    group_club_id = _lock_group_club_id(session, group_id)
    if not _user_has_active_club_membership(session, user_id=user_id, club_id=group_club_id):
        session.rollback()
        raise InstructorClubMembershipMissingError(user_id=user_id, club_id=group_club_id)

    assignment = GroupInstructorAssignment(
        group_id=group_id,
        user_id=user_id,
        role_in_group=role_in_group,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    session.add(assignment)
    session.commit()
    return assignment


__all__ = [
    "GroupOwnershipError",
    "GroupMembershipClubMismatchError",
    "InstructorClubMembershipMissingError",
    "create_group_membership",
    "create_group_instructor_assignment",
]
