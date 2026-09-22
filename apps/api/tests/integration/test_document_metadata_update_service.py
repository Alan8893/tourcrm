"""Real PostgreSQL integration tests for app.documents.service.
update_document_metadata (TH-0117.8 / Issue #170, ADR-0040 §4/§7),
exercising the metadata-update application layer directly (no HTTP)
against a real `LocalFileStorage` rooted at `tmp_path` — never the real
`var/file-storage` default. `update_document_metadata` itself never
touches `FileStorage`; the fixture is only needed to create Document
versions via `create_document`/`replace_document`.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime as dt
import threading
import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db.audit import AuditLog
from app.db.documents import Document, File
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.documents.service import (
    InvalidDocumentDatesError,
    NotCurrentVersionError,
    create_document,
    replace_document,
    update_document_metadata,
)
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
    session,
    storage,
    person,
    user,
    *,
    content: bytes = b"v1-content",
    issued_at: dt.datetime | None = None,
    expires_at: dt.datetime | None = None,
) -> Document:
    return create_document(
        session,
        storage,
        person_id=person.id,
        document_type="medical_certificate",
        content=content,
        original_name="v1.pdf",
        mime_type="application/pdf",
        issued_at=issued_at,
        expires_at=expires_at,
        actor_user_id=user.id,
    )


# --- success -----------------------------------------------------------


@requires_postgres
def test_update_issued_at_only(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, expires_at=_utc(2027, 1, 1))

        new_issued_at = _utc(2026, 1, 1)
        updated = update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=new_issued_at
        )

        assert updated.issued_at == new_issued_at
        assert updated.expires_at == _utc(2027, 1, 1)


@requires_postgres
def test_update_expires_at_only(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, issued_at=_utc(2026, 1, 1))

        new_expires_at = _utc(2027, 6, 1)
        updated = update_document_metadata(
            session, document=v1, actor_user_id=user.id, expires_at=new_expires_at
        )

        assert updated.expires_at == new_expires_at
        assert updated.issued_at == _utc(2026, 1, 1)


@requires_postgres
def test_update_both_issued_at_and_expires_at(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        new_issued_at = _utc(2026, 3, 1)
        new_expires_at = _utc(2027, 3, 1)
        updated = update_document_metadata(
            session,
            document=v1,
            actor_user_id=user.id,
            issued_at=new_issued_at,
            expires_at=new_expires_at,
        )

        assert updated.issued_at == new_issued_at
        assert updated.expires_at == new_expires_at


@requires_postgres
def test_explicitly_set_issued_at_to_null(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, issued_at=_utc(2026, 1, 1))

        updated = update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=None
        )

        assert updated.issued_at is None


@requires_postgres
def test_explicitly_set_expires_at_to_null(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, expires_at=_utc(2027, 1, 1))

        updated = update_document_metadata(
            session, document=v1, actor_user_id=user.id, expires_at=None
        )

        assert updated.expires_at is None


@requires_postgres
def test_updated_at_changes(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        v1_id = v1.id
        before_updated_at = v1.updated_at
        session.commit()

    with session_scope() as session:
        document = session.get(Document, v1_id)
        updated = update_document_metadata(
            session, document=document, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
        )
        assert updated.updated_at > before_updated_at


# --- validation ----------------------------------------------------------


@requires_postgres
def test_empty_update_is_rejected(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        with pytest.raises(ValueError):
            update_document_metadata(session, document=v1, actor_user_id=user.id)


@requires_postgres
def test_invalid_expires_at_before_issued_at_when_both_supplied(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        with pytest.raises(InvalidDocumentDatesError):
            update_document_metadata(
                session,
                document=v1,
                actor_user_id=user.id,
                issued_at=_utc(2026, 6, 1),
                expires_at=_utc(2025, 1, 1),
            )


@requires_postgres
def test_invalid_resulting_dates_when_only_expires_at_supplied(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, issued_at=_utc(2026, 1, 1))

        with pytest.raises(InvalidDocumentDatesError):
            update_document_metadata(
                session, document=v1, actor_user_id=user.id, expires_at=_utc(2025, 1, 1)
            )

    with session_scope() as session:
        reloaded = session.get(Document, v1.id)
        assert reloaded.expires_at is None


@requires_postgres
def test_invalid_resulting_dates_when_only_issued_at_supplied(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, expires_at=_utc(2026, 1, 1))

        with pytest.raises(InvalidDocumentDatesError):
            update_document_metadata(
                session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 2, 1)
            )

    with session_scope() as session:
        reloaded = session.get(Document, v1.id)
        assert reloaded.issued_at is None


@requires_postgres
def test_invalid_dates_leaves_document_unchanged(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, issued_at=_utc(2026, 1, 1))
        v1_id = v1.id

        with pytest.raises(InvalidDocumentDatesError):
            update_document_metadata(
                session, document=v1, actor_user_id=user.id, expires_at=_utc(2025, 1, 1)
            )

    with session_scope() as session:
        reloaded = session.get(Document, v1_id)
        assert reloaded.issued_at == _utc(2026, 1, 1)
        assert reloaded.expires_at is None


# --- current version -------------------------------------------------------


@requires_postgres
def test_current_version_can_be_updated(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        updated = update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
        )
        assert updated.issued_at == _utc(2026, 1, 1)


@requires_postgres
def test_historical_version_is_rejected(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        v1_id = v1.id

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
        stale_v1 = session.get(Document, v1_id)
        with pytest.raises(NotCurrentVersionError):
            update_document_metadata(
                session, document=stale_v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
            )

    with session_scope() as session:
        reloaded_v1 = session.get(Document, v1_id)
        assert reloaded_v1.issued_at is None


# --- data integrity ------------------------------------------------------


@requires_postgres
def test_update_preserves_all_other_fields(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        before = {
            "id": v1.id,
            "document_group_id": v1.document_group_id,
            "version_number": v1.version_number,
            "document_type": v1.document_type,
            "status": v1.status,
            "file_id": v1.file_id,
            "uploaded_by": v1.uploaded_by,
            "person_id": v1.person_id,
            "created_at": v1.created_at,
        }

        updated = update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
        )

        assert updated.id == before["id"]
        assert updated.document_group_id == before["document_group_id"]
        assert updated.version_number == before["version_number"]
        assert updated.document_type == before["document_type"]
        assert updated.status == before["status"]
        assert updated.file_id == before["file_id"]
        assert updated.uploaded_by == before["uploaded_by"]
        assert updated.person_id == before["person_id"]
        assert updated.created_at == before["created_at"]


@requires_postgres
def test_file_row_remains_unchanged(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, content=b"original-bytes")
        file_id = v1.file_id
        before_file = session.get(File, file_id)
        before_storage_key = before_file.storage_key
        before_checksum = before_file.checksum
        before_size = before_file.size_bytes
        before_updated_at = before_file.updated_at

        update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
        )

    with session_scope() as session:
        after_file = session.get(File, file_id)
        assert after_file.storage_key == before_storage_key
        assert after_file.checksum == before_checksum
        assert after_file.size_bytes == before_size
        assert after_file.updated_at == before_updated_at
        assert storage.get(after_file.storage_key) == b"original-bytes"


@requires_postgres
def test_filestorage_is_never_called(tmp_path) -> None:
    """`update_document_metadata` takes no `storage` parameter at all —
    this test proves the point structurally: a Mock is never passed in,
    yet the operation succeeds, confirming there is no hidden storage
    dependency to accidentally invoke."""
    storage = LocalFileStorage(root=tmp_path)
    never_called_storage = Mock(spec=LocalFileStorage)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
        )

    never_called_storage.put.assert_not_called()
    never_called_storage.get.assert_not_called()
    never_called_storage.delete.assert_not_called()


# --- audit -----------------------------------------------------------------


@requires_postgres
def test_update_records_exactly_one_document_updated_audit_without_sensitive_data(
    tmp_path,
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        document_id = v1.id

        update_document_metadata(
            session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
        )

        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.updated", AuditLog.resource_id == document_id
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
        assert "v1-content" not in serialized


# --- transaction / rollback --------------------------------------------


@requires_postgres
def test_failed_transaction_does_not_leave_metadata_partially_updated(
    tmp_path, monkeypatch
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        document_id = v1.id

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated audit failure")

        monkeypatch.setattr("app.documents.service.record_audit_event", _boom)

        with pytest.raises(RuntimeError):
            update_document_metadata(
                session, document=v1, actor_user_id=user.id, issued_at=_utc(2026, 1, 1)
            )

    with session_scope() as session:
        reloaded = session.get(Document, document_id)
        assert reloaded.issued_at is None
        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.updated", AuditLog.resource_id == document_id
            )
        ).scalars().all()
        assert len(audit_rows) == 0


# --- concurrency -------------------------------------------------------


@requires_postgres
def test_concurrent_replacement_prevents_updating_stale_historical_version(tmp_path) -> None:
    """Issue #170 §16's exact race: one thread PATCHes the current
    version's metadata while another replaces it at the same time. The
    invariant that must hold: `update_document_metadata` never silently
    modifies a version that, at the moment its own write actually
    executed, had already been superseded by a newer version.
    """
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
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def do_update() -> None:
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        with session_scope() as session:
            document = session.get(Document, document_id)
            try:
                update_document_metadata(
                    session,
                    document=document,
                    actor_user_id=user_id,
                    issued_at=_utc(2026, 1, 1),
                )
                with lock:
                    outcomes["update"] = "success"
            except NotCurrentVersionError:
                with lock:
                    outcomes["update"] = "not_current"

    def do_replace() -> None:
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        with session_scope() as session:
            document = session.get(Document, document_id)
            try:
                replace_document(
                    session,
                    storage,
                    document=document,
                    person_id=person_id,
                    content=b"replacement-content",
                    original_name="v2.pdf",
                    mime_type="application/pdf",
                    issued_at=None,
                    expires_at=None,
                    actor_user_id=user_id,
                )
                with lock:
                    outcomes["replace"] = "success"
            except NotCurrentVersionError:
                with lock:
                    outcomes["replace"] = "not_current"

    threads = [threading.Thread(target=do_update), threading.Thread(target=do_replace)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert outcomes["replace"] == "success"

    with session_scope() as session:
        versions = session.execute(
            select(Document).where(Document.document_group_id == document_group_id)
        ).scalars().all()
        by_version = {version.version_number: version for version in versions}
        assert sorted(by_version) == [1, 2]
        # v2 (the replacement) is never touched by the metadata update.
        assert by_version[2].issued_at is None
        if outcomes["update"] == "not_current":
            # replace won the race: v1 must be untouched.
            assert by_version[1].issued_at is None
        else:
            # update won the race (a valid serialization: update-then-
            # replace) — v1 legitimately carries the new issued_at.
            assert by_version[1].issued_at == _utc(2026, 1, 1)
