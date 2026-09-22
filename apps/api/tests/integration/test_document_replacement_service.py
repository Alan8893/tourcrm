"""Real PostgreSQL integration tests for app.documents.service.
replace_document (TH-0117.6 / Issue #166, ADR-0040 §1/§4/§7), exercising
the replacement application layer directly (no HTTP) against a real
`LocalFileStorage` rooted at `tmp_path` — never the real `var/file-storage`
default.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.audit import AuditLog
from app.db.documents import Document, File
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.documents.service import NotCurrentVersionError, create_document, replace_document
from app.storage.file_storage import FileStorageError
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


def _create_first_version(
    session, storage, person, user, *, content: bytes = b"v1-content", status: str = "active"
) -> Document:
    document = create_document(
        session,
        storage,
        person_id=person.id,
        document_type="medical_certificate",
        content=content,
        original_name="v1.pdf",
        mime_type="application/pdf",
        issued_at=None,
        expires_at=None,
        actor_user_id=user.id,
    )
    if status != "active":
        document.status = status
        session.commit()
    return document


# --- success -------------------------------------------------------------


@requires_postgres
def test_replace_creates_a_new_version_with_expected_fields(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        new_issued_at = _utc(2026, 1, 1)
        new_expires_at = _utc(2027, 1, 1)
        v2 = replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"v2-content",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=new_issued_at,
            expires_at=new_expires_at,
            actor_user_id=user.id,
        )

        assert v2.id != v1.id
        assert v2.person_id == v1.person_id
        assert v2.document_group_id == v1.document_group_id
        assert v2.version_number == v1.version_number + 1
        assert v2.document_type == v1.document_type
        assert v2.file_id != v1.file_id
        assert v2.status == "active"
        assert v2.issued_at == new_issued_at
        assert v2.expires_at == new_expires_at


@requires_postgres
def test_replace_stores_new_binary_via_filestorage(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        v2 = replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"brand-new-binary",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        v2_file = session.get(File, v2.file_id)

    assert storage.get(v2_file.storage_key) == b"brand-new-binary"


@requires_postgres
def test_replace_supports_zero_byte_content(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        v2 = replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"",
            original_name="empty.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        v2_file = session.get(File, v2.file_id)
        assert v2_file.size_bytes == 0

    assert storage.get(v2_file.storage_key) == b""


# --- history ---------------------------------------------------------------


@requires_postgres
def test_old_document_and_file_remain_unchanged_after_replace(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, content=b"original")
        v1_id = v1.id
        v1_file_id = v1.file_id
        v1_version_number = v1.version_number
        v1_status = v1.status

        replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"replacement",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

    with session_scope() as session:
        reloaded_v1 = session.get(Document, v1_id)
        assert reloaded_v1.file_id == v1_file_id
        assert reloaded_v1.version_number == v1_version_number
        assert reloaded_v1.status == v1_status

        reloaded_file = session.get(File, v1_file_id)
        assert reloaded_file.size_bytes == len(b"original")


@requires_postgres
def test_old_binary_remains_accessible_after_replace(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, content=b"original-bytes")
        v1_file_id = v1.file_id

        replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"new-bytes",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

    with session_scope() as session:
        v1_file = session.get(File, v1_file_id)
        assert storage.get(v1_file.storage_key) == b"original-bytes"


@requires_postgres
def test_historical_version_cannot_be_replaced(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"v2",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

    with session_scope() as session:
        stale_v1 = session.get(Document, v1.id)
        with pytest.raises(NotCurrentVersionError):
            replace_document(
                session,
                storage,
                document=stale_v1,
                person_id=person.id,
                content=b"v3-attempt",
                original_name="v3.pdf",
                mime_type="application/pdf",
                issued_at=None,
                expires_at=None,
                actor_user_id=user.id,
            )

    # No third version/file was created by the rejected attempt.
    with session_scope() as session:
        versions = session.execute(
            select(Document).where(Document.document_group_id == v1.document_group_id)
        ).scalars().all()
        assert len(versions) == 2


@requires_postgres
def test_current_version_can_be_replaced(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        v2 = replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"v2",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

        # v2 is now current and can itself be replaced.
        v3 = replace_document(
            session,
            storage,
            document=v2,
            person_id=person.id,
            content=b"v3",
            original_name="v3.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        assert v3.version_number == 3


# --- status ----------------------------------------------------------------


@pytest.mark.parametrize("current_status", ["active", "expired", "revoked"])
@requires_postgres
def test_replace_from_any_status_produces_an_active_new_version(tmp_path, current_status) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, status=current_status)
        assert v1.status == current_status

        v2 = replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"v2",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )
        assert v2.status == "active"


# --- authorization is the API layer's job — not exercised here -------------


# --- storage failures ------------------------------------------------------


@requires_postgres
def test_storage_failure_leaves_no_new_db_rows(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        v1_group_id = v1.document_group_id
        person_id = person.id
        user_id = user.id
        v1_id = v1.id
        session.commit()

    with session_scope() as session:
        stale_v1 = session.get(Document, v1_id)
        with pytest.raises(FileStorageError):
            replace_document(
                session,
                _AlwaysFailingStorage(),
                document=stale_v1,
                person_id=person_id,
                content=b"v2",
                original_name="v2.pdf",
                mime_type="application/pdf",
                issued_at=None,
                expires_at=None,
                actor_user_id=user_id,
            )

    with session_scope() as session:
        versions = session.execute(
            select(Document).where(Document.document_group_id == v1_group_id)
        ).scalars().all()
        assert len(versions) == 1


@requires_postgres
def test_db_failure_after_storage_success_cleans_up_the_new_object(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        person_id = person.id
        v1_id = v1.id
        session.commit()

    bogus_actor_id = uuid.uuid4()
    with session_scope() as session:
        stale_v1 = session.get(Document, v1_id)
        with pytest.raises(IntegrityError):
            replace_document(
                session,
                storage,
                document=stale_v1,
                person_id=person_id,
                content=b"orphan-candidate",
                original_name="v2.pdf",
                mime_type="application/pdf",
                issued_at=None,
                expires_at=None,
                # No User exists with this id: the File.created_by /
                # Document.uploaded_by FK fails at flush, *after*
                # storage.put() already succeeded.
                actor_user_id=bogus_actor_id,
            )

    stored_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(stored_files) == 1  # only the original v1 binary remains


# --- audit -----------------------------------------------------------------


@requires_postgres
def test_replace_records_exactly_one_document_replaced_audit_without_sensitive_data(
    tmp_path,
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        v2 = replace_document(
            session,
            storage,
            document=v1,
            person_id=person.id,
            content=b"v2",
            original_name="v2.pdf",
            mime_type="application/pdf",
            issued_at=None,
            expires_at=None,
            actor_user_id=user.id,
        )

        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.replaced", AuditLog.resource_id == v2.id
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        audit_row = audit_rows[0]
        assert audit_row.outcome == "success"
        assert audit_row.actor_user_id == user.id

        details = audit_row.details or {}
        assert all(not isinstance(value, bytes) for value in details.values())
        serialized = str(details)
        assert "storage_key" not in serialized
        assert str(tmp_path) not in serialized
        assert "v2-content" not in serialized


# --- concurrency -------------------------------------------------------


@requires_postgres
def test_concurrent_replacement_attempts_produce_exactly_one_success(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        person_id = person.id
        user_id = user.id
        document_id = v1.id
        document_group_id = v1.document_group_id
        session.commit()

    barrier = threading.Barrier(2)
    successes: list[uuid.UUID] = []
    failures: list[str] = []
    lock = threading.Lock()

    def attempt(label: str) -> None:
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        with session_scope() as session:
            document = session.get(Document, document_id)
            try:
                new_document = replace_document(
                    session,
                    storage,
                    document=document,
                    person_id=person_id,
                    content=f"content-{label}".encode(),
                    original_name=f"{label}.pdf",
                    mime_type="application/pdf",
                    issued_at=None,
                    expires_at=None,
                    actor_user_id=user_id,
                )
                with lock:
                    successes.append(new_document.id)
            except NotCurrentVersionError:
                with lock:
                    failures.append(label)

    threads = [threading.Thread(target=attempt, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(successes) == 1
    assert len(failures) == 1

    with session_scope() as session:
        versions = session.execute(
            select(Document).where(Document.document_group_id == document_group_id)
        ).scalars().all()
        assert sorted(version.version_number for version in versions) == [1, 2]

    # The losing attempt's binary was cleaned up — exactly 2 files remain
    # (the original v1 plus the one successful v2), never 3.
    stored_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(stored_files) == 2
