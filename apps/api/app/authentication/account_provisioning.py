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

   TH-0116 / GitHub Issue #150: `Person.email` is now optional (the
   Person-creation wizard's Step 1 no longer requires it). When absent,
   this same function instead creates a `pending` "stub" `User` — no
   `login_identifier`, no `password_hash`, no challenge issued (there is
   no identifier to send one to; no placeholder/fake login is invented).
   When called again later for that same Person once an email has been
   added, it *activates* the stub in place — sets `login_identifier`,
   flips `status` to `active`, and issues the first-access challenge —
   rather than rejecting with `PersonAlreadyHasAccountError` (which still
   applies to a Person whose `User` is already active). This remains one
   function/one workflow: the wizard, the original "Создать доступ"
   button, and this later activation are three different starting states
   of the exact same operation, never a second create-account mechanism.

2. Issues a fresh administrative reset challenge for a Person who
   already has a `User` (`admin_reset_password_for_person`) — again by
   calling `request_password_reset` directly, never re-implementing
   token generation, expiry, single-use or revoke-superseded semantics.
   Raises `PersonEmailMissingError` (not `PersonHasNoAccountError`) for a
   pending stub account — a `User` row exists, but there is still no
   identifier a reset challenge could be issued to.

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
# TH-0116: a stub account created for a Person with no email yet. Never
# confused with self-registration's own (differently-shaped) `pending`
# status — see app.db.identity.User's own docstring.
PENDING_STUB_STATUS = "pending"


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


def _is_pending_stub(user: User) -> bool:
    """TH-0116: a `User` created with no email yet — `status = 'pending'`
    and no `login_identifier` — as opposed to self-registration's own,
    differently-shaped `pending` (which always has both an identifier and
    a password hash; see app.db.identity.User's own docstring)."""
    return user.status == PENDING_STUB_STATUS and user.login_identifier is None


def _create_pending_stub_user(
    session: Session,
    *,
    person_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: Optional[str],
) -> User:
    """TH-0116: `Person.email` is absent — create the `User` anyway, as a
    stub with no `login_identifier`/`password_hash`, and issue no
    challenge (there is no identifier to send one to; no placeholder/fake
    login is invented). `user.created` is recorded exactly as it is for
    the with-email path below — this is the same operation, one of its
    two possible outcomes."""
    user = User(
        id=uuid.uuid4(),
        person_id=person_id,
        login_identifier=None,
        password_hash=None,
        status=PENDING_STUB_STATUS,
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
    except Exception:
        session.rollback()
        raise
    return user


def _create_active_user_with_email(
    session: Session,
    *,
    person_id: uuid.UUID,
    email: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str],
    issue_first_access: bool,
) -> tuple[User, Optional[str]]:
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

    if not issue_first_access:
        # TH-0118.3 (participant import, auth-and-authorization.md §5.3):
        # the account is created, but no first-access challenge is issued —
        # first access is issued later via `admin_reset_password_for_person`.
        return user, None

    # Reuses the exact same self-service challenge-issuance function — no
    # second token/challenge implementation. `email` is `user.
    # login_identifier` verbatim, so this always resolves back to the
    # User just created.
    raw_credential = request_password_reset(
        session, email, actor_type="user", actor_user_id=actor_user_id, request_id=request_id
    )
    assert raw_credential is not None
    return user, raw_credential


