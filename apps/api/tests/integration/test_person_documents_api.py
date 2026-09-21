"""HTTP-level integration tests for the participant Document API
foundation (TH-0117.3 / Issue #160, ADR-0040):

    POST   /api/v1/persons/{person_id}/documents
    GET    /api/v1/persons/{person_id}/documents
    GET    /api/v1/persons/{person_id}/documents/{document_id}
    GET    /api/v1/persons/{person_id}/documents/{document_id}/download

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_person_account_api.py's
fixture/factory conventions. `get_file_storage` is overridden for every
test to a `LocalFileStorage` rooted at `tmp_path` — no test ever touches
the real `var/file-storage` default.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import Document, File
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


# --- fixtures / factories ---------------------------------------------------


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
    """Ad hoc grant via a throwaway role, isolated from the real admin
    RolePermission wiring — mirrors test_person_account_api.py's
    identical helper."""
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


# --- create ------------------------------------------------------------


@requires_postgres
def test_create_document_requires_document_manage_not_person_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="person.read")
    _authenticate_as(actor_id)

    response = _upload(client, person_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_create_document_succeeds_with_document_manage(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = _upload(client, person_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["person_id"] == str(person_id)
    assert body["document_type"] == "medical_certificate"
    assert body["status"] == "active"
    assert body["version_number"] == 1
    assert body["document_group_id"] == body["id"]
    assert "file_id" in body
    assert "storage_key" not in response.text
    assert "var/file-storage" not in response.text


@requires_postgres
def test_create_document_persists_actual_uploaded_bytes(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    content = b"the real uploaded certificate content"

    create_response = _upload(client, person_id, content=content)
    assert create_response.status_code == 201, create_response.text
    document_id = create_response.json()["id"]

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    download_response = client.get(
        f"/api/v1/persons/{person_id}/documents/{document_id}/download"
    )
    assert download_response.status_code == 200, download_response.text
    assert download_response.content == content


@requires_postgres
def test_create_document_missing_filename_returns_422(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = client.post(
        f"/api/v1/persons/{person_id}/documents",
        files={"file": ("", b"content", "application/pdf")},
        data={"document_type": "medical_certificate"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 422, response.text


@requires_postgres
def test_create_document_nonexistent_person_returns_404(client: TestClient) -> None:
    club_id, _person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = _upload(client, uuid.uuid4())

    assert response.status_code == 404, response.text


@requires_postgres
def test_create_document_client_cannot_supply_storage_key(client: TestClient) -> None:
    """No `storage_key` field is accepted at all — an extra form field of
    that name is simply ignored, never used to influence where the
    content is actually stored."""
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = _upload(
        client, person_id, extra_data={"storage_key": "documents/person/attacker/evil"}
    )

    assert response.status_code == 201, response.text
    with session_scope() as session:
        file_row = session.get(File, uuid.UUID(response.json()["file_id"]))
        assert file_row.storage_key != "documents/person/attacker/evil"


# --- list ----------------------------------------------------------------


@requires_postgres
def test_list_documents_requires_document_read_not_person_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="person.read")
    _authenticate_as(actor_id)

    response = client.get(f"/api/v1/persons/{person_id}/documents")

    assert response.status_code == 404, response.text


@requires_postgres
def test_list_documents_returns_current_version_and_excludes_historical(
    client: TestClient,
) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)

    create_response = _upload(client, person_id, content=b"version-one")
    assert create_response.status_code == 201, create_response.text
    v1_body = create_response.json()

    # Simulate a second version by direct DB insert — the replace/new-
    # version endpoint is explicitly out of scope for this Issue.
    with session_scope() as session:
        v1 = session.get(Document, uuid.UUID(v1_body["id"]))
        v1_file = session.get(File, v1.file_id)
        v2_file = File(
            storage_key=f"documents/test/{uuid.uuid4()}",
            original_name="v2.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            checksum="0" * 64,
            storage_backend="local",
            created_by=v1_file.created_by,
        )
        session.add(v2_file)
        session.flush()
        v2 = Document(
            person_id=uuid.UUID(v1_body["person_id"]),
            document_group_id=v1.document_group_id,
            version_number=2,
            document_type="medical_certificate",
            status="active",
            file_id=v2_file.id,
        )
        session.add(v2)
        session.commit()
        v2_id = v2.id

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    list_response = client.get(f"/api/v1/persons/{person_id}/documents")

    assert list_response.status_code == 200, list_response.text
    body = list_response.json()
    assert body["pagination"]["total"] == 1
    ids = [item["id"] for item in body["items"]]
    assert ids == [str(v2_id)]
    for item in body["items"]:
        assert "storage_key" not in item


@requires_postgres
def test_list_documents_response_never_contains_storage_internals(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    _upload(client, person_id)

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = client.get(f"/api/v1/persons/{person_id}/documents")

    assert response.status_code == 200, response.text
    assert "storage_key" not in response.text
    assert "file-storage" not in response.text


# --- detail --------------------------------------------------------------


@requires_postgres
def test_get_document_detail_returns_expected_metadata(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    create_response = _upload(client, person_id)
    document_id = create_response.json()["id"]

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == document_id
    assert body["document_type"] == "medical_certificate"
    assert "storage_key" not in response.text


@requires_postgres
def test_get_document_detail_requires_document_read_not_person_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    document_id = _upload(client, person_id).json()["id"]

    read_only_person_actor = _make_actor(club_id, permission_code="person.read")
    _authenticate_as(read_only_person_actor)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}")

    assert response.status_code == 404, response.text


@requires_postgres
def test_get_document_detail_nonexistent_document_returns_404(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(actor_id)

    response = client.get(f"/api/v1/persons/{person_id}/documents/{uuid.uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "document_not_found"


# --- download --------------------------------------------------------------


@requires_postgres
def test_download_document_returns_exact_binary_and_mime_type(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    content = b"binary content for download round trip"
    document_id = _upload(
        client, person_id, content=content, content_type="application/pdf"
    ).json()["id"]

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")

    assert response.status_code == 200, response.text
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"


@requires_postgres
def test_download_document_requires_document_read_not_person_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    document_id = _upload(client, person_id).json()["id"]

    read_only_person_actor = _make_actor(club_id, permission_code="person.read")
    _authenticate_as(read_only_person_actor)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")

    assert response.status_code == 404, response.text


@requires_postgres
def test_download_document_nonexistent_returns_404(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(actor_id)

    response = client.get(
        f"/api/v1/persons/{person_id}/documents/{uuid.uuid4()}/download"
    )

    assert response.status_code == 404, response.text


@requires_postgres
def test_download_document_response_never_contains_storage_key_or_path(
    client: TestClient, tmp_path
) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    document_id = _upload(client, person_id).json()["id"]

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")

    assert response.status_code == 200, response.text
    assert str(tmp_path) not in str(response.headers)
    with session_scope() as session:
        file_row = session.execute(
            select(File).where(File.id == session.get(Document, uuid.UUID(document_id)).file_id)
        ).scalar_one()
        assert file_row.storage_key not in str(response.headers)


@requires_postgres
def test_download_document_sanitizes_a_malicious_filename(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    malicious_name = 'evil".pdf\r\nSet-Cookie: hijacked=1\r\nX-Injected: yes'

    create_response = _upload(client, person_id, filename=malicious_name)
    assert create_response.status_code == 201, create_response.text
    document_id = create_response.json()["id"]

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")

    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "Set-Cookie" not in response.headers
    assert "x-injected" not in {key.lower() for key in response.headers.keys()}


@requires_postgres
def test_download_document_records_document_downloaded_audit_event(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    document_id = _upload(client, person_id).json()["id"]

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")
    assert response.status_code == 200, response.text

    with session_scope() as session:
        audit_row = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.downloaded",
                AuditLog.resource_id == uuid.UUID(document_id),
            )
        ).scalar_one()
        assert audit_row.actor_user_id == read_actor_id
        assert audit_row.outcome == "success"
