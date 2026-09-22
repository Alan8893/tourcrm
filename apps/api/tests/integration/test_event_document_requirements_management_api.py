"""HTTP-level integration tests for the EventDocumentRequirement
management API (TH-0117.5 / Issue #164, ADR-0040 §5,
docs/05-api/events-api.md §31.1/§31.2):

    GET    /api/v1/events/{event_id}/document-requirements
    POST   /api/v1/events/{event_id}/document-requirements
    PATCH  /api/v1/events/{event_id}/document-requirements/{requirement_id}
    DELETE /api/v1/events/{event_id}/document-requirements/{requirement_id}

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_event_document_requirements_api.py's
fixture/factory conventions.
"""

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import EventDocumentRequirement
from app.db.events import Event
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ---------------------------------------------------


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Club {uuid.uuid4().hex[:8]}", "status": "active"}
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "competition",
        "title": "Test event",
        "start_at": _utc(2026, 10, 1, 10, 0),
        "end_at": _utc(2026, 10, 1, 12, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    """Ad hoc grant via a throwaway role, isolated from the real admin
    RolePermission wiring — mirrors test_event_document_requirements_api.py's
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


def _setup_event() -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (club_id, event_id)."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        event = _make_event(club)
        session.add(event)
        session.commit()
        return club.id, event.id


def _make_actor(club_id: uuid.UUID, *, permission_codes: list[str]) -> uuid.UUID:
    with session_scope() as session:
        actor_person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        actor_user = User(
            person=actor_person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([actor_person, actor_user])
        session.commit()
        actor_user_id = actor_user.id
    for code in permission_codes:
        _grant_permission(actor_user_id, code, scope_type="all", club_id=club_id)
    return actor_user_id


def _list_url(event_id: uuid.UUID) -> str:
    return f"/api/v1/events/{event_id}/document-requirements"


def _detail_url(event_id: uuid.UUID, requirement_id: uuid.UUID) -> str:
    return f"/api/v1/events/{event_id}/document-requirements/{requirement_id}"


def _create_requirement(
    event_id: uuid.UUID, *, document_type: str = "medical_certificate", required: bool = True
) -> uuid.UUID:
    with session_scope() as session:
        requirement = EventDocumentRequirement(
            event_id=event_id, document_type=document_type, required=required
        )
        session.add(requirement)
        session.commit()
        return requirement.id


# --- 1. GET list -----------------------------------------------------------


@requires_postgres
def test_list_returns_requirements_for_event(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    _create_requirement(event_id, document_type="medical_certificate")
    _create_requirement(event_id, document_type="insurance", required=False)
    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_list_url(event_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == 2
    by_type = {item["document_type"]: item for item in body["items"]}
    assert by_type["medical_certificate"]["required"] is True
    assert by_type["insurance"]["required"] is False


# --- 2. POST create ----------------------------------------------------


@requires_postgres
def test_create_persists_and_returns_projection(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["event_id"] == str(event_id)
    assert body["document_type"] == "medical_certificate"
    assert body["required"] is True
    assert set(body.keys()) == {"id", "event_id", "document_type", "required"}


# --- 3/4. PATCH required toggling -------------------------------------


@requires_postgres
def test_patch_changes_required_true_to_false(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    requirement_id = _create_requirement(event_id, required=True)
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.patch(
        _detail_url(event_id, requirement_id),
        json={"required": False},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["required"] is False


@requires_postgres
def test_patch_changes_required_false_to_true(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    requirement_id = _create_requirement(event_id, required=False)
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.patch(
        _detail_url(event_id, requirement_id),
        json={"required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["required"] is True


@requires_postgres
def test_patch_never_changes_document_type(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    requirement_id = _create_requirement(event_id, document_type="medical_certificate")
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.patch(
        _detail_url(event_id, requirement_id),
        json={"required": False},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_type"] == "medical_certificate"


@requires_postgres
def test_patch_body_cannot_change_document_type(client: TestClient) -> None:
    """PATCH's request schema has no `document_type` field at all —
    an extra field of that name in the request body is simply ignored."""
    club_id, event_id = _setup_event()
    requirement_id = _create_requirement(event_id, document_type="medical_certificate")
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.patch(
        _detail_url(event_id, requirement_id),
        json={"required": False, "document_type": "insurance"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_type"] == "medical_certificate"


# --- 5. DELETE -----------------------------------------------------------


@requires_postgres
def test_delete_removes_the_requirement(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    requirement_id = _create_requirement(event_id)
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.delete(_detail_url(event_id, requirement_id), headers=_csrf_headers(client))

    assert response.status_code == 204, response.text
    with session_scope() as session:
        assert session.get(EventDocumentRequirement, requirement_id) is None


# --- 6. duplicate (event_id, document_type) -------------------------------


@requires_postgres
def test_duplicate_document_type_for_same_event_is_rejected(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    _create_requirement(event_id, document_type="medical_certificate")
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_document_requirement"


# --- 7. document_type immutability (already covered above too) -------------


@requires_postgres
def test_changing_document_type_requires_delete_then_create(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    requirement_id = _create_requirement(event_id, document_type="medical_certificate")
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    delete_response = client.delete(
        _detail_url(event_id, requirement_id), headers=_csrf_headers(client)
    )
    assert delete_response.status_code == 204, delete_response.text

    create_response = client.post(
        _list_url(event_id),
        json={"document_type": "insurance", "required": True},
        headers=_csrf_headers(client),
    )
    assert create_response.status_code == 201, create_response.text
    assert create_response.json()["document_type"] == "insurance"


# --- 8/9/10. medical_certificate, required=true/false -----------------


@requires_postgres
def test_create_supports_medical_certificate_with_required_true(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["required"] is True


@requires_postgres
def test_create_supports_required_false(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "waiver", "required": False},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["required"] is False


# --- 11/12. GET authorization ----------------------------------------


@requires_postgres
def test_list_requires_event_read(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["document.read"])
    _authenticate_as(actor_id)

    response = client.get(_list_url(event_id))

    assert response.status_code == 404, response.text


@requires_postgres
def test_list_requires_document_read(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.read"])
    _authenticate_as(actor_id)

    response = client.get(_list_url(event_id))

    assert response.status_code == 404, response.text


# --- 13/14/15/16. mutation authorization -------------------------------


@requires_postgres
def test_create_requires_event_manage(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404, response.text


@requires_postgres
def test_create_requires_document_manage(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404, response.text


@requires_postgres
def test_event_manage_alone_is_denied(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    # event.manage only, no document.manage at all.
    actor_id = _make_actor(club_id, permission_codes=["event.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404, response.text


@requires_postgres
def test_document_manage_without_authorized_event_is_denied(client: TestClient) -> None:
    _club_id, event_id = _setup_event()
    other_club_id, _other_event_id = _setup_event()
    # document.manage granted, but scoped to a DIFFERENT club than the
    # target Event's — and no event.manage grant at all.
    actor_id = _make_actor(other_club_id, permission_codes=["document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404, response.text


# --- 17/18. inaccessible / nonexistent Event ----------------------------


@requires_postgres
def test_inaccessible_event_is_denied(client: TestClient) -> None:
    _club_id, event_id = _setup_event()
    other_club_id, _other_event_id = _setup_event()
    # Both permissions granted, but scoped to a different Club entirely.
    actor_id = _make_actor(
        other_club_id, permission_codes=["event.read", "document.read"]
    )
    _authenticate_as(actor_id)

    response = client.get(_list_url(event_id))

    assert response.status_code == 404, response.text


@requires_postgres
def test_nonexistent_event_is_denied(client: TestClient) -> None:
    club_id, _event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_list_url(uuid.uuid4()))

    assert response.status_code == 404, response.text


@requires_postgres
def test_nonexistent_requirement_is_denied(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.patch(
        _detail_url(event_id, uuid.uuid4()),
        json={"required": False},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404, response.text


@requires_postgres
def test_requirement_from_a_different_event_is_not_reachable(client: TestClient) -> None:
    club_id, event_id_a = _setup_event()
    _club_id_b, event_id_b = _setup_event()
    requirement_id = _create_requirement(event_id_b, document_type="medical_certificate")
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.patch(
        _detail_url(event_id_a, requirement_id),
        json={"required": False},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 404, response.text


# --- 19. fail-closed (unauthenticated) ----------------------------------


@requires_postgres
def test_unauthenticated_request_is_denied(client: TestClient) -> None:
    _club_id, event_id = _setup_event()

    response = client.get(_list_url(event_id))

    assert response.status_code == 401, response.text


# --- 20. response security -------------------------------------------


@requires_postgres
def test_response_never_contains_storage_or_document_internals(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    response = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 201, response.text
    body_text = response.text
    for forbidden in (
        "storage_key",
        "file_id",
        "storage_backend",
        "checksum",
        "original_name",
        "mime_type",
    ):
        assert forbidden not in body_text
    assert set(response.json().keys()) == {"id", "event_id", "document_type", "required"}


# --- 21. transaction rollback on mutation failure ---------------------


@requires_postgres
def test_failed_create_due_to_duplicate_leaves_original_row_untouched(client: TestClient) -> None:
    club_id, event_id = _setup_event()
    actor_id = _make_actor(club_id, permission_codes=["event.manage", "document.manage"])
    _authenticate_as(actor_id)

    first = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": True},
        headers=_csrf_headers(client),
    )
    assert first.status_code == 201, first.text

    second = client.post(
        _list_url(event_id),
        json={"document_type": "medical_certificate", "required": False},
        headers=_csrf_headers(client),
    )
    assert second.status_code == 409, second.text

    with session_scope() as session:
        rows = session.execute(
            select(EventDocumentRequirement).where(
                EventDocumentRequirement.event_id == event_id,
                EventDocumentRequirement.document_type == "medical_certificate",
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].required is True
