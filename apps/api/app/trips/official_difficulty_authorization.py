"""Official Difficulty authorization (Issue #268).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §4 —
Official Difficulty is an administrative classification fact: the
Administrator sets and changes it; Instructor gets no right to set or
change it and no Difficulty-specific permission.

No Difficulty-specific permission exists; the existing `trip.manage` is
reused exactly as for TourismType catalog management
(app.trips.tourism_type_authorization): setting, changing or clearing a
Trip's Official Difficulty requires a `trip.manage` grant with `all`
scope for the Trip's Club — the Administrator. Instructor's
`trip.manage` is scoped to own groups/events and therefore never
matches; Member/Guardian hold no `trip.manage` at all.

The Trip API calls this only after the caller already passed the Trip's
ordinary `trip.manage` check, so the 403 never discloses whether a Trip
exists. Reading the Difficulty follows the Trip's `trip.read`.
"""

import uuid

from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, can

TRIP_MANAGE_PERMISSION = "trip.manage"


def require_official_difficulty_manager(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> None:
    # No relationship field is resolved, so only an `all`-scope grant
    # reaching the Club matches (app.authorization.service.scope_matches).
    if not can(session, user_id, TRIP_MANAGE_PERMISSION, ResourceContext(club_id=club_id)):
        raise AuthorizationDenied(TRIP_MANAGE_PERMISSION)


__all__ = ["require_official_difficulty_manager"]
