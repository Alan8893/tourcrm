"""Real PostgreSQL integration tests for app.documents.event_requirements
and app.documents.queries.list_current_documents_for_person_by_type
(TH-0117.4 / Issue #162, ADR-0040 §5) — no HTTP.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime as dt
import hashlib
import uuid

from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.documents.event_requirements import check_person_document_requirements
from app.documents.queries import list_current_documents_for_person_by_type

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


# --- current-version selection (list_current_documents_for_person_by_type) -


@requires_postgres
def test_single_version_is_returned_as_current() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        file_row = _make_file(user)
        session.add(file_row)
        session.flush()
        doc = _make_document(
            person, file_row, document_group_id=uuid.uuid4(), version_number=1
        )
        session.add(doc)
        session.commit()

        rows = list_current_documents_for_person_by_type(
            session, person_id=person.id, document_type="medical_certificate"
        )
        assert [row.id for row in rows] == [doc.id]


@requires_postgres
def test_highest_version_number_is_selected_as_current() -> None:
    with session_scope() as session:
        person, user = _setup_person(session)
        file_v1, file_v2, file_v3 = _make_file(user), _make_file(user), _make_file(user)
        session.add_all([file_v1, file_v2, file_v3])
        session.flush()

        group_id = uuid.uuid4()
        v1 = _make_document(person, file_v1, document_group_id=group_id, version_number=1)
        v2 = _make_document(person, file_v2, document_group_id=group_id, version_number=2)
        v3 = _make_document(person, file_v3, document_group_id=group_id, version_number=3)
        session.add_all([v1, v2, v3])
        session.commit()

        rows = list_current_documents_for_person_by_type(
            session, person_id=person.id, document_type="medical_certificate"
        )
        assert [row.id for row in rows] == [v3.id]
        assert rows[0].version_number == 3


# --- EventDocumentRequirement evaluation --------------------------------


@requires_postgres
def test_requirement_satisfied_by_active_document_is_valid() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        file_row = _make_file(user)
        session.add(file_row)
        session.flush()
        session.add(
            _make_document(
                person, file_row, document_group_id=uuid.uuid4(), version_number=1, status="active"
            )
        )
        session.commit()

        checks = check_person_document_requirements(session, event_id=event.id, person_id=person.id)
        assert len(checks) == 1
        assert checks[0].document_type == "medical_certificate"
        assert checks[0].required is True
        assert checks[0].result == "valid"


@requires_postgres
def test_requirement_with_no_document_is_missing() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, _user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        session.commit()

        checks = check_person_document_requirements(session, event_id=event.id, person_id=person.id)
        assert checks[0].result == "missing"


@requires_postgres
def test_requirement_with_expired_document_is_expired() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        file_row = _make_file(user)
        session.add(file_row)
        session.flush()
        session.add(
            _make_document(
                person,
                file_row,
                document_group_id=uuid.uuid4(),
                version_number=1,
                status="active",
                expires_at=_utc(2020, 1, 1),
            )
        )
        session.commit()

        checks = check_person_document_requirements(
            session, event_id=event.id, person_id=person.id, now=_utc(2026, 1, 1)
        )
        assert checks[0].result == "expired"


@requires_postgres
def test_requirement_with_revoked_document_is_expired_not_revoked() -> None:
    # ADR-0040 §5's revoked-mapping amendment: revoked maps to `expired`,
    # never a fourth `revoked` requirement-check result.
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        file_row = _make_file(user)
        session.add(file_row)
        session.flush()
        session.add(
            _make_document(
                person, file_row, document_group_id=uuid.uuid4(), version_number=1, status="revoked"
            )
        )
        session.commit()

        checks = check_person_document_requirements(session, event_id=event.id, person_id=person.id)
        assert checks[0].result == "expired"
        assert checks[0].result != "revoked"


@requires_postgres
def test_historical_valid_version_does_not_override_current_expired() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        file_v1, file_v2 = _make_file(user), _make_file(user)
        session.add_all([file_v1, file_v2])
        session.flush()

        group_id = uuid.uuid4()
        # v1: was valid at the time (no expiry), but is no longer current.
        v1 = _make_document(
            person, file_v1, document_group_id=group_id, version_number=1, status="active"
        )
        # v2 (current): expired.
        v2 = _make_document(
            person,
            file_v2,
            document_group_id=group_id,
            version_number=2,
            status="active",
            expires_at=_utc(2020, 1, 1),
        )
        session.add_all([v1, v2])
        session.commit()

        checks = check_person_document_requirements(
            session, event_id=event.id, person_id=person.id, now=_utc(2026, 1, 1)
        )
        assert checks[0].result == "expired"


@requires_postgres
def test_historical_valid_version_does_not_override_current_revoked() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        file_v1, file_v2 = _make_file(user), _make_file(user)
        session.add_all([file_v1, file_v2])
        session.flush()

        group_id = uuid.uuid4()
        v1 = _make_document(
            person, file_v1, document_group_id=group_id, version_number=1, status="active"
        )
        v2 = _make_document(
            person, file_v2, document_group_id=group_id, version_number=2, status="revoked"
        )
        session.add_all([v1, v2])
        session.commit()

        checks = check_person_document_requirements(session, event_id=event.id, person_id=person.id)
        assert checks[0].result == "expired"


@requires_postgres
def test_historical_expired_version_does_not_prevent_current_valid() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add(_make_requirement(event))
        file_v1, file_v2 = _make_file(user), _make_file(user)
        session.add_all([file_v1, file_v2])
        session.flush()

        group_id = uuid.uuid4()
        v1 = _make_document(
            person,
            file_v1,
            document_group_id=group_id,
            version_number=1,
            status="active",
            expires_at=_utc(2020, 1, 1),
        )
        v2 = _make_document(
            person, file_v2, document_group_id=group_id, version_number=2, status="active"
        )
        session.add_all([v1, v2])
        session.commit()

        checks = check_person_document_requirements(
            session, event_id=event.id, person_id=person.id, now=_utc(2026, 1, 1)
        )
        assert checks[0].result == "valid"


@requires_postgres
def test_multiple_requirements_produce_independent_results() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add_all(
            [
                _make_requirement(event, document_type="medical_certificate"),
                _make_requirement(event, document_type="insurance"),
                _make_requirement(event, document_type="waiver", required=False),
            ]
        )
        file_row = _make_file(user)
        session.add(file_row)
        session.flush()
        # Only "medical_certificate" has a document at all.
        session.add(
            _make_document(
                person, file_row, document_group_id=uuid.uuid4(), version_number=1, status="active"
            )
        )
        session.commit()

        checks = check_person_document_requirements(session, event_id=event.id, person_id=person.id)
        by_type = {check.document_type: check for check in checks}
        assert len(checks) == 3
        assert by_type["medical_certificate"].result == "valid"
        assert by_type["insurance"].result == "missing"
        assert by_type["waiver"].result == "missing"
        assert by_type["waiver"].required is False


@requires_postgres
def test_mixed_valid_missing_expired_response() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.flush()
        session.add_all(
            [
                _make_requirement(event, document_type="medical_certificate"),
                _make_requirement(event, document_type="insurance"),
                _make_requirement(event, document_type="waiver"),
            ]
        )
        file_valid = _make_file(user)
        file_expired = _make_file(user)
        session.add_all([file_valid, file_expired])
        session.flush()
        session.add(
            _make_document(
                person,
                file_valid,
                document_group_id=uuid.uuid4(),
                version_number=1,
                document_type="medical_certificate",
                status="active",
            )
        )
        session.add(
            _make_document(
                person,
                file_expired,
                document_group_id=uuid.uuid4(),
                version_number=1,
                document_type="insurance",
                status="active",
                expires_at=_utc(2020, 1, 1),
            )
        )
        # "waiver" has no Document at all -> missing.
        session.commit()

        checks = check_person_document_requirements(
            session, event_id=event.id, person_id=person.id, now=_utc(2026, 1, 1)
        )
        by_type = {check.document_type: check.result for check in checks}
        assert by_type == {
            "medical_certificate": "valid",
            "insurance": "expired",
            "waiver": "missing",
        }


@requires_postgres
def test_no_requirements_for_event_returns_empty_list() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        person, _user = _setup_person(session)
        event = _make_event(club)
        session.add(event)
        session.commit()

        checks = check_person_document_requirements(session, event_id=event.id, person_id=person.id)
        assert checks == []
