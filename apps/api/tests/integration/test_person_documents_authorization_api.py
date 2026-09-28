"""Participant Document API authorization (people-api.md §32, PO decisions
PD-1 = B and PD-2 = A), across all seven person-document endpoints.

- Read (list / detail / download): `person.read` + `document.read`.
- Mutation (create / patch / replace / revoke): `person.update` +
  `document.manage`.
- The Person must be within the scope of BOTH permissions (intersection).
- Authorization before existence: a missing required permission is 403
  regardless of whether the Person exists; only after both permissions are
  held does a missing / out-of-scope Person get 404, and a missing or
  foreign Document the existing `document_not_found` 404.

Self-contained factories, per this codebase's convention of not importing
helpers across test files.
"""

import datetime
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres

READ = ("person.read", "document.read")
MANAGE = ("person.update", "document.manage")


@pytest.fixture
def client(tmp_path) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=tmp_path)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _grant(
    user_id: uuid.UUID, permission_code: str, *, club_id: uuid.UUID, scope_type: str = "all"
) -> None:
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(code=permission_code)
            session.add(permission)
            session.commit()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.commit()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _new_user(session) -> uuid.UUID:  # type: ignore[no-untyped-def]
    person = Person(last_name="Actor", first_name=f"A-{uuid.uuid4().hex[:8]}")
    user = User(
        person=person,
        login_identifier=f"actor-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add_all([person, user])
    session.flush()
    return user.id


@dataclass(frozen=True)
class _World:
    club_id: uuid.UUID
    caller_id: uuid.UUID
    person_id: uuid.UUID
    document_id: uuid.UUID
    # A second Person in the same Club with its own Document — used for the
    # "document of another Person" case.
    other_person_id: uuid.UUID
    other_document_id: uuid.UUID


def _upload(client: TestClient, person_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/persons/{person_id}/documents",
        files={"file": ("cert.pdf", b"%PDF-1.4 fake certificate bytes", "application/pdf")},
        data={"document_type": "medical_certificate"},
        headers=_csrf_headers(client),
    )


def _seed(client: TestClient) -> _World:
    """Two target Persons (each with an active ClubMembership and one
    Document, uploaded by a separate fully-authorized seeding actor) and a
    caller with no grants at all."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        people = []
        for _ in range(2):
            person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
            session.add(person)
            session.flush()
            session.add(
                ClubMembership(
                    club_id=club.id,
                    person_id=person.id,
                    membership_type="member",
                    status="active",
                    joined_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
                )
            )
            people.append(person.id)
        seeder_id = _new_user(session)
        caller_id = _new_user(session)
        session.commit()
        club_id = club.id

    for code in (*READ, *MANAGE):
        _grant(seeder_id, code, club_id=club_id)
    _authenticate_as(seeder_id)
    document_ids = []
    for person_id in people:
        response = _upload(client, person_id)
        assert response.status_code == 201, response.text
        document_ids.append(uuid.UUID(response.json()["id"]))

    return _World(
        club_id=club_id,
        caller_id=caller_id,
        person_id=people[0],
        document_id=document_ids[0],
        other_person_id=people[1],
        other_document_id=document_ids[1],
    )


# --- endpoint table -------------------------------------------------------------

SendFn = Callable[[TestClient, uuid.UUID, uuid.UUID], object]


@dataclass(frozen=True)
class _Endpoint:
    name: str
    permissions: tuple[str, str]  # (Person permission, Document permission)
    send: SendFn  # (client, person_id, document_id)
    success_status: int
    has_document_id: bool = True


def _list(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return client.get(f"/api/v1/persons/{person_id}/documents")


def _detail(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return client.get(f"/api/v1/persons/{person_id}/documents/{document_id}")


def _download(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")


def _create(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return _upload(client, person_id)


def _patch(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return client.patch(
        f"/api/v1/persons/{person_id}/documents/{document_id}",
        json={"issued_at": "2026-01-01T00:00:00+00:00"},
        headers=_csrf_headers(client),
    )


def _replace(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/persons/{person_id}/documents/{document_id}/replace",
        files={"file": ("cert-v2.pdf", b"%PDF-1.4 v2", "application/pdf")},
        headers=_csrf_headers(client),
    )


def _revoke(client, person_id, document_id):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/persons/{person_id}/documents/{document_id}/revoke",
        headers=_csrf_headers(client),
    )


_ENDPOINTS = [
    _Endpoint("list", READ, _list, 200, has_document_id=False),
    _Endpoint("detail", READ, _detail, 200),
    _Endpoint("download", READ, _download, 200),
    _Endpoint("create", MANAGE, _create, 201, has_document_id=False),
    _Endpoint("patch", MANAGE, _patch, 200),
    _Endpoint("replace", MANAGE, _replace, 201),
    _Endpoint("revoke", MANAGE, _revoke, 200),
]
_IDS = [endpoint.name for endpoint in _ENDPOINTS]
_READ_ENDPOINTS = [e for e in _ENDPOINTS if e.permissions == READ]
_MANAGE_ENDPOINTS = [e for e in _ENDPOINTS if e.permissions == MANAGE]
_DOC_ENDPOINTS = [e for e in _ENDPOINTS if e.has_document_id]


def _call_as(
    client: TestClient,
    world: _World,
    endpoint: _Endpoint,
    grants: dict[str, str],
    *,
    person_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
):  # type: ignore[no-untyped-def]
    """`grants` maps permission code -> scope_type for the caller."""
    for code, scope_type in grants.items():
        _grant(world.caller_id, code, club_id=world.club_id, scope_type=scope_type)
    _authenticate_as(world.caller_id)
    return endpoint.send(
        client,
        person_id if person_id is not None else world.person_id,
        document_id if document_id is not None else world.document_id,
    )


def _assert_forbidden(response) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def _assert_person_not_found(response) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == 404, response.text


# --- 1-4: one permission alone is never enough ------------------------------------


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_document_permission_alone_is_forbidden(client: TestClient, endpoint: _Endpoint) -> None:
    """(1) only document.read / (2) only document.manage -> 403."""
    world = _seed(client)
    _, document_permission = endpoint.permissions
    _assert_forbidden(_call_as(client, world, endpoint, {document_permission: "all"}))


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_person_permission_alone_is_forbidden(client: TestClient, endpoint: _Endpoint) -> None:
    """(3) only person.read / (4) only person.update -> 403."""
    world = _seed(client)
    person_permission, _ = endpoint.permissions
    _assert_forbidden(_call_as(client, world, endpoint, {person_permission: "all"}))


# --- 5: both permissions, matching scope -> success --------------------------------


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_both_permissions_in_scope_succeed(client: TestClient, endpoint: _Endpoint) -> None:
    world = _seed(client)
    grants = dict.fromkeys(endpoint.permissions, "all")
    response = _call_as(client, world, endpoint, grants)
    assert response.status_code == endpoint.success_status, response.text  # type: ignore[attr-defined]


# --- 6-7: scope is the intersection of both permissions -----------------------------


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_person_outside_person_permission_scope_is_404(
    client: TestClient, endpoint: _Endpoint
) -> None:
    """(6) Both permissions held; the Document permission covers the Person
    (`all`) but the Person permission does not (`self`, caller is not the
    target) -> 404."""
    world = _seed(client)
    person_permission, document_permission = endpoint.permissions
    response = _call_as(
        client, world, endpoint, {person_permission: "self", document_permission: "all"}
    )
    _assert_person_not_found(response)


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_person_outside_document_permission_scope_is_404(
    client: TestClient, endpoint: _Endpoint
) -> None:
    """(7) Both permissions held; the Person permission covers the Person
    (`all`) but the Document permission does not (`self`) -> 404."""
    world = _seed(client)
    person_permission, document_permission = endpoint.permissions
    response = _call_as(
        client, world, endpoint, {person_permission: "all", document_permission: "self"}
    )
    _assert_person_not_found(response)


# --- 8-10: authorization before existence --------------------------------------------


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_no_permission_same_403_for_existing_and_missing_person(
    client: TestClient, endpoint: _Endpoint
) -> None:
    """(8) existing Person -> 403, (9) nonexistent Person -> the same 403."""
    world = _seed(client)
    existing = _call_as(client, world, endpoint, {})
    missing = _call_as(client, world, endpoint, {}, person_id=uuid.uuid4())

    _assert_forbidden(existing)
    _assert_forbidden(missing)
    existing_error = {k: v for k, v in existing.json()["error"].items() if k != "request_id"}  # type: ignore[attr-defined]
    missing_error = {k: v for k, v in missing.json()["error"].items() if k != "request_id"}  # type: ignore[attr-defined]
    assert existing_error == missing_error


@requires_postgres
@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_IDS)
def test_authorized_caller_missing_person_is_404(client: TestClient, endpoint: _Endpoint) -> None:
    """(10) Both permissions held; nonexistent Person -> 404."""
    world = _seed(client)
    grants = dict.fromkeys(endpoint.permissions, "all")
    _assert_person_not_found(_call_as(client, world, endpoint, grants, person_id=uuid.uuid4()))


# --- 11: document of another Person --------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("endpoint", _DOC_ENDPOINTS, ids=[e.name for e in _DOC_ENDPOINTS])
def test_document_of_another_person_is_404(client: TestClient, endpoint: _Endpoint) -> None:
    world = _seed(client)
    grants = dict.fromkeys(endpoint.permissions, "all")
    response = _call_as(client, world, endpoint, grants, document_id=world.other_document_id)
    assert response.status_code == 404, response.text  # type: ignore[attr-defined]
    assert response.json()["error"]["code"] == "document_not_found"  # type: ignore[attr-defined]


# --- 12-13: read and manage never imply each other -----------------------------------


@requires_postgres
@pytest.mark.parametrize("endpoint", _MANAGE_ENDPOINTS, ids=[e.name for e in _MANAGE_ENDPOINTS])
def test_read_permissions_do_not_allow_mutation(client: TestClient, endpoint: _Endpoint) -> None:
    """(12) person.read + person.update + document.read (no document.manage)
    -> 403 on every mutation."""
    world = _seed(client)
    grants = {"person.read": "all", "person.update": "all", "document.read": "all"}
    _assert_forbidden(_call_as(client, world, endpoint, grants))


@requires_postgres
@pytest.mark.parametrize("endpoint", _READ_ENDPOINTS, ids=[e.name for e in _READ_ENDPOINTS])
def test_manage_permission_does_not_imply_read(client: TestClient, endpoint: _Endpoint) -> None:
    """(13) person.read + person.update + document.manage (no document.read)
    -> 403 on every read: ADR-0040 §6 defines document.read and
    document.manage as separate permissions with no implication."""
    world = _seed(client)
    grants = {"person.read": "all", "person.update": "all", "document.manage": "all"}
    _assert_forbidden(_call_as(client, world, endpoint, grants))
