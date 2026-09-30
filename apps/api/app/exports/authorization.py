"""Participant Export authorization (participant-export-api.md §2; GAP-1).

One policy for every output format — XLSX, PDF and print never have
separate authorization paths (§7, §9). No new permission or scope exists
for this workflow; access is composed entirely from existing mechanisms:

1. the requester holds the canonical system `admin` Role (recognized
   exactly as app.authentication.bootstrap/app.imports.authorization
   recognize it: `Role.code == ADMIN_ROLE_CODE` *and* `Role.is_system`)
   through a currently-effective assignment that reaches the current Club
   (installation-wide `club_id IS NULL`, or scoped to that Club);
2. for every data type actually included in the export, the requester
   holds the existing read permission with `all` scope in that Club —
   checked through the ordinary engine (`Authorizer.check`) with a
   `ResourceContext` carrying only `club_id`, which leaves every
   relationship scope unresolved so only `all` can match.

Either failing raises `AuthorizationDenied` (→ the canonical 403).
"""

import uuid
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authentication.bootstrap import ADMIN_ROLE_CODE
from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, Authorizer
from app.db.authorization import Role, UserRoleAssignment

# Label carried by the AuthorizationDenied raised for a missing
# Administrator role — never shown to the client (the 403 envelope carries
# no permission/role detail, app.api.errors).
ADMINISTRATOR_REQUIREMENT = "participant_export.administrator"


def is_club_administrator(session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID) -> bool:
    """True if `user_id` has a currently-effective assignment of the
    canonical system `admin` Role reaching `club_id`. Uses the same
    `valid_from <= now() AND (valid_to IS NULL OR now() < valid_to)`
    effectivity rule as app.authorization.service.applicable_grants."""
    now = sa.func.now()
    stmt = (
        sa.select(UserRoleAssignment.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            UserRoleAssignment.user_id == user_id,
            Role.code == ADMIN_ROLE_CODE,
            Role.is_system.is_(True),
            sa.or_(UserRoleAssignment.club_id.is_(None), UserRoleAssignment.club_id == club_id),
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def require_club_administrator(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> None:
    if not is_club_administrator(session, user_id=user_id, club_id=club_id):
        raise AuthorizationDenied(ADMINISTRATOR_REQUIREMENT)


def require_club_wide_read(
    session: Session,
    *,
    user_id: uuid.UUID,
    club_id: uuid.UUID,
    permission_codes: Iterable[str],
) -> None:
    """Each permission must reach `club_id` with `all` scope."""
    context = ResourceContext(club_id=club_id)
    for code in sorted(set(permission_codes)):
        Authorizer(session=session, user_id=user_id, permission_code=code).check(context)


__all__ = [
    "ADMINISTRATOR_REQUIREMENT",
    "is_club_administrator",
    "require_club_administrator",
    "require_club_wide_read",
]
