"""HTTP-level integration tests for the Person profile photo API
(TH-0119 / Issue #176; docs/05-api/profile-photo-api.md):

    GET    /api/v1/persons/{person_id}/photo
    PUT    /api/v1/persons/{person_id}/photo
    DELETE /api/v1/persons/{person_id}/photo
    GET    /api/v1/auth/me  (Person summary id/photo_file_id)

Against the real app and PostgreSQL, mirroring
test_person_documents_api.py's fixture conventions. `get_file_storage` is
overridden to a `LocalFileStorage` rooted at `tmp_path`.
"""

import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import File
from app.db.identity import Person, User
from app.db.session import session_scope
from app.main import app
from app.storage.file_storage import FileStorageError
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres


@pytest.fixture
def storage_root(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def client(storage_root) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=storage_root)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- helpers ---------------------------------------------------------------


def _image_bytes(fmt: str = "PNG", color=(10, 120, 200), size=(300, 300)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _grant(user_id: uuid.UUID, permission_code: str, scope_type: str) -> None:
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
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type=scope_type))
        session.commit()


def _make_self_user() -> tuple[uuid.UUID, uuid.UUID]:
    """A user holding only `self`-scoped person.read/person.update — the
    ordinary "any role may manage their own photo" case (ADR-0035)."""
    with session_scope() as session:
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([person, user])
        session.commit()
        user_id, person_id = user.id, person.id
    _grant(user_id, "person.read", "self")
    _grant(user_id, "person.update", "self")
    return user_id, person_id


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _put(
    client: TestClient, person_id: uuid.UUID, content: bytes, *, name="p.png", mime="image/png"
):
    return client.put(
        f"/api/v1/persons/{person_id}/photo",
        files={"photo": (name, content, mime)},
        headers=_csrf_headers(client),
    )


def _delete(client: TestClient, person_id: uuid.UUID):
    return client.delete(f"/api/v1/persons/{person_id}/photo", headers=_csrf_headers(client))


def _stored_objects(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _photo_file_id(person_id: uuid.UUID) -> uuid.UUID | None:
    with session_scope() as session:
        person = session.get(Person, person_id)
        assert person is not None
        return person.photo_file_id


def _photo_files(person_id: uuid.UUID) -> list[File]:
    with session_scope() as session:
        rows = session.execute(
            select(File).where(File.storage_key.like(f"photos/person/{person_id}/%"))
        ).scalars()
        return list(rows)


# --- upload ----------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize(
    "fmt,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_initial_upload_of_own_photo_stores_512_webp(
    client: TestClient, storage_root: Path, fmt: str, mime: str
) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)

    response = _put(client, person_id, _image_bytes(fmt), mime=mime)

    assert response.status_code == 200, response.text
    photo_file_id = response.json()["photo_file_id"]
    assert photo_file_id is not None
    assert "storage_key" not in response.text
    assert "photos/person" not in response.text
    assert str(storage_root) not in response.text

    objects = _stored_objects(storage_root)
    assert len(objects) == 1  # processed crop only; original never retained
    stored = Image.open(objects[0])
    assert stored.format == "WEBP"
    assert stored.size == (512, 512)
    assert stored.mode == "RGB"

    files = _photo_files(person_id)
    assert [str(f.id) for f in files] == [photo_file_id]
    assert files[0].mime_type == "image/webp"


@requires_postgres
def test_fake_image_is_rejected_and_nothing_stored(client: TestClient, storage_root: Path) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)

    response = _put(client, person_id, b"not an image at all", name="x.jpg", mime="image/jpeg")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_photo"
    assert _stored_objects(storage_root) == []
    assert _photo_file_id(person_id) is None


@requires_postgres
def test_upload_over_10_mb_is_rejected(client: TestClient, storage_root: Path) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)

    response = _put(client, person_id, b"\0" * (10 * 1024 * 1024 + 1))

    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "photo_too_large"
    assert _stored_objects(storage_root) == []


# --- read / auth/me -------------------------------------------------------


