"""Secure asynchronous delivery of the password-reset code (Issue #342,
auth-api.md §13, authentication-persistence.md §5, ADR-0045 §4).

The one-time secret of a PasswordResetChallenge is verified only through
its SHA-256 `token_hash`. To email it from the outbox worker, the request
transaction also stores an encrypted copy in
`password_reset_challenges.delivery_secret_ciphertext`:

- AES-256-GCM through the existing, reviewed key-ring implementation
  (app.notification_settings.crypto), but with its OWN key ring
  AUTH_DELIVERY_ENCRYPTION_KEYS (deployment secret, never in the database)
  and its own associated-data domain bound to the challenge id — a
  settings secret and a delivery secret never share key material, and a
  ciphertext copied to another challenge fails authentication;
- the Notification carries only `subject_type`/`subject_id` (the
  challenge id) and an empty `render_context`; the outbox payload only the
  Delivery id. The plaintext exists only in the request's memory (before
  encryption) and in the worker's memory between decryption and the SMTP
  call.

Lifecycle of the copy (all writes are single UPDATE statements, so they
serialize on the challenge row with the atomic consumption in
app.authentication.service):

- retry / ambiguous SMTP outcome (e.g. a timeout after the server may have
  accepted the message): the copy is kept and the next attempt sends the
  SAME code — the worker never issues a new challenge;
- delivered, terminally failed or abandoned: cleared in the worker's
  recording transaction, atomically with the job result;
- consumed or revoked (a newer request supersedes it): cleared in the same
  UPDATE that consumes/revokes it; a CHECK constraint forbids a copy on a
  consumed/revoked row;
- expired: the worker refuses to send it and clears it; every recording
  transaction and every recovery request also clears all expired copies.

The worker re-checks the challenge (not consumed, not revoked, not
expired, same recipient) in the same short read in which it decrypts.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.authentication import PasswordResetChallenge
from app.db.notifications import Notification, NotificationDelivery
from app.notification_settings.crypto import (
    KeyRing,
    KeyRingUnavailable,
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
)
from app.notifications.delivery import ChannelResult

AUTH_DELIVERY_KEY_RING_ENV = "AUTH_DELIVERY_ENCRYPTION_KEYS"
_AAD_PREFIX = b"tourcrm.auth_delivery_secret.v1:"

PASSWORD_RESET_EVENT_TYPE = "auth.password_reset_requested"
PASSWORD_RESET_SUBJECT_TYPE = "password_reset_challenge"
PASSWORD_RESET_TEMPLATE_CODE = "auth_password_reset_code.email"

# Safe, stable failure codes of the worker side.
CHALLENGE_NOT_DELIVERABLE = "challenge_not_deliverable"
DELIVERY_ENCRYPTION_UNAVAILABLE = "delivery_encryption_unavailable"


def _secret_name(challenge_id: uuid.UUID) -> str:
    return f"password_reset_challenge:{challenge_id}"


def load_delivery_key_ring() -> KeyRing:
    """Raises KeyRingUnavailable (safe message, no key material)."""
    return KeyRing.from_env(AUTH_DELIVERY_KEY_RING_ENV)


def encrypt_challenge_secret(key_ring: KeyRing, challenge_id: uuid.UUID, raw_token: str) -> str:
    return encrypt_secret(key_ring, _secret_name(challenge_id), raw_token, aad_prefix=_AAD_PREFIX)


def discard_expired_delivery_secrets(session: Session) -> None:
    """Clear every encrypted copy whose challenge has expired (no commit)."""
    session.execute(
        sa.update(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.delivery_secret_ciphertext.is_not(None),
            PasswordResetChallenge.expires_at <= sa.func.now(),
        )
        .values(delivery_secret_ciphertext=None)
        .execution_options(synchronize_session=False)
    )


class PasswordResetCodeDelivery:
    """The worker side: the Email Adapter's DeliverySecretResolver for
    PASSWORD_RESET_EVENT_TYPE and the delivery handler's
    DeliverySecretCleanup."""

    def __repr__(self) -> str:
        return "PasswordResetCodeDelivery()"

    def resolve(self, session: Session, notification: Notification) -> str | ChannelResult:
        if (
            notification.event_type != PASSWORD_RESET_EVENT_TYPE
            or notification.subject_type != PASSWORD_RESET_SUBJECT_TYPE
            or notification.subject_id is None
        ):
            return ChannelResult.permanent(CHALLENGE_NOT_DELIVERABLE)
        challenge = session.get(PasswordResetChallenge, notification.subject_id)
        now = datetime.now(timezone.utc)
        if (
            challenge is None
            or challenge.user_id != notification.recipient_user_id
            or challenge.consumed_at is not None
            or challenge.revoked_at is not None
            or challenge.expires_at <= now
            or challenge.delivery_secret_ciphertext is None
        ):
            return ChannelResult.permanent(CHALLENGE_NOT_DELIVERABLE)
        try:
            key_ring = load_delivery_key_ring()
        except KeyRingUnavailable:
            # Deployment configuration may still be fixed: bounded retry.
            return ChannelResult.retryable(DELIVERY_ENCRYPTION_UNAVAILABLE)
        try:
            return decrypt_secret(
                key_ring,
                _secret_name(challenge.id),
                challenge.delivery_secret_ciphertext,
                aad_prefix=_AAD_PREFIX,
            )
        except SecretDecryptionError as exc:
            code = exc.code
        return ChannelResult.permanent(code)

    def release(self, session: Session, delivery_id: uuid.UUID, *, finished: bool) -> None:
        challenge_id = (
            sa.select(Notification.subject_id)
            .join(NotificationDelivery, NotificationDelivery.notification_id == Notification.id)
            .where(
                NotificationDelivery.id == delivery_id,
                Notification.event_type == PASSWORD_RESET_EVENT_TYPE,
                Notification.subject_type == PASSWORD_RESET_SUBJECT_TYPE,
            )
            .scalar_subquery()
        )
        statement = sa.update(PasswordResetChallenge).where(
            PasswordResetChallenge.id == challenge_id,
            PasswordResetChallenge.delivery_secret_ciphertext.is_not(None),
        )
        if not finished:
            # Kept for the retry unless it can no longer be used anyway.
            statement = statement.where(PasswordResetChallenge.expires_at <= sa.func.now())
        session.execute(
            statement.values(delivery_secret_ciphertext=None).execution_options(
                synchronize_session=False
            )
        )
        discard_expired_delivery_secrets(session)


__all__ = [
    "AUTH_DELIVERY_KEY_RING_ENV",
    "PASSWORD_RESET_EVENT_TYPE",
    "PASSWORD_RESET_SUBJECT_TYPE",
    "PASSWORD_RESET_TEMPLATE_CODE",
    "CHALLENGE_NOT_DELIVERABLE",
    "DELIVERY_ENCRYPTION_UNAVAILABLE",
    "load_delivery_key_ring",
    "encrypt_challenge_secret",
    "discard_expired_delivery_secrets",
    "PasswordResetCodeDelivery",
]
