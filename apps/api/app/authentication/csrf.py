"""CSRF protection for the cookie-based session (auth-api.md §19: "if
cookie-based auth is used, state-changing endpoints must be protected
against CSRF").

Double-submit cookie pattern: login sets a CSRF token in a
non-HttpOnly cookie (so it is readable by same-origin JavaScript) and
returns it in the login response body; every state-changing authenticated
request must echo it back in the `X-CSRF-Token` header. A cross-site
request forged by a third-party page carries the session cookie
automatically but has no way to read or set that header, so it cannot
supply a matching value.
"""

import hmac

from fastapi import HTTPException, status

from app.authentication.tokens import generate_token

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def generate_csrf_token() -> str:
    return generate_token()


def require_matching_csrf_token(csrf_cookie: str | None, csrf_header: str | None) -> None:
    """Plain helper (no FastAPI parameter parsing of its own) — the
    caller (see app.api.deps) reads the `csrf_token` cookie and the
    `X-CSRF-Token` header via normal FastAPI parameter declarations and
    passes both values in. Constant-time comparison avoids a timing
    side-channel on the token itself.
    """
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token"
        )
