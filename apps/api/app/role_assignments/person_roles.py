"""Person-centric system role management for Person Detail (TH-0112,
implementing ADR-0039).

Canonical sources: docs/03-architecture/adr/ADR-0039-person-role-
assignment.md, docs/02-requirements/roles-and-permissions.md §13.1,
ADR-0025 §6 (the canonical RoleAssignment resource is the flat
`/api/v1/role-assignments` collection, not a nested `.../roles` shape),
ADR-0026 (RoleAssignment create/revoke decisions).

This module introduces no new model, no new table, and no new
authorization semantics. It is a thin, Person/canonical-role-code-shaped
entry point onto the exact same `UserRoleAssignment` row and the exact
same `app.role_assignments.service.create_role_assignment`/
`revoke_role_assignment` functions the generic `/api/v1/role-assignments`
API already uses (ADR-0025 §6 is not being re-litigated: that flat
resource remains canonical; this only adds a Person-scoped, restricted
view onto it, mirroring how `/persons/{id}/memberships` and
`/persons/{id}/guardian-relationships` already coexist as scoped views
onto their own flat resources).

Two things the generic API leaves to its caller are decided here, once,
for this one restricted entry point:

1. **Identity.** The client never supplies a `user_id` (ADR-0039 requires
   identity to be resolved "backend через Person -> User"). `Person.user`
   is optional (a Person created by an admin via `POST /persons` has no
   User at all until/unless it is later linked by a self-registration
   matching identifier — TH-0112 does not introduce or change any
   account-creation/linking workflow). `PersonHasNoUserAccountError` is
   raised, never a User created implicitly, when the target Person has no
   User yet.

2. **`scope_type`.** A `UserRoleAssignment` scope is a property of the
   *assignment*, not the role (app.db.authorization.UserRoleAssignment's
   own docstring) — the generic API's caller must always choose one.
   `role.manage` itself is only ever effective with `scope_type='all'`
   (app.role_assignments.authorization module docstring), and — as of
   this writing — `instructor`/`member`/`guardian` carry **zero**
   `RolePermission` grants (only `admin` is fully seeded;
   `95487f3b616b` grants `instructor` exactly one narrow permission,
   `user.directory.read`, whose own policy ignores `scope_type` entirely
   — see app.users.authorization). Mapping roles-and-permissions.md §7's
   matrix into concrete `own_groups`/`self`/`children` grants for these
   three roles was explicitly deferred to a future issue
   (`e5ae1ad9e1e1`'s and `6a99a77234ba`'s own migration docstrings) — this
   task must not make that product decision either. `scope_type='all'`
   is therefore the only value that is already, demonstrably correct for
   every canonical role today (it cannot over-grant a permission that
   does not exist yet), and is used uniformly for admin/instructor/
   member/guardian alike. Whoever eventually seeds real scope-sensitive
   grants for instructor/member/guardian must revisit this default at
   the same time — this module's docstring is the flag for that.

`club_id` is always the installation's sole Club (TourCRM is not
multi-club — see `resolve_sole_club_id` below): the admin never picks a
Club, matching every other single-Club resolution already in this
codebase (app.people.authorization.resolve_current_club_id_for_person_
create's identical two-step "establish the sole Club, never guess among
several" shape).
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.authorization import BASELINE_ROLE_CODES, Role, UserRoleAssignment
from app.db.identity import Club, User
from app.role_assignments import service as role_assignment_service

# ADR-0039 §3: the closed MVP role set. Re-exported so callers (the API
# router) validate against the same single source of truth.
CANONICAL_PERSON_ROLE_CODES = BASELINE_ROLE_CODES

# See module docstring point 2. Uniform across all four canonical roles.
_PERSON_ROLE_SCOPE_TYPE = "all"


class PersonRoleAssignmentError(Exception):
    """Base class for this module's typed, expected failures."""


class PersonHasNoUserAccountError(PersonRoleAssignmentError):
    """The target Person has no linked User — role assignment (a User-
    level concept) is not yet possible. Never resolved by creating a User
    here: role assignment is not account creation (ADR-0039 is silent on
    account provisioning; inventing one would be an unrelated, unrequested
    decision)."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} has no linked User account")
        self.person_id = person_id


class NoClubConfiguredError(PersonRoleAssignmentError):
    """No Club exists yet. Should be unreachable — bootstrap creates the
    installation's one Club atomically with its first administrator."""


class MultipleClubsConfiguredError(PersonRoleAssignmentError):
    """More than one Club exists — a violation of TourCRM's current
    single-Club product invariant, not a "which one did they mean"
    question this module is prepared to answer (mirrors
    app.people.authorization.MultipleClubsConfiguredError's identical
    reasoning for the same invariant)."""


class InvalidPersonRoleCodeError(PersonRoleAssignmentError):
    """`role_code` is not one of ADR-0039 §3's four canonical codes."""

    def __init__(self, *, role_code: str) -> None:
        super().__init__(f"{role_code!r} is not a canonical role code")
        self.role_code = role_code


class PersonRoleAssignmentNotFoundError(PersonRoleAssignmentError):
    """No currently-effective assignment of `role_code` exists for this
    Person to revoke."""

    def __init__(self, *, person_id: uuid.UUID, role_code: str) -> None:
        super().__init__(f"Person {person_id} has no active {role_code!r} role assignment")
        self.person_id = person_id
        self.role_code = role_code


