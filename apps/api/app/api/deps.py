"""Dependency-injection boundary for authentication and authorization
(Issue #29).

Authentication itself is out of scope for Issue #29 (ADR-0009 accepts the
baseline architecture, but no login/session mechanism is implemented in
code yet — see docs/03-architecture/adr/ADR-0017-identity-documentation-canonicalization.md
§6). `get_current_principal` therefore still returns `None` in production;
nothing here performs real authentication. Tests that need an authenticated
context override this dependency directly (`app.dependency_overrides[get_current_principal] = ...`)
with a technical `CurrentPrincipal`, per Issue #29 §10 — deliberately not a
real login flow.

`require_permission` is the reusable FastAPI enforcement point: it 401s an
unauthenticated caller, otherwise returns an `Authorizer` bound to the real
(session, user id, permission) so the endpoint can resolve the actual
resource relationship and call `.check(context)` — see
app.authorization.service and app.authorization.context for why that
resolution is deliberately not done here from a client-supplied ID.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.authorization.service import Authorizer
from app.db.session import get_db


@dataclass(frozen=True)
class CurrentPrincipal:
    """Minimal authenticated identity context.

    Left intentionally minimal: docs/05-api/api-conventions.md §15 lists a
    richer canonical identity context (person id, club context, session
    metadata) that a future authentication Issue will add here; Issue #29
    only needs the authenticated user's id to query UserRoleAssignment.
    """

    user_id: uuid.UUID


def get_current_principal() -> CurrentPrincipal | None:
    """Not implemented: no authentication mechanism exists in code yet.
    Returns None so every caller is treated as unauthenticated until this
    is replaced by real authentication — no endpoint should treat a
    non-None value as a security guarantee before that.
    """
    return None


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
