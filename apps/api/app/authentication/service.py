"""Application-managed authentication business logic (Issue #33).

Canonical sources: docs/03-architecture/adr/ADR-0009-authentication-mechanism.md,
docs/03-architecture/authentication-persistence.md, docs/05-api/auth-api.md,
docs/02-requirements/functional-requirements.md (FR-AUTH-*, FR-REG-*).

Pure Python + SQLAlchemy — no FastAPI import, so this is testable without
HTTP (mirroring app.authorization.service's separation). Each public
function owns and commits its own transaction (there is no ambient
request-scoped unit of work in this codebase yet), matching the shape of
app.db.session.session_scope()'s single-session-per-operation usage
elsewhere.

This module never touches Role/Permission/RolePermission/
UserRoleAssignment: authentication (who the caller is) is deliberately
independent of authorization (what they may do), per ADR-0005/ADR-0009.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authentication.password_reset_delivery import (
    PASSWORD_RESET_EVENT_TYPE,
    PASSWORD_RESET_SUBJECT_TYPE,
    PASSWORD_RESET_TEMPLATE_CODE,
    discard_expired_delivery_secrets,
    encrypt_challenge_secret,
    load_delivery_key_ring,
)
from app.authentication.passwords import (
    hash_password,
    validate_password_policy,
    verify_password,
    verify_password_or_dummy,
)
from app.authentication.tokens import generate_token, hash_token
from app.db.authentication import (
    AuthenticatedSession,
    EmailVerificationChallenge,
    PasswordResetChallenge,
)
from app.db.identity import Person, User, normalize_login_identifier
from app.notification_settings.crypto import KeyRingUnavailable
from app.notifications.email_adapter import parse_email_address
from app.notifications.ports import AdminPolicy
from app.notifications.security_email import queue_security_email, security_email_available

# No specific duration is mandated by any canonical document (auth-and-
# authorization.md §7 only requires credentials to have "a bounded
# lifetime"); these are implementation-level defaults, centralized so a
# future issue can make them configurable without touching call sites.
SESSION_TTL = timedelta(days=14)
EMAIL_VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)

ACTIVE_USER_STATUS = "active"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthenticationError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidCredentialsError(AuthenticationError):
    """Identifier not found OR password mismatch — deliberately a single,
    indistinguishable outcome for both cases (enumeration resistance)."""


class AccountNotActiveError(AuthenticationError):
    """Credentials were correct, but the account is not `active`. Carries
    the real status so the API layer can return a specific (but still
    secret-free) response — safe to disclose only because the caller has
    already proven they know the password."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class EmailAlreadyRegisteredError(AuthenticationError):
    """auth-api.md §4 rule 3: an existing User with this normalized
    identifier already exists."""


class InvalidOrExpiredTokenError(AuthenticationError):
    """A single, deterministic failure for any invalid one-time token —
    unknown, already consumed, revoked, or expired — never distinguishing
    which (authentication-persistence.md §4/§5: "repeated use has no
    credential-disclosing side effect")."""


class IncorrectCurrentPasswordError(AuthenticationError):
    pass


class PasswordConfirmationMismatchError(AuthenticationError):
    """auth-api.md §14: the new password and its confirmation differ."""


# Outcome of request_password_reset_email — server-side diagnostics only;
# the API answers every outcome with the same generic response.
PasswordResetEmailOutcome = Literal[
    "queued",
    "unknown_identifier",
    "destination_invalid",
    "channel_disabled",
    "template_unavailable",
    "encryption_unavailable",
]


@dataclass(frozen=True)
class ResolvedSession:
    user_id: uuid.UUID
    session_id: uuid.UUID


# --- Registration / email verification --------------------------------


