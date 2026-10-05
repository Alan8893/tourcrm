"""Duration Classification authorization (Issue #274).

Canonical sources: docs/04-modules/trips-and-tourist-profile.md §10 and
docs/02-requirements/roles-and-permissions.md (Trip: Official Difficulty,
Duration Classification — set/change: Administrator only) — the
Administrator sets and changes the classification through ordinary Trip
editing; Instructor gets no duration-classification permission and no
right to set it.

No Duration-specific permission exists: exactly as for Official
Difficulty (app.trips.official_difficulty_authorization), setting or
changing it requires a `trip.manage` grant with `all` scope for the
Trip's Club. The Trip API calls this only after the caller already
passed the Trip's ordinary `trip.manage` check, so the 403 never
discloses whether a Trip exists. Reading follows the Trip's `trip.read`.
"""

import uuid

from sqlalchemy.orm import Session

from app.trips.official_difficulty_authorization import require_official_difficulty_manager


def require_duration_classification_manager(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> None:
    require_official_difficulty_manager(session, user_id=user_id, club_id=club_id)


__all__ = ["require_duration_classification_manager"]
