"""Pure-Python unit tests for the Issue #33 authentication primitives —
no database, no HTTP. Full flows (register/login/sessions/verification/
reset) are covered in tests/integration/test_authentication_service.py
and tests/integration/test_authentication_api.py.
"""

import pytest
from fastapi import HTTPException

from app.authentication.csrf import require_matching_csrf_token
from app.authentication.passwords import (
    MINIMUM_PASSWORD_LENGTH,
    WeakPasswordError,
    hash_password,
    validate_password_policy,
    verify_password,
    verify_password_or_dummy,
)
from app.authentication.tokens import generate_token, hash_token

# --- passwords -------------------------------------------------------------


def test_hash_password_never_returns_the_plaintext() -> None:
    password = "correcthorsebattery"
    hashed = hash_password(password)
    assert hashed != password
    assert password not in hashed


def test_verify_password_accepts_the_correct_password() -> None:
    hashed = hash_password("correcthorsebattery")
    assert verify_password("correcthorsebattery", hashed) is True


def test_verify_password_rejects_the_wrong_password() -> None:
    hashed = hash_password("correcthorsebattery")
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert verify_password("anything", "not-a-real-argon2-hash") is False


def test_verify_password_or_dummy_rejects_when_no_hash_exists() -> None:
    # Simulates "identifier not found": must still run a real verification
    # against a fixed dummy hash, never short-circuit to False directly.
    assert verify_password_or_dummy("whatever", None) is False


def test_verify_password_or_dummy_accepts_a_real_matching_hash() -> None:
    hashed = hash_password("correcthorsebattery")
    assert verify_password_or_dummy("correcthorsebattery", hashed) is True


def test_password_policy_accepts_a_long_enough_password() -> None:
    validate_password_policy("a" * MINIMUM_PASSWORD_LENGTH)  # must not raise


def test_password_policy_rejects_a_too_short_password() -> None:
    with pytest.raises(WeakPasswordError):
        validate_password_policy("a" * (MINIMUM_PASSWORD_LENGTH - 1))


# --- tokens -----------------------------------------------------------------


def test_generate_token_produces_distinct_values() -> None:
    assert generate_token() != generate_token()


def test_hash_token_is_deterministic() -> None:
    token = generate_token()
    assert hash_token(token) == hash_token(token)


def test_hash_token_never_contains_the_raw_token() -> None:
    token = generate_token()
    assert token not in hash_token(token)


def test_different_tokens_hash_differently() -> None:
    assert hash_token(generate_token()) != hash_token(generate_token())


# --- CSRF --------------------------------------------------------------


def test_matching_csrf_cookie_and_header_is_accepted() -> None:
    require_matching_csrf_token("same-value", "same-value")  # must not raise


def test_mismatched_csrf_cookie_and_header_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_matching_csrf_token("cookie-value", "different-header-value")
    assert exc_info.value.status_code == 403


def test_missing_csrf_header_is_rejected() -> None:
    with pytest.raises(HTTPException):
        require_matching_csrf_token("cookie-value", None)


def test_missing_csrf_cookie_is_rejected() -> None:
    with pytest.raises(HTTPException):
        require_matching_csrf_token(None, "header-value")


def test_both_missing_is_rejected() -> None:
    with pytest.raises(HTTPException):
        require_matching_csrf_token(None, None)
