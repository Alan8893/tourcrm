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

2. **`scope_type`** (AUTH-2, PO decision — GAP-4 option B). A
   `UserRoleAssignment` scope is a property of the *assignment*, not of
   each permission (`RolePermission` carries no scope), so a role whose
   canonical permissions need two scopes is granted as two assignments
   of the same role — `CANONICAL_ROLE_SCOPE_TYPES`:

       admin      -> all
       instructor -> own_groups + self
       member     -> self
       guardian   -> children + self

   Accepted limitation of the current model: every permission of a
   two-assignment role is effective through *both* scopes. The set is
   created/revoked atomically as one unit (one `role_assignment.created`/
   `.revoked` audit record per assignment row, ADR-0026 §6). Duplicate
   detection and revoke look at *every* active assignment of the role in
   the Club regardless of scope, so a historical `scope_type='all'`
   assignment (created before AUTH-2, never migrated) counts as "already
   has the role" and is revoked by the same remove flow. The generic
   `/api/v1/role-assignments` API is unchanged: its caller still chooses
   `scope_type` explicitly.

`club_id` is always the installation's sole Club (TourCRM is not
multi-club — see `resolve_sole_club_id` below): the admin never picks a
Club, matching every other single-Club resolution already in this
codebase (app.people.authorization.resolve_current_club_id_for_person_
create's identical two-step "establish the sole Club, never guess among
several" shape).
"""

import uuid
from typing import Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.authorization import BASELINE_ROLE_CODES, Role, UserRoleAssignment
from app.db.identity import Club, User
from app.role_assignments import service as role_assignment_service

# ADR-0039 §3: the closed MVP role set. Re-exported so callers (the API
# router) validate against the same single source of truth.
CANONICAL_PERSON_ROLE_CODES = BASELINE_ROLE_CODES

# ADR-0039 §3's canonical human-readable labels. Kept here, not only in the
# frontend's own `personRoleLabel` (apps/web/src/api/people.ts), because
# the People list's backend-authoritative combined search (TH-0114,
# people-api.md §4) must resolve a Russian role label to a role code
# itself, server-side, without a second round-trip to the client.
# Duplicated rather than shared across the two runtimes/languages, per
# this codebase's own established convention for small cross-boundary
# constants (e.g. app.api.v1.auth._apply_rate_limit).
PERSON_ROLE_LABELS: dict[str, str] = {
    "admin": "Администратор",
    "instructor": "Инструктор",
    "member": "Участник",
    "guardian": "Родитель",
}


def role_codes_matching_search_term(term: str) -> list[str]:
    """Canonical role codes whose code or ADR-0039 §3 human-readable label
    contains `term`, case-insensitively (TH-0114: the People list's single
    combined search field must match a Person's role alongside their
    name). Purely in-memory — the role catalog is four fixed values, never
    a database lookup."""
    lowered = term.casefold()
    return [
        code
        for code in CANONICAL_PERSON_ROLE_CODES
        if lowered in code.casefold() or lowered in PERSON_ROLE_LABELS[code].casefold()
    ]

# See module docstring point 2. The one source of truth for both Person
# Detail and the Person wizard (app.people.wizard).
CANONICAL_ROLE_SCOPE_TYPES: dict[str, tuple[str, ...]] = {
    "admin": ("all",),
    "instructor": ("own_groups", "self"),
    "member": ("self",),
    "guardian": ("children", "self"),
}


class _DeferredCommitSession:
    """Turns each nested `.commit()` into `.flush()` so several
    `create_role_assignment`/`revoke_role_assignment` calls (each of which
    commits on its own) share one transaction; `.rollback()` and every
    other call go to the real session. Duplicated from
    app.people.wizard's identical proxy, per this codebase's convention
    for small helpers (importing it would create an import cycle)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.flush()

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)


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
            .order_by(UserRoleAssignment.created_at.desc(), UserRoleAssignment.id)
        )
        .scalars()
        .all()
    )
    return list(rows)