def register(
    session: Session,
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    middle_name: str | None = None,
    birth_date: date | None = None,
) -> tuple[User, str]:
    """FR-REG-001: self-registration always creates a `pending` User.

    No club-policy engine exists to make this conditional (auth-api.md
    §4 rule 5's "if club policy requires admin confirmation" has no
    implemented policy to consult), and FR-REG-001 itself is an
    unconditional MUST — so `pending` is the only state this ever
    produces. No role is ever granted (Issue #29's RolePermission/
    UserRoleAssignment tables are not touched here at all).

    Does not attempt to match/reuse an existing Person by "confirmed
    identifier" (auth-api.md §4 rule 4) — what makes an identifier
    "confirmed" enough to auto-link two records safely is a product
    policy question this Issue must not invent an answer to (see PR
    description). Every self-registration creates a new Person.

    Returns (user, raw_email_verification_token).
    """
    normalized = normalize_login_identifier(email)
    existing = session.execute(
        select(User).where(User.normalized_login_identifier == normalized)
    ).scalar_one_or_none()
    if existing is not None:
        raise EmailAlreadyRegisteredError()

    validate_password_policy(password)

    person = Person(
        id=uuid.uuid4(),
        first_name=first_name,
        last_name=last_name,
        middle_name=middle_name,
        birth_date=birth_date,
        email=email,
    )
    user = User(
        id=uuid.uuid4(),
        person=person,
        login_identifier=email,
        password_hash=hash_password(password),
        status="pending",
    )
    session.add_all([person, user])
    try:
        # No ORM relationship links User to EmailVerificationChallenge, so
        # the unit of work does not know to order that insert after this
        # one — flush explicitly so the FK is satisfied. This is also the
        # point where a concurrent duplicate registration (both requests
        # pass the SELECT above before either commits) surfaces: the
        # database's own unique constraint on normalized_login_identifier
        # is the actual source of truth here, not the pre-check.
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_duplicate_login_identifier_violation(exc):
            raise EmailAlreadyRegisteredError() from exc
        raise
    raw_token = _issue_email_verification_challenge(session, user.id)
    session.commit()
    return user, raw_token


def _is_duplicate_login_identifier_violation(exc: IntegrityError) -> bool:
    """True only for a violation of `users.uq_users_normalized_login_identifier`
    — never for an unrelated IntegrityError, which must keep propagating as
    a real 500 rather than being papered over as a false 409.
    """
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_users_normalized_login_identifier"


def _issue_email_verification_challenge(session: Session, user_id: uuid.UUID) -> str:
    # "a new challenge may supersede older outstanding challenges"
    # (authentication-persistence.md §4).
    session.execute(
        update(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.user_id == user_id,
            EmailVerificationChallenge.consumed_at.is_(None),
            EmailVerificationChallenge.revoked_at.is_(None),
        )
        .values(revoked_at=_utcnow())
    )
    raw_token = generate_token()
    session.add(
        EmailVerificationChallenge(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=hash_token(raw_token),
            expires_at=_utcnow() + EMAIL_VERIFICATION_TTL,
        )
    )
    return raw_token


def resend_verification(session: Session, identifier: str) -> str | None:
    """auth-api.md §6: the caller MUST return an identical generic
    response whether or not this returns a token. Returns the raw token
    only when a new challenge was actually issued (for a future
    notification-delivery integration — see PR description; no such
    channel exists yet, so nothing sends it anywhere today).
    """
    normalized = normalize_login_identifier(identifier)
    user = session.execute(
        select(User).where(User.normalized_login_identifier == normalized)
    ).scalar_one_or_none()
    if user is None or user.email_verified_at is not None:
        return None
    raw_token = _issue_email_verification_challenge(session, user.id)
    session.commit()
    return raw_token


def verify_email(session: Session, raw_token: str) -> None:
    challenge = session.execute(
        select(EmailVerificationChallenge).where(
            EmailVerificationChallenge.token_hash == hash_token(raw_token)
        )
    ).scalar_one_or_none()
    now = _utcnow()
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.revoked_at is not None
        or challenge.expires_at <= now
    ):
        raise InvalidOrExpiredTokenError()

    challenge.consumed_at = now
    user = session.get(User, challenge.user_id)
    assert user is not None  # FK guarantees the referenced User exists
    user.email_verified_at = now
    session.commit()


# --- Login / sessions ----------------------------------------------------


def login(
    session: Session,
    *,
    identifier: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, datetime]:
    """Raises InvalidCredentialsError (bad identifier or password —
    indistinguishable) or AccountNotActiveError (correct credentials, a
    non-`active` account). Returns (user, raw_session_token, expires_at).
    """
    normalized = normalize_login_identifier(identifier)
    user = session.execute(
        select(User).where(User.normalized_login_identifier == normalized)
    ).scalar_one_or_none()

    # Always performs a real password verification, even for a nonexistent
    # identifier (against a fixed dummy hash) — so response timing does
    # not reveal whether the identifier exists (ADR-0009 / auth-api.md §7).
    if not verify_password_or_dummy(password, user.password_hash if user else None):
        raise InvalidCredentialsError()
    assert user is not None  # verify_password_or_dummy only ever matches a real hash

    if user.status != ACTIVE_USER_STATUS:
        raise AccountNotActiveError(user.status)

    raw_token = generate_token()
    now = _utcnow()
    expires_at = now + SESSION_TTL
    session.add(
        AuthenticatedSession(
            id=uuid.uuid4(),
            user_id=user.id,
            session_token_hash=hash_token(raw_token),
            status="active",
            expires_at=expires_at,
            created_ip_address=ip_address,
            created_user_agent=user_agent,
        )
    )
    user.last_login_at = now
    session.commit()
    return user, raw_token, expires_at


