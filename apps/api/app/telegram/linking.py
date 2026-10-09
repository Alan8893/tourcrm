"""Telegram account linking application service (Issue #329, ADR-0047 §4).

Transport-agnostic: nothing here calls the Bot API, so a future webhook
transport reuses it unchanged (ADR-0047 §7). Like app.audit.service, no
function commits or rolls back — the caller owns the transaction (the API
request, or the poller's per-update transaction that also advances the
update checkpoint, ADR-0047 §4.1 step 6).

Lifecycle semantics:

- **Issue / reissue** (`create_link_challenge`): only an authenticated,
  active User, for themselves. A fresh raw token
  (app.authentication.tokens.generate_token) is returned exactly once,
  inside the deep link; only its SHA-256 hash is stored. Issuing revokes
  the User's previous pending challenge (at most one pending per User),
  and at most CHALLENGE_RATE_LIMIT_COUNT challenges may be issued per
  rolling CHALLENGE_RATE_LIMIT_WINDOW — counted in PostgreSQL under a lock
  on the User row, so concurrent requests cannot exceed it.
- **Consume** (`consume_link_challenge`): the Telegram identity is the
  sender of a trusted Bot API update — never a client-supplied value. The
  challenge must be pending and unexpired by database time. Outcomes:
  - the sender is not linked anywhere -> linked to the challenge owner; an
    owner's different active identity is ended as `replaced`;
  - the sender is already the owner's active identity -> confirmed, no
    change (the challenge is still consumed);
  - the sender is actively linked to a *different* User -> rejected; the
    identity is never transferred and the challenge stays pending (the
    owner can still use it from the right Telegram account). The other
    User must unlink first.
  Every rejection (unknown, expired, consumed, revoked, malformed token,
  inactive owner, identity conflict) returns the same `LINK_REJECTED`
  outcome, so the bot's answer reveals nothing (ADR-0047 §4.3). The
  challenge, both identities and the owner are locked `FOR UPDATE`, and
  the partial UNIQUE indexes are the final arbiter under concurrency: a
  losing concurrent insert is rolled back to a savepoint and rejected.
- **Unlink** (`unlink_telegram_identity`): the User ends their own active
  identity (`unlinked`) and revokes any pending challenge. Idempotent.

Audit (ADR-0024 via record_audit_event): metadata only — never the token,
the Telegram user id or any message content.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal, Optional
from urllib.parse import quote

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authentication.tokens import generate_token, hash_token
from app.db.identity import User
from app.db.telegram import TelegramIdentity, TelegramLinkChallenge
from app.telegram.vocabulary import (
    CHALLENGE_CONSUMED,
    CHALLENGE_PENDING,
    CHALLENGE_RATE_LIMIT_COUNT,
    CHALLENGE_RATE_LIMIT_WINDOW,
    CHALLENGE_REVOKED,
    CHALLENGE_TTL,
    IDENTITY_ACTIVE,
    IDENTITY_REPLACED,
    IDENTITY_UNLINKED,
    START_PARAMETER_PATTERN,
)

ACTIVE_USER_STATUS = "active"
_START_PARAMETER = re.compile(START_PARAMETER_PATTERN)

LINKED: Final = "linked"
ALREADY_LINKED: Final = "already_linked"
LINK_REJECTED: Final = "rejected"
LinkOutcome = Literal["linked", "already_linked", "rejected"]


class LinkChallengeRateLimited(Exception):
    """The User issued too many challenges in the rate-limit window."""


class LinkingUnavailable(Exception):
    """The User may not link a Telegram account (not an active User)."""


@dataclass(frozen=True)
class IssuedLinkChallenge:
    """The one-time issuance result. `deep_link` embeds the raw token and
    is the only place it ever appears; it is excluded from repr."""

    challenge_id: uuid.UUID
    expires_at: datetime
    deep_link: str = field(repr=False)


@dataclass(frozen=True)
class LinkStatus:
    linked: bool
    linked_at: Optional[datetime]


def _db_now(session: Session) -> datetime:
    return session.execute(sa.select(sa.func.now())).scalar_one()


def _lock_user(session: Session, user_id: uuid.UUID) -> Optional[User]:
    return session.execute(
        sa.select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


def _active_identity_for_user(session: Session, user_id: uuid.UUID) -> Optional[TelegramIdentity]:
    return session.execute(
        sa.select(TelegramIdentity)
        .where(TelegramIdentity.user_id == user_id, TelegramIdentity.status == IDENTITY_ACTIVE)
        .with_for_update()
    ).scalar_one_or_none()


def _revoke_pending_challenges(session: Session, user_id: uuid.UUID) -> int:
    result = session.execute(
        sa.update(TelegramLinkChallenge)
        .where(
            TelegramLinkChallenge.user_id == user_id,
            TelegramLinkChallenge.status == CHALLENGE_PENDING,
        )
        .values(status=CHALLENGE_REVOKED, revoked_at=sa.func.now())
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def build_deep_link(bot_username: str, raw_token: str) -> str:
    return f"https://t.me/{quote(bot_username)}?start={quote(raw_token)}"


def create_link_challenge(
    session: Session,
    *,
    user_id: uuid.UUID,
    bot_username: str,
    request_id: Optional[str] = None,
) -> IssuedLinkChallenge:
    """Issue a one-time challenge for `user_id` (the authenticated caller).
    Raises LinkingUnavailable or LinkChallengeRateLimited, persisting
    nothing."""
    user = _lock_user(session, user_id)
    if user is None or user.status != ACTIVE_USER_STATUS:
        raise LinkingUnavailable()
    now = _db_now(session)
    recent = session.execute(
        sa.select(sa.func.count())
        .select_from(TelegramLinkChallenge)
        .where(
            TelegramLinkChallenge.user_id == user_id,
            TelegramLinkChallenge.created_at > now - CHALLENGE_RATE_LIMIT_WINDOW,
        )
    ).scalar_one()
    if recent >= CHALLENGE_RATE_LIMIT_COUNT:
        raise LinkChallengeRateLimited()

    reissue = _revoke_pending_challenges(session, user_id) > 0
    raw_token = generate_token()
    if not _START_PARAMETER.fullmatch(raw_token):  # pragma: no cover - alphabet invariant
        raise RuntimeError("generated token does not fit the deep-link alphabet")
    challenge = TelegramLinkChallenge(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        status=CHALLENGE_PENDING,
        created_at=now,
        expires_at=now + CHALLENGE_TTL,
    )
    session.add(challenge)
    session.flush()
    record_audit_event(
        session,
        action="telegram_link_challenge.created",
        actor_type="user",
        actor_user_id=user_id,
        outcome="success",
        resource_type="telegram_link_challenge",
        resource_id=challenge.id,
        request_id=request_id,
        details={"reissue": reissue},
    )
    return IssuedLinkChallenge(
        challenge_id=challenge.id,
        expires_at=challenge.expires_at,
        deep_link=build_deep_link(bot_username, raw_token),
    )


def consume_link_challenge(
    session: Session, *, raw_token: str, telegram_user_id: int
) -> LinkOutcome:
    """Consume the challenge `raw_token` for the Telegram sender
    `telegram_user_id` of a trusted update. See the module docstring for
    the outcomes; every failure is LINK_REJECTED."""
    if (
        not _START_PARAMETER.fullmatch(raw_token)
        or isinstance(telegram_user_id, bool)
        or telegram_user_id <= 0
    ):
        return LINK_REJECTED
    challenge = session.execute(
        sa.select(TelegramLinkChallenge)
        .where(TelegramLinkChallenge.token_hash == hash_token(raw_token))
        .with_for_update()
    ).scalar_one_or_none()
    if challenge is None or challenge.status != CHALLENGE_PENDING:
        return LINK_REJECTED
    if challenge.expires_at <= _db_now(session):
        return LINK_REJECTED
    owner = _lock_user(session, challenge.user_id)
    if owner is None or owner.status != ACTIVE_USER_STATUS:
        return LINK_REJECTED

    holder = session.execute(
        sa.select(TelegramIdentity)
        .where(
            TelegramIdentity.telegram_user_id == telegram_user_id,
            TelegramIdentity.status == IDENTITY_ACTIVE,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if holder is not None and holder.user_id != owner.id:
        # Never a silent transfer between accounts.
        record_audit_event(
            session,
            action="telegram_identity.linked",
            actor_type="user",
            actor_user_id=owner.id,
            outcome="failure",
            resource_type="telegram_link_challenge",
            resource_id=challenge.id,
            details={"reason": "identity_linked_to_another_user"},
        )
        return LINK_REJECTED

    current = _active_identity_for_user(session, owner.id)
    if current is not None and current.telegram_user_id == telegram_user_id:
        identity = current
        outcome: LinkOutcome = ALREADY_LINKED
        replaced: Optional[TelegramIdentity] = None
    else:
        savepoint = session.begin_nested()
        try:
            if current is not None:
                current.status = IDENTITY_REPLACED
                current.ended_at = _db_now(session)
                session.flush()
            identity = TelegramIdentity(
                user_id=owner.id, telegram_user_id=telegram_user_id, status=IDENTITY_ACTIVE
            )
            session.add(identity)
            session.flush()
        except IntegrityError:
            # A concurrent link won the partial UNIQUE index.
            savepoint.rollback()
            return LINK_REJECTED
        savepoint.commit()
        outcome = LINKED
        replaced = current

    challenge.status = CHALLENGE_CONSUMED
    challenge.consumed_at = _db_now(session)
    challenge.telegram_identity_id = identity.id
    session.flush()

    if replaced is not None:
        record_audit_event(
            session,
            action="telegram_identity.unlinked",
            actor_type="user",
            actor_user_id=owner.id,
            outcome="success",
            resource_type="telegram_identity",
            resource_id=replaced.id,
            details={"reason": "replaced"},
        )
    record_audit_event(
        session,
        action="telegram_identity.linked",
        actor_type="user",
        actor_user_id=owner.id,
        outcome="success",
        resource_type="telegram_identity",
        resource_id=identity.id,
        details={"already_linked": outcome == ALREADY_LINKED, "replaced_previous": bool(replaced)},
    )
    return outcome


def unlink_telegram_identity(
    session: Session, *, user_id: uuid.UUID, request_id: Optional[str] = None
) -> bool:
    """End the caller's active identity and revoke pending challenges.
    Returns whether an identity was unlinked."""
    if _lock_user(session, user_id) is None:
        return False
    _revoke_pending_challenges(session, user_id)
    identity = _active_identity_for_user(session, user_id)
    if identity is None:
        session.flush()
        return False
    identity.status = IDENTITY_UNLINKED
    identity.ended_at = _db_now(session)
    session.flush()
    record_audit_event(
        session,
        action="telegram_identity.unlinked",
        actor_type="user",
        actor_user_id=user_id,
        outcome="success",
        resource_type="telegram_identity",
        resource_id=identity.id,
        request_id=request_id,
        details={"reason": "user_unlinked"},
    )
    return True


def get_link_status(session: Session, *, user_id: uuid.UUID) -> LinkStatus:
    identity = session.execute(
        sa.select(TelegramIdentity).where(
            TelegramIdentity.user_id == user_id, TelegramIdentity.status == IDENTITY_ACTIVE
        )
    ).scalar_one_or_none()
    return LinkStatus(
        linked=identity is not None,
        linked_at=identity.linked_at if identity is not None else None,
    )


__all__ = [
    "LINKED",
    "ALREADY_LINKED",
    "LINK_REJECTED",
    "LinkOutcome",
    "LinkChallengeRateLimited",
    "LinkingUnavailable",
    "IssuedLinkChallenge",
    "LinkStatus",
    "build_deep_link",
    "create_link_challenge",
    "consume_link_challenge",
    "unlink_telegram_identity",
    "get_link_status",
]
