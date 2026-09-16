"""Initial installation bootstrap (TH-0091 / TH-0089).

Creates the first primary Club together with the initial administrator in
one transaction. The operation remains outside the public HTTP API.

Canonical sources: ADR-0027, ADR-0009, ADR-0013, ADR-0026.
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
from app.db.identity import Club, Person, User, normalize_login_identifier
from app.role_assignments.lifecycle import validate_role_assignment_scope

ADMIN_ROLE_CODE = "admin"
GLOBAL_ADMIN_SCOPE_TYPE = "all"

BOOTSTRAP_ADMIN_FIRST_NAME = "Admin"
BOOTSTRAP_ADMIN_LAST_NAME = "Admin"

_BOOTSTRAP_ADVISORY_LOCK_KEY = 891_234_756_190_223

ACTIVE_USER_STATUS = "active"
ACTIVE_CLUB_STATUS = "active"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BootstrapError(Exception):
    """Base class for expected bootstrap failures."""


class AdministratorAlreadyExistsError(BootstrapError):
    """An active bootstrap administrator already exists."""


class AdminRoleInconsistentError(BootstrapError):
    """The canonical system admin role is missing or inconsistent."""


class EmailAlreadyRegisteredError(BootstrapError):
    """The supplied administrator identifier is already registered."""


class ClubAlreadyExistsError(BootstrapError):
    """Bootstrap is intended for an empty installation and found a Club."""


@dataclass(frozen=True)
class BootstrapResult:
    club_id: uuid.UUID
    person_id: uuid.UUID
    user_id: uuid.UUID
    role_assignment_id: uuid.UUID
    role_id: uuid.UUID


def global_administrator_exists(session: Session) -> bool:
    """Return whether an effective canonical bootstrap administrator exists."""
    now = sa.func.statement_timestamp()
    stmt = (
        sa.select(UserRoleAssignment.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            Role.code == ADMIN_ROLE_CODE,
            UserRoleAssignment.scope_type == GLOBAL_ADMIN_SCOPE_TYPE,
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


def _assert_empty_installation(session: Session) -> None:
    existing_club = session.execute(sa.select(Club.id).limit(1)).scalar_one_or_none()
    if existing_club is not None:
        raise ClubAlreadyExistsError(
            "A Club already exists. Initial bootstrap is only for a fresh installation."
        )


def bootstrap_initial_administrator(
    session: Session,
    *,
    club_name: str,
    email: str,
    password: str,
    request_id: Optional[str] = None,
) -> BootstrapResult:
    """Create the primary Club and first administrator atomically.

    The advisory lock serializes concurrent first-run attempts. All
    preconditions are checked before mutation. The Club, Person, User,
    RoleAssignment and canonical audit events commit together or roll back
    together.
    """
    _acquire_bootstrap_lock(session)

    try:
        if global_administrator_exists(session):
            raise AdministratorAlreadyExistsError(
                "An administrator already exists; bootstrap refuses to create another"
            )

        _assert_empty_installation(session)
        admin_role = _load_consistent_admin_role(session)

        if not club_name.strip():
            raise BootstrapError("Club name must not be empty")

        validate_password_policy(password)

        normalized = normalize_login_identifier(email)
        existing_user = session.execute(
            sa.select(User.id).where(User.normalized_login_identifier == normalized)
        ).scalar_one_or_none()
        if existing_user is not None:
            raise EmailAlreadyRegisteredError(f"A User already exists for {normalized!r}")

        validate_role_assignment_scope(
            scope_type=GLOBAL_ADMIN_SCOPE_TYPE, club_id=None, scope_ref_id=None
        )
    except Exception:
        session.rollback()
        raise

    club = Club(
        id=uuid.uuid4(),
        name=club_name.strip(),
        status=ACTIVE_CLUB_STATUS,
    )
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
    session.add_all([club, person, user])

    try:
        session.flush()

        assignment = UserRoleAssignment(
            user_id=user.id,
            role_id=admin_role.id,
            club_id=club.id,
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
        club_id=club.id,
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
    "ClubAlreadyExistsError",
    "BootstrapResult",
    "global_administrator_exists",
    "bootstrap_initial_administrator",
]
