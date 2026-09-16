"""Initial administrator bootstrap (Issue #99 / TH-0089).

Canonical sources: docs/03-architecture/adr/ADR-0027-initial-
administrator-bootstrap.md (primary decision), ADR-0009 (authentication
mechanism), ADR-0013 (scope vocabulary), ADR-0026 (RoleAssignment API
decisions), docs/02-requirements/roles-and-permissions.md.

Pure Python + SQLAlchemy — no FastAPI, no `input()`/`getpass` here (the
interactive operator prompt lives in app.cli.bootstrap_admin, which is the
only caller). This split mirrors app.authentication.service/
app.role_assignments.service: the transaction/business logic is testable
without a terminal attached, and the CLI module owns nothing but I/O.

## Identity (ADR-0027 §"Decision")

Bootstrap creates a normal `Person` + `User` (never a separate Admin
entity) and assigns the existing canonical `admin` Role via the existing
`UserRoleAssignment` model with the canonical `all` scope, `club_id=NULL`,
`scope_ref_id=NULL` — "a global installation administrator" (Issue #99
§4). No new role, permission, scope, or authentication mechanism is
introduced.

## Role/permission consistency — PO decision (Variant A)

Issue #99 §4 / ADR-0027 "Administrator scope" require bootstrap to fail
clearly, rather than silently invent policy, when "the role-permission
state required for normal administrator operation is missing/
inconsistent". The `admin` Role itself is seeded by migration
`e5ae1ad9e1e1` (`code='admin'`, `is_system=True`), but that same migration
deliberately seeds *zero* `RolePermission` grants for it — mapping the
roles-and-permissions.md matrix into concrete grants was explicitly
deferred to a future issue, and Issue #99's own non-goals list forbids
"RolePermission administration; changing role-permission policy" here.

Enforcing a stricter check (requiring >=1 RolePermission grant to exist)
would therefore make bootstrap permanently unable to succeed on any
installation today — a genuine contradiction between Issue #99's own Goal
and its §4 failure rule, reported and resolved by explicit PO decision:
`_load_consistent_admin_role` below verifies only that the canonical
`admin` Role exists as the expected system role. It deliberately does
NOT check for any RolePermission grant, and this module never creates
one. The resulting administrator's RolePermission state is therefore
left completely unchanged — if it was empty before bootstrap, it remains
empty after (see the PR's "GAPs" section: this is a pre-existing,
system-wide authorization gap, not something bootstrap introduces).

## Concurrency (ADR-0027 §8/§9, Issue #99 §1.7/§1.8)

`_BOOTSTRAP_ADVISORY_LOCK_KEY` is acquired via `pg_advisory_xact_lock`
before the "does a global admin already exist" check, and held for the
remainder of the same DB transaction (released automatically on commit
or rollback — never explicitly unlocked, so there is no lock-leak path).
This turns the naive, race-prone "SELECT then INSERT" shape into a
serialized check-and-create: a second concurrent caller blocks on the
same lock until the first transaction ends, then re-runs its own check
against the now-committed (or rolled-back) result — never both checking
against the same "no admin yet" snapshot.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authentication.passwords import hash_password, validate_password_policy
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Person, User, normalize_login_identifier
from app.role_assignments.lifecycle import validate_role_assignment_scope

ADMIN_ROLE_CODE = "admin"
GLOBAL_ADMIN_SCOPE_TYPE = "all"

# ADR-0027 §2: the bootstrap Person's identity is a fixed placeholder,
# not a mandatory-profile-completion form — the operator can rename the
# Person later through the ordinary account-management mechanism.
BOOTSTRAP_ADMIN_FIRST_NAME = "Admin"
BOOTSTRAP_ADMIN_LAST_NAME = "Admin"

# A fixed, deterministic key for PostgreSQL's session-level advisory lock
# family (`pg_advisory_xact_lock(bigint)`), scoped to this one operation
# only — not a shared/general-purpose lock namespace. Arbitrary but
# constant, well within the signed-bigint range.
_BOOTSTRAP_ADVISORY_LOCK_KEY = 891_234_756_190_223

ACTIVE_USER_STATUS = "active"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BootstrapError(Exception):
    """Base class for this module's typed, expected failures. None of
    these carry the supplied password or any hash (ADR-0027 "Failure
    behavior": "Failure must not disclose passwords, hashes, tokens, or
    internal security data")."""


class AdministratorAlreadyExistsError(BootstrapError):
    """ADR-0027 §1/§7: an active global administrator already exists.
    Bootstrap is not a general-purpose admin-creation mechanism and
    refuses outright — no partial check, no mutation."""


class AdminRoleInconsistentError(BootstrapError):
    """Issue #99 §4 / ADR-0027 "Administrator scope": the canonical
    `admin` Role (seeded by migration `e5ae1ad9e1e1`) is missing or is
    not the expected system role. Bootstrap fails clearly rather than
    creating it — see module docstring "Role/permission consistency"."""


class EmailAlreadyRegisteredError(BootstrapError):
    """A User with this normalized identifier already exists. Bootstrap
    always creates a new Person+User; it never adopts an existing
    account, even one that is not itself a global administrator."""


@dataclass(frozen=True)
class BootstrapResult:
    person_id: uuid.UUID
    user_id: uuid.UUID
    role_assignment_id: uuid.UUID
    role_id: uuid.UUID


def global_administrator_exists(session: Session) -> bool:
    """ADR-0027 §1: "no existing active administrator account according
    to the canonical `admin` role and effective role-assignment model" —
    scoped to the global installation administrator this Issue defines
    (`role=admin`, `scope_type=all`, `club_id IS NULL`). Mirrors
    app.authorization.service.applicable_assignments's own
    `valid_from <= now() AND (valid_to IS NULL OR now() < valid_to)`
    effective-interval convention, but with `statement_timestamp()`
    rather than `now()`/`CURRENT_TIMESTAMP`: the latter is frozen at
    *transaction* start in PostgreSQL, not statement execution time, so
    inside `bootstrap_initial_administrator`'s own transaction — which
    can sit blocked on `pg_advisory_xact_lock` for an arbitrary duration
    before this check ever runs — `now()` would keep returning the
    timestamp from *before* a concurrent bootstrap's row was even
    inserted, making this check blind to a row that has since been
    committed and is otherwise perfectly visible. `applicable_assignments`
    never holds a transaction open across a blocking wait like this, so
    it does not need the distinction; this function does.

    Safe to call standalone (e.g. for an early, lock-free "should I even
    ask for a password" UX check) — the authoritative, race-safe check
    is `bootstrap_initial_administrator`'s own advisory-lock-guarded
    re-check immediately before creating anything.
    """
    now = sa.func.statement_timestamp()
    stmt = (
        sa.select(UserRoleAssignment.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            Role.code == ADMIN_ROLE_CODE,
            UserRoleAssignment.scope_type == GLOBAL_ADMIN_SCOPE_TYPE,
            UserRoleAssignment.club_id.is_(None),
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def _acquire_bootstrap_lock(session: Session) -> None:
    session.execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _BOOTSTRAP_ADVISORY_LOCK_KEY}
    )


def _load_consistent_admin_role(session: Session) -> Role:
    role = session.execute(sa.select(Role).where(Role.code == ADMIN_ROLE_CODE)).scalar_one_or_none()
    if role is None or not role.is_system:
        raise AdminRoleInconsistentError(
            f"Canonical role {ADMIN_ROLE_CODE!r} is missing or is not a system role; "
            "bootstrap will not invent it"
        )
    return role


def bootstrap_initial_administrator(
    session: Session, *, email: str, password: str, request_id: Optional[str] = None
) -> BootstrapResult:
    """Create the first global administrator, transactionally (Issue #99
    §1.7 / ADR-0027 §9): `Person` + `User` (`status=active`) +
    `UserRoleAssignment` (`admin`, `scope_type=all`, `club_id=NULL`) plus
    their `person.created`/`user.created`/`role_assignment.created` audit
    records (`actor_type="system"` — ADR-0024's own actor vocabulary for
    an operation with no authenticated principal; see
    app.audit.vocabulary) are either all persisted or none are.

    Order of checks (each fail-closed, no mutation before this point):

    1. acquire the bootstrap advisory lock (blocks until any concurrent
       bootstrap's transaction ends);
    2. refuse if a global administrator already effectively exists
       (`AdministratorAlreadyExistsError`);
    3. refuse if the canonical `admin` Role is missing/inconsistent
       (`AdminRoleInconsistentError` — never invents the Role or any
       RolePermission grant, see module docstring);
    4. refuse if `password` fails the existing password policy
       (`app.authentication.passwords.WeakPasswordError`);
    5. refuse if `email`'s normalized identifier is already registered
       (`EmailAlreadyRegisteredError`).

    Only after all five hold does this create anything. Raises whatever
    the underlying commit raises (e.g. a genuine `IntegrityError` from an
    unrelated constraint) with everything rolled back — never a usable
    half-created account (ADR-0027 §9).
    """
    _acquire_bootstrap_lock(session)

    # Every precondition below runs inside the same lock-holding
    # transaction; any failure — a typed refusal or an unexpected
    # exception alike — must roll back (releasing the lock promptly)
    # before propagating, so nothing is ever left half-checked or
    # half-created (ADR-0027 §9).
    try:
        if global_administrator_exists(session):
            raise AdministratorAlreadyExistsError(
                "A global administrator already exists; bootstrap refuses to create another"
            )

        admin_role = _load_consistent_admin_role(session)

        # Fail-closed ordering: never hash/store a password that fails
        # policy, and never create anything before every precondition holds.
        validate_password_policy(password)

        normalized = normalize_login_identifier(email)
        existing_user = session.execute(
            sa.select(User.id).where(User.normalized_login_identifier == normalized)
        ).scalar_one_or_none()
        if existing_user is not None:
            raise EmailAlreadyRegisteredError(f"A User already exists for {normalized!r}")

        # ADR-0026 §2 / Issue #99 §4: reuse the same canonical-scope
        # validator app.role_assignments.service.create_role_assignment
        # itself calls, rather than re-deriving "is (all, NULL, NULL) a
        # valid combination" by hand — a second source of truth for the
        # same rule would be the actual invention this module must avoid.
        validate_role_assignment_scope(
            scope_type=GLOBAL_ADMIN_SCOPE_TYPE, club_id=None, scope_ref_id=None
        )
    except Exception:
        session.rollback()
        raise

    person = Person(
        id=uuid.uuid4(),
        first_name=BOOTSTRAP_ADMIN_FIRST_NAME,
        last_name=BOOTSTRAP_ADMIN_LAST_NAME,
    )
    user = User(
        id=uuid.uuid4(),
        person=person,
        login_identifier=email,
        password_hash=hash_password(password),
        status=ACTIVE_USER_STATUS,
    )
    session.add_all([person, user])
    try:
        session.flush()

        assignment = UserRoleAssignment(
            user_id=user.id,
            role_id=admin_role.id,
            club_id=None,
            scope_type=GLOBAL_ADMIN_SCOPE_TYPE,
            scope_ref_id=None,
            valid_from=_utcnow(),
        )
        session.add(assignment)
        session.flush()

        for action, resource_type, resource_id in (
            ("person.created", "person", person.id),
            ("user.created", "user", user.id),
            ("role_assignment.created", "role_assignment", assignment.id),
        ):
            record_audit_event(
                session,
                action=action,
                actor_type="system",
                outcome="success",
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return BootstrapResult(
        person_id=person.id,
        user_id=user.id,
        role_assignment_id=assignment.id,
        role_id=admin_role.id,
    )


__all__ = [
    "ADMIN_ROLE_CODE",
    "GLOBAL_ADMIN_SCOPE_TYPE",
    "BOOTSTRAP_ADMIN_FIRST_NAME",
    "BOOTSTRAP_ADMIN_LAST_NAME",
    "BootstrapError",
    "AdministratorAlreadyExistsError",
    "AdminRoleInconsistentError",
    "EmailAlreadyRegisteredError",
    "BootstrapResult",
    "global_administrator_exists",
    "bootstrap_initial_administrator",
]
