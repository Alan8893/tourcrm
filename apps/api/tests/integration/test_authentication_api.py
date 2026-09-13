"""HTTP-level integration tests for /api/v1/auth (Issue #33), against the
REAL shipped app (these are real production endpoints, unlike Issue #29's
test-only probe app) and a real PostgreSQL database.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal, require_permission
from app.authorization.service import Authorizer
from app.db.authentication import AuthenticatedSession
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

API_ROOT = Path(__file__).resolve().parents[2]

_PASSWORD = "correcthorsebattery"


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _migrated_schema(database_url: str) -> None:
    result = _run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _register(client: TestClient, email: str = "alice@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "first_name": "Alice",
            "last_name": "Smith",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _activate(email: str = "alice@example.com") -> None:
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.login_identifier == email)
        ).scalar_one()
        user.status = "active"
        session.commit()


def _register_activate_and_login(client: TestClient, email: str = "alice@example.com") -> dict:
    _register(client, email)
    _activate(email)
    response = client.post("/api/v1/auth/login", json={"identifier": email, "password": _PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def _csrf_headers(client: TestClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("csrf_token")}


# --- Registration ---------------------------------------------------------


@requires_postgres
def test_register_returns_201_with_a_pending_user_and_no_secrets(client: TestClient) -> None:
    body = _register(client)
    assert body["user"]["status"] == "pending"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert _PASSWORD not in client.cookies  # sanity: nothing plaintext leaked into cookies


@requires_postgres
def test_register_conflicts_on_a_duplicate_identifier(client: TestClient) -> None:
    _register(client, "dup@example.com")
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "DUP@example.com",
            "password": _PASSWORD,
            "first_name": "Someone",
            "last_name": "Else",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


@requires_postgres
def test_register_rejects_a_weak_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "a", "first_name": "A", "last_name": "B"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "weak_password"


# --- Login / account states -------------------------------------------------


@requires_postgres
def test_login_succeeds_and_sets_session_and_csrf_cookies(client: TestClient) -> None:
    _register_activate_and_login(client)
    assert "session_token" in client.cookies
    assert "csrf_token" in client.cookies


@requires_postgres
def test_login_response_never_contains_the_password(client: TestClient) -> None:
    body = _register_activate_and_login(client)
    assert "password" not in str(body)


@requires_postgres
def test_login_rejects_wrong_password_with_generic_error(client: TestClient) -> None:
    _register(client, "wrongpw@example.com")
    _activate("wrongpw@example.com")
    response = client.post(
        "/api/v1/auth/login", json={"identifier": "wrongpw@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@requires_postgres
def test_login_rejects_unknown_identifier_with_the_identical_error(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"identifier": "nobody@example.com", "password": "whatever1"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@requires_postgres
def test_login_response_bodies_are_identical_for_wrong_password_and_unknown_identifier(
    client: TestClient,
) -> None:
    """Enumeration resistance: an attacker must not be able to distinguish
    "this account exists but the password is wrong" from "this account
    does not exist" by comparing response bodies."""
    _register(client, "known@example.com")
    _activate("known@example.com")

    wrong_password = client.post(
        "/api/v1/auth/login", json={"identifier": "known@example.com", "password": "wrong"}
    )
    unknown_identifier = client.post(
        "/api/v1/auth/login", json={"identifier": "unknown@example.com", "password": "wrong"}
    )
    assert wrong_password.status_code == unknown_identifier.status_code == 401
    body_a = wrong_password.json()
    body_b = unknown_identifier.json()
    body_a["error"].pop("request_id")
    body_b["error"].pop("request_id")
    assert body_a == body_b


@requires_postgres
def test_login_for_pending_account_returns_a_distinct_but_safe_error(client: TestClient) -> None:
    _register(client, "stillpending@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": "stillpending@example.com", "password": _PASSWORD},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "account_pending"
    assert body["error"]["details"] == {}


# --- /me and session listing ----------------------------------------------


@requires_postgres
def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@requires_postgres
def test_me_returns_the_authenticated_users_own_identity(client: TestClient) -> None:
    login_body = _register_activate_and_login(client, "selfcheck@example.com")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["user"]["id"] == login_body["user"]["id"]


@requires_postgres
def test_sessions_listing_never_contains_a_token_or_hash_field(client: TestClient) -> None:
    _register_activate_and_login(client, "listsessions@example.com")
    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == 200
    body_text = response.text
    for forbidden in ("session_token_hash", "token_hash", client.cookies["session_token"]):
        assert forbidden not in body_text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["is_current"] is True


