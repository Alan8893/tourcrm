"""HTTP-level integration tests for participant Document metadata update
(TH-0117.8 / Issue #170, ADR-0040):

    PATCH /api/v1/persons/{person_id}/documents/{document_id}

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring test_person_document_revoke_api.py's fixture/factory
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


# --- fixtures / factories (mirrors test_person_document_revoke_api.py) -----


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
):
    return client.post(
        f"/api/v1/persons/{person_id}/documents",
        files={"file": (filename, content, content_type)},
        data={"document_type": document_type},
        headers=_csrf_headers(client),
    )


def _patch(client: TestClient, person_id: uuid.UUID, document_id, body: dict):
    return client.patch(
        f"/api/v1/persons/{person_id}/documents/{document_id}",
        json=body,
        headers=_csrf_headers(client),
    )


def _create_first_version(client: TestClient, club_id: uuid.UUID, person_id: uuid.UUID) -> str:
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _upload(client, person_id)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- success ---------------------------------------------------------------


@requires_postgres
def test_patch_update_issued_at_returns_200(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["issued_at"].startswith("2026-01-01")
    assert body["id"] == document_id


@requires_postgres
def test_patch_update_expires_at_returns_200(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"expires_at": "2027-01-01T00:00:00+00:00"})

    assert response.status_code == 200, response.text
    assert response.json()["expires_at"].startswith("2027-01-01")


@requires_postgres
def test_patch_update_both_fields(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(
        client,
        person_id,
        document_id,
        {"issued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2027-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["issued_at"].startswith("2026-01-01")
    assert body["expires_at"].startswith("2027-01-01")


@requires_postgres
def test_patch_explicit_null_clears_field(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    upload_response = _upload(client, person_id)
    assert upload_response.status_code == 201, upload_response.text
    document_id = upload_response.json()["id"]

    _authenticate_as(manage_actor_id)
    first = _patch(client, person_id, document_id, {"expires_at": "2027-01-01T00:00:00+00:00"})
    assert first.status_code == 200, first.text
    assert first.json()["expires_at"] is not None

    _authenticate_as(manage_actor_id)
    second = _patch(client, person_id, document_id, {"expires_at": None})

    assert second.status_code == 200, second.text
    assert second.json()["expires_at"] is None


@requires_postgres
def test_patch_response_is_the_existing_document_out_shape(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _grant_permission(manage_actor_id, "document.read", club_id=club_id)
    _authenticate_as(manage_actor_id)
    detail_response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}")
    _authenticate_as(manage_actor_id)
    patch_response = _patch(
        client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"}
    )

    assert patch_response.status_code == 200, patch_response.text
    assert set(patch_response.json().keys()) == set(detail_response.json().keys())


# --- validation ----------------------------------------------------------


@requires_postgres
def test_patch_empty_body_rejected(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "empty_document_update"


@requires_postgres
def test_patch_invalid_date_ordering_returns_422(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(
        client,
        person_id,
        document_id,
        {"issued_at": "2026-06-01T00:00:00+00:00", "expires_at": "2025-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_document_dates"


@requires_postgres
def test_patch_invalid_resulting_dates_with_only_one_field_supplied(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    upload_response = _upload(client, person_id)
    document_id = upload_response.json()["id"]

    _authenticate_as(manage_actor_id)
    set_issued = _patch(client, person_id, document_id, {"issued_at": "2026-06-01T00:00:00+00:00"})
    assert set_issued.status_code == 200, set_issued.text

    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"expires_at": "2025-01-01T00:00:00+00:00"})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_document_dates"


@requires_postgres
def test_patch_document_type_cannot_be_changed_via_request(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = client.patch(
        f"/api/v1/persons/{person_id}/documents/{document_id}",
        json={"issued_at": "2026-01-01T00:00:00+00:00", "document_type": "insurance"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_type"] == "medical_certificate"


# --- history -----------------------------------------------------------


@requires_postgres
def test_historical_document_returns_404(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    replace_response = client.post(
        f"/api/v1/persons/{person_id}/documents/{document_id}/replace",
        files={"file": ("v2.pdf", b"v2-content", "application/pdf")},
        headers=_csrf_headers(client),
    )
    assert replace_response.status_code == 201, replace_response.text

    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "document_not_found"

    with session_scope() as session:
        historical = session.get(Document, uuid.UUID(document_id))
        assert historical.issued_at is None


# --- authorization -------------------------------------------------------


@requires_postgres
def test_patch_requires_document_manage_not_document_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 404, response.text


@requires_postgres
def test_patch_requires_document_manage_not_person_read(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    person_read_actor_id = _make_actor(club_id, permission_code="person.read")
    _authenticate_as(person_read_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 404, response.text


@requires_postgres
def test_patch_denied_when_person_not_visible_to_actor(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    other_club_id, _ = _setup_target_person()
    actor_id = _make_actor(other_club_id, permission_code="document.manage")
    _authenticate_as(actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 404, response.text


@requires_postgres
def test_patch_cannot_target_another_persons_document(client: TestClient) -> None:
    club_id, person_a_id = _setup_target_person()
    _, person_b_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_a_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_b_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 404, response.text

    with session_scope() as session:
        untouched = session.get(Document, uuid.UUID(document_id))
        assert untouched.issued_at is None


@requires_postgres
def test_patch_nonexistent_document_returns_404_without_disclosure(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = _patch(
        client, person_id, uuid.uuid4(), {"issued_at": "2026-01-01T00:00:00+00:00"}
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "document_not_found"


@requires_postgres
def test_patch_nonexistent_person_returns_404(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)
    actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(actor_id)

    response = _patch(
        client, uuid.uuid4(), document_id, {"issued_at": "2026-01-01T00:00:00+00:00"}
    )

    assert response.status_code == 404, response.text


# --- audit / storage --------------------------------------------------


@requires_postgres
def test_patch_records_exactly_one_document_updated_audit_event(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})
    assert response.status_code == 200, response.text

    with session_scope() as session:
        rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "document.updated",
                    AuditLog.resource_id == uuid.UUID(document_id),
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].actor_user_id == manage_actor_id
        assert rows[0].outcome == "success"


@requires_postgres
def test_patch_response_never_contains_storage_internals(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 200, response.text
    assert "storage_key" not in response.text
    assert "file-storage" not in response.text


@requires_postgres
def test_patch_never_changes_the_file_or_binary_content(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    content = b"binary content that must survive a metadata patch"
    upload_response = _upload(client, person_id, content=content)
    assert upload_response.status_code == 201, upload_response.text
    document_id = upload_response.json()["id"]
    file_id_before = upload_response.json()["file_id"]

    _authenticate_as(manage_actor_id)
    patch_response = _patch(
        client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"}
    )
    assert patch_response.status_code == 200, patch_response.text
    assert patch_response.json()["file_id"] == file_id_before

    read_actor_id = _make_actor(club_id, permission_code="document.read")
    _authenticate_as(read_actor_id)
    download_response = client.get(f"/api/v1/persons/{person_id}/documents/{document_id}/download")
    assert download_response.status_code == 200, download_response.text
    assert download_response.content == content


@requires_postgres
def test_patch_does_not_change_status(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


@requires_postgres
def test_patch_does_not_create_a_new_file_row(client: TestClient) -> None:
    club_id, person_id = _setup_target_person()
    document_id = _create_first_version(client, club_id, person_id)

    manage_actor_id = _make_actor(club_id, permission_code="document.manage")
    _authenticate_as(manage_actor_id)
    response = _patch(client, person_id, document_id, {"issued_at": "2026-01-01T00:00:00+00:00"})
    assert response.status_code == 200, response.text

    with session_scope() as session:
        file_rows = session.execute(select(File)).scalars().all()
        assert len(file_rows) == 1
