"""HTTP-level integration tests for the Event document requirements check
(TH-0117.4 / Issue #162, ADR-0040 §5):

    GET /api/v1/events/{event_id}/document-requirements/{person_id}

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_person_documents_api.py's
fixture/factory conventions.
"""

import datetime as dt
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event
from app.db.identity import Club, ClubMembership, Person, User
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
        "joined_at": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


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


def _make_file(user: User, **overrides: object) -> File:
    defaults: dict[str, object] = {
        "storage_key": f"documents/test/{uuid.uuid4()}",
        "original_name": "cert.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 3,
        "checksum": hashlib.sha256(b"abc").hexdigest(),
        "storage_backend": "local",
        "created_by": user.id,
    }
    defaults.update(overrides)
    return File(**defaults)  # type: ignore[arg-type]


def _make_document(
    person: Person, file_row: File, *, document_group_id: uuid.UUID, version_number: int, **kw
) -> Document:
    defaults: dict[str, object] = {
        "person_id": person.id,
        "document_group_id": document_group_id,
        "version_number": version_number,
        "document_type": "medical_certificate",
        "status": "active",
        "file_id": file_row.id,
    }
    defaults.update(kw)
    return Document(**defaults)  # type: ignore[arg-type]


def _make_requirement(event: Event, **overrides: object) -> EventDocumentRequirement:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "document_type": "medical_certificate",
        "required": True,
    }
    defaults.update(overrides)
    return EventDocumentRequirement(**defaults)  # type: ignore[arg-type]


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    """Ad hoc grant via a throwaway role, isolated from the real admin
    RolePermission wiring — mirrors test_person_documents_api.py's
    identical helper. Calling this more than once for the same user adds
    an additional role/grant rather than replacing the previous one, so a
    caller needing both `event.read` and `document.read` calls it twice.
    """
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


def _setup_event_and_person() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, event_id, person_id) — person has an active
    ClubMembership in the same Club as the Event."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.flush()
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_club_membership(club, person))
        session.commit()
        return club.id, event.id, person.id


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


def _url(event_id: uuid.UUID, person_id: uuid.UUID) -> str:
    return f"/api/v1/events/{event_id}/document-requirements/{person_id}"


# --- authorization -----------------------------------------------------


@requires_postgres
def test_missing_document_read_is_denied(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_and_person()
    # Holds event.read but not document.read.
    actor_id = _make_actor(club_id, permission_codes=["event.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, person_id))

    assert response.status_code == 404, response.text


@requires_postgres
def test_person_read_alone_is_not_sufficient(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_and_person()
    actor_id = _make_actor(club_id, permission_codes=["event.read", "person.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, person_id))

    assert response.status_code == 404, response.text


@requires_postgres
def test_missing_event_read_is_denied(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_and_person()
    # Holds document.read but not event.read.
    actor_id = _make_actor(club_id, permission_codes=["document.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, person_id))

    assert response.status_code == 404, response.text


@requires_postgres
def test_nonexistent_event_is_denied(client: TestClient) -> None:
    club_id, _event_id, person_id = _setup_event_and_person()
    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(uuid.uuid4(), person_id))

    assert response.status_code == 404, response.text


@requires_postgres
def test_nonexistent_person_is_denied(client: TestClient) -> None:
    club_id, event_id, _person_id = _setup_event_and_person()
    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, uuid.uuid4()))

    assert response.status_code == 404, response.text


@requires_postgres
def test_authorized_request_succeeds(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_and_person()
    with session_scope() as session:
        event = session.get(Event, event_id)
        session.add(_make_requirement(event))
        session.commit()
    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, person_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event_id"] == str(event_id)
    assert body["person_id"] == str(person_id)
    assert body["requirements"] == [
        {"document_type": "medical_certificate", "required": True, "result": "missing"}
    ]


# --- end-to-end requirement results --------------------------------------


@requires_postgres
def test_mixed_results_over_http(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_and_person()
    with session_scope() as session:
        event = session.get(Event, event_id)
        session.add_all(
            [
                _make_requirement(event, document_type="medical_certificate"),
                _make_requirement(event, document_type="insurance"),
            ]
        )
        person = session.get(Person, person_id)
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([actor_person, actor_user])
        session.flush()
        file_row = _make_file(actor_user)
        session.add(file_row)
        session.flush()
        session.add(
            _make_document(
                person,
                file_row,
                document_group_id=uuid.uuid4(),
                version_number=1,
                document_type="medical_certificate",
                status="revoked",
            )
        )
        session.commit()

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, person_id))

    assert response.status_code == 200, response.text
    by_type = {item["document_type"]: item["result"] for item in response.json()["requirements"]}
    assert by_type == {"medical_certificate": "expired", "insurance": "missing"}
    # ADR-0040 §5: revoked never surfaces as a fourth result value.
    assert "revoked" not in {item["result"] for item in response.json()["requirements"]}


# --- response security ---------------------------------------------------


@requires_postgres
def test_response_never_exposes_storage_internals(client: TestClient) -> None:
    club_id, event_id, person_id = _setup_event_and_person()
    with session_scope() as session:
        event = session.get(Event, event_id)
        session.add(_make_requirement(event))
        person = session.get(Person, person_id)
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([actor_person, actor_user])
        session.flush()
        file_row = _make_file(actor_user, storage_key="documents/person/secret-key-value")
        session.add(file_row)
        session.flush()
        session.add(
            _make_document(
                person, file_row, document_group_id=uuid.uuid4(), version_number=1, status="active"
            )
        )
        session.commit()

    actor_id = _make_actor(club_id, permission_codes=["event.read", "document.read"])
    _authenticate_as(actor_id)

    response = client.get(_url(event_id, person_id))

    assert response.status_code == 200, response.text
    body_text = response.text
    assert "storage_key" not in body_text
    assert "secret-key-value" not in body_text
    assert "file_id" not in body_text
    assert "storage_backend" not in body_text
    assert "original_name" not in body_text
    assert "checksum" not in body_text
    assert set(response.json().keys()) == {"event_id", "person_id", "requirements"}
    for item in response.json()["requirements"]:
        assert set(item.keys()) == {"document_type", "required", "result"}