def test_sessions_listing_uses_the_canonical_collection_envelope(client: TestClient) -> None:
    """ADR-0014 / api-contract.md §7: every collection response is
    `{"items": [...], "pagination": {"page","page_size","total","pages"}}`
    — never a bare JSON array."""
    _register_activate_and_login(client, "envelope@example.com")
    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert isinstance(body["items"], list)
    assert body["pagination"] == {"page": 1, "page_size": 50, "total": 1, "pages": 1}
    assert "data" not in body
    assert "meta" not in body


def test_sessions_listing_pagination_reflects_page_size(client: TestClient) -> None:
    _register_activate_and_login(client, "paginated@example.com")
    # A second session for the same user (a second "browser").
    TestClient(app).post(
        "/api/v1/auth/login",
        json={"identifier": "paginated@example.com", "password": _PASSWORD},
    )

    first_page = client.get("/api/v1/auth/sessions", params={"page": 1, "page_size": 1})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 1
    assert first_body["pagination"] == {"page": 1, "page_size": 1, "total": 2, "pages": 2}

    second_page = client.get("/api/v1/auth/sessions", params={"page": 2, "page_size": 1})
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["pagination"]["page"] == 2
    assert second_body["items"][0]["id"] != first_body["items"][0]["id"]


@pytest.mark.parametrize("new_status", ["pending", "locked", "suspended", "disabled", "archived"])
@requires_postgres
def test_a_session_stops_authenticating_once_the_account_leaves_active(
    client: TestClient, new_status: str
) -> None:
    """End-to-end HTTP proof of the review finding: an administrator
    changing the account's state (directly in the DB here — there is no
    admin endpoint in this Issue's scope) must immediately invalidate the
    existing session for every subsequent request."""
    login_body = _register_activate_and_login(client, f"stateflip-{new_status}@example.com")
    assert client.get("/api/v1/auth/me").status_code == 200

    with session_scope() as session:
        user = session.get(User, login_body["user"]["id"])
        user.status = new_status
        session.commit()

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@requires_postgres
def test_restoring_the_account_does_not_resurrect_the_old_session_over_http(
    client: TestClient,
) -> None:
    login_body = _register_activate_and_login(client, "restored@example.com")

    with session_scope() as session:
        user = session.get(User, login_body["user"]["id"])
        user.status = "suspended"
        session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401

    with session_scope() as session:
        user = session.get(User, login_body["user"]["id"])
        user.status = "active"
        session.commit()

    # The browser still carries the OLD (now-revoked) session cookie —
    # restoring the account must not make it valid again.
    assert client.get("/api/v1/auth/me").status_code == 401


# --- Logout / revoke / logout-all + CSRF ------------------------------------


@requires_postgres
def test_logout_without_csrf_token_is_rejected(client: TestClient) -> None:
    _register_activate_and_login(client, "logoutcsrf@example.com")
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    # The session must still be usable — the rejected logout must not
    # have silently revoked it.
    assert client.get("/api/v1/auth/me").status_code == 200


@requires_postgres
def test_logout_with_csrf_token_invalidates_the_session(client: TestClient) -> None:
    _register_activate_and_login(client, "logoutok@example.com")
    response = client.post("/api/v1/auth/logout", headers=_csrf_headers(client))
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


@requires_postgres
def test_logout_is_idempotent(client: TestClient) -> None:
    _register_activate_and_login(client, "idempotent@example.com")
    headers = _csrf_headers(client)
    first = client.post("/api/v1/auth/logout", headers=headers)
    second = client.post("/api/v1/auth/logout", headers=headers)
    assert first.status_code == second.status_code == 200