@requires_postgres
def test_get_own_photo_and_auth_me_projection(client: TestClient) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)

    missing = client.get(f"/api/v1/persons/{person_id}/photo")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "photo_not_found"

    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["person"]["id"] == str(person_id)
    assert me["user"]["person"]["photo_file_id"] is None

    photo_file_id = _put(client, person_id, _image_bytes()).json()["photo_file_id"]

    response = client.get(f"/api/v1/persons/{person_id}/photo?v={photo_file_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["etag"] == f'"{photo_file_id}"'
    assert "no-cache" in response.headers["cache-control"]
    assert Image.open(io.BytesIO(response.content)).size == (512, 512)

    not_modified = client.get(
        f"/api/v1/persons/{person_id}/photo", headers={"If-None-Match": f'"{photo_file_id}"'}
    )
    assert not_modified.status_code == 304

    me = client.get("/api/v1/auth/me")
    assert me.json()["user"]["person"]["photo_file_id"] == photo_file_id
    assert "storage_key" not in me.text


# --- authorization --------------------------------------------------------


@requires_postgres
def test_other_persons_photo_is_hidden_and_not_writable(client: TestClient) -> None:
    owner_id, owner_person_id = _make_self_user()
    _authenticate_as(owner_id)
    photo_file_id = _put(client, owner_person_id, _image_bytes()).json()["photo_file_id"]

    stranger_id, _ = _make_self_user()
    _authenticate_as(stranger_id)

    assert client.get(f"/api/v1/persons/{owner_person_id}/photo").status_code == 404
    assert _put(client, owner_person_id, _image_bytes()).status_code == 404
    assert _delete(client, owner_person_id).status_code == 404
    assert str(_photo_file_id(owner_person_id)) == photo_file_id


@requires_postgres
def test_read_only_access_cannot_modify_photo(client: TestClient) -> None:
    owner_id, owner_person_id = _make_self_user()
    _authenticate_as(owner_id)
    _put(client, owner_person_id, _image_bytes())

    reader_id, _ = _make_self_user()
    _grant(reader_id, "person.read", "all")
    _authenticate_as(reader_id)

    assert client.get(f"/api/v1/persons/{owner_person_id}/photo").status_code == 200
    assert _put(client, owner_person_id, _image_bytes()).status_code == 404
    assert _delete(client, owner_person_id).status_code == 404


@requires_postgres
def test_unauthenticated_access_is_rejected(client: TestClient) -> None:
    _, person_id = _make_self_user()
    assert client.get(f"/api/v1/persons/{person_id}/photo").status_code == 401


# --- replacement lifecycle -----------------------------------------------


@requires_postgres
def test_replacement_deletes_old_object_after_commit(
    client: TestClient, storage_root: Path
) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)

    first = _put(client, person_id, _image_bytes(color=(255, 0, 0))).json()["photo_file_id"]
    first_objects = _stored_objects(storage_root)
    second = _put(client, person_id, _image_bytes(color=(0, 255, 0))).json()["photo_file_id"]

    assert first != second
    assert str(_photo_file_id(person_id)) == second
    objects = _stored_objects(storage_root)
    assert len(objects) == 1
    assert objects[0] not in first_objects
    assert objects[0].name == second
    # No avatar history: the previous File row is gone too.
    assert [str(f.id) for f in _photo_files(person_id)] == [second]

    with session_scope() as session:
        audits = session.execute(
            select(AuditLog).where(
                AuditLog.resource_id == person_id, AuditLog.action == "person.updated"
            )
        ).scalars()
        changes = [a.details["changes"]["photo_file_id"] for a in audits]
    assert {"from": first, "to": second} in changes


@requires_postgres
def test_failed_replacement_preserves_old_photo_and_compensates(
    client: TestClient, storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)
    original = _put(client, person_id, _image_bytes()).json()["photo_file_id"]
    original_objects = _stored_objects(storage_root)

    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated DB persistence failure")

    monkeypatch.setattr("app.people.photo.record_audit_event", _fail)
    with pytest.raises(RuntimeError):
        _put(client, person_id, _image_bytes(color=(0, 0, 0)))

    assert str(_photo_file_id(person_id)) == original
    assert _stored_objects(storage_root) == original_objects  # new object compensated
    assert [str(f.id) for f in _photo_files(person_id)] == [original]
    assert client.get(f"/api/v1/persons/{person_id}/photo").status_code == 200


@requires_postgres
def test_old_object_delete_failure_keeps_new_photo(client: TestClient, storage_root: Path) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)
    _put(client, person_id, _image_bytes())

    class _DeleteFailingStorage(LocalFileStorage):
        def delete(self, storage_key: str) -> None:
            raise FileStorageError("simulated storage outage")

    app.dependency_overrides[get_file_storage] = lambda: _DeleteFailingStorage(root=storage_root)
    response = _put(client, person_id, _image_bytes(color=(0, 0, 0)))

    assert response.status_code == 200, response.text
    new_id = response.json()["photo_file_id"]
    assert str(_photo_file_id(person_id)) == new_id
    assert client.get(f"/api/v1/persons/{person_id}/photo").headers["etag"] == f'"{new_id}"'


# --- delete ---------------------------------------------------------------


@requires_postgres
def test_delete_and_repeated_delete(client: TestClient, storage_root: Path) -> None:
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)
    _put(client, person_id, _image_bytes())

    first = _delete(client, person_id)
    assert first.status_code == 204
    assert _photo_file_id(person_id) is None
    assert _stored_objects(storage_root) == []
    assert _photo_files(person_id) == []
    assert client.get(f"/api/v1/persons/{person_id}/photo").status_code == 404
    assert client.get("/api/v1/auth/me").json()["user"]["person"]["photo_file_id"] is None

    second = _delete(client, person_id)
    assert second.status_code == 204


@requires_postgres
def test_foreign_file_reference_is_never_served_or_deleted(
    client: TestClient, storage_root: Path
) -> None:
    """A `photo_file_id` pointing at a File outside this Person's photo
    prefix (e.g. set through the legacy PATCH field) is never streamed as
    the photo nor removed from storage — only the reference is cleared."""
    user_id, person_id = _make_self_user()
    _authenticate_as(user_id)
    storage = LocalFileStorage(root=storage_root)
    foreign_key = f"documents/person/{uuid.uuid4()}/{uuid.uuid4()}"
    storage.put(foreign_key, b"%PDF-1.4 someone else's document")
    with session_scope() as session:
        foreign = File(
            storage_key=foreign_key,
            original_name="doc.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            checksum="x",
            storage_backend="local",
        )
        session.add(foreign)
        session.flush()
        person = session.get(Person, person_id)
        assert person is not None
        person.photo_file_id = foreign.id
        session.commit()
        foreign_id = foreign.id

    assert client.get(f"/api/v1/persons/{person_id}/photo").status_code == 404
    assert _delete(client, person_id).status_code == 204
    assert _photo_file_id(person_id) is None
    assert storage.exists(foreign_key)
    with session_scope() as session:
        assert session.get(File, foreign_id) is not None
