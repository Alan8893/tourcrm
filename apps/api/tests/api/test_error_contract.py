"""Error contract per docs/03-architecture/adr/ADR-0014-api-response-envelope.md:

    {"error": {"code": "...", "message": "...", "details": {}, "request_id": "..."}}

Covers validation errors, HTTP errors, unknown endpoints and unexpected
internal errors — and that none of them leak secrets/stack traces.
"""


def _assert_canonical_error_shape(body: dict) -> None:
    assert set(body.keys()) == {"error"}
    error = body["error"]
    assert set(error.keys()) == {"code", "message", "details", "request_id"}
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    assert isinstance(error["request_id"], str) and error["request_id"]
    assert "data" not in body
    assert "meta" not in body


def test_validation_error_returns_422_with_field_details(probe_client) -> None:
    response = probe_client.get("/probe-validate", params={"count": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    _assert_canonical_error_shape(body)
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["fields"]
    assert body["error"]["details"]["fields"][0]["field"] == "count"


def test_http_error_uses_canonical_contract(probe_client) -> None:
    response = probe_client.get("/probe-error/403")

    assert response.status_code == 403
    body = response.json()
    _assert_canonical_error_shape(body)
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["message"] == "probe http error"


def test_unknown_endpoint_returns_documented_error_contract(real_client) -> None:
    response = real_client.get("/api/v1/this-endpoint-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    _assert_canonical_error_shape(body)
    assert body["error"]["code"] == "not_found"


def test_unknown_endpoint_outside_versioned_prefix_also_uses_error_contract(
    real_client,
) -> None:
    response = real_client.get("/totally-unknown-path")

    assert response.status_code == 404
    _assert_canonical_error_shape(response.json())


def test_internal_error_does_not_leak_stack_trace_or_secrets(probe_client) -> None:
    response = probe_client.get("/probe-crash")

    assert response.status_code == 500
    body = response.json()
    _assert_canonical_error_shape(body)
    assert body["error"]["code"] == "internal_error"

    raw = response.text
    for forbidden in (
        "hunter2",  # the secret embedded in the probe's raised exception
        "/var/lib/tourcrm",  # filesystem path embedded in the probe's exception
        "RuntimeError",  # exception class name
        "Traceback",
        "boom: unexpected probe failure",  # raw exception message
        ".py",  # any source file reference
    ):
        assert forbidden not in raw, f"leaked into error response: {forbidden!r}"