@requires_postgres
def test_logout_without_any_session_is_a_noop_success(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200


@requires_postgres
def test_revoking_a_session_requires_csrf(client: TestClient) -> None:
    _register_activate_and_login(client, "revokecsrf@example.com")
    session_id = client.get("/api/v1/auth/sessions").json()["items"][0]["id"]
    response = client.delete(f"/api/v1/auth/sessions/{session_id}")
    assert response.status_code == 403


@requires_postgres
def test_revoking_ones_own_session_by_id_invalidates_it(client: TestClient) -> None:
    _register_activate_and_login(client, "revokeself@example.com")
    session_id = client.get("/api/v1/auth/sessions").json()["items"][0]["id"]
    response = client.delete(
        f"/api/v1/auth/sessions/{session_id}", headers=_csrf_headers(client)
    )
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


@requires_postgres
def test_revoking_a_nonexistent_session_id_returns_404(client: TestClient) -> None:
    _register_activate_and_login(client, "revoke404@example.com")
    response = client.delete(
        "/api/v1/auth/sessions/00000000-0000-0000-0000-000000000000",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


@requires_postgres
def test_cannot_revoke_another_users_session_by_guessing_its_id(client: TestClient) -> None:
    # Log in as Bob, capture his session id via /me's own listing (never
    # trust a client-supplied id blindly, but the test needs the real one
    # to prove it is STILL rejected for a different caller).
    _register_activate_and_login(client, "bobtarget@example.com")
    bob_session_id = client.get("/api/v1/auth/sessions").json()["items"][0]["id"]
    client.post("/api/v1/auth/logout", headers=_csrf_headers(client))

    _register_activate_and_login(client, "eveattacker@example.com")
    response = client.delete(
        f"/api/v1/auth/sessions/{bob_session_id}", headers=_csrf_headers(client)
    )
    assert response.status_code == 404  # not 200/204 — Eve's request had no effect

    # Bob's session must genuinely still be revoked from his own earlier
    # logout — not un-revoked or otherwise touched by Eve's attempt.
    with session_scope() as session:
        row = session.get(AuthenticatedSession, bob_session_id)
        assert row.status == "revoked"


@requires_postgres
def test_logout_all_requires_csrf(client: TestClient) -> None:
    _register_activate_and_login(client, "logoutallcsrf@example.com")
    response = client.post("/api/v1/auth/logout-all")
    assert response.status_code == 403


@requires_postgres
def test_logout_all_revokes_every_session(client: TestClient) -> None:
    _register_activate_and_login(client, "multisession@example.com")
    first_client_cookies = dict(client.cookies)
    # A second "browser" logging in as the same user.
    second_client = TestClient(app, raise_server_exceptions=True)
    second_client.post(
        "/api/v1/auth/login",
        json={"identifier": "multisession@example.com", "password": _PASSWORD},
    )

    response = client.post("/api/v1/auth/logout-all", headers=_csrf_headers(client))
    assert response.status_code == 200

    assert client.get("/api/v1/auth/me").status_code == 401
    assert second_client.get("/api/v1/auth/me").status_code == 401
    assert first_client_cookies  # sanity: the first client really had cookies to begin with


# --- Password reset / change -------------------------------------------------


@requires_postgres
def test_password_reset_request_is_identical_for_existing_and_unknown_identifiers(
    client: TestClient,
) -> None:
    _register(client, "resetme@example.com")
    known = client.post(
        "/api/v1/auth/password-reset/request", json={"identifier": "resetme@example.com"}
    )
    unknown = client.post(
        "/api/v1/auth/password-reset/request", json={"identifier": "nosuchaccount@example.com"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


@requires_postgres
def test_password_reset_confirm_rejects_an_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "newpassword123"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_or_expired_token"


@requires_postgres
def test_change_password_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": _PASSWORD, "new_password": "newpassword123"},
    )
    assert response.status_code == 401


@requires_postgres
def test_change_password_requires_csrf(client: TestClient) -> None:
    _register_activate_and_login(client, "changepwcsrf@example.com")
    response = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": _PASSWORD, "new_password": "newpassword123"},
    )
    assert response.status_code == 403


@requires_postgres
def test_change_password_rejects_the_wrong_current_password(client: TestClient) -> None:
    _register_activate_and_login(client, "changepwwrong@example.com")
    response = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "wrong-password", "new_password": "newpassword123"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "incorrect_current_password"


@requires_postgres
def test_change_password_succeeds_with_the_correct_current_password(client: TestClient) -> None:
    _register_activate_and_login(client, "changepwok@example.com")
    response = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": _PASSWORD, "new_password": "newpassword123"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200

    fresh_client = TestClient(app, raise_server_exceptions=True)
    relogin = fresh_client.post(
        "/api/v1/auth/login",
        json={"identifier": "changepwok@example.com", "password": "newpassword123"},
    )
    assert relogin.status_code == 200


# --- Regression: client-supplied ids cannot select the principal ----------


@requires_postgres
def test_spoofed_headers_and_query_params_cannot_override_the_session_derived_principal(
    client: TestClient,
) -> None:
    alice = _register_activate_and_login(client, "alice-real@example.com")
    _register_activate_and_login(TestClient(app), "carol-victim@example.com")
    carol_id = None
    with session_scope() as session:
        carol = session.execute(
            select(User).where(User.login_identifier == "carol-victim@example.com")
        ).scalar_one()
        carol_id = str(carol.id)

    # Alice's own session cookie is what the client actually carries;
    # nothing else in the request should be able to make the server treat
    # this request as Carol's.
    response = client.get(
        "/api/v1/auth/me",
        headers={"X-User-Id": carol_id, "X-Impersonate": carol_id},
        params={"user_id": carol_id},
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == alice["user"]["id"]
    assert response.json()["user"]["id"] != carol_id


@requires_postgres
def test_an_unrecognized_session_cookie_value_is_never_treated_as_authenticated(
    client: TestClient,
) -> None:
    client.cookies.set("session_token", "a-completely-fabricated-token-value")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


# --- Regression: authentication and authorization stay separate -----------
#
# These two tests need a probe endpoint alongside the real auth routes, so
# they build their own FastAPI app (same middleware/error-handler wiring as
# app.main, matching tests/api/conftest.py's existing probe-app pattern)
# instead of mutating the shared `app` singleton other tests in this
# process also import.


def _build_app_with_probe() -> FastAPI:
    from app.api.errors import register_exception_handlers
    from app.api.request_context import RequestIDMiddleware
    from app.api.v1.router import router as v1_router

    probe_app = FastAPI()
    probe_app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(probe_app)
    probe_app.include_router(v1_router)

    probe_router = APIRouter()

    @probe_router.get("/probe/authz-authn-separation")
    def _probe(authorizer: Authorizer = Depends(require_permission("widget.read"))) -> dict:
        authorizer.check()
        return {"status": "ok"}

    probe_app.include_router(probe_router)
    return probe_app


@requires_postgres
def test_authentication_and_authorization_are_evaluated_independently() -> None:
    """Uses the REAL session-cookie authentication (Issue #33) together
    with the existing require_permission dependency (Issue #29) on a
    dedicated probe route, proving the 401 (unauthenticated) -> 403
    (authenticated, no grant) -> 200 (authenticated + granted) progression
    is driven by two independent mechanisms, neither aware of the other.
    """
    probe_client = TestClient(_build_app_with_probe(), raise_server_exceptions=True)

    # 1. No session at all -> 401 (authentication boundary).
    unauthenticated = probe_client.get("/probe/authz-authn-separation")
    assert unauthenticated.status_code == 401

    # 2. Authenticated, but no RolePermission grant exists -> 403
    # (authorization boundary; RBAC untouched by this Issue).
    _register_activate_and_login(probe_client, "separation@example.com")
    no_grant = probe_client.get("/probe/authz-authn-separation")
    assert no_grant.status_code == 403

    # 3. Grant the permission via the real, unmodified Issue #29 tables ->
    # 200. Authentication did not change; only authorization data did.
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.login_identifier == "separation@example.com")
        ).scalar_one()
        role = Role(code="probe-role", name="Probe Role")
        permission = Permission(code="widget.read")
        session.add_all([role, permission])
        session.flush()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all"))
        session.commit()

    granted = probe_client.get("/probe/authz-authn-separation")
    assert granted.status_code == 200

    # 4. Logging out removes the authenticated identity entirely, even
    # though the RolePermission grant still exists — authentication is
    # what changed, not authorization data.
    probe_client.post("/api/v1/auth/logout", headers=_csrf_headers(probe_client))
    logged_out = probe_client.get("/probe/authz-authn-separation")
    assert logged_out.status_code == 401


@requires_postgres
def test_dependency_override_technical_principal_still_works_for_authorization_tests() -> None:
    """Issue #29 §10's test-only override path must keep working
    unchanged now that get_current_principal is a real dependency in
    production — tests of pure authorization logic should not need a real
    login flow."""
    probe_app = _build_app_with_probe()
    technical_user_id = uuid.uuid4()
    probe_app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=technical_user_id, session_id=uuid.uuid4()
    )
    probe_client = TestClient(probe_app, raise_server_exceptions=True)

    response = probe_client.get("/probe/authz-authn-separation")
    assert response.status_code == 403  # authenticated (overridden), no grant
