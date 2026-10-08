"""The outbox payload secret-prohibition boundary (ADR-0045 §4,
ADR-0046 §5.7).

Reuses the audit `details` denylist (app.audit.security) rather than
introducing a second, drifting copy: an outbox payload carries only
identifiers/context for the worker, never passwords, provider
credentials, bot tokens or raw verification/reset/linking tokens.
"""

from typing import Any

from app.audit.security import AuditDetailsError, assert_safe_audit_details


class OutboxPayloadError(ValueError):
    """The payload is not a dict, contains a prohibited secret/credential
    key, or contains a value that is not plain JSON-safe data."""


def assert_safe_outbox_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise OutboxPayloadError(f"payload must be a dict, got {type(payload).__name__}")
    try:
        assert_safe_audit_details(payload)
    except AuditDetailsError as exc:
        raise OutboxPayloadError(
            "outbox payload must contain only JSON-safe, non-secret data"
        ) from exc


__all__ = ["OutboxPayloadError", "assert_safe_outbox_payload"]
