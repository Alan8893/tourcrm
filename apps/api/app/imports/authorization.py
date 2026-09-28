"""ImportJob authorization and object-access policy (TH-0118.1 / Issue #185).

Canonical source: docs/05-api/people-api.md §22 "Import authorization and
object access":

- every import operation requires `membership.import` with `all` scope in
  the job's Club;
- an ImportJob is bound to exactly one Club and records its creating User;
- a requester may access a job only when (1) they hold `membership.import`
  with `all` scope in the job's Club, and (2) they are the job's creator
  **or** an Administrator authorized for that Club;
- an existing job outside the requester's authorized object scope is
  answered exactly like a nonexistent one (404, existence-hiding).

Built entirely on the existing engine's building blocks
(`applicable_grants`/`club_boundary_matches`/`scope_matches` from
app.authorization.service) — no parallel authorization mechanism.
"Administrator" is recognized through the same identity
app.authentication.bootstrap and app.people.authorization already use for
the canonical administrator (`Role.code == ADMIN_ROLE_CODE` *and*
`Role.is_system`), never an ad hoc role-name comparison; "authorized for
that Club" means that administrator role is the one granting the
qualifying `membership.import` + `all` assignment for the job's Club.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authentication.bootstrap import ADMIN_ROLE_CODE
from app.authorization.context import ResourceContext
from app.authorization.service import (
    PermissionGrant,
    applicable_grants,
    club_boundary_matches,
    scope_matches,
)
from app.db.identity import Club
from app.db.imports import ImportJob

PERMISSION_CODE = "membership.import"


class NoClubConfiguredError(Exception):
    """No Club exists yet. Should be unreachable — bootstrap creates the
    installation's one Club atomically with its first administrator."""


class MultipleClubsConfiguredError(Exception):
    """More than one Club exists — a violation of TourCRM's current
    single-Club product invariant (mirrors app.role_assignments.
    person_roles.MultipleClubsConfiguredError's identical reasoning)."""


def resolve_sole_club_id(session: Session) -> uuid.UUID:
    """The installation's one Club — the Club a new ImportJob is bound to.
    `POST /memberships/imports` accepts no `club_id` from the client, so
    this mirrors app.role_assignments.person_roles.resolve_sole_club_id
    exactly (duplicated rather than imported across this domain boundary,
    per this codebase's own convention): never a per-request choice, never
    `.first()` among several."""
    club_ids = session.execute(sa.select(Club.id)).scalars().all()
    if len(club_ids) == 0:
        raise NoClubConfiguredError("No Club exists yet; bootstrap must run first")
    if len(club_ids) > 1:
        raise MultipleClubsConfiguredError(
            "More than one Club exists; TourCRM's current product requires exactly one"
        )
    return club_ids[0]


def _qualifying_grants(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> list[PermissionGrant]:
    """The requester's currently-effective `membership.import` grants
    that reach `club_id` with `all` scope. A `ResourceContext` carrying only
    `club_id` leaves every relationship scope unresolved (`None`), so only
    `scope_type == "all"` can match — exactly the canonical requirement."""
    context = ResourceContext(club_id=club_id)
    return [
        grant
        for grant in applicable_grants(session, user_id, PERMISSION_CODE)
        if club_boundary_matches(grant.club_id, context.club_id)
        and scope_matches(grant.scope_type, context)
    ]


def can_access_import_job(session: Session, *, job: ImportJob, user_id: uuid.UUID) -> bool:
    """people-api.md §22 object policy: `membership.import` + `all` in the
    job's Club, and either the job's creator or an Administrator authorized
    for that Club."""
    grants = _qualifying_grants(session, user_id=user_id, club_id=job.club_id)
    if not grants:
        return False
    if job.created_by_user_id == user_id:
        return True
    return any(
        grant.assignment.role.code == ADMIN_ROLE_CODE and grant.assignment.role.is_system
        for grant in grants
    )


__all__ = [
    "PERMISSION_CODE",
    "NoClubConfiguredError",
    "MultipleClubsConfiguredError",
    "resolve_sole_club_id",
    "can_access_import_job",
]
