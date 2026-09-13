"""Pure-Python unit tests for the `details` secret-prohibition boundary
(ADR-0024 §6) — no database, no HTTP.

See tests/integration/test_audit_log.py for the ORM-level `@validates`
hook proof (that this boundary also fires on direct AuditLog
construction, not only through app.audit.service).
"""

import pytest

from app.audit.security import AuditDetailsError, assert_safe_audit_details


def test_none_details_is_safe() -> None:
    assert_safe_audit_details(None)  # must not raise


def test_plain_safe_details_is_accepted() -> None:
    assert_safe_audit_details(
        {
            "changes": {"status": {"from": "active", "to": "suspended"}},
            "count": 3,
            "confirmed": True,
            "note": None,
            "tags": ["a", "b"],
        }
    )  # must not raise


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Password",
        "new_password",
        "password_hash",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "reset_token",
        "verification_token",
        "invitation_token",
        "csrf_token",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "API-Key",
        "cookie",
        "cookies",
        "authorization",
        "Authorization",
        "credential",
        "credentials",
    ],
)
def test_prohibited_top_level_key_is_rejected(key: str) -> None:
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details({key: "whatever"})


def test_prohibited_key_is_rejected_when_nested() -> None:
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details({"changes": {"new_password": "hunter2"}})


def test_prohibited_key_is_rejected_inside_a_list_of_dicts() -> None:
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details({"items": [{"ok": 1}, {"session_token": "abc"}]})


def test_masking_a_secret_value_does_not_make_the_key_acceptable() -> None:
    # The prohibition is on the key, not the literal value — a caller
    # cannot "launder" a secret field through a masked/redacted value.
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details({"password": "***REDACTED***"})


@pytest.mark.parametrize(
    "details",
    [
        {"count": object()},
        {"when": __import__("datetime").datetime.now()},
        {"nested": {"bad": object()}},
        {"items": [1, object()]},
    ],
)
def test_non_json_safe_value_is_rejected(details: dict) -> None:
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details(details)


def test_top_level_non_dict_is_rejected() -> None:
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details("not-a-dict")  # type: ignore[arg-type]


def test_non_string_key_is_rejected() -> None:
    with pytest.raises(AuditDetailsError):
        assert_safe_audit_details({1: "value"})  # type: ignore[dict-item]
