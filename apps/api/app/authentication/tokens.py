"""Opaque secret generation/hashing for sessions and one-time challenges.

authentication-persistence.md §2.3: raw session credentials and one-time
challenge secrets must not be stored in plaintext when a hash-based lookup
is sufficient. Unlike a password, these values are already
high-entropy/random (never user-chosen or memorable), so a fast
cryptographic hash (SHA-256) — not a slow, memory-hard password-hashing
algorithm — is the appropriate, standard mechanism: it makes the stored
value useless to an attacker who reads the database while keeping lookup
by hash cheap, and there is no brute-force-by-guessing risk to defend
against for a 256-bit random secret the way there is for a human password.
"""

import hashlib
import secrets

# 256 bits of entropy, URL-safe — the same conservative sizing already
# used for the dummy password hash secret and for request/technical ids
# elsewhere in this codebase.
_TOKEN_BYTES = 32


def generate_token() -> str:
    """A fresh, unguessable raw secret — a session token or a one-time
    challenge token. Callers must hash it (hash_token) before persisting
    and must never log or return the raw value except at the single point
    of issuance (a Set-Cookie header or, if a real notification channel
    existed, an out-of-band delivery)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
