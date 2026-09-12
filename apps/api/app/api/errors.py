"""Canonical error contract per docs/03-architecture/adr/ADR-0014-api-response-envelope.md:

    {"error": {"code": "...", "message": "...", "details": {}, "request_id": "..."}}

No stack trace, DSN, credentials, tokens, environment secrets, or filesystem
paths are ever placed in an error response — unexpected exceptions are
logged server-side (with the request_id for correlation) and answered with a
generic message only.
"""

import logging
from typing import Any

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.request_context import get_request_id

logger = logging.getLogger("tourcrm.api")

_STATUS_CODE_TO_ERROR_CODE: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
    status.HTTP_429_TOO_MANY_REQUESTS: "too_many_requests",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class APIError(Exception):
    """Raise to produce the canonical error contract with an explicit,
    domain-appropriate machine-readable code instead of a generic mapping
    from the HTTP status alone. Foundation-only: no domain module is wired
    to this yet.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _error_envelope(
    *, code: str, message: str, details: dict[str, Any], request_id: str
) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details, request_id=request_id)
    ).model_dump()


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    request_id = get_request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(
            code=exc.code, message=exc.message, details=exc.details, request_id=request_id
        ),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = get_request_id(request)
    # error["loc"] is e.g. ("query", "count") or ("body", "email"); the first
    # element is the parameter source, not part of the field's identity.
    fields = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][-1]),
            "code": error["type"],
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_envelope(
            code="validation_error",
            message="Request validation failed",
            details={"fields": fields},
            request_id=request_id,
        ),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    request_id = get_request_id(request)
    code = _STATUS_CODE_TO_ERROR_CODE.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(code=code, message=message, details={}, request_id=request_id),
        headers=getattr(exc, "headers", None) or None,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id(request)
    # Full exception detail is server-side only, correlated by request_id.
    # Never included in the client-facing response (no message/type/traceback).
    logger.exception("unhandled_exception request_id=%s", request_id, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_envelope(
            code="internal_error",
            message="An unexpected error occurred",
            details={},
            request_id=request_id,
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
