"""Liveness/readiness endpoints (Issue #10).

Operational, not versioned business API — per docs/05-api/api-conventions.md
§2 ("Operational endpoints such as health checks may live outside the
versioned business API"), these are mounted directly on the app, not under
app.api.v1.router. URLs match the existing canonical naming in
docs/05-api/endpoint-inventory.md §26: `GET /health/live`, `GET /health/ready`.

Responses are minimal operational JSON — not the business error/collection
envelope (ADR-0014). HTTP status is the primary signal; the body never
carries exception text, DSN, credentials, or any other diagnostic detail.
"""

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import ConfigurationError
from app.db.errors import DatabaseConnectionError
from app.db.session import check_connection

logger = logging.getLogger("tourcrm.api")

router = APIRouter()


@router.get("/health/live", include_in_schema=True)
def liveness() -> dict[str, str]:
    """The application process is up and can handle an HTTP request.

    Deliberately does not touch PostgreSQL or any other dependency — must
    stay cheap and reliable. If this function runs at all, it succeeds.
    """
    return {"status": "ok"}


def _not_ready() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "unavailable"},
    )


@router.get("/health/ready", include_in_schema=True)
def readiness() -> JSONResponse:
    """Verifies the one required technical dependency on this foundation
    (PostgreSQL) via the existing app.db.session.check_connection(), which
    already sanitizes failures (see app/db/errors.py::DatabaseConnectionError)
    — no second/parallel DB-probe mechanism is introduced here.

    Any failure to establish connectivity — including DATABASE_URL being
    unset (ConfigurationError), which check_connection() raises before it
    even attempts to connect — means the application is not ready, so it is
    reported as 503, not as a generic unhandled-exception 500.
    """
    try:
        check_connection()
    except (DatabaseConnectionError, ConfigurationError) as exc:
        # Both messages are already known-safe to log (no DSN/credentials —
        # see check_connection and ConfigurationError). The HTTP response
        # itself never carries either, only the status.
        logger.warning("readiness check failed: %s", exc)
        return _not_ready()
    except Exception as exc:  # readiness must never 500 on an unexpected error
        # Defensive catch-all: an unreviewed future exception type might not
        # have a guaranteed-safe message, so only its class name is logged.
        logger.warning("readiness check failed: %s", type(exc).__name__)
        return _not_ready()
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})
