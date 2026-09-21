"""Administrative User account provisioning for an existing Person
(TH-0113, implementing ADR-0038 — Account Provisioning and Password
Lifecycle).

Canonical sources: docs/03-architecture/adr/ADR-0038-account-
provisioning-password-lifecycle.md, docs/05-api/auth-api.md §15.1,
docs/03-architecture/authentication-persistence.md §5.

This module introduces no new persistence entity, no new token/challenge
model, and no new password mechanism. It is a thin administrative entry
point that:

1. Creates the `User` row for a Person who does not have one yet
   (`create_user_for_person`) — `login_identifier = Person.email`
   (ADR-0038 does not introduce a separate account-creation identifier;
   see module docstring point below on why `email` is never copied onto
   `User`), `password_hash = NULL`, and immediately issues a first-access
   challenge by calling the *existing*
   `app.authentication.service.request_password_reset` — the exact same
   function the self-service "forgot password" flow already uses. No
   second challenge/token implementation exists here.

2. Issues a fresh administrative reset challenge for a Person who
   already has a `User` (`admin_reset_password_for_person`) — again by
   calling `request_password_reset` directly, never re-implementing
   token generation, expiry, single-use or revoke-superseded semantics.

`ACCOUNT_INITIAL_STATUS = "active"`: an admin-created account has, by
the very act of an administrator creating it, already received the
approval that the `pending` state's self-registration workflow
(auth-and-authorization.md §5.1's "pending user -> ... -> admin approval
-> active user") exists to gate on — there is no separate approval step
left to wait for. This also requires no change to `login()`'s existing
behavior: a `password_hash IS NULL` account already cannot authenticate
(the dummy-hash comparison in
app.authentication.passwords.verify_password_or_dummy fails
deterministically), so `active` does not allow sign-in before the user
completes first-access setup — it only ever matters once
`confirm_password_reset` has set a real password, at which point the
account should already be usable, matching ADR-0038 §4 ("After first
setup, normal authentication uses the existing login contract").

`Person.email` is never copied onto `User` as a new field (ADR-0038
context; the task's own explicit instruction): `User.login_identifier`
and `Person.email` remain two independent fields after account creation.
If `Person.email` is edited later, `User.login_identifier` is NOT
automatically resynchronized — that would be a new canonical rule this
module does not introduce.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authentication.service import request_password_reset
from app.db.identity import Club, Person, User

ACCOUNT_INITIAL_STATUS = "active"


class AccountProvisioningError(Exception):
    """Base class for this module's typed, expected failures."""


class PersonEmailMissingError(AccountProvisioningError):
    """ADR-0038 / TH-0113 rule A: `Person.email` is absent, so no User can
    be created — no fictitious/generated login identifier is invented."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} has no email; cannot create a User account")
        self.person_id = person_id


class PersonAlreadyHasAccountError(AccountProvisioningError):
    """`User.person_id` is unique — a Person may have at most one User."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} already has a User account")
        self.person_id = person_id


class DuplicateLoginIdentifierError(AccountProvisioningError):
    """`Person.email` normalizes to an identifier already used by a
    different User — the database's own unique constraint
    (`uq_users_normalized_login_identifier`) is the authoritative check;
    this is its typed surface."""

    def __init__(self, *, email: str) -> None:
        super().__init__(f"A User with identifier {email!r} already exists")
        self.email = email


class PersonHasNoAccountError(AccountProvisioningError):
    """The administrative reset operation was called for a Person with no
    User — it must not create one implicitly (create-account and reset
    are separate operations, per the task's own explicit instruction)."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} has no User account to reset")
        self.person_id = person_id


class NoClubConfiguredError(AccountProvisioningError):
    """No Club exists yet. Should be unreachable — bootstrap creates the
    installation's one Club atomically with its first administrator."""


class MultipleClubsConfiguredError(AccountProvisioningError):
    """More than one Club exists — a violation of TourCRM's current
    single-Club product invariant (mirrors app.role_assignments.
    person_roles.MultipleClubsConfiguredError's identical reasoning)."""


