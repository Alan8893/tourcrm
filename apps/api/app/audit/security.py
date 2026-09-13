"""The `details` secret-prohibition boundary (ADR-0024 §6).

Pure Python, no ORM/FastAPI import — used both by app.audit.service
(the reusable write boundary) and by an `@validates` hook on
app.db.audit.AuditLog itself, so a caller cannot bypass the check by
constructing/assigning to an `AuditLog` row directly instead of going
through the service (the same defense-in-depth shape already used for
`Event.timezone` via app.events.lifecycle.validate_timezone).

This is deliberately a small, focused denylist rather than a general
security subsystem: docs/03-architecture/adr/ADR-0024-audit-
infrastructure.md §6 lists the exact prohibited categories (passwords,
password hashes, access/refresh/session/reset/verification/invitation
tokens, API keys, cookies, Authorization header values, other
credentials/secrets), and no reusable secret-redaction helper already
existed elsewhere in this codebase to reuse instead.

Prohibited values are rejected outright — never masked or truncated.
"docs/03-architecture/adr/ADR-0024-audit-infrastructure.md" §6: "does not
mask, does not truncate, does not 'best-effort sanitize'".
"""

from typing import Any

# Substrings checked against a normalized (lowercased, `-`/` ` -> `_`) key
# name, at any nesting depth. Substring matching is deliberately broad:
# `new_password`, `auth_token`, `session_token_hash`, `x_api_key` etc. must
# all be caught, not only an exact `password`/`token` key.
_PROHIBITED_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "cookie",
    "authorization",
    "credential",
)

_JSON_SAFE_SCALAR_TYPES = (str, int, float, bool, type(None))


class AuditDetailsError(ValueError):
    """Raised when `details` contains a prohibited key or an unsafe value.

    Never raised for a value that is merely inconvenient to store — only
    for a prohibited secret/credential key, or a value that is not plain
    JSON-safe data (str/int/float/bool/None/dict/list), the latter guarding
    against an ORM entity, HTTP request/response object, or live Session
    being passed through and silently serialized.
    """


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _is_prohibited_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(substring in normalized for substring in _PROHIBITED_KEY_SUBSTRINGS)


def _assert_safe_value(value: Any, *, key_path: str) -> None:
    if isinstance(value, _JSON_SAFE_SCALAR_TYPES):
        return
    if isinstance(value, dict):
        assert_safe_audit_details(value, _key_path=key_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_safe_value(item, key_path=f"{key_path}[{index}]")
        return
    raise AuditDetailsError(
        f"details.{key_path} has type {type(value).__name__}, which is not "
        "JSON-safe (only str/int/float/bool/None/dict/list are permitted); "
        "ORM entities, requests, sessions and other live objects must never "
        "be passed as audit details"
    )


def assert_safe_audit_details(
    details: dict[str, Any] | None, *, _key_path: str = ""
) -> None:
    """Raise AuditDetailsError if `details` contains a prohibited
    secret/credential key (at any nesting depth) or a value that is not
    plain JSON-safe data. `None` is always safe (no details supplied).

    `_key_path` is an internal recursion accumulator for error messages;
    callers should not pass it.
    """
    if details is None:
        return
    if not isinstance(details, dict):
        raise AuditDetailsError(
            f"details must be a dict or None, got {type(details).__name__}"
        )
    for key, value in details.items():
        if not isinstance(key, str):
            raise AuditDetailsError(f"details keys must be strings, got {type(key).__name__}")
        path = f"{_key_path}.{key}" if _key_path else key
        if _is_prohibited_key(key):
            raise AuditDetailsError(
                f"details.{path} looks like a secret/credential field and must "
                "not be stored in AuditLog.details"
            )
        _assert_safe_value(value, key_path=path)


__all__ = ["AuditDetailsError", "assert_safe_audit_details"]
