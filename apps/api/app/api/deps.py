"""Dependency-injection boundary for authentication and authorization.

`get_current_principal` (Issue #33) is the ONLY source of an authenticated
identity: it reads the opaque session cookie, looks up the matching
`AuthenticatedSession` server-side (app.authentication.service.resolve_session)
and returns the `user_id`/`session_id` that row names — never a
client-supplied user id in any form (header, query param, body field).
Tests that need an authenticated context still override this dependency
directly (`app.dependency_overrides[get_current_principal] = ...`) with a
technical `CurrentPrincipal`, which remains a legitimate substitute for a
real login flow in tests per Issue #29 §10, and continues to work
unchanged now that this dependency is real.

`require_permission` (Issue #29) is the reusable authorization enforcement
point: it 401s an unauthenticated caller, otherwise returns an
`Authorizer` bound to the real (session, user id, permission) so the
endpoint can resolve the actual resource relationship and call
`.check(context)`. `require_authenticated_principal` is the plain
authentication-only counterpart for endpoints that need no permission
check at all (managing one's own session/account is inherent to being
that user, not an RBAC-gated resource) — this is the concrete proof that
authentication and authorization stay separate: the former only ever
identifies the caller, the latter is a distinct opt-in check layered on
top of it.
"""

import uuid
from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.authentication.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, require_matching_csrf_token
from app.authentication.service import resolve_session
from app.authorization.service import Authorizer
from app.db.session import get_db

SESSION_COOKIE_NAME = "session_token"


@dataclass(frozen=True)
class CurrentPrincipal:
    """The authenticated identity for the current request.

    docs/05-api/api-conventions.md §15 lists a richer canonical identity
    context (person id, club context) a future Issue may add here;
    `session_id` is included because several auth-api.md behaviors need
    it (the "current" flag in session listing, revoking "this" session on
    logout) without a second lookup.
    """

    user_id: uuid.UUID
    session_id: uuid.UUID


def get_current_principal(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> CurrentPrincipal | None:
    """Resolves the caller's identity purely from the opaque session
    cookie's server-side record. No parameter here or anywhere downstream
    accepts a caller-asserted user id — the only way to become a given
    CurrentPrincipal is to present the raw secret for a session that
    app.authentication.service.login already created for that exact user.
    """
    if not session_token:
        return None
    resolved = resolve_session(db, session_token)
    if resolved is None:
        return None
    return CurrentPrincipal(user_id=resolved.user_id, session_id=resolved.session_id)


def require_authenticated_principal(
    principal: CurrentPrincipal | None = Depends(get_current_principal),
) -> CurrentPrincipal:
    """401s an unauthenticated caller; otherwise returns the principal
    as-is. For endpoints that require only "some authenticated user",
    never a specific permission (see module docstring)."""
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return principal


def require_csrf_token(
    request: Request,
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE_NAME),
) -> None:
    """Dependency for every state-changing endpoint reachable with a
    session cookie (auth-api.md §19). Reads the header manually (rather
    than via a FastAPI Header(...) parameter) so this stays a single,
    reusable dependency regardless of the route's other parameters.
    """
    require_matching_csrf_token(csrf_cookie, request.headers.get(CSRF_HEADER_NAME))


def require_permission(permission_code: str):
    """FastAPI dependency factory: `Depends(require_permission("person.read"))`.

    401s when unauthenticated. Otherwise returns an `Authorizer` — it does
    NOT itself decide allow/deny, because whether a specific resource
    matches the caller's scope (self/children/own_groups/own_events)
    requires the real relationship, which only the endpoint's own domain
    logic can resolve safely. Call `authorizer.check(context)` after
    building that context; an `all`/`none`-only permission with no
    resource-specific context can call `authorizer.check()` with no
    arguments.
    """

    def dependency(
        principal: CurrentPrincipal | None = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ) -> Authorizer:
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )
        return Authorizer(session=db, user_id=principal.user_id, permission_code=permission_code)

    return dependency
