"""Achievement authorization (Issue #220, A10).

Canonical source: docs/04-modules/achievements-and-norms.md §6, §17, §25.

- `achievement.manage` — Definitions, Rule Versions, Normative
  Requirement Sets (incl. activation/deactivation, version management)
  and Engine reconciliation;
- `achievement.award` — manual Award issuance and Award revocation;
- `achievement.read` — reading the Award history.

All three are checked through the existing RBAC engine
(app.authorization.service) against the installation's one Club with a
`ResourceContext` that carries only `club_id`: every relationship scope
stays unresolved, so only an `all`-scope grant can match. That is exactly
the seeded Administrator grant (migration b7e3d1a9c5f2); Instructor,
Member and Guardian hold no such grant and are denied. The backend check
is authoritative — frontend visibility is UX only.

The check runs before any target record is loaded, so a denied caller
gets the same 403 whether or not an id exists.
"""

import uuid

from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, can
from app.imports.authorization import resolve_sole_club_id


def require_achievement_permission(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> None:
    """Raise AuthorizationDenied (-> 403) unless `user_id` holds
    `permission_code` with `all` scope in the installation's Club."""
    club_id = resolve_sole_club_id(session)
    if not can(session, user_id, permission_code, ResourceContext(club_id=club_id)):
        raise AuthorizationDenied(permission_code)


__all__ = ["require_achievement_permission"]
