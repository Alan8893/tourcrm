"""HTTP-level integration tests for the Event competition document
package export (TH-0117.9 / Issue #172, ADR-0040 §5/§6/§7,
events-api.md §31.5):

    POST /api/v1/events/{event_id}/document-package

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_event_document_requirements_api.py's
fixture/factory conventions. `get_file_storage` is overridden for every
test to a `LocalFileStorage` rooted at `tmp_path` — no test ever touches
the real `var/file-storage` default.
"""

import io
import json
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event, EventParticipation
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


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "competition",
        "title": "Regional Championship",
        "start_at": "2026-10-01T10:00:00+00:00",
        "end_at": "2026-10-01T12:00:00+00:00",
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_requirement(event: Event, **overrides: object) -> EventDocumentRequirement:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "document_type": "medical_certificate",
        "required": True,
    }
    defaults.update(overrides)
    return EventDocumentRequirement(**defaults)  # type: ignore[arg-type]


def _make_participation(event: Event, person: Person, **overrides: object) -> EventParticipation:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "person_id": person.id,
        "registration_status": "registered",
    }
    defaults.update(overrides)
    return EventParticipation(**defaults)  # type: ignore[arg-type]


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


def _make_actor(club_id: uuid.UUID, *, permission_codes: list[str]) -> uuid.UUID:
    with session_scope() as session:
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([actor_person, actor_user])
        session.commit()
        actor_user_id = actor_user.id
    for code in permission_codes:
        _grant_permission(actor_user_id, code, scope_type="all", club_id=club_id)
    return actor_user_id


def _setup_event_with_participant() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, event_id, person_id) — the Person is a
    registered participant of the Event, with an EventDocumentRequirement
    for `medical_certificate` (required)."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        event = _make_event(club)
        session.add(event)
        session.flush()
        person = _make_person()
        session.add(person)
        session.flush()
        session.add(_make_club_membership(club, person))
        session.add(_make_participation(event, person))
        session.add(_make_requirement(event))
        session.commit()
        return club.id, event.id, person.id


def _upload_document_via_api(
    client: TestClient,
    club_id: uuid.UUID,
    person_id: uuid.UUID,
    *,
    content: bytes = b"cert-bytes",
    document_type: str = "medical_certificate",
) -> str:
    manage_actor_id = _make_actor(club_id, permission_codes=["document.manage"])
    _authenticate_as(manage_actor_id)
    response = client.post(
        f"/api/v1/persons/{person_id}/documents",
        files={"file": ("cert.pdf", content, "application/pdf")},
        data={"document_type": document_type},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _url(event_id: uuid.UUID) -> str:
    return f"/api/v1/events/{event_id}/document-package"


def _export(client: TestClient, event_id: uuid.UUID, body: dict | None = None):
    kwargs: dict = {"headers": _csrf_headers(client)}
    if body is not None:
        kwargs["json"] = body
    return client.post(_url(event_id), **kwargs)


def _manifest(response) -> dict:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))


# --- success ---------------------------------------------------------------


@requires_postgres
def test_export_returns_200_zip(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id, content=b"cert-bytes")

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert zipfile.is_zipfile(io.BytesIO(response.content))


