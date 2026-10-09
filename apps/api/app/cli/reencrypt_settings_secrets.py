"""Re-encrypt UI-managed integration secrets with the primary key
(Issue #333, ADR-0048 §2.6 key rotation):

    SETTINGS_ENCRYPTION_KEYS=<new_id>:<new key>,<old_id>:<old key> \\
        python -m app.cli.reencrypt_settings_secrets

Decrypts every `integration_secrets` row with the configured key ring and
re-encrypts the ones not yet under the first (primary) key, in ONE
transaction: if any value cannot be decrypted (unknown key id, wrong key,
tampered ciphertext) nothing is written and the command exits 1. Output is
counts and key ids only — never a secret, plaintext or ciphertext.

Exit status: 0 success, 1 a value could not be decrypted, 2 the key ring is
missing or invalid.
"""

import sys

import sqlalchemy as sa

import app.db.identity  # noqa: F401  (registers `users` for the FK on updated_by_user_id)
from app.db.notification_settings import IntegrationSecret
from app.db.session import session_scope
from app.notification_settings.crypto import (
    KeyRing,
    KeyRingUnavailable,
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    token_key_id,
)


def reencrypt_all(key_ring: KeyRing) -> tuple[int, int]:
    """Returns (re-encrypted, already current). Raises SecretDecryptionError
    without writing anything."""
    primary = key_ring.primary.key_id
    reencrypted = current = 0
    with session_scope() as session:
        rows = session.execute(
            sa.select(IntegrationSecret).order_by(IntegrationSecret.name).with_for_update()
        ).scalars().all()
        for row in rows:
            # Decrypt even rows under the primary key: proves every stored
            # value is readable before anything is committed.
            plaintext = decrypt_secret(key_ring, row.name, row.ciphertext)
            if token_key_id(row.ciphertext) == primary:
                current += 1
                continue
            row.ciphertext = encrypt_secret(key_ring, row.name, plaintext)
            reencrypted += 1
        session.commit()
    return reencrypted, current


def main() -> int:
    try:
        key_ring = KeyRing.from_env()
    except KeyRingUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        reencrypted, current = reencrypt_all(key_ring)
    except SecretDecryptionError as exc:
        print(
            f"error: a stored secret cannot be decrypted ({exc.code}); nothing was changed",
            file=sys.stderr,
        )
        return 1
    print(
        f"primary_key_id={key_ring.primary.key_id} reencrypted={reencrypted} "
        f"already_current={current} remaining_old_key=0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