def _active_role_assignments(
    session: Session, *, user_id: uuid.UUID, role_id: uuid.UUID, club_id: uuid.UUID
) -> list[UserRoleAssignment]:
    """Every currently-effective assignment of `role_id` for `user_id` in
    `club_id`, whatever its `scope_type` (see module docstring point 2),
    locked for the duplicate/revoke decision that follows."""
    now = sa.func.now()
    return list(
        session.execute(
            sa.select(UserRoleAssignment)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.role_id == role_id,
                UserRoleAssignment.club_id == club_id,
                UserRoleAssignment.valid_from <= now,
                sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
            )
            .order_by(UserRoleAssignment.created_at, UserRoleAssignment.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )


def create_canonical_role_assignments(
    session: Session,
    *,
    user_id: uuid.UUID,
    role: Role,
    club_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> list[UserRoleAssignment]:
    """Grant `role` to `user_id` as `CANONICAL_ROLE_SCOPE_TYPES[role.code]`
    — one `create_role_assignment` call (and audit record) per scope.

    Raises DuplicateRoleAssignmentError when *any* active assignment of
    the role already exists in `club_id`, whatever its scope. Commits via
    `session.commit()` after each assignment, exactly like
    `create_role_assignment` itself: callers that need the whole set to be
    atomic pass a deferred-commit proxy (see `add_person_role` and
    app.people.wizard) and commit once themselves.
    """
    existing = _active_role_assignments(
        session, user_id=user_id, role_id=role.id, club_id=club_id
    )
    if existing:
        session.rollback()
        raise role_assignment_service.DuplicateRoleAssignmentError(
            user_id=user_id,
            role_id=role.id,
            club_id=club_id,
            scope_type=existing[0].scope_type,
        )
    return [
        role_assignment_service.create_role_assignment(
            session,
            user_id=user_id,
            role_id=role.id,
            scope_type=scope_type,
            club_id=club_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        for scope_type in CANONICAL_ROLE_SCOPE_TYPES[role.code]
    ]


def list_active_role_codes_by_person(
    session: Session, person_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Active RoleAssignment role codes for each of `person_ids`, batched
    into one query (TH-0114: the People list shows every Person's active
    system roles without an N+1 query per row). Uses the exact same
    `UserRoleAssignment`/`Role` tables and the same "currently effective"
    interval check as `list_person_role_assignments` — this is a
    read/batch-shaped sibling of that function, not a new authorization
    concept. A Person absent from `person_ids`' role data, or with no
    User at all, maps to an empty list. Each list is ordered by ADR-0039
    §3's canonical role order (admin, instructor, member, guardian), never
    by database insertion order."""
    result: dict[uuid.UUID, list[str]] = {person_id: [] for person_id in person_ids}
    if not person_ids:
        return result
    now = sa.func.now()
    rows = session.execute(
        sa.select(User.person_id, Role.code)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            User.person_id.in_(person_ids),
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
    ).all()
    order = {code: index for index, code in enumerate(CANONICAL_PERSON_ROLE_CODES)}
    codes_by_person: dict[uuid.UUID, set[str]] = {}
    for person_id, code in rows:
        codes_by_person.setdefault(person_id, set()).add(code)
    for person_id, codes in codes_by_person.items():
        result[person_id] = sorted(codes, key=lambda code: order.get(code, len(order)))
    return result


def add_person_role(
    session: Session,
    *,
    person_id: uuid.UUID,
    role_code: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> UserRoleAssignment:
    """ADR-0039: grant `role_code` to `person_id`'s own User as its
    canonical scope set (module docstring point 2), atomically. Does not
    create a User, does not mutate Person/ClubMembership, and creates no
    Group/Event/GuardianRelationship side effect — every row is created by
    `app.role_assignments.service.create_role_assignment`, the exact same
    function the generic `/api/v1/role-assignments` API uses.

    Returns the first assignment of the set (the router's response
    represents the role, not each scope).

    Raises PersonHasNoUserAccountError, InvalidPersonRoleCodeError, or
    (bubbled from the underlying service, unchanged) RoleAssignmentClub
    MembershipMissingError / DuplicateRoleAssignmentError — persisting
    nothing in every case.
    """
    role = _role_by_code(session, role_code)
    user_id = user_id_for_person(session, person_id)
    if user_id is None:
        raise PersonHasNoUserAccountError(person_id=person_id)
    club_id = resolve_sole_club_id(session)

    assignments = create_canonical_role_assignments(
        _DeferredCommitSession(session),  # type: ignore[arg-type]
        user_id=user_id,
        role=role,
        club_id=club_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    session.commit()
    return assignments[0]


def remove_person_role(
    session: Session,
    *,
    person_id: uuid.UUID,
    role_code: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> UserRoleAssignment:
    """ADR-0039: revoke `person_id`'s own currently-effective `role_code`
    — never another Person's, and never any other role this Person holds.
    Revokes *every* active assignment of that role in the Club, whatever
    its scope (the canonical two-scope set and any historical `all`
    assignment alike — module docstring point 2), atomically; each row is
    revoked by `app.role_assignments.service.revoke_role_assignment`.
    Returns the first revoked assignment.

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

    assignments = _active_role_assignments(
        session, user_id=user_id, role_id=role.id, club_id=club_id
    )
    if not assignments:
        raise PersonRoleAssignmentNotFoundError(person_id=person_id, role_code=role_code)

    deferred = _DeferredCommitSession(session)
    revoked = [
        role_assignment_service.revoke_role_assignment(
            deferred,  # type: ignore[arg-type]
            assignment=assignment,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        for assignment in assignments
    ]
    session.commit()
    return revoked[0]


__all__ = [
    "CANONICAL_PERSON_ROLE_CODES",
    "PERSON_ROLE_LABELS",
    "role_codes_matching_search_term",
    "PersonRoleAssignmentError",
    "PersonHasNoUserAccountError",
    "NoClubConfiguredError",
    "MultipleClubsConfiguredError",
    "InvalidPersonRoleCodeError",
    "PersonRoleAssignmentNotFoundError",
    "user_id_for_person",
    "resolve_sole_club_id",
    "CANONICAL_ROLE_SCOPE_TYPES",
    "create_canonical_role_assignments",
    "list_person_role_assignments",
    "list_active_role_codes_by_person",
    "add_person_role",
    "remove_person_role",
]
