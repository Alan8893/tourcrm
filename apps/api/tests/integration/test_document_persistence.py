"""Integration tests for the TH-0117.1 / Issue #156 Document/File
persistence foundation (ADR-0040), implemented by migration
`c70bcac43032`.

Persistence-foundation only — there is no service/API layer yet, so these
tests exercise `app.db.documents` directly through the ORM, the same way
`tests/integration/test_audit_log.py` exercises `app.db.audit.AuditLog`'s
own DB-level constraints without going through a service.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.service import record_audit_event
from app.audit.vocabulary import CANONICAL_AUDIT_ACTIONS
from app.db.audit import AuditLog
from app.db.authorization import DOCUMENTED_PERMISSION_CODES, Permission, Role, RolePermission
from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event
from app.db.identity import Club, Person, User
from app.db.session import session_scope

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_MIGRATION_REVISION = "c70bcac43032"
_BEFORE_MIGRATION_REVISION = "9a1c2f5e7b3d"

_DOCUMENT_AUDIT_ACTIONS = (
    "document.created",
    "document.updated",
    "document.replaced",
    "document.revoked",
    "document.downloaded",
    "document.exported",
)


# --- fixtures / factories ---------------------------------------------------


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


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


def _make_file(**overrides: object) -> File:
    defaults: dict[str, object] = {
        "storage_key": f"documents/{uuid.uuid4().hex}",
        "original_name": "certificate.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12345,
        "checksum": "a" * 64,
        "storage_backend": "local",
    }
    defaults.update(overrides)
    return File(**defaults)  # type: ignore[arg-type]


def _make_document(person: Person, file: File, **overrides: object) -> Document:
    defaults: dict[str, object] = {
        "person_id": person.id,
        "document_group_id": uuid.uuid4(),
        "version_number": 1,
        "document_type": "medical_certificate",
        "status": "active",
        "file_id": file.id,
    }
    defaults.update(overrides)
    return Document(**defaults)  # type: ignore[arg-type]


# --- File --------------------------------------------------------------


@requires_postgres
def test_file_persists_all_canonical_metadata_fields() -> None:
    with session_scope() as session:
        user_person = _make_person()
        user = _make_user(user_person)
        session.add_all([user_person, user])
        session.commit()

        file = _make_file(
            original_name="scan.pdf",
            mime_type="application/pdf",
            size_bytes=4096,
            checksum="deadbeef",
            storage_backend="local",
            created_by=user.id,
        )
        session.add(file)
        session.commit()
        file_id = file.id

    with session_scope() as session:
        fetched = session.execute(select(File).where(File.id == file_id)).scalar_one()
        assert fetched.original_name == "scan.pdf"
        assert fetched.mime_type == "application/pdf"
        assert fetched.size_bytes == 4096
        assert fetched.checksum == "deadbeef"
        assert fetched.storage_backend == "local"
        assert fetched.created_by == user.id
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_file_storage_key_is_unique() -> None:
    with session_scope() as session:
        shared_key = f"documents/{uuid.uuid4().hex}"
        session.add(_make_file(storage_key=shared_key))
        session.commit()

        session.add(_make_file(storage_key=shared_key))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_file_created_by_must_reference_an_existing_user() -> None:
    with session_scope() as session:
        session.add(_make_file(created_by=uuid.uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()


def test_file_has_no_binary_content_column() -> None:
    # ADR-0040 §1/§3: File is metadata-only — binary content is stored
    # outside PostgreSQL and this module implements no FileStorage
    # adapter. Asserting the exact column set (rather than merely the
    # absence of one guessed name) catches any future column added here
    # that would smuggle content into the database.
    assert {column.name for column in File.__table__.columns} == {
        "id",
        "storage_key",
        "original_name",
        "mime_type",
        "size_bytes",
        "checksum",
        "storage_backend",
        "created_by",
        "created_at",
        "updated_at",
    }


# --- Document ------------------------------------------------------------


@requires_postgres
def test_document_has_explicit_person_id_association() -> None:
    # ADR-0040 §2: no polymorphic subject_type/subject_id columns exist.
    column_names = {column.name for column in Document.__table__.columns}
    assert "person_id" in column_names
    assert "subject_type" not in column_names
    assert "subject_id" not in column_names

    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        document = _make_document(person, file)
        session.add(document)
        session.commit()
        document_id = document.id

    with session_scope() as session:
        fetched = session.execute(select(Document).where(Document.id == document_id)).scalar_one()
        assert fetched.person_id == person.id


@requires_postgres
def test_document_file_id_must_reference_an_existing_file() -> None:
    with session_scope() as session:
        person = _make_person()
        session.add(person)
        session.commit()

        document = Document(
            person_id=person.id,
            document_group_id=uuid.uuid4(),
            version_number=1,
            document_type="medical_certificate",
            status="active",
            file_id=uuid.uuid4(),
        )
        session.add(document)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
@pytest.mark.parametrize("status", ["active", "expired", "revoked"])
def test_document_accepts_every_canonical_lifecycle_status(status: str) -> None:
    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        document = _make_document(person, file, status=status)
        session.add(document)
        session.commit()
        document_id = document.id

    with session_scope() as session:
        fetched = session.execute(select(Document).where(Document.id == document_id)).scalar_one()
        assert fetched.status == status


@requires_postgres
def test_document_rejects_missing_as_a_stored_status() -> None:
    # ADR-0040 §4/§5: `missing` is never a persisted Document.status value
    # — it is only ever the result of an EventDocumentRequirement check.
    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        session.add(_make_document(person, file, status="missing"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_document_revoked_status_is_not_converted_to_expired() -> None:
    # Review-flagged constraint (ADR-0040 §5 amendment): no code path in
    # this module maps a revoked current version to `expired` — a document
    # explicitly revoked must remain stored as exactly `revoked`.
    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        document = _make_document(person, file, status="revoked")
        session.add(document)
        session.commit()
        document_id = document.id

    with session_scope() as session:
        fetched = session.execute(select(Document).where(Document.id == document_id)).scalar_one()
        assert fetched.status == "revoked"


@requires_postgres
def test_document_type_supports_medical_certificate() -> None:
    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        document = _make_document(person, file, document_type="medical_certificate")
        session.add(document)
        session.commit()
        document_id = document.id

    with session_scope() as session:
        fetched = session.execute(select(Document).where(Document.id == document_id)).scalar_one()
        assert fetched.document_type == "medical_certificate"


@requires_postgres
def test_document_version_number_starts_at_one() -> None:
    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        document = _make_document(person, file, version_number=1)
        session.add(document)
        session.commit()
        assert document.version_number == 1


@requires_postgres
def test_document_version_number_below_one_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        file = _make_file()
        session.add_all([person, file])
        session.commit()

        session.add(_make_document(person, file, version_number=0))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_document_replacement_creates_a_distinct_version_and_file_and_preserves_history() -> None:
    # ADR-0040 §1/§4: replacement = new File + new Document version; the
    # prior version's row and file are never overwritten, and both remain
    # queryable as history under the same document_group_id.
    with session_scope() as session:
        person = _make_person()
        file_v1 = _make_file()
        session.add_all([person, file_v1])
        session.commit()

        group_id = uuid.uuid4()
        document_v1 = _make_document(
            person, file_v1, document_group_id=group_id, version_number=1, status="expired"
        )
        session.add(document_v1)
        session.commit()

        file_v2 = _make_file()
        session.add(file_v2)
        session.commit()

        document_v2 = _make_document(
            person, file_v2, document_group_id=group_id, version_number=2, status="active"
        )
        session.add(document_v2)
        session.commit()

        v1_id, v2_id = document_v1.id, document_v2.id
        v1_file_id, v2_file_id = file_v1.id, file_v2.id

    with session_scope() as session:
        versions = (
            session.execute(
                select(Document)
                .where(Document.document_group_id == group_id)
                .order_by(Document.version_number)
            )
            .scalars()
            .all()
        )
        assert [v.id for v in versions] == [v1_id, v2_id]
        assert [v.version_number for v in versions] == [1, 2]
        assert versions[0].file_id == v1_file_id
        assert versions[1].file_id == v2_file_id
        assert versions[0].file_id != versions[1].file_id
        # The historical version is untouched by the replacement.
        assert versions[0].status == "expired"
        assert versions[1].status == "active"


@requires_postgres
def test_document_duplicate_version_number_in_same_group_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        file_a = _make_file()
        file_b = _make_file()
        session.add_all([person, file_a, file_b])
        session.commit()

        group_id = uuid.uuid4()
        session.add(_make_document(person, file_a, document_group_id=group_id, version_number=1))
        session.commit()

        session.add(_make_document(person, file_b, document_group_id=group_id, version_number=1))
        with pytest.raises(IntegrityError):
            session.commit()


# --- EventDocumentRequirement --------------------------------------------


@requires_postgres
def test_event_document_requirement_persists_event_relationship_and_required_flag() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club)
        session.add(event)
        session.commit()

        requirement = EventDocumentRequirement(
            event_id=event.id, document_type="medical_certificate", required=True
        )
        session.add(requirement)
        session.commit()
        requirement_id, event_id = requirement.id, event.id

    with session_scope() as session:
        fetched = session.execute(
            select(EventDocumentRequirement).where(EventDocumentRequirement.id == requirement_id)
        ).scalar_one()
        assert fetched.event_id == event_id
        assert fetched.document_type == "medical_certificate"
        assert fetched.required is True


@requires_postgres
def test_event_document_requirement_event_id_must_reference_an_existing_event() -> None:
    with session_scope() as session:
        session.add(
            EventDocumentRequirement(
                event_id=uuid.uuid4(), document_type="medical_certificate", required=True
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_event_document_requirement_is_unique_per_event_and_document_type() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club)
        session.add(event)
        session.commit()

        session.add(
            EventDocumentRequirement(
                event_id=event.id, document_type="medical_certificate", required=True
            )
        )
        session.commit()

        session.add(
            EventDocumentRequirement(
                event_id=event.id, document_type="medical_certificate", required=False
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_event_document_requirement_allows_different_document_types_for_same_event() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        event = _make_event(club)
        session.add(event)
        session.commit()

        session.add_all(
            [
                EventDocumentRequirement(
                    event_id=event.id, document_type="medical_certificate", required=True
                ),
                EventDocumentRequirement(
                    event_id=event.id, document_type="insurance", required=False
                ),
            ]
        )
        session.commit()

        rows = (
            session.execute(
                select(EventDocumentRequirement).where(
                    EventDocumentRequirement.event_id == event.id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2


# --- Permissions -----------------------------------------------------------


def test_document_permission_catalog_has_exactly_the_three_canonical_codes() -> None:
    assert "document.read" in DOCUMENTED_PERMISSION_CODES
    assert "document.manage" in DOCUMENTED_PERMISSION_CODES
    assert "document.export" in DOCUMENTED_PERMISSION_CODES
    assert "document.download" not in DOCUMENTED_PERMISSION_CODES
    document_codes = {code for code in DOCUMENTED_PERMISSION_CODES if code.startswith("document.")}
    assert document_codes == {"document.read", "document.manage", "document.export"}


@requires_postgres
def test_document_permission_rows_exist_in_the_database() -> None:
    with session_scope() as session:
        codes = set(
            session.execute(
                select(Permission.code).where(Permission.code.like("document.%"))
            )
            .scalars()
            .all()
        )
    assert codes == {"document.read", "document.manage", "document.export"}


# --- Audit vocabulary -------------------------------------------------------


def test_canonical_audit_vocabulary_contains_all_six_document_actions() -> None:
    for action in _DOCUMENT_AUDIT_ACTIONS:
        assert action in CANONICAL_AUDIT_ACTIONS


@requires_postgres
@pytest.mark.parametrize("action", _DOCUMENT_AUDIT_ACTIONS)
def test_audit_log_accepts_every_canonical_document_action(action: str) -> None:
    # Proves the database CHECK constraint itself (ck_audit_logs_action_valid),
    # not just the Python-side vocabulary constant — record_audit_event is
    # the existing canonical write boundary (ADR-0024 §3); no parallel
    # audit mechanism is introduced for this domain.
    with session_scope() as session:
        record = record_audit_event(
            session, action=action, actor_type="system", outcome="success"
        )
        session.commit()
        record_id = record.id

    with session_scope() as session:
        fetched = session.execute(select(AuditLog).where(AuditLog.id == record_id)).scalar_one()
        assert fetched.action == action


# --- Migration upgrade/downgrade --------------------------------------------


def _public_tables(database_url: str) -> set[str]:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return set(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()


@requires_postgres
def test_migration_creates_all_three_tables_at_head(database_url: str) -> None:
    tables = _public_tables(database_url)
    assert {"files", "documents", "event_document_requirements"} <= tables


def _admin_has_document_export_grant() -> bool:
    with session_scope() as session:
        return (
            session.execute(
                select(RolePermission)
                .join(Role, Role.id == RolePermission.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Role.code == "admin", Permission.code == "document.export")
            ).first()
            is not None
        )


def _db_accepts_document_created_audit_action() -> bool:
    # Bypasses record_audit_event's Python-side CANONICAL_AUDIT_ACTIONS
    # check on purpose — that constant does not change when the schema is
    # downgraded, so only a raw insert proves what the database's own
    # ck_audit_logs_action_valid CHECK constraint currently accepts. Always
    # rolls back (never commits) so this probe never leaves a real row
    # behind — a committed "document.created" row would itself block a
    # later downgrade back to the narrower CHECK constraint within the
    # same test.
    with session_scope() as session:
        session.add(AuditLog(actor_type="system", action="document.created", outcome="success"))
        try:
            session.flush()
        except IntegrityError:
            return False
        finally:
            session.rollback()
        return True


@requires_postgres
def test_downgrade_drops_tables_reverts_audit_vocabulary_and_removes_only_the_admin_grant(
    database_url: str,
) -> None:
    assert _admin_has_document_export_grant()
    assert _db_accepts_document_created_audit_action()

    try:
        downgrade = run_alembic("downgrade", _BEFORE_MIGRATION_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr

        remaining = {"files", "documents", "event_document_requirements"} & _public_tables(
            database_url
        )
        assert not remaining
        assert not _admin_has_document_export_grant()
        assert not _db_accepts_document_created_audit_action()

        with session_scope() as session:
            # The permission row itself is stable catalog data and
            # survives downgrade (matching the f85df8d36f12/75b7574b6e44
            # precedent) — only the (admin, document.export) grant and the
            # three new tables are removed by this migration's downgrade.
            still_exists = session.execute(
                select(Permission).where(Permission.code == "document.export")
            ).scalar_one_or_none()
        assert still_exists is not None
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr

    assert {"files", "documents", "event_document_requirements"} <= _public_tables(database_url)
    assert _admin_has_document_export_grant()
    assert _db_accepts_document_created_audit_action()
