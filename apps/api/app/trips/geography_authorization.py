"""Country/Region catalog authorization (Issue #271).

Canonical sources: docs/04-modules/trips-and-tourist-profile.md §9,
docs/02-requirements/roles-and-permissions.md — the Country/Region
catalogs are maintained by the Administrator only; Instructor, Member
and Guardian never create, change, activate or deactivate entries.

No Geography-specific permission exists; the existing Trip permissions
are reused exactly as for the TourismType catalog
(app.trips.tourism_type_authorization):

- managing either catalog requires a `trip.manage` grant with `all`
  scope in the installation's Club — the Administrator. Instructor's
  `trip.manage` is scoped to own groups/events and therefore never
  matches; Member/Guardian hold no `trip.manage` at all;
- reading either catalog requires any `trip.read` grant reaching the
  Club — every role that can see a Trip can resolve its Geography.

Assigning Geography to a Trip is ordinary Trip editing and follows the
Trip's own `trip.manage` scope (app.api.v1.trips). Both checks run
before any record is loaded, so a denied caller gets the same 403
whether or not an id exists.
"""

import uuid

from sqlalchemy.orm import Session

from app.trips.tourism_type_authorization import (
    require_tourism_type_manager,
    require_tourism_type_reader,
)


def require_geography_manager(session: Session, *, user_id: uuid.UUID) -> None:
    require_tourism_type_manager(session, user_id=user_id)


def require_geography_reader(session: Session, *, user_id: uuid.UUID) -> None:
    require_tourism_type_reader(session, user_id=user_id)


__all__ = ["require_geography_manager", "require_geography_reader"]
