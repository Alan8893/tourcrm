"""Real PostgreSQL integration tests for the Issue #59 canonical audit
infrastructure (ADR-0024): AuditLog ORM + migration + DB-level
constraints/indexes, and app.audit.service.record_audit_event's
transaction/fail-closed behavior.

See tests/unit/test_audit_security.py and tests/unit/test_audit_service.py
for the pure-Python validation tests that need no database.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_events.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.audit.security import AuditDetailsError
from app.audit.service import (
    InvalidAuditActorError,
    record_audit_event,
)
from app.db.audit import AuditLog
from app.db.identity import Club, Person, User
from app.db.session import session_scope

from .conftest import requires_postgres


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {
        "name": f"Test Club {uuid.uuid4().hex[:8]}",
        "status": "active",
    }
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {"last_name": "Ivanova", "first_name": "Anna"}
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


# --- creation through the reusable service boundary -------------------------


@requires_postgres
def test_record_audit_event_persists_a_system_actor_row() -> None:
    with session_scope() as session:
        record = record_audit_event(
            session,
            action="event.created",
            actor_type="system",
            outcome="success",
        )
        session.commit()

        assert isinstance(record.id, uuid.UUID)
        assert record.actor_type == "system"
        assert record.actor_user_id is None

        fetched = session.execute(
            select(AuditLog).where(AuditLog.id == record.id)
        ).scalar_one()
        assert fetched.action == "event.created"
        assert fetched.outcome == "success"


@requires_postgres
def test_occurred_at_is_timezone_aware_utc() -> None:
    with session_scope() as session:
        record = record_audit_event(
            session, action="user.created", actor_type="system", outcome="success"
        )
        session.commit()

        assert record.occurred_at.tzinfo is not None
        assert record.occurred_at.utcoffset() == datetime.timedelta(0)


@requires_postgres
def test_user_actor_attribution() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()

        record = record_audit_event(
            session,
            action="user.status_changed",
            actor_type="user",
            actor_user_id=user.id,
            outcome="success",
            details={"changes": {"status": {"from": "pending", "to": "active"}}},
        )
        session.commit()

        assert record.actor_type == "user"
        assert record.actor_user_id == user.id
        assert record.details == {"changes": {"status": {"from": "pending", "to": "active"}}}


@requires_postgres
def test_club_attribution() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        record = record_audit_event(
            session,
            action="membership.created",
            actor_type="system",
            outcome="success",
            club_id=club.id,
        )
        session.commit()

        assert record.club_id == club.id


@requires_postgres
def test_resource_attribution() -> None:
    with session_scope() as session:
        resource_id = uuid.uuid4()
        record = record_audit_event(
            session,
            action="group.created",
            actor_type="system",
            outcome="success",
            resource_type="group",
            resource_id=resource_id,
        )
        session.commit()

        assert record.resource_type == "group"
        assert record.resource_id == resource_id


@requires_postgres
def test_request_and_correlation_id_are_persisted() -> None:
    with session_scope() as session:
        record = record_audit_event(
            session,
            action="event.updated",
            actor_type="system",
            outcome="failure",
            request_id="req-12345",
            correlation_id="corr-67890",
        )
        session.commit()

        fetched = session.execute(
            select(AuditLog).where(AuditLog.id == record.id)
        ).scalar_one()
        assert fetched.request_id == "req-12345"
        assert fetched.correlation_id == "corr-67890"
        assert fetched.outcome == "failure"


# --- DB-level constraints (bypassing the service, direct ORM construction) --


@requires_postgres
def test_db_rejects_non_canonical_actor_type() -> None:
    with session_scope() as session:
        session.add(AuditLog(actor_type="admin", action="event.created", outcome="success"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_user_actor_without_actor_user_id() -> None:
    with session_scope() as session:
        session.add(AuditLog(actor_type="user", action="event.created", outcome="success"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_system_actor_with_actor_user_id() -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                actor_type="system",
                actor_user_id=uuid.uuid4(),
                action="event.created",
                outcome="success",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_non_canonical_outcome() -> None:
    with session_scope() as session:
        session.add(AuditLog(actor_type="system", action="event.created", outcome="ok"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_non_canonical_action() -> None:
    with session_scope() as session:
        session.add(AuditLog(actor_type="system", action="totally.invented", outcome="success"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_resource_type_without_resource_id() -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                actor_type="system",
                action="event.created",
                outcome="success",
                resource_type="event",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_resource_id_without_resource_type() -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                actor_type="system",
                action="event.created",
                outcome="success",
                resource_id=uuid.uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_unknown_actor_user_id_fk() -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                actor_type="user",
                actor_user_id=uuid.uuid4(),
                action="event.created",
                outcome="success",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_db_rejects_unknown_club_id_fk() -> None:
    with session_scope() as session:
        session.add(
            AuditLog(
                actor_type="system",
                club_id=uuid.uuid4(),
                action="event.created",
                outcome="success",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_orm_level_validates_hook_rejects_secret_even_bypassing_the_service() -> None:
    # ADR-0024 §6: the @validates hook on AuditLog.details fires even when
    # a caller constructs the row directly instead of going through
    # app.audit.service.record_audit_event.
    with pytest.raises(AuditDetailsError):
        AuditLog(
            actor_type="system",
            action="event.created",
            outcome="success",
            details={"password": "hunter2"},
        )


@requires_postgres
def test_referenced_user_cannot_be_deleted_while_audit_log_references_it() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()

        record_audit_event(
            session,
            action="user.locked",
            actor_type="user",
            actor_user_id=user.id,
            outcome="success",
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user.id)})
            session.commit()


# --- indexes ------------------------------------------------------------


@requires_postgres
def test_expected_indexes_exist(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            index_names = {
                row[0]
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_logs'")
                )
            }
    finally:
        engine.dispose()

    for expected in (
        "ix_audit_logs_occurred_at",
        "ix_audit_logs_actor_user_id",
        "ix_audit_logs_club_id",
        "ix_audit_logs_resource_type_resource_id",
        "ix_audit_logs_request_id",
    ):
        assert expected in index_names


# --- immutability -----------------------------------------------------------


@requires_postgres
def test_audit_log_has_no_updated_at_column() -> None:
    # ADR-0024 §1: occurred_at is the only timestamp; there is no
    # updated_at because rows are never updated after insertion.
    assert "updated_at" not in AuditLog.__table__.columns.keys()


# --- transaction atomicity / fail-closed semantics ---------------------------


@requires_postgres
def test_audit_insert_failure_rolls_back_the_whole_transaction() -> None:
    """The canonical ADR-0024 §5 pattern: a business mutation and its
    audit record share one transaction. When the audit insert is
    rejected (here: an inconsistent actor_type/actor_user_id combination
    surfacing as a real DB constraint violation, since the row is
    constructed directly to bypass the service's own pre-check), rolling
    back must undo the business mutation too — fail-closed, not
    "business mutation succeeds anyway".
    """
    club_name = f"Fail-closed club {uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        club = _make_club(name=club_name)
        session.add(club)
        session.flush()  # business mutation is visible within the transaction

        # Directly constructed (bypassing record_audit_event) to reach an
        # actual database-level failure rather than the service's own
        # earlier Python-level check.
        session.add(
            AuditLog(actor_type="user", action="event.created", outcome="success")
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with session_scope() as verify_session:
        remaining = verify_session.execute(
            select(Club).where(Club.name == club_name)
        ).scalar_one_or_none()
        assert remaining is None, "business mutation must not survive a rolled-back audit insert"


@requires_postgres
def test_audit_service_validation_failure_rolls_back_the_whole_transaction() -> None:
    """Same fail-closed contract, but the audit insert fails at
    record_audit_event's own Python-level validation (before any SQL is
    sent for the audit row) rather than at the database.
    """
    club_name = f"Fail-closed club {uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        try:
            club = _make_club(name=club_name)
            session.add(club)
            session.flush()

            record_audit_event(
                session,
                action="event.created",
                actor_type="user",  # missing required actor_user_id
                outcome="success",
            )
            session.commit()
        except InvalidAuditActorError:
            session.rollback()

    with session_scope() as verify_session:
        remaining = verify_session.execute(
            select(Club).where(Club.name == club_name)
        ).scalar_one_or_none()
        assert remaining is None, "business mutation must not survive a rolled-back audit insert"


@requires_postgres
def test_successful_audit_insert_commits_alongside_the_business_mutation() -> None:
    """The positive counterpart: when both steps succeed, both are
    durably committed together.
    """
    club_name = f"Success club {uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        club = _make_club(name=club_name)
        session.add(club)
        session.flush()

        record_audit_event(
            session,
            action="membership.created",
            actor_type="system",
            outcome="success",
            club_id=club.id,
        )
        session.commit()

    with session_scope() as verify_session:
        remaining = verify_session.execute(
            select(Club).where(Club.name == club_name)
        ).scalar_one()
        audit_row = verify_session.execute(
            select(AuditLog).where(AuditLog.club_id == remaining.id)
        ).scalar_one()
        assert audit_row.action == "membership.created"
