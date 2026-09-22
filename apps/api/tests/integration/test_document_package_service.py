"""Real PostgreSQL integration tests for app.documents.package
(TH-0117.9 / Issue #172, ADR-0040 §5/§6/§7), exercising the competition
document package export application layer directly (no HTTP) against a
real `LocalFileStorage` rooted at `tmp_path` — never the real
`var/file-storage` default.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile

import pytest
from sqlalchemy import select

from app.db.audit import AuditLog
from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event, EventParticipation
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.documents.package import (
    DocumentPackageIncompleteError,
    _build_zip_archive,
    _evaluate_entries,
    _list_registered_participants,
    _list_requirements,
    generate_event_document_package,
)
from app.storage.file_storage import FileStorageError, ObjectNotFoundError
from app.storage.local import LocalFileStorage

from .conftest import requires_postgres


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
        "title": "Regional Championship",
        "start_at": _utc(2026, 10, 1, 10, 0),
        "end_at": _utc(2026, 10, 1, 12, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_participation(event: Event, person: Person, **overrides: object) -> EventParticipation:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "person_id": person.id,
        "registration_status": "registered",
    }
    defaults.update(overrides)
    return EventParticipation(**defaults)  # type: ignore[arg-type]


def _make_requirement(event: Event, **overrides: object) -> EventDocumentRequirement:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "document_type": "medical_certificate",
        "required": True,
    }
    defaults.update(overrides)
    return EventDocumentRequirement(**defaults)  # type: ignore[arg-type]


def _make_file(user: User, *, content: bytes = b"abc", **overrides: object) -> File:
    defaults: dict[str, object] = {
        "storage_key": f"documents/test/{uuid.uuid4()}",
        "original_name": "cert.pdf",
        "mime_type": "application/pdf",
        "size_bytes": len(content),
        "checksum": hashlib.sha256(content).hexdigest(),
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


def _setup_club_and_event(session, **event_overrides: object) -> tuple[Club, Event]:
    club = _make_club()
    session.add(club)
    session.flush()
    event = _make_event(club, **event_overrides)
    session.add(event)
    session.flush()
    return club, event


def _add_registered_participant(
    session,
    club: Club,
    event: Event,
    *,
    content: bytes | None = None,
    storage=None,
    **person_overrides,
) -> tuple[Person, User]:
    person = _make_person(**person_overrides)
    session.add(person)
    session.flush()
    session.add(_make_club_membership(club, person))
    session.add(_make_participation(event, person))
    actor_person = _make_person()
    actor_user = _make_user(actor_person)
    session.add_all([actor_person, actor_user])
    session.flush()
    if content is not None and storage is not None:
        file_row = _make_file(actor_user, content=content)
        session.add(file_row)
        session.flush()
        storage.put(file_row.storage_key, content)
        session.add(
            _make_document(person, file_row, document_group_id=uuid.uuid4(), version_number=1)
        )
    return person, actor_user


def _manifest(archive_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))


# --- success ---------------------------------------------------------------


@requires_postgres
def test_one_participant_one_valid_required_document(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(
            session, club, event, content=b"certificate-bytes", storage=storage
        )
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    assert zipfile.is_zipfile(io.BytesIO(archive_bytes))
    manifest = _manifest(archive_bytes)
    assert len(manifest["documents"]) == 1
    entry = manifest["documents"][0]
    assert entry["result"] == "valid"
    assert entry["document_type"] == "medical_certificate"
    assert entry["required"] is True
    assert entry["filename"] == "participants/001/medical_certificate.pdf"

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.read(entry["filename"]) == b"certificate-bytes"


@requires_postgres
def test_multiple_participants(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(
            session, club, event, content=b"a-content", storage=storage, last_name="Aaa"
        )
        _add_registered_participant(
            session, club, event, content=b"b-content", storage=storage, last_name="Bbb"
        )
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    manifest = _manifest(archive_bytes)
    assert len(manifest["documents"]) == 2
    ordinals = sorted(item["participant_ordinal"] for item in manifest["documents"])
    assert ordinals == [1, 2]


@requires_postgres
def test_multiple_document_requirements(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add_all(
            [
                _make_requirement(event, document_type="medical_certificate"),
                _make_requirement(event, document_type="insurance"),
            ]
        )
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        file_row = _make_file(actor_user, content=b"med")
        session.add(file_row)
        session.flush()
        storage.put(file_row.storage_key, b"med")
        session.add(
            _make_document(
                person,
                file_row,
                document_group_id=uuid.uuid4(),
                version_number=1,
                document_type="medical_certificate",
            )
        )
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError) as exc_info:
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    by_type = {e.document_type: e.result for e in exc_info.value.entries}
    assert by_type == {"medical_certificate": "valid", "insurance": "missing"}


@requires_postgres
def test_optional_requirement_with_valid_document_is_included(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event, document_type="waiver", required=False))
        person, actor_user = _add_registered_participant(session, club, event)
        session.commit()

    with session_scope() as session:
        event = session.get(Event, event.id)
        person = session.get(Person, person.id)
        actor_user = session.get(User, actor_user.id)
        file_row = _make_file(actor_user, content=b"waiver-bytes")
        session.add(file_row)
        session.flush()
        storage.put(file_row.storage_key, b"waiver-bytes")
        session.add(
            _make_document(
                person,
                file_row,
                document_group_id=uuid.uuid4(),
                version_number=1,
                document_type="waiver",
            )
        )
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    manifest = _manifest(archive_bytes)
    assert len(manifest["documents"]) == 1
    assert manifest["documents"][0]["result"] == "valid"
    assert manifest["documents"][0]["required"] is False


# --- incomplete warning ----------------------------------------------------


@requires_postgres
def test_missing_required_document_triggers_incomplete(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(session, club, event)
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError) as exc_info:
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    assert any(e.result == "missing" for e in exc_info.value.entries)


@requires_postgres
def test_expired_required_document_triggers_incomplete(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        file_row = _make_file(actor_user, content=b"x")
        session.add(file_row)
        session.flush()
        storage.put(file_row.storage_key, b"x")
        session.add(
            _make_document(
                person, file_row, document_group_id=uuid.uuid4(), version_number=1, status="expired"
            )
        )
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError) as exc_info:
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    assert any(e.result == "expired" for e in exc_info.value.entries)


@requires_postgres
def test_revoked_current_document_triggers_expired_warning(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        file_row = _make_file(actor_user, content=b"x")
        session.add(file_row)
        session.flush()
        storage.put(file_row.storage_key, b"x")
        session.add(
            _make_document(
                person, file_row, document_group_id=uuid.uuid4(), version_number=1, status="revoked"
            )
        )
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError) as exc_info:
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    entries = [e for e in exc_info.value.entries if e.document_type == "medical_certificate"]
    assert len(entries) == 1
    assert entries[0].result == "expired"


@requires_postgres
def test_historical_valid_version_does_not_satisfy_current_revoked(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        group_id = uuid.uuid4()
        v1_file = _make_file(actor_user, content=b"v1")
        v2_file = _make_file(actor_user, content=b"v2")
        session.add_all([v1_file, v2_file])
        session.flush()
        storage.put(v1_file.storage_key, b"v1")
        storage.put(v2_file.storage_key, b"v2")
        session.add(
            _make_document(
                person, v1_file, document_group_id=group_id, version_number=1, status="active"
            )
        )
        session.add(
            _make_document(
                person, v2_file, document_group_id=group_id, version_number=2, status="revoked"
            )
        )
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError) as exc_info:
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    assert [e.result for e in exc_info.value.entries] == ["expired"]


@requires_postgres
def test_confirm_incomplete_false_prevents_package(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(session, club, event)
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError):
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    with session_scope() as session:
        audit_rows = session.execute(
            select(AuditLog).where(AuditLog.action == "document.exported")
        ).scalars().all()
        assert len(audit_rows) == 0


@requires_postgres
def test_confirm_incomplete_true_generates_package(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(session, club, event)
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=True
        )

    manifest = _manifest(archive_bytes)
    assert manifest["documents"][0]["result"] == "missing"
    assert manifest["documents"][0]["filename"] is None


@requires_postgres
def test_optional_missing_participates_in_warning(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event, document_type="waiver", required=False))
        _, actor_user = _add_registered_participant(session, club, event)
        session.commit()

        with pytest.raises(DocumentPackageIncompleteError) as exc_info:
            generate_event_document_package(
                session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
            )

    assert exc_info.value.entries[0].required is False
    assert exc_info.value.entries[0].result == "missing"


@requires_postgres
def test_no_missing_or_expired_confirm_incomplete_has_no_effect(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(
            session, club, event, content=b"content", storage=storage
        )
        session.commit()

        archive_false = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    with session_scope() as session:
        event = session.get(Event, event.id)
        archive_true = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=True
        )

    assert _manifest(archive_false)["documents"] == _manifest(archive_true)["documents"]


# --- binary correctness ----------------------------------------------------


@requires_postgres
def test_only_current_valid_binary_is_included(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        group_id = uuid.uuid4()
        v1_file = _make_file(actor_user, content=b"old")
        v2_file = _make_file(actor_user, content=b"new")
        session.add_all([v1_file, v2_file])
        session.flush()
        storage.put(v1_file.storage_key, b"old")
        storage.put(v2_file.storage_key, b"new")
        session.add(
            _make_document(person, v1_file, document_group_id=group_id, version_number=1)
        )
        session.add(
            _make_document(person, v2_file, document_group_id=group_id, version_number=2)
        )
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    manifest = _manifest(archive_bytes)
    filename = manifest["documents"][0]["filename"]
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.read(filename) == b"new"
        assert b"old" not in archive.read(filename)


@requires_postgres
def test_historical_binary_never_appears_anywhere_in_archive(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        group_id = uuid.uuid4()
        v1_file = _make_file(
            actor_user, content=b"historical-secret-bytes"
        )
        v2_file = _make_file(actor_user, content=b"current-bytes")
        session.add_all([v1_file, v2_file])
        session.flush()
        storage.put(v1_file.storage_key, b"historical-secret-bytes")
        storage.put(v2_file.storage_key, b"current-bytes")
        session.add(
            _make_document(person, v1_file, document_group_id=group_id, version_number=1)
        )
        session.add(
            _make_document(person, v2_file, document_group_id=group_id, version_number=2)
        )
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    assert b"historical-secret-bytes" not in archive_bytes


# --- archive/manifest content -------------------------------------------


@requires_postgres
def test_archive_contains_no_internal_uuid_or_storage_key(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(
            session, club, event, content=b"content", storage=storage
        )
        person_id = person.id
        session.commit()

        with session_scope() as inner:
            document = inner.execute(
                select(Document).where(Document.person_id == person_id)
            ).scalar_one()
            document_id = document.id
            file_id = document.file_id
            file_row = inner.get(File, file_id)
            storage_key = file_row.storage_key

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            assert str(person_id) not in name
            assert str(document_id) not in name
            assert str(file_id) not in name
            assert storage_key not in name
    manifest_text = _manifest(archive_bytes)
    manifest_json = json.dumps(manifest_text)
    assert str(person_id) not in manifest_json
    assert str(document_id) not in manifest_json
    assert str(file_id) not in manifest_json
    assert storage_key not in manifest_json
    assert "storage_key" not in manifest_json
    assert "checksum" not in manifest_json


@requires_postgres
def test_manifest_contains_expected_fields(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session, title="Spring Cup")
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(
            session, club, event, content=b"content", storage=storage
        )
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    manifest = _manifest(archive_bytes)
    assert manifest["event_display_name"] == "Spring Cup"
    assert "generated_at" in manifest
    assert "event_date" in manifest
    entry = manifest["documents"][0]
    assert set(entry.keys()) == {
        "participant_ordinal",
        "participant_display_name",
        "document_type",
        "required",
        "result",
        "filename",
    }


# --- storage -----------------------------------------------------------


class _MissingObjectStorage:
    backend_name = "missing"

    def put(self, storage_key: str, content: bytes) -> None:
        raise NotImplementedError

    def get(self, storage_key: str) -> bytes:
        raise ObjectNotFoundError(storage_key)

    def exists(self, storage_key: str) -> bool:
        return False

    def delete(self, storage_key: str) -> None:
        raise NotImplementedError


@requires_postgres
def test_missing_filestorage_object_fails_safely_without_audit(tmp_path) -> None:
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        file_row = _make_file(actor_user, content=b"x", storage_key="documents/test/gone")
        session.add(file_row)
        session.flush()
        session.add(
            _make_document(person, file_row, document_group_id=uuid.uuid4(), version_number=1)
        )
        session.commit()

        with pytest.raises(FileStorageError):
            generate_event_document_package(
                session,
                _MissingObjectStorage(),
                event=event,
                actor_user_id=actor_user.id,
                confirm_incomplete=False,
            )

    with session_scope() as session:
        audit_rows = session.execute(
            select(AuditLog).where(AuditLog.action == "document.exported")
        ).scalars().all()
        assert len(audit_rows) == 0


class _SpyStorage:
    def __init__(self, delegate: LocalFileStorage) -> None:
        self._delegate = delegate
        self.get_calls: list[str] = []

    @property
    def backend_name(self) -> str:
        return self._delegate.backend_name

    def put(self, storage_key: str, content: bytes) -> None:
        self._delegate.put(storage_key, content)

    def get(self, storage_key: str) -> bytes:
        self.get_calls.append(storage_key)
        return self._delegate.get(storage_key)

    def exists(self, storage_key: str) -> bool:
        return self._delegate.exists(storage_key)

    def delete(self, storage_key: str) -> None:
        self._delegate.delete(storage_key)


@requires_postgres
def test_filestorage_is_actually_used_for_binary_reads(tmp_path) -> None:
    spy = _SpyStorage(LocalFileStorage(root=tmp_path))
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(
            session, club, event, content=b"content", storage=spy
        )
        file_row = session.execute(
            select(File).where(File.created_by == actor_user.id)
        ).scalar_one()
        expected_key = file_row.storage_key
        session.commit()

        generate_event_document_package(
            session, spy, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    assert spy.get_calls == [expected_key]


# --- audit -----------------------------------------------------------------


@requires_postgres
def test_successful_export_creates_document_exported_audit(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        _, actor_user = _add_registered_participant(
            session, club, event, content=b"content", storage=storage
        )
        session.commit()
        event_id = event.id

        generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.exported", AuditLog.resource_id == event_id
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        audit_row = audit_rows[0]
        assert audit_row.outcome == "success"
        assert audit_row.actor_user_id == actor_user.id
        assert audit_row.resource_type == "event"
        details = audit_row.details or {}
        serialized = json.dumps(details)
        assert "storage_key" not in serialized
        assert "content" not in serialized


# --- edge cases --------------------------------------------------------


@requires_postgres
def test_event_with_no_participants_produces_valid_empty_zip(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([actor_person, actor_user])
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    assert zipfile.is_zipfile(io.BytesIO(archive_bytes))
    manifest = _manifest(archive_bytes)
    assert manifest["documents"] == []


@requires_postgres
def test_event_with_no_requirements_produces_valid_empty_zip(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        _, actor_user = _add_registered_participant(session, club, event)
        session.commit()

        archive_bytes = generate_event_document_package(
            session, storage, event=event, actor_user_id=actor_user.id, confirm_incomplete=False
        )

    assert zipfile.is_zipfile(io.BytesIO(archive_bytes))
    manifest = _manifest(archive_bytes)
    assert manifest["documents"] == []


# --- consistency ---------------------------------------------------------


@requires_postgres
def test_evaluated_entry_binary_is_stable_against_a_later_concurrent_change(tmp_path) -> None:
    """Proves the module's own documented invariant directly: once
    `_evaluate_entries` has captured a specific current `Document` row
    as the `valid` candidate, a change to the group that happens *after*
    that evaluation (simulating a concurrent replace landing in the gap
    between evaluation and archive assembly) does not affect what
    `_build_zip_archive` reads for that already-captured entry — it
    reads via the captured row's own, permanently-fixed `file_id`.
    """
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        club, event = _setup_club_and_event(session)
        session.add(_make_requirement(event))
        person, actor_user = _add_registered_participant(session, club, event)
        session.flush()
        group_id = uuid.uuid4()
        v1_file = _make_file(actor_user, content=b"v1-content")
        session.add(v1_file)
        session.flush()
        storage.put(v1_file.storage_key, b"v1-content")
        session.add(
            _make_document(person, v1_file, document_group_id=group_id, version_number=1)
        )
        session.commit()

        now = dt.datetime.now(dt.timezone.utc)
        participants = _list_registered_participants(session, event_id=event.id)
        requirements = _list_requirements(session, event_id=event.id)
        entries = _evaluate_entries(
            session, participants=participants, requirements=requirements, now=now
        )
        assert entries[0].result == "valid"

        # Simulate a concurrent replace landing after evaluation: a new
        # current version (v2) is inserted; v1 is now historical.
        v2_file = _make_file(actor_user, content=b"v2-content")
        session.add(v2_file)
        session.flush()
        storage.put(v2_file.storage_key, b"v2-content")
        session.add(
            _make_document(person, v2_file, document_group_id=group_id, version_number=2)
        )
        session.commit()

        archive_bytes = _build_zip_archive(session, storage, event=event, entries=entries, now=now)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        filename = archive.namelist()[0]
        assert filename != "manifest.json"
        content = archive.read(filename)
    # The archive reflects what was evaluated (v1), not the version that
    # became current afterward (v2) — no silent mixing of versions.
    assert content == b"v1-content"