def user_id_for_person(session: Session, person_id: uuid.UUID) -> Optional[uuid.UUID]:
    """The User linked to `person_id`, or `None` if that Person has no
    User yet (`User.person_id` is unique but not required — see
    app.db.identity.Person's own docstring: "Person is a physical person,
    independent of any account")."""
    return session.execute(
        sa.select(User.id).where(User.person_id == person_id)
    ).scalar_one_or_none()


def resolve_sole_club_id(session: Session) -> uuid.UUID:
    """The installation's one Club (see module docstring) — never a
    per-request choice, never `.first()` among several."""
    club_ids = session.execute(sa.select(Club.id)).scalars().all()
    if len(club_ids) == 0:
        raise NoClubConfiguredError("No Club exists yet; bootstrap must run first")
    if len(club_ids) > 1:
        raise MultipleClubsConfiguredError(
            "More than one Club exists; TourCRM's current product requires exactly one"
        )
    return club_ids[0]


def _role_by_code(session: Session, role_code: str) -> Role:
    if role_code not in CANONICAL_PERSON_ROLE_CODES:
        raise InvalidPersonRoleCodeError(role_code=role_code)
    role = session.execute(sa.select(Role).where(Role.code == role_code)).scalar_one_or_none()
    if role is None:
        # Unreachable in practice: e5ae1ad9e1e1 seeds all four baseline
        # roles unconditionally. Left uncaught, like the analogous
        # "should never happen" cases in app.people.authorization/
        # app.authentication.bootstrap — the generic 500 handler is the
        # controlled error path for a genuine data-integrity gap.
        raise PersonRoleAssignmentError(f"Canonical role {role_code!r} is missing")
    return role


def list_person_role_assignments(
    session: Session, *, person_id: uuid.UUID
) -> list[UserRoleAssignment]:
    """Every currently-effective RoleAssignment for `person_id`'s own
    User, newest first. Returns an empty list — not an error — for a
    Person with no linked User: "no roles" and "cannot have roles yet"
    render identically in the list (the distinction only matters for the
    mutating operations below, which do raise
    `PersonHasNoUserAccountError`)."""
    user_id = user_id_for_person(session, person_id)
    if user_id is None:
        return []
    now = sa.func.now()
    rows = (
        session.execute(
            sa.select(UserRoleAssignment)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.valid_from <= now,
                sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
            )
            .order_by(UserRoleAssignment.created_at.desc())
        )
        .scalars()
        .all()
    )
    return list(rows)


def add_person_role(
    session: Session,
    *,
    person_id: uuid.UUID,
    role_code: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> UserRoleAssignment:
    """ADR-0039: grant `role_code` to `person_id`'s own User. Does not
    create a User, does not mutate Person/ClubMembership, and creates no
    Group/Event/GuardianRelationship side effect — this only ever calls
    `app.role_assignments.service.create_role_assignment`, the exact same
    function the generic `/api/v1/role-assignments` API uses.

    Raises PersonHasNoUserAccountError, InvalidPersonRoleCodeError, or
    (bubbled from the underlying service, unchanged) RoleAssignmentClub
    MembershipMissingError / DuplicateRoleAssignmentError.
    """
    role = _role_by_code(session, role_code)
    user_id = user_id_for_person(session, person_id)
    if user_id is None:
        raise PersonHasNoUserAccountError(person_id=person_id)
    club_id = resolve_sole_club_id(session)

    return role_assignment_service.create_role_assignment(
        session,
        user_id=user_id,
        role_id=role.id,
        scope_type=_PERSON_ROLE_SCOPE_TYPE,
        club_id=club_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )


def remove_person_role(
    session: Session,
    *,
    person_id: uuid.UUID,
    role_code: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> UserRoleAssignment:
    """ADR-0039: revoke `person_id`'s own currently-effective `role_code`
    assignment — never another Person's, and never any other role this
    Person holds. Only ever calls `app.role_assignments.service.
    revoke_role_assignment` on the one matching row.

    Raises PersonHasNoUserAccountError, InvalidPersonRoleCodeError, or
    PersonRoleAssignmentNotFoundError (no currently-effective assignment
    of that role exists for this Person — including "never had one" and
    "already revoked", both idempotent-not-found from this module's own
    point of view; the router maps this to the same 404 the generic
    `/api/v1/role-assignments/{id}/revoke` endpoint uses for "nothing to
    act on").
    """
    role = _role_by_code(session, role_code)
    user_id = user_id_for_person(session, person_id)
    if user_id is None:
        raise PersonHasNoUserAccountError(person_id=person_id)
    club_id = resolve_sole_club_id(session)

    now = sa.func.now()
    assignment = session.execute(
        sa.select(UserRoleAssignment)
        .where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == role.id,
            UserRoleAssignment.club_id == club_id,
            UserRoleAssignment.scope_type == _PERSON_ROLE_SCOPE_TYPE,
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if assignment is None:
        raise PersonRoleAssignmentNotFoundError(person_id=person_id, role_code=role_code)

    return role_assignment_service.revoke_role_assignment(
        session,
        assignment=assignment,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )


__all__ = [
    "CANONICAL_PERSON_ROLE_CODES",
    "PersonRoleAssignmentError",
    "PersonHasNoUserAccountError",
    "NoClubConfiguredError",
    "MultipleClubsConfiguredError",
    "InvalidPersonRoleCodeError",
    "PersonRoleAssignmentNotFoundError",
    "user_id_for_person",
    "resolve_sole_club_id",
    "list_person_role_assignments",
    "add_person_role",
    "remove_person_role",
]
