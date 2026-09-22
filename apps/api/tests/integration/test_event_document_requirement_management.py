"""Real PostgreSQL integration tests for
app.documents.requirement_management (TH-0117.5 / Issue #164, ADR-0040
§5, docs/05-api/events-api.md §31.1) — no HTTP.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db.documents import EventDocumentRequirement
from app.db.events import Event
from app.db.identity import Club
from app.db.session import session_scope
from app.documents.requirement_management import (
    DuplicateEventDocumentRequirementError,
    create_event_document_requirement,
    delete_event_document_requirement,
    get_event_document_requirement,
    list_event_document_requirements,
    update_event_document_requirement,
)

from .conftest import requires_postgres


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


def _setup_event() -> uuid.UUID:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.flush()
        event = _make_event(club)
        session.add(event)
        session.commit()
        return event.id


@requires_postgres
def test_create_persists_requirement_with_supplied_fields() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        assert requirement.event_id == event_id
        assert requirement.document_type == "medical_certificate"
        assert requirement.required is True


@requires_postgres
def test_create_persists_required_false_exactly_as_supplied() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="insurance", required=False
        )
        assert requirement.required is False


@requires_postgres
def test_medical_certificate_document_type_is_supported() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        assert requirement.document_type == "medical_certificate"


@requires_postgres
def test_duplicate_event_id_document_type_is_rejected() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )

    with session_scope() as session:
        with pytest.raises(DuplicateEventDocumentRequirementError):
            create_event_document_requirement(
                session, event_id=event_id, document_type="medical_certificate", required=False
            )


@requires_postgres
def test_failed_create_rolls_back_and_leaves_no_row() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )

    with session_scope() as session:
        with pytest.raises(DuplicateEventDocumentRequirementError):
            create_event_document_requirement(
                session, event_id=event_id, document_type="medical_certificate", required=False
            )

    with session_scope() as session:
        rows = session.execute(
            select(EventDocumentRequirement).where(
                EventDocumentRequirement.event_id == event_id,
                EventDocumentRequirement.document_type == "medical_certificate",
            )
        ).scalars().all()
        # Exactly the first, original row — the failed second create left
        # no partial/duplicate state and required its own transaction
        # rollback rather than corrupting the first insert.
        assert len(rows) == 1
        assert rows[0].required is True


@requires_postgres
def test_different_document_types_for_same_event_are_allowed() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        create_event_document_requirement(
            session, event_id=event_id, document_type="insurance", required=False
        )
        rows, total = list_event_document_requirements(
            session, event_id=event_id, page=1, page_size=50
        )
        assert total == 2
        assert {row.document_type for row in rows} == {"medical_certificate", "insurance"}


@requires_postgres
def test_list_is_scoped_to_its_own_event() -> None:
    event_id_a = _setup_event()
    event_id_b = _setup_event()
    with session_scope() as session:
        create_event_document_requirement(
            session, event_id=event_id_a, document_type="medical_certificate", required=True
        )
        create_event_document_requirement(
            session, event_id=event_id_b, document_type="waiver", required=True
        )

        rows, total = list_event_document_requirements(
            session, event_id=event_id_a, page=1, page_size=50
        )
        assert total == 1
        assert rows[0].document_type == "medical_certificate"


@requires_postgres
def test_get_by_id_is_scoped_to_its_own_event() -> None:
    event_id_a = _setup_event()
    event_id_b = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id_a, document_type="medical_certificate", required=True
        )
        requirement_id = requirement.id

        assert (
            get_event_document_requirement(
                session, event_id=event_id_a, requirement_id=requirement_id
            )
            is not None
        )
        assert (
            get_event_document_requirement(
                session, event_id=event_id_b, requirement_id=requirement_id
            )
            is None
        )


@requires_postgres
def test_update_changes_required_true_to_false() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        updated = update_event_document_requirement(
            session, requirement=requirement, required=False
        )
        assert updated.required is False

    with session_scope() as session:
        row = session.execute(
            select(EventDocumentRequirement).where(EventDocumentRequirement.event_id == event_id)
        ).scalar_one()
        assert row.required is False


@requires_postgres
def test_update_changes_required_false_to_true() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=False
        )
        updated = update_event_document_requirement(session, requirement=requirement, required=True)
        assert updated.required is True


@requires_postgres
def test_update_never_changes_document_type() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        updated = update_event_document_requirement(
            session, requirement=requirement, required=False
        )
        assert updated.document_type == "medical_certificate"


@requires_postgres
def test_delete_removes_the_row() -> None:
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        requirement_id = requirement.id
        delete_event_document_requirement(session, requirement=requirement)

    with session_scope() as session:
        assert (
            get_event_document_requirement(
                session, event_id=event_id, requirement_id=requirement_id
            )
            is None
        )


@requires_postgres
def test_delete_then_recreate_same_document_type_succeeds() -> None:
    # Confirms "change document_type" is DELETE + POST, not a second
    # mutation model, and that deleting truly frees the unique slot.
    event_id = _setup_event()
    with session_scope() as session:
        requirement = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=True
        )
        delete_event_document_requirement(session, requirement=requirement)
        recreated = create_event_document_requirement(
            session, event_id=event_id, document_type="medical_certificate", required=False
        )
        assert recreated.required is False
