"""HTTP-level integration tests for participant Document replacement
(TH-0117.6 / Issue #166, ADR-0040):

    POST /api/v1/persons/{person_id}/documents/{document_id}/replace

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring test_person_documents_api.py's fixture/factory
conventions. `get_file_storage` is overridden for every test to a
`LocalFileStorage` rooted at `tmp_path` — no test ever touches the real
`var/file-storage` default.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import Document
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres


@pytest.fixture
def client(tmp_path) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=tmp_path)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories (mirrors test_person_documents_api.py) -----------


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Club {uuid.uuid4().hex[:8]}", "status": "active"}
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": "Ivanova",
        "first_name": f"P-{uuid.uuid4().hex[:8]}",
    }
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


def _make_club_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "person_id": person.id,
        "membership_type": "member",
        "status": "active",
        "joined_at": "2020-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
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


def _setup_target_person() -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.flush()
        session.add(_make_club_membership(club, person))
        session.commit()
        return club.id, person.id


def _make_actor(
    club_id: uuid.UUID, *, permission_code: str | None, scope_type: str = "all"
) -> uuid.UUID:
    with session_scope() as session:
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([actor_person, actor_user])
        session.commit()
        actor_user_id = actor_user.id
    if permission_code is not None:
        _grant_permission(actor_user_id, permission_code, scope_type=scope_type, club_id=club_id)
    return actor_user_id


def _upload(
    client: TestClient,
    person_id: uuid.UUID,
    *,
    filename: str = "cert.pdf",
    content: bytes = b"%PDF-1.4 fake certificate bytes",
    content_type: str = "application/pdf",
    document_type: str = "medical_certificate",
    extra_data: dict | None = None,
):
    data = {"document_type": document_type}
    if extra_data:
        data.update(extra_data)
    return client.post(
        f"/api/v1/persons/{person_id}/documents",
        files={"file": (filename, content, content_type)},
        data=data,
        headers=_csrf_headers(client),
    )


def _replace(
    client: TestClient,
    person_id: uuid.UUID,
    document_id,
    *,
    filename: str = "cert-v2.pdf",
    content: bytes = b"%PDF-1.4 replacement bytes",
    content_type: str = "application/pdf",
    extra_data: dict | None = None,
):
    data = dict(extra_data) if extra_data else {}
    return client.post(
        f"/api/v1/persons/{person_id}/documents/{document_id}/replace",
        files={"file": (filename, content, content_type)},
        data=data,
        headers=_csrf_headers(client),
    )


def _create_first_version(client: TestClient, club_id: uuid.UUID, person_id: uuid.UUID) -> str:
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _upload(client, person_id, content=b"version-one")
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- success ---------------------------------------------------------------


@requires_postgres
def test_replace_creates_a_new_version_with_expected_fields(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(client, person_id, v1_id, content=b"version-two")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != v1_id
    assert body["person_id"] == str(person_id)
    assert body["document_type"] == "medical_certificate"
    assert body["status"] == "active"
    assert body["version_number"] == 2
    with session_scope() as session:
        v1 = session.get(Document, uuid.UUID(v1_id))
        assert body["document_group_id"] == str(v1.document_group_id)
    assert "file_id" in body
    assert "storage_key" not in response.text
    assert "var/file-storage" not in response.text


@requires_postgres
def test_replace_response_is_the_existing_document_out_shape(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _grant_permission(manage_actor_id, "document.read", club_id=club_id)
    _authenticate_as(manage_actor_id)
    detail_response = client.get(f"/api/v1/persons/{person_id}/documents/{v1_id}")
    _authenticate_as(manage_actor_id)
    replace_response = _replace(client, person_id, v1_id)

    assert replace_response.status_code == 201, replace_response.text
    assert set(replace_response.json().keys()) == set(detail_response.json().keys())


# --- history -----------------------------------------------------------


@requires_postgres
def test_old_document_and_file_remain_unchanged_after_replace(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)
    with session_scope() as session:
        v1_before = session.get(Document, uuid.UUID(v1_id))
        v1_file_id_before = v1_before.file_id
        v1_status_before = v1_before.status

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(client, person_id, v1_id)
    assert response.status_code == 201, response.text

    with session_scope() as session:
        v1_after = session.get(Document, uuid.UUID(v1_id))
        assert v1_after.file_id == v1_file_id_before
        assert v1_after.status == v1_status_before
        assert v1_after.version_number == 1


@requires_postgres
def test_old_binary_remains_accessible_after_replace(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    v1_content = b"original certificate bytes"
    v1_id = _upload(client, person_id, content=v1_content).json()["id"]

    _authenticate_as(manage_actor_id)
    replace_response = _replace(client, person_id, v1_id, content=b"replacement bytes")
    assert replace_response.status_code == 201, replace_response.text

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    download_response = client.get(f"/api/v1/persons/{person_id}/documents/{v1_id}/download")

    assert download_response.status_code == 200, download_response.text
    assert download_response.content == v1_content


@requires_postgres
def test_historical_version_cannot_be_replaced(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    first_replace = _replace(client, person_id, v1_id)
    assert first_replace.status_code == 201, first_replace.text

    _authenticate_as(manage_actor_id)
    second_replace = _replace(client, person_id, v1_id)

    assert second_replace.status_code == 404, second_replace.text
    assert second_replace.json()["error"]["code"] == "document_not_found"


@requires_postgres
def test_current_version_can_be_replaced_again(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    v2_response = _replace(client, person_id, v1_id)
    assert v2_response.status_code == 201, v2_response.text
    v2_id = v2_response.json()["id"]

    _authenticate_as(manage_actor_id)
    v3_response = _replace(client, person_id, v2_id)

    assert v3_response.status_code == 201, v3_response.text
    assert v3_response.json()["version_number"] == 3


# --- authorization -------------------------------------------------------


@requires_postgres
def test_replace_requires_document_manage_not_document_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = _replace(client, person_id, v1_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_replace_requires_document_manage_not_person_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    person_read_actor_id = _make_actor(club_id, permission_code="person.read")
    _authenticate_as(person_read_actor_id)
    response = _replace(client, person_id, v1_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_replace_denied_when_person_not_visible_to_actor(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    other_club_id, _ = _setup_target_person()
    actor_id = _make_actor(other_club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    response = _replace(client, person_id, v1_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_replace_cannot_target_another_persons_document(client: TestClient) -> None:
    club_id, person_a_id = _setup_target_person()
    _, person_b_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_a_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(client, person_b_id, v1_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_replace_nonexistent_document_returns_404_without_disclosure(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = _replace(client, person_id, uuid.uuid4())

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "document_not_found"


# --- validation ------------------------------------------------------------


@requires_postgres
def test_replace_missing_file_returns_422(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = client.post(
        f"/api/v1/persons/{person_id}/documents/{v1_id}/replace",
        data={},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 422, response.text


@requires_postgres
def test_replace_expires_at_before_issued_at_returns_422(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(
        client,
        person_id,
        v1_id,
        extra_data={
            "issued_at": "2026-06-01T00:00:00+00:00",
            "expires_at": "2026-01-01T00:00:00+00:00",
        },
    )

    assert response.status_code == 422, response.text


@requires_postgres
def test_replace_accepts_new_issued_and_expires_at(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(
        client,
        person_id,
        v1_id,
        extra_data={
            "issued_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-12-31T00:00:00+00:00",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["issued_at"].startswith("2026-01-01")
    assert body["expires_at"].startswith("2026-12-31")


@requires_postgres
def test_replace_document_type_cannot_be_changed_via_request(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(
        client, person_id, v1_id, extra_data={"document_type": "insurance"}
    )

    assert response.status_code == 201, response.text
    assert response.json()["document_type"] == "medical_certificate"


@requires_postgres
def test_replace_supports_zero_byte_content(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(client, person_id, v1_id, content=b"")

    assert response.status_code == 201, response.text


# --- audit ------------------------------------------------------------


@requires_postgres
def test_replace_records_exactly_one_document_replaced_audit_event(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(client, person_id, v1_id)
    assert response.status_code == 201, response.text
    new_document_id = response.json()["id"]

    with session_scope() as session:
        rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "document.replaced",
                    AuditLog.resource_id == uuid.UUID(new_document_id),
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].actor_user_id == manage_actor_id
        assert rows[0].outcome == "success"


@requires_postgres
def test_replace_response_never_contains_storage_internals(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    v1_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _replace(client, person_id, v1_id)

    assert response.status_code == 201, response.text
    assert "storage_key" not in response.text
    assert "file-storage" not in response.text
