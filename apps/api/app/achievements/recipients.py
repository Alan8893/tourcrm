"""Achievement recipients (Issue #220, A12).

Canonical sources: docs/04-modules/achievements-and-norms.md §10, §27;
ADR-0039 §2-§3 (RoleAssignment is the canonical source of a Person's
system role; `member` — club participant; ClubMembership.membership_type
is never a substitute for a role).

A Person is an Achievement recipient iff their User currently holds the
canonical system `member` Role — a currently-effective
`UserRoleAssignment` (`valid_from <= now() AND (valid_to IS NULL OR
now() < valid_to)`, the same effectivity rule as
app.authorization.service.applicable_grants). Holding `admin`,
`instructor` or `guardian` never makes a Person a recipient, and Event
participation alone never does either; a Person who additionally holds
`member` is a recipient as a Member. No achievement-specific recipient
classification exists.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.achievements.vocabulary import MEMBER_ROLE_CODE
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import User


def _member_person_ids_query() -> sa.Select[tuple[uuid.UUID]]:
    now = sa.func.now()
    return (
        sa.select(User.person_id)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            Role.code == MEMBER_ROLE_CODE,
            Role.is_system.is_(True),
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
        .distinct()
    )


def is_member(session: Session, person_id: uuid.UUID) -> bool:
    stmt = _member_person_ids_query().where(User.person_id == person_id)
    return session.execute(stmt.limit(1)).first() is not None


def member_person_ids(
    session: Session, person_ids: Sequence[uuid.UUID] | None = None
) -> list[uuid.UUID]:
    """Members among `person_ids` (or every Member when `None`)."""
    stmt = _member_person_ids_query()
    if person_ids is not None:
        if not person_ids:
            return []
        stmt = stmt.where(User.person_id.in_(list(person_ids)))
    return sorted(session.execute(stmt).scalars().all())


__all__ = ["is_member", "member_person_ids"]
