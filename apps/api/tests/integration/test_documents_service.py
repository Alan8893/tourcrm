"""Real PostgreSQL integration tests for app.documents.service and
app.documents.queries (TH-0117.3 / Issue #160, ADR-0040), exercising the
create/list/get application layer directly (no HTTP) against a real
`LocalFileStorage` rooted at `tmp_path` — never the real `var/file-storage`
default.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.audit import AuditLog
from app.db.documents import Document, File
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.documents.queries import get_document_for_person, list_current_documents_for_person
from app.documents.service import create_document, read_document_content
from app.storage.file_storage import FileStorageError
from app.storage.local import LocalFileStorage

from .conftest import requires_postgres

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


class _AlwaysFailingStorage:
    """A FileStorage double whose put() always fails before touching any
    real storage — proves a storage failure never reaches the database.
    """

    backend_name = "always-failing"

    def put(self, storage_key: str, content: bytes) -> None:
        raise FileStorageError("simulated storage failure")

    def get(self, storage_key: str) -> bytes:
        raise NotImplementedError

    def exists(self, storage_key: str) -> bool:
        raise NotImplementedError

    def delete(self, storage_key: str) -> None:
        raise NotImplementedError


def _setup_person(session) -> tuple[Person, User]:
    club = _make_club()
    session.add(club)
    session.flush()
    person = _make_person()
    session.add(person)
    session.flush()
    user = _make_user(person)
    session.add(user)
    session.flush()
    return person, user


# --- create_document ---------------------------------------------------


@requires_postgres
def test_create_document_persists_file_and_document_with_expected_relationship(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"certificate-bytes",
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

        file_row = session.get(File, document.file_id)
        assert file_row is not None
        assert document.person_id == person.id
        assert document.file_id == file_row.id


@requires_postgres
def test_create_document_computes_checksum_and_size_from_actual_bytes(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    content = b"the actual stored bytes, not a client-supplied claim"
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=content,
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

        file_row = session.get(File, document.file_id)
        assert file_row.checksum == hashlib.sha256(content).hexdigest()
        assert file_row.size_bytes == len(content)


@requires_postgres
def test_create_document_stores_binary_reachable_through_filestorage(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    content = b"binary-round-trip"
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=content,
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        file_row = session.get(File, document.file_id)

    assert storage.get(file_row.storage_key) == content


@requires_postgres
def test_create_document_zero_byte_content_is_supported(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"",
            original_name="empty.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        file_row = session.get(File, document.file_id)
        assert file_row.size_bytes == 0
        assert file_row.checksum == hashlib.sha256(b"").hexdigest()

    assert storage.get(file_row.storage_key) == b""


@requires_postgres
def test_create_document_sets_initial_status_active(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"x",
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        assert document.status == "active"


@requires_postgres
def test_create_document_first_version_is_one_with_a_fresh_group_id(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"x",
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        assert document.version_number == 1
        assert document.document_group_id == document.id


@requires_postgres
def test_create_document_two_calls_produce_distinct_group_ids(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)

        first = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"first",
            original_name="a.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        second = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"second",
            original_name="b.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        assert first.document_group_id != second.document_group_id


@requires_postgres
def test_create_document_records_document_created_audit_event_without_sensitive_data(
    tmp_path,
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)

        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=b"x",
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

        audit_row = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.created", AuditLog.resource_id == document.id
            )
        ).scalar_one()
        assert audit_row.outcome == "success"
        assert audit_row.actor_user_id == user.id
        details = audit_row.details or {}
        assert all(not isinstance(value, bytes) for value in details.values())
        serialized = str(details)
        assert "storage_key" not in serialized
        assert str(tmp_path) not in serialized


@requires_postgres
def test_create_document_storage_failure_leaves_no_committed_db_rows() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        person_id = person.id
        user_id = user.id
        session.commit()

    with session_scope() as session:
        with pytest.raises(FileStorageError):
            create_document(
                session,
                _AlwaysFailingStorage(),
                person_id=person_id,
                document_type="medical_certificate",
                content=b"x",
                original_name="cert.pdf",
                mime_type="application/pdf",
                issued_at=None,
                expires_at=None,
                actor_user_id=user_id,
            )

    with session_scope() as session:
        remaining = session.execute(
            select(Document).where(Document.person_id == person_id)
        ).scalars().all()
        assert remaining == []
        remaining_files = session.execute(
            select(File).where(File.created_by == user_id)
        ).scalars().all()
        assert remaining_files == []


@requires_postgres
def test_create_document_db_failure_after_storage_success_cleans_up_stored_object(
    tmp_path,
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    nonexistent_person_id = uuid.uuid4()
    with session_scope() as session:
        _, user = _setup_person(session)
        session.commit()
        user_id = user.id

    with session_scope() as session:
        with pytest.raises(IntegrityError):
            create_document(
                session,
                storage,
                # No Person row exists for this id: Document.person_id's
                # FK constraint fails at flush, *after* storage.put()
                # already succeeded — proving the cleanup path runs.
                person_id=nonexistent_person_id,
                document_type="medical_certificate",
                content=b"orphan-candidate",
                original_name="cert.pdf",
                mime_type="application/pdf",
                issued_at=None,
                expires_at=None,
                actor_user_id=user_id,
            )

    assert list(tmp_path.rglob("*.pdf")) == []
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


# --- list_current_documents_for_person / get_document_for_person -----------


def _make_document_row(
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


def _make_file_row(user: User, **overrides: object) -> File:
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


@requires_postgres
def test_list_current_documents_returns_only_the_current_version() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        file_v1 = _make_file_row(user)
        file_v2 = _make_file_row(user)
        session.add_all([file_v1, file_v2])
        session.flush()

        group_id = uuid.uuid4()
        v1 = _make_document_row(
            person, file_v1, document_group_id=group_id, version_number=1, status="expired"
        )
        v2 = _make_document_row(person, file_v2, document_group_id=group_id, version_number=2)
        session.add_all([v1, v2])
        session.commit()

        rows, total = list_current_documents_for_person(
            session, person_id=person.id, page=1, page_size=50
        )
        assert total == 1
        assert len(rows) == 1
        assert rows[0].id == v2.id
        assert rows[0].version_number == 2


@requires_postgres
def test_list_current_documents_excludes_documents_of_other_persons() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        other_person = _make_person()
        session.add(other_person)
        session.flush()
        file_row = _make_file_row(user)
        other_file_row = _make_file_row(user)
        session.add_all([file_row, other_file_row])
        session.flush()

        doc = _make_document_row(
            person, file_row, document_group_id=uuid.uuid4(), version_number=1
        )
        other_doc = _make_document_row(
            other_person, other_file_row, document_group_id=uuid.uuid4(), version_number=1
        )
        session.add_all([doc, other_doc])
        session.commit()

        rows, total = list_current_documents_for_person(
            session, person_id=person.id, page=1, page_size=50
        )
        assert total == 1
        assert [row.id for row in rows] == [doc.id]


@requires_postgres
def test_get_document_for_person_returns_a_historical_version_by_id() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        file_v1 = _make_file_row(user)
        file_v2 = _make_file_row(user)
        session.add_all([file_v1, file_v2])
        session.flush()

        group_id = uuid.uuid4()
        v1 = _make_document_row(person, file_v1, document_group_id=group_id, version_number=1)
        v2 = _make_document_row(person, file_v2, document_group_id=group_id, version_number=2)
        session.add_all([v1, v2])
        session.commit()

        fetched = get_document_for_person(session, person_id=person.id, document_id=v1.id)
        assert fetched is not None
        assert fetched.version_number == 1


@requires_postgres
def test_get_document_for_person_returns_none_for_wrong_person() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        other_person = _make_person()
        session.add(other_person)
        session.flush()
        file_row = _make_file_row(user)
        session.add(file_row)
        session.flush()
        doc = _make_document_row(
            person, file_row, document_group_id=uuid.uuid4(), version_number=1
        )
        session.add(doc)
        session.commit()

        fetched = get_document_for_person(
            session, person_id=other_person.id, document_id=doc.id
        )
        assert fetched is None


@requires_postgres
def test_get_document_for_person_returns_none_for_nonexistent_id() -> None:
    with session_scope() as session:
        person, _user = _setup_person(session)
        session.commit()

        fetched = get_document_for_person(
            session, person_id=person.id, document_id=uuid.uuid4()
        )
        assert fetched is None


# --- read_document_content ---------------------------------------------


@requires_postgres
def test_read_document_content_returns_stored_bytes_and_records_download_audit(
    tmp_path,
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    content = b"downloadable-content"
    with session_scope() as session:
        person, user = _setup_person(session)
        document = create_document(
            session,
            storage,
            person_id=person.id,
            document_type="medical_certificate",
            content=content,
            original_name="cert.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        file_row = session.get(File, document.file_id)

        result = read_document_content(
            session, storage, document=document, file=file_row, actor_user_id=user.id
        )
        assert result == content

        audit_row = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.downloaded", AuditLog.resource_id == document.id
            )
        ).scalar_one()
        assert audit_row.outcome == "success"