def resolve_session(session: Session, raw_session_token: str) -> ResolvedSession | None:
    """The ONLY path by which an authenticated identity is established:
    the caller proves possession of a raw session secret; the user_id
    returned is looked up server-side from that secret's matching
    AuthenticatedSession row — never taken from any client-supplied user
    id (Issue #33's core security requirement).

    Also re-checks the owning User's CURRENT status on every resolution
    (not just at login): a session created while the account was `active`
    must stop authenticating the moment an administrator moves that
    account to any other state (auth-and-authorization.md §4/§7). The
    session found to belong to a no-longer-active account is revoked here
    (not merely denied for this one request), so it stays denied even
    after the account is later restored to `active` — restoring the
    account never resurrects an old session; a fresh login is required.
    """
    row = session.execute(
        select(AuthenticatedSession).where(
            AuthenticatedSession.session_token_hash == hash_token(raw_session_token)
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    now = _utcnow()
    if row.status == "active" and row.expires_at <= now:
        # Lazy expiry: the persistence contract's own recommended index
        # ("(expires_at, status) for cleanup/expiry processing") implies a
        # background sweep may exist later; until then, a lookup that
        # discovers an overdue session corrects its stored status here.
        row.status = "expired"
        session.commit()
        return None
    if row.status != "active":
        return None

    user = session.get(User, row.user_id)
    if user is None or user.status != ACTIVE_USER_STATUS:
        row.status = "revoked"
        row.revoked_at = now
        row.revoked_reason = "account_not_active"
        session.commit()
        return None

    row.last_seen_at = now
    session.commit()
    return ResolvedSession(user_id=row.user_id, session_id=row.id)


def revoke_session(
    session: Session, *, user_id: uuid.UUID, session_id: uuid.UUID, reason: str
) -> bool:
    """Revokes one of `user_id`'s own sessions. Returns False when no such
    session exists for this user — including when `session_id` belongs to
    a different user — so the caller can answer with a uniform 404
    without confirming another user's session exists (auth-api.md §11:
    revoking another user's session needs a separate permission this
    Issue does not grant; see PR description). Idempotent: revoking an
    already revoked/expired session still returns True and does not
    raise.
    """
    row = session.execute(
        select(AuthenticatedSession).where(
            AuthenticatedSession.id == session_id, AuthenticatedSession.user_id == user_id
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    if row.status == "active":
        row.status = "revoked"
        row.revoked_at = _utcnow()
        row.revoked_reason = reason
        session.commit()
    return True


def logout_all(session: Session, *, user_id: uuid.UUID, reason: str = "logout_all") -> None:
    now = _utcnow()
    session.execute(
        update(AuthenticatedSession)
        .where(AuthenticatedSession.user_id == user_id, AuthenticatedSession.status == "active")
        .values(status="revoked", revoked_at=now, revoked_reason=reason)
    )
    session.commit()


def list_sessions(session: Session, *, user_id: uuid.UUID) -> list[AuthenticatedSession]:
    """Safe metadata only — callers must never serialize
    `session_token_hash` (auth-api.md §10)."""
    return list(
        session.execute(
            select(AuthenticatedSession)
            .where(AuthenticatedSession.user_id == user_id)
            .order_by(AuthenticatedSession.created_at.desc())
        )
        .scalars()
        .all()
    )


def list_sessions_page(
    session: Session, *, user_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[AuthenticatedSession], int]:
    """Page/page_size variant of list_sessions for the API layer's
    canonical collection envelope (ADR-0014 / api-contract.md §7). Returns
    (rows for this page, total row count across all pages) — the API
    layer computes `pages` from `total`/`page_size` itself.
    """
    total = session.execute(
        select(func.count())
        .select_from(AuthenticatedSession)
        .where(AuthenticatedSession.user_id == user_id)
    ).scalar_one()
    rows = list(
        session.execute(
            select(AuthenticatedSession)
            .where(AuthenticatedSession.user_id == user_id)
            .order_by(AuthenticatedSession.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total


# --- Password reset / change --------------------------------------------


def _issue_password_reset_challenge(
    session: Session,
    user: User,
    *,
    actor_type: Literal["user", "system"],
    actor_user_id: Optional[uuid.UUID],
    request_id: Optional[str],
) -> tuple[PasswordResetChallenge, str]:
    """Supersede the User's open challenges and add a new one with its
    `password_reset_challenge.created` audit event. No commit."""
    session.execute(
        update(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.user_id == user.id,
            PasswordResetChallenge.consumed_at.is_(None),
            PasswordResetChallenge.revoked_at.is_(None),
        )
        # A superseded challenge's queued delivery must not send it anymore.
        .values(revoked_at=_utcnow(), delivery_secret_ciphertext=None)
    )
    raw_token = generate_token()
    challenge = PasswordResetChallenge(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=_utcnow() + PASSWORD_RESET_TTL,
    )
    session.add(challenge)
    record_audit_event(
        session,
        action="password_reset_challenge.created",
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        resource_type="password_reset_challenge",
        resource_id=challenge.id,
        outcome="success",
        request_id=request_id,
    )
    return challenge, raw_token


def _find_user(session: Session, identifier: str) -> User | None:
    normalized = normalize_login_identifier(identifier)
    return session.execute(
        select(User).where(User.normalized_login_identifier == normalized)
    ).scalar_one_or_none()


def request_password_reset(
    session: Session,
    identifier: str,
    *,
    actor_type: Literal["user", "system"] = "system",
    actor_user_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
) -> str | None:
    """Issue a challenge and return its raw secret to the caller for
    administrator display (ADR-0038 §3: first access / admin reset, via
    app.authentication.account_provisioning, with `actor_type="user"` and
    the acting admin's id). Nothing is sent anywhere; self-service
    recovery uses request_password_reset_email instead — the challenge
    mechanism is identical either way (ADR-0038 §7: "reuse the existing
    password-reset/security model rather than introduce a parallel password
    mechanism").

    Records `password_reset_challenge.created` (ADR-0038 §8) in the same
    transaction as the challenge row; never for an unknown identifier
    (nothing was actually created, and auditing a lookup miss would leak
    exactly the account-existence signal auth-api.md §13 forbids).
    """
    user = _find_user(session, identifier)
    if user is None:
        return None
    _challenge, raw_token = _issue_password_reset_challenge(
        session,
        user,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    session.commit()
    return raw_token


def request_password_reset_email(
    session: Session,
    identifier: str,
    *,
    admin_policy: AdminPolicy,
    request_id: Optional[str] = None,
) -> PasswordResetEmailOutcome:
    """auth-api.md §13 self-service recovery (Issue #342): issue a
    challenge and queue its code for email delivery through the
    Notification Center, in ONE transaction — the challenge, its encrypted
    delivery copy (app.authentication.password_reset_delivery), the
    Notification, its Delivery and the outbox job commit together or not
    at all.

    The caller MUST answer every outcome with the same generic response;
    the outcome is for safe server-side diagnostics only. When the code
    cannot be delivered (no usable email address, Global email OFF, no
    template, no delivery key) no challenge is issued, so a previously
    issued code (e.g. an administrator's first-access code) stays valid.
    Delivery goes to the account's `login_identifier` even when it is not
    verified, and ignores notification rules and preferences (PO decisions
    of 2026-10-12); the Global email switch stays absolute.
    """
    discard_expired_delivery_secrets(session)
    user = _find_user(session, identifier)
    outcome: PasswordResetEmailOutcome
    if user is None:
        outcome = "unknown_identifier"
    elif parse_email_address(user.login_identifier) is None:
        outcome = "destination_invalid"
    else:
        available = security_email_available(
            session, template_code=PASSWORD_RESET_TEMPLATE_CODE, admin_policy=admin_policy
        ).outcome
        outcome = "queued" if available == "queued" else available
    if outcome != "queued":
        session.commit()
        return outcome
    assert user is not None
    try:
        key_ring = load_delivery_key_ring()
    except KeyRingUnavailable:
        session.commit()
        return "encryption_unavailable"

    challenge, raw_token = _issue_password_reset_challenge(
        session, user, actor_type="system", actor_user_id=None, request_id=request_id
    )
    challenge.delivery_secret_ciphertext = encrypt_challenge_secret(
        key_ring, challenge.id, raw_token
    )
    del raw_token
    queued = queue_security_email(
        session,
        event_type=PASSWORD_RESET_EVENT_TYPE,
        subject_type=PASSWORD_RESET_SUBJECT_TYPE,
        subject_id=challenge.id,
        recipient_user_id=user.id,
        template_code=PASSWORD_RESET_TEMPLATE_CODE,
        admin_policy=admin_policy,
    )
    if queued.outcome != "queued":
        # Policy/template changed since the check above: issue nothing.
        session.rollback()
        return queued.outcome
    session.commit()
    return "queued"


def confirm_password_reset(
    session: Session,
    *,
    raw_token: str,
    new_password: str,
    new_password_confirmation: str,
    request_id: Optional[str] = None,
) -> None:
    """Records `password_reset_challenge.completed` (ADR-0038 §8) in the
    same transaction as the password change, with `actor_type="user"` —
    the caller has just proven possession of a valid one-time challenge
    for this specific User, which is the relevant "who" for this event
    even though no full authenticated session exists yet. `details`
    carries only the count of sessions revoked as a consequence (a plain
    int, never a session id/token/hash) rather than a separate audit
    action — mirrors `event_participation.status_changed`'s "one action,
    details carry the rest" shape.

    Order (Issue #342): an invalid/expired/consumed/revoked code fails
    first (InvalidOrExpiredTokenError); then a confirmation mismatch
    (PasswordConfirmationMismatchError) or a weak password
    (WeakPasswordError) fails WITHOUT consuming — the same code can be
    retried. Consumption itself is one conditional UPDATE: of two
    concurrent confirmations of the same code exactly one matches the row
    (the other waits on its row lock, re-evaluates the condition and
    matches nothing), so a code can never be used twice. The same UPDATE
    clears any pending delivery copy of the secret.
    """
    token_hash = hash_token(raw_token)
    now = _utcnow()
    open_challenge = (
        PasswordResetChallenge.token_hash == token_hash,
        PasswordResetChallenge.consumed_at.is_(None),
        PasswordResetChallenge.revoked_at.is_(None),
        PasswordResetChallenge.expires_at > now,
    )
    if session.execute(select(PasswordResetChallenge.id).where(*open_challenge)).first() is None:
        raise InvalidOrExpiredTokenError()

    # Validate BEFORE consuming: a mismatched or weak new_password must not
    # burn the single-use token — the caller can retry with the same token.
    if new_password != new_password_confirmation:
        raise PasswordConfirmationMismatchError()
    validate_password_policy(new_password)

    consumed = session.execute(
        update(PasswordResetChallenge)
        .where(*open_challenge)
        .values(consumed_at=now, delivery_secret_ciphertext=None)
        .returning(PasswordResetChallenge.id, PasswordResetChallenge.user_id)
        .execution_options(synchronize_session=False)
    ).first()
    if consumed is None:
        # Consumed/revoked concurrently since the check above.
        session.rollback()
        raise InvalidOrExpiredTokenError()
    challenge_id, user_id = consumed
    user = session.get(User, user_id)
    assert user is not None
    user.password_hash = hash_password(new_password)
    # authentication-persistence.md §5 / auth-api.md §14: invalidate
    # previously active sessions.
    result = session.execute(
        update(AuthenticatedSession)
        .where(AuthenticatedSession.user_id == user.id, AuthenticatedSession.status == "active")
        .values(status="revoked", revoked_at=now, revoked_reason="password_reset")
    )
    record_audit_event(
        session,
        action="password_reset_challenge.completed",
        actor_type="user",
        actor_user_id=user.id,
        resource_type="password_reset_challenge",
        resource_id=challenge_id,
        outcome="success",
        request_id=request_id,
        details={"sessions_revoked": result.rowcount},
    )
    session.commit()


def change_password(
    session: Session, *, user_id: uuid.UUID, current_password: str, new_password: str
) -> None:
    """Requires an authenticated session (enforced by the API layer, not
    here). Unlike confirm_password_reset, this does NOT invalidate other
    sessions — auth-api.md §14 requires invalidation only for a reset,
    not for an in-session change; that distinction is the document's, not
    invented here.
    """
    user = session.get(User, user_id)
    assert user is not None  # the API layer only calls this for a resolved principal
    if not verify_password(current_password, user.password_hash or ""):
        raise IncorrectCurrentPasswordError()
    validate_password_policy(new_password)
    user.password_hash = hash_password(new_password)
    session.commit()
