"""HTTP-level enforcement tests for the Issue #29 authorization mechanism.

Uses a test-only probe app (same style as tests/api/conftest.py's
`build_probe_app`, per Issue #29 §13 — no fictitious domain endpoint is
added to the real app/main.py) that reuses the SAME production
error handlers, request-id middleware and `app.api.deps.require_permission`
dependency as the shipped app, so these tests exercise real production
enforcement code paths against a real PostgreSQL-backed authorization
decision.

`get_current_principal` is overridden per test via FastAPI's own
`app.dependency_overrides` mechanism to simulate an authenticated caller —
this is the "minimal test authentication abstraction" Issue #29 §10 calls
for; no real login/session code is added anywhere.
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
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal, require_permission
from app.api.errors import register_exception_handlers
from app.api.request_context import RequestIDMiddleware
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Person, User
from app.db.session import get_db, session_scope

from .conftest import requires_postgres

API_ROOT = Path(__file__).resolve().parents[2]


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


# --- Probe app --------------------------------------------------------------
#
# A fake, in-memory "resource directory" standing in for a real domain
# table (Issue #29 §13 forbids adding a real Person/Group/Event CRUD
# endpoint just to test this). The important part it demonstrates: the
# endpoint below never compares the client-supplied `resource_id` to the
# caller's own id directly — it looks up who really owns the resource from
# this trusted, server-side-only store, then asks the database who the
# authenticated caller's real Person is, and only THEN builds the
# ResourceContext. Changing the URL's `resource_id` cannot forge ownership.
_FAKE_RESOURCE_OWNERS: dict[str, uuid.UUID] = {}

probe_router = APIRouter()


@probe_router.get("/probe/authz/global-resource")
def get_global_resource(
    authorizer: Authorizer = Depends(require_permission("widget.manage")),
) -> dict:
    authorizer.check()
    return {"status": "ok"}


@probe_router.get("/probe/authz/self-resource/{resource_id}")
def get_self_scoped_resource(
    resource_id: str,
    authorizer: Authorizer = Depends(require_permission("widget.read")),
    db: Session = Depends(get_db),
) -> dict:
    owner_person_id = _FAKE_RESOURCE_OWNERS.get(resource_id)
    principal_person_id = db.execute(
        select(User.person_id).where(User.id == authorizer.user_id)
    ).scalar_one()
    authorizer.check(ResourceContext(is_self=(owner_person_id == principal_person_id)))
    return {"resource_id": resource_id}


def _build_probe_app() -> FastAPI:
    app = FastAPI(title="TourCRM Authorization Probe")
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(probe_router)
    return app


@pytest.fixture
def probe_app() -> FastAPI:
    _FAKE_RESOURCE_OWNERS.clear()
    app = _build_probe_app()
    yield app
    app.dependency_overrides.clear()
    _FAKE_RESOURCE_OWNERS.clear()


@pytest.fixture
def probe_client(probe_app: FastAPI) -> TestClient:
    return TestClient(probe_app, raise_server_exceptions=False)


def _authenticate_as(probe_app: FastAPI, user_id: uuid.UUID) -> None:
    probe_app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id
    )


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {"last_name": "Ivanova", "first_name": "Anna"}
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_user(person: Person, **overrides: object) -> User:
    defaults: dict[str, object] = {
        "person": person,
        "login_identifier": f"user-{uuid.uuid4().hex[:8]}@example.com",
        "status": "active",
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def _make_role(**overrides: object) -> Role:
    defaults: dict[str, object] = {"code": f"role-{uuid.uuid4().hex[:8]}", "name": "Test Role"}
    defaults.update(overrides)
    return Role(**defaults)  # type: ignore[arg-type]


def _make_permission(**overrides: object) -> Permission:
    defaults: dict[str, object] = {"code": f"resource-{uuid.uuid4().hex[:8]}.read"}
    defaults.update(overrides)
    return Permission(**defaults)  # type: ignore[arg-type]


def _grant_permission_via_role(
    user_id: uuid.UUID, permission_code: str, scope_type: str = "all"
) -> None:
    with session_scope() as session:
        role = _make_role()
        permission = _make_permission(code=permission_code)
        session.add_all([role, permission])
        session.commit()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type=scope_type))
        session.commit()


# --- Authentication boundary -------------------------------------------


@requires_postgres
def test_unauthenticated_request_gets_401(probe_client: TestClient) -> None:
    response = probe_client.get("/probe/authz/global-resource")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"


@requires_postgres
def test_authenticated_request_without_permission_gets_403(
    probe_app: FastAPI, probe_client: TestClient
) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id

    _authenticate_as(probe_app, user_id)
    response = probe_client.get("/probe/authz/global-resource")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_authenticated_request_with_granted_permission_gets_200(
    probe_app: FastAPI, probe_client: TestClient
) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission_via_role(user_id, "widget.manage", scope_type="all")

    _authenticate_as(probe_app, user_id)
    response = probe_client.get("/probe/authz/global-resource")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Security: response does not leak policy information -----------------


@requires_postgres
def test_401_response_does_not_leak_internal_details(probe_client: TestClient) -> None:
    response = probe_client.get("/probe/authz/global-resource")

    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message", "details", "request_id"}
    assert body["error"]["details"] == {}
    for forbidden in ("widget.manage", "role", "permission_id", "Traceback", "sqlalchemy"):
        assert forbidden not in response.text


@requires_postgres
def test_403_response_does_not_leak_internal_details(
    probe_app: FastAPI, probe_client: TestClient
) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id

    _authenticate_as(probe_app, user_id)
    response = probe_client.get("/probe/authz/global-resource")

    body = response.json()
    assert body["error"]["details"] == {}
    for forbidden in ("widget.manage", "role", "permission_id", str(user_id), "Traceback"):
        assert forbidden not in response.text


# --- Security: resource ID cannot be used to impersonate ownership --------


@requires_postgres
def test_changing_the_resource_id_does_not_grant_access_to_someone_elses_resource(
    probe_app: FastAPI, probe_client: TestClient
) -> None:
    with session_scope() as session:
        owner_person = _make_person(first_name="Owner")
        caller_person = _make_person(first_name="Caller")
        caller_user = _make_user(caller_person)
        session.add_all([owner_person, caller_person, caller_user])
        session.commit()
        caller_user_id = caller_user.id
        caller_person_id = caller_person.id
        owner_person_id = owner_person.id
    _grant_permission_via_role(caller_user_id, "widget.read", scope_type="self")

    _FAKE_RESOURCE_OWNERS["mine"] = caller_person_id
    _FAKE_RESOURCE_OWNERS["someone-elses"] = owner_person_id

    _authenticate_as(probe_app, caller_user_id)

    own_response = probe_client.get("/probe/authz/self-resource/mine")
    assert own_response.status_code == 200

    # Simply changing the path parameter to another real resource id must
    # not grant access — the mechanism compares real backend-resolved
    # ownership, not the raw id.
    others_response = probe_client.get("/probe/authz/self-resource/someone-elses")
    assert others_response.status_code == 403

    # An id that was never registered as anyone's resource must also deny,
    # not error or default-allow.
    unknown_response = probe_client.get("/probe/authz/self-resource/does-not-exist")
    assert unknown_response.status_code == 403


@requires_postgres
def test_frontend_visibility_is_irrelevant_direct_api_access_is_still_enforced(
    probe_client: TestClient,
) -> None:
    # No UI/frontend is involved in these tests at all — every request goes
    # directly to the API, exactly as a client bypassing the frontend
    # would, and enforcement is unaffected either way (Issue #29 §9/§16:
    # frontend visibility is never a security boundary).
    response = probe_client.get("/probe/authz/global-resource")

    assert response.status_code == 401