def resolve_sole_club_id(session: Session) -> uuid.UUID:
    """The installation's one Club — used only to establish the `account.
    manage` authorization boundary (mirrors app.role_assignments.
    person_roles.resolve_sole_club_id exactly; duplicated rather than
    imported across this domain boundary, per this codebase's own
    convention)."""
    club_ids = session.execute(sa.select(Club.id)).scalars().all()
    if len(club_ids) == 0:
        raise NoClubConfiguredError("No Club exists yet; bootstrap must run first")
    if len(club_ids) > 1:
        raise MultipleClubsConfiguredError(
            "More than one Club exists; TourCRM's current product requires exactly one"
        )
    return club_ids[0]


def _is_duplicate_login_identifier_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_users_normalized_login_identifier"


def user_id_for_person(session: Session, person_id: uuid.UUID) -> Optional[uuid.UUID]:
    return session.execute(
        sa.select(User.id).where(User.person_id == person_id)
    ).scalar_one_or_none()


def create_user_for_person(
    session: Session,
    *,
    person_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[User, str]:
    """ADR-0038 §2: create a User for `person_id` and immediately issue a
    one-time first-access setup challenge. The caller (the API router)
    must have already confirmed `person_id` exists and resolved
    `account.manage`.

    Raises PersonEmailMissingError, PersonAlreadyHasAccountError, or
    DuplicateLoginIdentifierError, persisting nothing in any of those
    cases. Returns (user, raw_temporary_credential) — the raw credential
    exists only in this return value and must never be persisted,
    logged, or included in an audit `details` payload by any caller.
    """
    person = session.get(Person, person_id)
    assert person is not None  # the router already checked existence

    email = (person.email or "").strip()
    if not email:
        raise PersonEmailMissingError(person_id=person_id)

    if user_id_for_person(session, person_id) is not None:
        raise PersonAlreadyHasAccountError(person_id=person_id)

    user = User(
        id=uuid.uuid4(),
        person_id=person_id,
        login_identifier=email,
        password_hash=None,
        status=ACCOUNT_INITIAL_STATUS,
    )
    session.add(user)
    try:
        session.flush()
        record_audit_event(
            session,
            action="user.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="user",
            resource_id=user.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_duplicate_login_identifier_violation(exc):
            raise DuplicateLoginIdentifierError(email=email) from exc
        raise

    # Reuses the exact same self-service challenge-issuance function — no
    # second token/challenge implementation. `email` is `user.
    # login_identifier` verbatim, so this always resolves back to the
    # User just created.
    raw_credential = request_password_reset(
        session, email, actor_type="user", actor_user_id=actor_user_id, request_id=request_id
    )
    assert raw_credential is not None
    return user, raw_credential


def admin_reset_password_for_person(
    session: Session,
    *,
    person_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[User, str]:
    """ADR-0038 §7: issue a fresh one-time reset challenge for the User
    already linked to `person_id`, superseding any outstanding challenge
    (the existing `request_password_reset` behavior, unchanged). Never
    creates a User — raises PersonHasNoAccountError instead.
    """
    user = session.execute(
        sa.select(User).where(User.person_id == person_id)
    ).scalar_one_or_none()
    if user is None:
        raise PersonHasNoAccountError(person_id=person_id)

    raw_credential = request_password_reset(
        session,
        user.login_identifier,
        actor_type="user",
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    assert raw_credential is not None
    return user, raw_credential


__all__ = [
    "ACCOUNT_INITIAL_STATUS",
    "AccountProvisioningError",
    "PersonEmailMissingError",
    "PersonAlreadyHasAccountError",
    "DuplicateLoginIdentifierError",
    "PersonHasNoAccountError",
    "NoClubConfiguredError",
    "MultipleClubsConfiguredError",
    "resolve_sole_club_id",
    "user_id_for_person",
    "create_user_for_person",
    "admin_reset_password_for_person",
]
