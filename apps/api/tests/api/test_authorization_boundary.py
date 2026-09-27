"""Structural backend-authorization boundary check (Issue #184).

Frontend navigation is never a security boundary (ADR-0005,
auth-and-authorization.md §11/§24): every mounted route must itself
require an authenticated principal, except the small, documented set of
public endpoints below. This walks the REAL shipped app's route table (not
docs/05-api/endpoint-inventory.md), so a newly mounted route that forgets
`require_authenticated_principal` fails here before it can ship.

The runtime counterpart — every protected route actually answering an
anonymous request with 401 — lives in
tests/integration/test_backend_authorization_boundary.py.
"""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.deps import require_authenticated_principal
from app.main import app

# The only routes reachable without a session: health probes
# (endpoint-inventory.md §26), the public Auth flows
# (endpoint-inventory.md §1 "Public", auth-api.md), and logout, which is
# idempotent for an already-logged-out caller (auth-api.md §8).
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/verify-email"),
        ("POST", "/api/v1/auth/resend-verification"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/password-reset/request"),
        ("POST", "/api/v1/auth/password-reset/confirm"),
        ("POST", "/api/v1/auth/logout"),
    }
)


def _mounted_routes() -> list[tuple[str, str, APIRoute]]:
    return [
        (method, route.path, route)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods)
    ]


def _depends_on(dependant: Dependant, target: object) -> bool:
    return any(sub.call is target or _depends_on(sub, target) for sub in dependant.dependencies)


def protected_routes() -> list[tuple[str, str]]:
    """Every mounted (method, path) outside PUBLIC_ROUTES. Shared with the
    integration-level 401 test so both assert over the same route set."""
    return [
        (method, path)
        for method, path, _route in _mounted_routes()
        if (method, path) not in PUBLIC_ROUTES
    ]


def test_every_non_public_route_requires_an_authenticated_principal() -> None:
    unprotected = [
        f"{method} {path}"
        for method, path, route in _mounted_routes()
        if (method, path) not in PUBLIC_ROUTES
        and not _depends_on(route.dependant, require_authenticated_principal)
    ]
    assert unprotected == []


def test_public_route_allow_list_matches_mounted_routes_exactly() -> None:
    """A stale allow-list entry would silently exempt a future route that
    reuses the same path — every entry must name a real, mounted route."""
    mounted = {(method, path) for method, path, _route in _mounted_routes()}
    assert PUBLIC_ROUTES <= mounted


def test_public_routes_do_not_require_authentication() -> None:
    """Guards the allow-list in the other direction: an entry that has
    since become protected belongs in the protected set, not here."""
    routes = {(method, path): route for method, path, route in _mounted_routes()}
    assert [
        f"{method} {path}"
        for method, path in sorted(PUBLIC_ROUTES)
        if _depends_on(routes[(method, path)].dependant, require_authenticated_principal)
    ] == []


def test_protected_route_table_is_not_empty() -> None:
    # Sanity: the walk above must actually see the mounted domain routers.
    assert len(protected_routes()) >= 90