@requires_postgres
def test_content_disposition_is_safe_and_event_derived(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "Regional" in disposition or "Regional-Championship" in disposition
    assert str(event_id) not in disposition


@requires_postgres
def test_manifest_is_valid_utf8_json(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        raw = archive.read("manifest.json")
    decoded = raw.decode("utf-8")
    manifest = json.loads(decoded)
    assert manifest["documents"][0]["result"] == "valid"


# --- incomplete --------------------------------------------------------


@requires_postgres
def test_incomplete_package_returns_409(client: TestClient) -> None:
    club_id, event_id, _person_id = _setup_event_with_participant()

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "document_package_incomplete"


@requires_postgres
def test_incomplete_package_details_are_safe(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 409, response.text
    body = response.json()
    incomplete = body["error"]["details"]["incomplete"]
    assert len(incomplete) == 1
    assert incomplete[0]["document_type"] == "medical_certificate"
    assert incomplete[0]["result"] == "missing"
    assert incomplete[0]["required"] is True
    assert "participant_display_name" in incomplete[0]

    body_text = response.text
    assert str(person_id) not in body_text
    assert "storage_key" not in body_text


@requires_postgres
def test_confirm_incomplete_true_bypasses_warning(client: TestClient) -> None:
    club_id, event_id, _person_id = _setup_event_with_participant()

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id, {"confirm_incomplete": True})

    assert response.status_code == 200, response.text
    manifest = _manifest(response)
    assert manifest["documents"][0]["result"] == "missing"


@requires_postgres
def test_omitted_body_defaults_confirm_incomplete_to_false(client: TestClient) -> None:
    club_id, event_id, _person_id = _setup_event_with_participant()

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id, body=None)

    assert response.status_code == 409, response.text


# --- authorization -------------------------------------------------------


@requires_postgres
def test_event_read_and_document_export_both_required_succeeds(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 200, response.text


@requires_postgres
def test_event_read_without_document_export_denied(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_document_export_without_event_authorization_denied(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_document_read_alone_denied(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_document_manage_alone_does_not_grant_export(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.manage"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 404, response.text


@requires_postgres
def test_nonexistent_event_uses_established_404(client: TestClient) -> None:
    club_id, _event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, uuid.uuid4())

    assert response.status_code == 404, response.text


@requires_postgres
def test_inaccessible_event_uses_established_404(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    other_club_id = uuid.uuid4()
    with session_scope() as session:
        session.add(_make_club(id=other_club_id))
        session.commit()
    actor_id = _make_actor(other_club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 404, response.text


# --- security --------------------------------------------------------------


@requires_postgres
def test_client_supplied_person_id_has_no_effect(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id)

    other_club_id, other_event_id, other_person_id = _setup_event_with_participant()
    _upload_document_via_api(client, other_club_id, other_person_id)

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    # Attempting to smuggle an unrelated Person's id into the request body.
    response = client.post(
        _url(event_id),
        json={"confirm_incomplete": False, "person_ids": [str(other_person_id)]},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    manifest = _manifest(response)
    assert len(manifest["documents"]) == 1


@requires_postgres
def test_archive_has_no_uuid_or_storage_internals(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_with_participant()
    document_id = _upload_document_via_api(client, club_id, person_id, content=b"secret-cert")

    with session_scope() as session:
        document = session.get(Document, uuid.UUID(document_id))
        file_id = document.file_id
        file_row = session.get(File, file_id)
        storage_key = file_row.storage_key

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.export"])
    _authenticate_as(actor_id)
    response = _export(client, event_id)

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        for name in names:
            assert str(person_id) not in name
            assert document_id not in name
            assert str(file_id) not in name
            assert storage_key not in name
    manifest_text = json.dumps(_manifest(response))
    assert str(person_id) not in manifest_text
    assert document_id not in manifest_text
    assert str(file_id) not in manifest_text
    assert storage_key not in manifest_text
    assert "storage_key" not in manifest_text


@requires_postgres
def test_medical_certificate_never_appears_in_ordinary_person_document_list(
    client: TestClient,
) -> None:
    """Security regression: the package endpoint is additive — it must
    not cause the ordinary participant Document list to start exposing
    binary content, and vice versa the ordinary list must never expose
    what the package endpoint exposes beyond metadata."""
    club_id, event_id, person_id = _setup_event_with_participant()
    _upload_document_via_api(client, club_id, person_id, content=b"secret-medical-content")

    read_actor_id = _make_actor(club_id, permission_codes=["document.read"])
    _authenticate_as(read_actor_id)
    list_response = client.get(f"/api/v1/persons/{person_id}/documents")

    assert list_response.status_code == 200, list_response.text
    assert "secret-medical-content" not in list_response.text
    assert "storage_key" not in list_response.text


# --- openapi -------------------------------------------------------------


@requires_postgres
def test_openapi_contains_exact_endpoint(client: TestClient) -> None:
    response = client.get("/openapi.json")
    schema = response.json()
    path_item = schema["paths"]["/api/v1/events/{event_id}/document-package"]
    assert "post" in path_item
