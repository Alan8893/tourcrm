"""TourismType catalog authorization (Issue #264).

No TourismType-specific permission exists; the existing Trip permissions
are reused (roles-and-permissions.md §11.1):

- managing the catalog (create/update/activate/deactivate) requires a
  `trip.manage` grant with `all` scope in the installation's Club — the
  Administrator. Instructor's `trip.manage` is scoped to own groups/events
  and therefore never manages the catalog; Member/Guardian hold no
  `trip.manage` at all;
- reading the catalog requires any `trip.read` grant reaching the Club —
  every role that can see a Trip can resolve the TourismType it
  references.

Both checks run before any record is loaded, so a denied caller gets the
same 403 whether or not an id exists.
"""

import uuid

from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.authorization.service import (
    AuthorizationDenied,
    applicable_grants,
    can,
    club_boundary_matches,
)
from app.imports.authorization import resolve_sole_club_id

TRIP_READ_PERMISSION = "trip.read"
TRIP_MANAGE_PERMISSION = "trip.manage"


def require_tourism_type_manager(session: Session, *, user_id: uuid.UUID) -> None:
    club_id = resolve_sole_club_id(session)
    if not can(session, user_id, TRIP_MANAGE_PERMISSION, ResourceContext(club_id=club_id)):
        raise AuthorizationDenied(TRIP_MANAGE_PERMISSION)


def require_tourism_type_reader(session: Session, *, user_id: uuid.UUID) -> None:
    club_id = resolve_sole_club_id(session)
    if not any(
        club_boundary_matches(grant.club_id, club_id)
        for grant in applicable_grants(session, user_id, TRIP_READ_PERMISSION)
    ):
        raise AuthorizationDenied(TRIP_READ_PERMISSION)


__all__ = ["require_tourism_type_manager", "require_tourism_type_reader"]
