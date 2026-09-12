"""Liveness/readiness endpoint tests (Issue #10) — no database required.

Success-path readiness (real PostgreSQL reachable) lives in
tests/integration/test_health.py, since that genuinely needs a database;
everything here — including the "database unavailable" failure path —
uses the real app with app.db.session.check_connection monkeypatched, so
it stays fast and independent of any database (matching this suite's
existing no-DB contract, see tests/api/conftest.py).
"""

import app.api.health as health_module
from app.core.config import ConfigurationError
from app.db.errors import DatabaseConnectionError


def test_liveness_returns_ok(real_client) -> None:
    response = real_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_response_has_no_business_envelope(real_client) -> None:
    body = real_client.get("/health/live").json()

    assert set(body.keys()) == {"status"}
    assert "data" not in body
    assert "meta" not in body
    assert "error" not in body


def test_liveness_does_not_touch_the_database(real_client, monkeypatch) -> None:
    def _boom() -> None:
        raise AssertionError("liveness must never call check_connection()")

    monkeypatch.setattr(health_module, "check_connection", _boom)

    response = real_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_200_when_check_connection_succeeds(real_client, monkeypatch) -> None:
    monkeypatch.setattr(health_module, "check_connection", lambda: None)

    response = real_client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_503_when_database_unavailable(real_client, monkeypatch) -> None:
    sentinel = "simulated failure — should never reach the HTTP response"

    def _raise() -> None:
        raise DatabaseConnectionError(sentinel)

    monkeypatch.setattr(health_module, "check_connection", _raise)

    response = real_client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body == {"status": "unavailable"}
    assert sentinel not in response.text


def test_readiness_returns_503_when_database_url_is_unconfigured(real_client, monkeypatch) -> None:
    # check_connection() itself raises ConfigurationError (not
    # DatabaseConnectionError) before attempting to connect at all when
    # DATABASE_URL is unset — readiness must treat that as "not ready" too,
    # not let it fall through to a generic 500.
    def _raise() -> None:
        raise ConfigurationError("DATABASE_URL environment variable is not set.")

    monkeypatch.setattr(health_module, "check_connection", _raise)

    response = real_client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_failure_response_has_no_business_envelope_or_diagnostics(
    real_client, monkeypatch
) -> None:
    def _raise() -> None:
        raise DatabaseConnectionError(
            "Could not connect to PostgreSQL at db.internal:5432/tourcrm (OperationalError)"
        )

    monkeypatch.setattr(health_module, "check_connection", _raise)

    response = real_client.get("/health/ready")
    body = response.json()

    assert set(body.keys()) == {"status"}
    for forbidden in ("data", "meta", "error", "request_id", "code", "details"):
        assert forbidden not in body
    for forbidden in ("password", "DATABASE_URL", "postgresql://", "Traceback", "db.internal"):
        assert forbidden not in response.text


def test_health_endpoints_are_outside_the_versioned_api(real_client) -> None:
    for path in ("/health/live", "/health/ready"):
        assert not path.startswith("/api/v1")
        assert real_client.get(path).status_code != 404
