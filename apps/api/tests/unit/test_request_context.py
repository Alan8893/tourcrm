"""Pure unit tests for request-id validation logic (no HTTP, no DB) —
app.api.request_context (Issue #6 foundation), exercised here as a plain
function rather than through a live request.
"""

from app.api.request_context import _is_valid_client_request_id


def test_accepts_a_reasonable_token() -> None:
    assert _is_valid_client_request_id("client-supplied-id-123") is True


def test_rejects_none() -> None:
    assert _is_valid_client_request_id(None) is False


def test_rejects_empty_string() -> None:
    assert _is_valid_client_request_id("") is False


def test_rejects_whitespace_and_control_characters() -> None:
    assert _is_valid_client_request_id("has space") is False
    assert _is_valid_client_request_id("has\x00null") is False


def test_rejects_overly_long_values() -> None:
    assert _is_valid_client_request_id("a" * 129) is False
    assert _is_valid_client_request_id("a" * 128) is True