def _activate_pending_stub_user(
    session: Session,
    *,
    user: User,
    email: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str],
) -> tuple[User, str]:
    """TH-0116: `person_id` already has a pending-stub `User` (see
    `_is_pending_stub`) and now has an email — set `login_identifier`,
    flip `status` to active, and issue the first-access challenge, all in
    the same transaction. `login_identifier` is treated the same way
    Person's other contact fields are in audit (ADR-0035 §4-style
    `{"changed": True}`, never the raw value)."""
    old_status = user.status
    user.login_identifier = email
    user.status = ACCOUNT_INITIAL_STATUS
    try:
        session.flush()
        record_audit_event(
            session,
            action="user.status_changed",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="user",
            resource_id=user.id,
            outcome="success",
            request_id=request_id,
            details={
                "changes": {
                    "status": {"from": old_status, "to": ACCOUNT_INITIAL_STATUS},
                    "login_identifier": {"changed": True},
                }
            },
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_duplicate_login_identifier_violation(exc):
            raise DuplicateLoginIdentifierError(email=email) from exc
        raise

    raw_credential = request_password_reset(
        session, email, actor_type="user", actor_user_id=actor_user_id, request_id=request_id
    )
    assert raw_credential is not None
    return user, raw_credential


def create_user_for_person(
    session: Session,
    *,
    person_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
    issue_first_access: bool = True,
) -> tuple[User, Optional[str]]:
    """ADR-0038 §2, extended by TH-0116: create a User for `person_id` (or,
    for a Person with no email, a pending stub — see module docstring)
    and immediately issue a one-time first-access setup challenge when an
    identifier exists to send it to. The caller (the API router) must
    have already confirmed `person_id` exists and resolved
    `account.manage`.

    Three outcomes:
    - No existing User, `Person.email` present -> new active User +
      challenge. Returns (user, raw_temporary_credential).
    - No existing User, `Person.email` absent -> new pending-stub User,
      no challenge. Returns (user, None).
    - An existing pending-stub User (see `_is_pending_stub`) and
      `Person.email` now present -> activates it in place + issues a
      challenge. Returns (user, raw_temporary_credential).

    Raises PersonEmailMissingError (only for the third shape, still with
    no email), PersonAlreadyHasAccountError (an existing User that is
    NOT a pending stub — i.e. already active/locked/suspended/etc), or
    DuplicateLoginIdentifierError, persisting nothing in any of those
    cases. The raw credential, when returned, exists only in that return
    value and must never be persisted, logged, or included in an audit
    `details` payload by any caller.

    `issue_first_access=False` (TH-0118.3, participant import —
    auth-and-authorization.md §5.3) creates a new active User without
    issuing the first-access challenge at all: no PasswordResetChallenge
    row, no `password_reset_challenge.created` audit, `(user, None)` is
    returned. First access is then issued later through
    `admin_reset_password_for_person`. It only applies to creating a new
    User with an email; the other outcomes are unchanged.
    """
    person = session.get(Person, person_id)
    assert person is not None  # the router already checked existence
    email = (person.email or "").strip()

    existing_user = session.execute(
        sa.select(User).where(User.person_id == person_id)
    ).scalar_one_or_none()
    if existing_user is not None:
        if not _is_pending_stub(existing_user):
            raise PersonAlreadyHasAccountError(person_id=person_id)
        if not email:
            raise PersonEmailMissingError(person_id=person_id)
        return _activate_pending_stub_user(
            session,
            user=existing_user,
            email=email,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    if not email:
        user = _create_pending_stub_user(
            session, person_id=person_id, actor_user_id=actor_user_id, request_id=request_id
        )
        return user, None

    return _create_active_user_with_email(
        session,
        person_id=person_id,
        email=email,
        actor_user_id=actor_user_id,
        request_id=request_id,
        issue_first_access=issue_first_access,
    )


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
    creates a User — raises PersonHasNoAccountError instead. Raises
    PersonEmailMissingError (TH-0116) for a pending-stub User — a User
    row exists, but there is still no identifier a reset challenge could
    be issued to; use `create_user_for_person` once the Person has an
    email, which activates the stub instead.
    """
    user = session.execute(
        sa.select(User).where(User.person_id == person_id)
    ).scalar_one_or_none()
    if user is None:
        raise PersonHasNoAccountError(person_id=person_id)
    if _is_pending_stub(user):
        raise PersonEmailMissingError(person_id=person_id)

    assert user.login_identifier is not None  # guaranteed by _is_pending_stub above
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
    "PENDING_STUB_STATUS",
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
