"""Password hashing (ADR-0009: "passwords are stored only as strong
password hashes using a current password-hashing algorithm selected at
implementation time").

Argon2id (via argon2-cffi) is used — the algorithm OWASP currently
recommends as the first choice for new applications. This module is the
only place in the codebase that touches a raw password.
"""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# auth-and-authorization.md §8: "minimum length" is the one concrete,
# unconditional requirement; no complexity-composition rule (forced
# uppercase/digit/symbol) is documented, so none is invented here — this
# mirrors current NIST guidance that length matters more than composition
# theater. Checking known-compromised-password lists is explicitly
# conditional ("if the chosen mechanism supports it") and would require a
# new external dependency/network call; deferred, not silently done.
MINIMUM_PASSWORD_LENGTH = 8


class WeakPasswordError(ValueError):
    """Raised by validate_password_policy() when a password fails the
    minimum documented policy."""


def validate_password_policy(password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {MINIMUM_PASSWORD_LENGTH} characters long"
        )


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# A fixed, precomputed hash of a value nobody will ever type, used to keep
# login's timing identical whether or not the identifier resolves to a
# real User — verifying against a real Argon2 hash either way, rather than
# short-circuiting on "user not found", is what actually defeats a timing
# side-channel for account enumeration (auth-api.md §7 / ADR-0009: "use
# generic responses ... to reduce account enumeration risk").
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Always performs a real Argon2 verification, even when `password_hash`
    is None (no such user) — so login's response time does not depend on
    whether the identifier exists.
    """
    return verify_password(password, password_hash or _DUMMY_PASSWORD_HASH)
