"""Request ID / correlation ID foundation (docs/05-api/api-contract.md §28).

The documented contract requires every request to have a request_id that is
present in logs and returned in error responses. The docs do not name a
specific inbound header for client-supplied correlation IDs; `X-Request-ID`
is used here as the de facto standard convention for this purpose — an
implementation-level choice, not a business/architecture decision. It is
also echoed back on every response (not only errors) so callers and tests
can observe both client-supplied and server-generated IDs consistently.
"""

import logging
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# Conservative allowlist: printable, no whitespace/control characters, bounded
# length. Guards against header/log injection from a client-supplied value;
# it does not assert any particular ID format/version.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = logging.getLogger("tourcrm.api")


def _is_valid_client_request_id(value: str | None) -> bool:
    if not value:
        return False
    return bool(_VALID_REQUEST_ID.match(value))


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        client_request_id = request.headers.get(REQUEST_ID_HEADER)
        if _is_valid_client_request_id(client_request_id):
            request_id = client_request_id
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        logger.info(
            "request.start method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            request_id,
        )

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def get_request_id(request: Request) -> str:
    """Read the current request's ID; falls back to a fresh one if the
    middleware was somehow not applied (e.g. a handler invoked directly).
    """
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())
