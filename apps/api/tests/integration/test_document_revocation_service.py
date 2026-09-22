"""Real PostgreSQL integration tests for app.documents.service.
revoke_document (TH-0117.7 / Issue #168, ADR-0040 §4), exercising the
revocation application layer directly (no HTTP) against a real
`LocalFileStorage` rooted at `tmp_path` — never the real `var/file-storage`
default. `revoke_document` itself never touches `FileStorage`; the fixture
is only needed to create Document versions via `create_document`/
`replace_document`.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select

from app.db.audit import AuditLog
from app.db.documents import Document, File
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.documents.service import (
    AlreadyRevokedError,
    NotCurrentVersionError,
    create_document,
    replace_document,
    revoke_document,
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


# --- success -----------------------------------------------------------


@pytest.mark.parametrize("current_status", ["active", "expired"])
@requires_postgres
def test_revoke_current_document_succeeds(tmp_path, current_status) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, status=current_status)
        v1_id = v1.id
        v1_file_id = v1.file_id
        v1_group_id = v1.document_group_id
        v1_version_number = v1.version_number

        revoked = revoke_document(session, document=v1, actor_user_id=user.id)

        assert revoked.status == "revoked"
        assert revoked.id == v1_id
        assert revoked.file_id == v1_file_id
        assert revoked.document_group_id == v1_group_id
        assert revoked.version_number == v1_version_number


@requires_postgres
def test_revoke_persists_status_revoked(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        v1_id = v1.id
        revoke_document(session, document=v1, actor_user_id=user.id)

    with session_scope() as session:
        reloaded = session.get(Document, v1_id)
        assert reloaded.status == "revoked"


# --- history -------------------------------------------------------------


@requires_postgres
def test_historical_versions_remain_unchanged_after_revoking_current(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, content=b"original")
        v1_id = v1.id
        v1_file_id = v1.file_id
        v2 = replace_document(
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
        v2_id = v2.id

        revoke_document(session, document=v2, actor_user_id=user.id)

    with session_scope() as session:
        reloaded_v1 = session.get(Document, v1_id)
        assert reloaded_v1.status == "active"
        assert reloaded_v1.file_id == v1_file_id
        reloaded_v2 = session.get(Document, v2_id)
        assert reloaded_v2.status == "revoked"


@requires_postgres
def test_historical_version_cannot_be_revoked(tmp_path) -> None:
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
            revoke_document(session, document=stale_v1, actor_user_id=user.id)

    with session_scope() as session:
        reloaded_v1 = session.get(Document, v1_id)
        assert reloaded_v1.status == "active"


@requires_postgres
def test_revoking_current_version_does_not_create_another_version(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        group_id = v1.document_group_id

        revoke_document(session, document=v1, actor_user_id=user.id)

        versions = session.execute(
            select(Document).where(Document.document_group_id == group_id)
        ).scalars().all()
        assert len(versions) == 1
        assert versions[0].version_number == 1


@requires_postgres
def test_revoke_creates_no_new_file(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)

        revoke_document(session, document=v1, actor_user_id=user.id)

        file_count = session.execute(select(File)).scalars().all()
        assert len(file_count) == 1


@requires_postgres
def test_revoke_never_deletes_the_file(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user, content=b"binary-content")
        v1_file_id = v1.file_id

        revoke_document(session, document=v1, actor_user_id=user.id)

    with session_scope() as session:
        file_row = session.get(File, v1_file_id)
        assert file_row is not None
        assert storage.get(file_row.storage_key) == b"binary-content"


# --- already revoked -------------------------------------------------------


@requires_postgres
def test_revoking_already_revoked_current_version_raises_deterministically(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        revoke_document(session, document=v1, actor_user_id=user.id)

    with session_scope() as session:
        stale_v1 = session.get(Document, v1.id)
        with pytest.raises(AlreadyRevokedError):
            revoke_document(session, document=stale_v1, actor_user_id=user.id)


@requires_postgres
def test_revoking_already_revoked_creates_no_duplicate_version(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        group_id = v1.document_group_id
        revoke_document(session, document=v1, actor_user_id=user.id)

    with session_scope() as session:
        stale_v1 = session.get(Document, v1.id)
        with pytest.raises(AlreadyRevokedError):
            revoke_document(session, document=stale_v1, actor_user_id=user.id)

        versions = session.execute(
            select(Document).where(Document.document_group_id == group_id)
        ).scalars().all()
        assert len(versions) == 1


@requires_postgres
def test_revoking_already_revoked_does_not_write_a_duplicate_audit_event(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        document_id = v1.id
        revoke_document(session, document=v1, actor_user_id=user.id)

    with session_scope() as session:
        stale_v1 = session.get(Document, document_id)
        with pytest.raises(AlreadyRevokedError):
            revoke_document(session, document=stale_v1, actor_user_id=user.id)

    with session_scope() as session:
        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.revoked", AuditLog.resource_id == document_id
            )
        ).scalars().all()
        assert len(audit_rows) == 1


# --- audit -----------------------------------------------------------------


@requires_postgres
def test_revoke_records_exactly_one_document_revoked_audit_without_sensitive_data(
    tmp_path,
) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        document_id = v1.id

        revoke_document(session, document=v1, actor_user_id=user.id)

        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.revoked", AuditLog.resource_id == document_id
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
def test_failed_transaction_does_not_leave_status_revoked(tmp_path, monkeypatch) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        document_id = v1.id

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated audit failure")

        monkeypatch.setattr("app.documents.service.record_audit_event", _boom)

        with pytest.raises(RuntimeError):
            revoke_document(session, document=v1, actor_user_id=user.id)

    with session_scope() as session:
        reloaded = session.get(Document, document_id)
        assert reloaded.status == "active"
        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.revoked", AuditLog.resource_id == document_id
            )
        ).scalars().all()
        assert len(audit_rows) == 0


# --- concurrency -------------------------------------------------------


@requires_postgres
def test_concurrent_revoke_and_replace_never_revokes_a_historical_version(tmp_path) -> None:
    """Issue #168 §12's exact race: one thread revokes the current
    version while another replaces it at the same time. Whichever
    ordering the database actually serializes, the invariant that must
    hold is: a Document row with `status = 'revoked'` is never one that,
    at the moment `revoke_document`'s write actually executed, was
    already superseded by a newer version — i.e. `revoke_document` never
    blindly revokes a stale, already-historical row.
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

    def do_revoke() -> None:
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        with session_scope() as session:
            document = session.get(Document, document_id)
            try:
                revoke_document(session, document=document, actor_user_id=user_id)
                with lock:
                    outcomes["revoke"] = "success"
            except NotCurrentVersionError:
                with lock:
                    outcomes["revoke"] = "not_current"

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

    threads = [threading.Thread(target=do_revoke), threading.Thread(target=do_replace)]
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

        # Whatever revoke's outcome was, the invariant holds: a
        # `revoked` row is never accompanied by a *later* version whose
        # existence revoke's own atomic check should have already
        # detected — i.e. v1 is revoked only if revoke's write won the
        # race against replace's insert (a valid serialization), and v2
        # (the replacement) is always present and always active.
        assert by_version[2].status == "active"
        assert by_version[1].status in ("active", "revoked")
        if outcomes["revoke"] == "not_current":
            # replace won the race first: v1 must be untouched.
            assert by_version[1].status == "active"


@requires_postgres
def test_concurrent_revoke_attempts_produce_exactly_one_success(tmp_path) -> None:
    storage = LocalFileStorage(root=tmp_path)
    with session_scope() as session:
        person, user = _setup_person(session)
        v1 = _create_first_version(session, storage, person, user)
        user_id = user.id
        document_id = v1.id
        session.commit()

    barrier = threading.Barrier(2)
    successes: list[str] = []
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
                revoke_document(session, document=document, actor_user_id=user_id)
                with lock:
                    successes.append(label)
            except AlreadyRevokedError:
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
        audit_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "document.revoked", AuditLog.resource_id == document_id
            )
        ).scalars().all()
        assert len(audit_rows) == 1
