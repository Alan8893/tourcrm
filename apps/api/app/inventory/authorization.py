"""Inventory authorization (docs/04-domain/inventory.md §3).

Every Inventory operation — reads included — is Administrator-only.
The Administrator is recognized exactly as the other Administrator-only
workflows recognize it (Participant Export/Import, News):
app.exports.authorization.is_club_administrator — a currently-effective
assignment of the canonical system `admin` Role reaching the Club. No new
role and no permission is introduced; the catalog codes
`equipment.read`/`equipment.manage` are deliberately not consulted
(PO decision G7).

The check runs before the target record is loaded, so a
non-Administrator gets the same 403 whether or not an id exists.
"""

import uuid

from sqlalchemy.orm import Session

from app.authorization.service import AuthorizationDenied
from app.exports.authorization import is_club_administrator
from app.imports.authorization import resolve_sole_club_id

# Label carried by the AuthorizationDenied raised for a non-Administrator —
# never shown to the client (the 403 envelope carries no role detail).
ADMINISTRATOR_REQUIREMENT = "inventory.administrator"


def require_inventory_administrator(session: Session, *, user_id: uuid.UUID) -> uuid.UUID:
    """Return the installation's Club id if `user_id` is its Administrator;
    raise AuthorizationDenied (→ 403) otherwise."""
    club_id = resolve_sole_club_id(session)
    if not is_club_administrator(session, user_id=user_id, club_id=club_id):
        raise AuthorizationDenied(ADMINISTRATOR_REQUIREMENT)
    return club_id


__all__ = ["ADMINISTRATOR_REQUIREMENT", "require_inventory_administrator"]
