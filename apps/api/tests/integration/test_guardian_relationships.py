"""Real PostgreSQL integration tests for the Issue #50 GuardianRelationship
persistence foundation (ORM model + migration + DB-level constraints/FK
integrity), including real-concurrency proof for its two GiST-exclusion
invariants.

These tests exercise the constraints that only PostgreSQL itself can
enforce (CHECK constraints, exclusion constraints, foreign keys,
indexes) — GuardianRelationship is Club-neutral (ADR-0023 §3) and has
no application/service ownership boundary of its own (unlike
EventStaffAssignment/EventGroupTarget), so unlike
tests/integration/test_event_staff_assignments_service.py there is no
separate "service" test file here: every invariant this Issue defines
is expressed as a physical PostgreSQL constraint.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import threading
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.identity import GuardianRelationship, Person
from app.db.session import session_scope

from ._schema_reset import run_alembic
from .conftest import requires_postgres


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": "Ivanova",
        "first_name": f"Anna-{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_relationship(
    guardian: Person, child: Person, **overrides: object
) -> GuardianRelationship:
    defaults: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return GuardianRelationship(**defaults)  # type: ignore[arg-type]


# --- model / field persistence ------------------------------------------


@requires_postgres
def test_creating_a_valid_guardian_relationship_persists_all_fields() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        relationship = _make_relationship(
            guardian,
            child,
            relationship_type="mother",
            is_primary_contact=True,
            valid_to=_utc(2030, 1, 1),
        )
        session.add(relationship)
        session.commit()

        fetched = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == relationship.id)
        ).scalar_one()
        assert fetched.guardian_person_id == guardian.id
        assert fetched.child_person_id == child.id
        assert fetched.relationship_type == "mother"
        assert fetched.status == "active"
        assert fetched.is_primary_contact is True
        assert fetched.valid_from == _utc(2024, 1, 1)
        assert fetched.valid_to == _utc(2030, 1, 1)
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_is_primary_contact_defaults_to_false_when_not_specified() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        relationship = GuardianRelationship(
            guardian_person_id=guardian.id,
            child_person_id=child.id,
            relationship_type="parent",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        session.add(relationship)
        session.commit()

        fetched = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == relationship.id)
        ).scalar_one()
        assert fetched.is_primary_contact is False


@requires_postgres
@pytest.mark.parametrize(
    "type_value", ["parent", "grandparent", "legal_guardian", "some-made-up-type", "anything"]
)
def test_relationship_type_accepts_any_string_no_enum_exists(type_value: str) -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        relationship = _make_relationship(guardian, child, relationship_type=type_value)
        session.add(relationship)
        session.commit()  # must not raise: no CHECK/enum constraint exists

        fetched = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == relationship.id)
        ).scalar_one()
        assert fetched.relationship_type == type_value


# --- status vocabulary ------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("status_value", ["active", "inactive", "revoked"])
def test_every_canonical_status_can_be_persisted(status_value: str) -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        relationship = _make_relationship(guardian, child, status=status_value)
        session.add(relationship)
        session.commit()  # must not raise

        fetched = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == relationship.id)
        ).scalar_one()
        assert fetched.status == status_value


@requires_postgres
@pytest.mark.parametrize("status_value", ["pending", "suspended", "archived", "ACTIVE", ""])
def test_invalid_status_is_rejected(status_value: str) -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        session.add(_make_relationship(guardian, child, status=status_value))
        with pytest.raises(IntegrityError):
            session.commit()


# --- self-link ----------------------------------------------------------


@requires_postgres
def test_self_link_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        session.add(person)
        session.commit()

        session.add(_make_relationship(person, person))
        with pytest.raises(IntegrityError):
            session.commit()


# --- duplicate active relationship (guardian, child, type) ----------------


@requires_postgres
def test_duplicate_active_relationship_for_same_guardian_child_type_is_rejected() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()
        session.add(_make_relationship(guardian, child, relationship_type="parent"))
        session.commit()

        session.add(
            _make_relationship(
                guardian, child, relationship_type="parent", valid_from=_utc(2024, 6, 1)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_historical_inactive_relationship_for_same_guardian_child_type_is_allowed() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()
        session.add(
            _make_relationship(guardian, child, relationship_type="parent", status="active")
        )
        session.commit()

        # A second, simultaneously-valid row of the same
        # guardian/child/type is fine as long as it is NOT active.
        session.add(
            _make_relationship(guardian, child, relationship_type="parent", status="inactive")
        )
        session.commit()  # must not raise


@requires_postgres
def test_historical_revoked_relationship_for_same_guardian_child_type_is_allowed() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()
        session.add(
            _make_relationship(guardian, child, relationship_type="parent", status="active")
        )
        session.commit()

        session.add(
            _make_relationship(guardian, child, relationship_type="parent", status="revoked")
        )
        session.commit()  # must not raise


@requires_postgres
def test_different_relationship_type_allows_simultaneous_active_relationships() -> None:
    """The exclusion constraint must not be stricter than ADR-0023
    requires: it is scoped to (guardian, child, type) together, so two
    active relationships of different types for the same guardian/child
    pair are unrelated facts, not a duplicate."""
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        session.add_all(
            [
                _make_relationship(guardian, child, relationship_type="parent"),
                _make_relationship(guardian, child, relationship_type="legal_guardian"),
            ]
        )
        session.commit()  # must not raise


@requires_postgres
def test_new_active_relationship_allowed_after_previous_ones_validity_ends() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        first = _make_relationship(
            guardian,
            child,
            relationship_type="parent",
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
        )
        session.add(first)
        session.commit()

        second = _make_relationship(
            guardian, child, relationship_type="parent", valid_from=_utc(2024, 6, 1)
        )
        session.add(second)
        session.commit()  # must not raise: validity periods do not overlap

        # The historical (closed) relationship is preserved, not deleted.
        fetched_first = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == first.id)
        ).scalar_one()
        assert fetched_first.valid_to == _utc(2024, 6, 1)


# --- validity interval ----------------------------------------------------


@requires_postgres
def test_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        session.add(
            _make_relationship(
                guardian, child, valid_from=_utc(2024, 6, 1), valid_to=_utc(2024, 1, 1)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_valid_to_equal_to_valid_from_is_accepted() -> None:
    """Matches the existing project convention (Group,
    GroupInstructorAssignment, GroupMembership, EventStaffAssignment,
    EventGroupTarget all use `valid_to >= valid_from`, i.e. an inclusive
    boundary) — no new semantics invented here."""
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        same = _utc(2024, 6, 1)
        session.add(_make_relationship(guardian, child, valid_from=same, valid_to=same))
        session.commit()  # must not raise: boundary is >=, not >


@requires_postgres
def test_valid_to_is_optional_for_an_open_ended_relationship() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        relationship = _make_relationship(guardian, child)
        session.add(relationship)
        session.commit()  # must not raise

        fetched = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == relationship.id)
        ).scalar_one()
        assert fetched.valid_to is None


@requires_postgres
def test_closing_a_relationship_period_preserves_the_row() -> None:
    """History is never deleted; a closed period is a fact recorded via
    `valid_to`, not a row removal (ADR-0023 §3)."""
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()

        relationship = _make_relationship(guardian, child)
        session.add(relationship)
        session.commit()

        relationship.valid_to = _utc(2024, 12, 31)
        session.commit()

        fetched = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == relationship.id)
        ).scalar_one()
        assert fetched.valid_to == _utc(2024, 12, 31)


# --- primary contact ------------------------------------------------------


@requires_postgres
def test_duplicate_active_primary_contact_for_same_child_is_rejected() -> None:
    with session_scope() as session:
        guardian_a = _make_person()
        guardian_b = _make_person()
        child = _make_person()
        session.add_all([guardian_a, guardian_b, child])
        session.commit()

        session.add(
            _make_relationship(
                guardian_a, child, relationship_type="mother", is_primary_contact=True
            )
        )
        session.commit()

        session.add(
            _make_relationship(
                guardian_b,
                child,
                relationship_type="father",
                is_primary_contact=True,
                valid_from=_utc(2024, 6, 1),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_historical_non_overlapping_primary_contacts_for_same_child_are_allowed() -> None:
    with session_scope() as session:
        guardian_a = _make_person()
        guardian_b = _make_person()
        child = _make_person()
        session.add_all([guardian_a, guardian_b, child])
        session.commit()

        first_primary = _make_relationship(
            guardian_a,
            child,
            relationship_type="mother",
            is_primary_contact=True,
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
        )
        session.add(first_primary)
        session.commit()

        second_primary = _make_relationship(
            guardian_b,
            child,
            relationship_type="father",
            is_primary_contact=True,
            valid_from=_utc(2024, 6, 1),
        )
        session.add(second_primary)
        session.commit()  # must not raise: validity periods do not overlap

        fetched_first = session.execute(
            select(GuardianRelationship).where(GuardianRelationship.id == first_primary.id)
        ).scalar_one()
        assert fetched_first.valid_to == _utc(2024, 6, 1)


@requires_postgres
def test_revoked_primary_contact_does_not_block_a_new_one() -> None:
    """Scoped to status='active' rows (see model docstring): revoking a
    primary contact frees the child for a new one even without first
    closing valid_to, since the row no longer counts as active."""
    with session_scope() as session:
        guardian_a = _make_person()
        guardian_b = _make_person()
        child = _make_person()
        session.add_all([guardian_a, guardian_b, child])
        session.commit()

        first_primary = _make_relationship(
            guardian_a,
            child,
            relationship_type="mother",
            is_primary_contact=True,
            status="revoked",
        )
        session.add(first_primary)
        session.commit()

        second_primary = _make_relationship(
            guardian_b, child, relationship_type="father", is_primary_contact=True
        )
        session.add(second_primary)
        session.commit()  # must not raise: the first row is not active


@requires_postgres
def test_non_primary_relationships_do_not_conflict_with_primary_contact() -> None:
    with session_scope() as session:
        guardian_a = _make_person()
        guardian_b = _make_person()
        child = _make_person()
        session.add_all([guardian_a, guardian_b, child])
        session.commit()

        session.add(
            _make_relationship(
                guardian_a, child, relationship_type="mother", is_primary_contact=True
            )
        )
        session.add(
            _make_relationship(
                guardian_b, child, relationship_type="father", is_primary_contact=False
            )
        )
        session.commit()  # must not raise: only is_primary_contact=true rows conflict


@requires_postgres
def test_multiple_active_non_primary_relationships_for_same_child_are_allowed() -> None:
    with session_scope() as session:
        guardian_a = _make_person()
        guardian_b = _make_person()
        child = _make_person()
        session.add_all([guardian_a, guardian_b, child])
        session.commit()

        session.add_all(
            [
                _make_relationship(guardian_a, child, relationship_type="mother"),
                _make_relationship(guardian_b, child, relationship_type="father"),
            ]
        )
        session.commit()  # must not raise: neither is a primary contact


# --- foreign key integrity -------------------------------------------------


@requires_postgres
def test_guardian_person_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        child = _make_person()
        session.add(child)
        session.commit()

        bogus_guardian = Person(id=uuid.uuid4(), last_name="Nobody", first_name="Nobody")
        session.add(_make_relationship(bogus_guardian, child))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_child_person_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        guardian = _make_person()
        session.add(guardian)
        session.commit()

        bogus_child = Person(id=uuid.uuid4(), last_name="Nobody", first_name="Nobody")
        session.add(_make_relationship(guardian, bogus_child))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_guardian_with_a_relationship_is_restricted() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()
        session.add(_make_relationship(guardian, child))
        session.commit()

        session.delete(guardian)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_child_with_a_relationship_is_restricted() -> None:
    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()
        session.add(_make_relationship(guardian, child))
        session.commit()

        session.delete(child)
        with pytest.raises(IntegrityError):
            session.commit()


# --- concurrency: duplicate active relationship ---------------------------


@requires_postgres
def test_concurrent_duplicate_active_relationship_leaves_exactly_one() -> None:
    """Real-concurrency proof for the GiST exclusion constraint backing
    "duplicate active relationships for the same guardian, child and
    relationship type are forbidden" (ADR-0023 §3): two transactions
    racing to create an overlapping active relationship for the same
    (guardian, child, type) must never both succeed.

    Two concurrent inserts each checking the other's not-yet-committed
    row against a GiST exclusion constraint can resolve either as an
    ExclusionViolation (IntegrityError) or, if both sides are mid-check
    at once, as a genuine PostgreSQL deadlock (DeadlockDetected, which
    SQLAlchemy surfaces as OperationalError, not IntegrityError) — one
    side is chosen as the deadlock victim and its transaction is
    aborted. Both outcomes mean "this attempt did not persist its row",
    so both count as "rejected" here; only catching IntegrityError would
    let a real deadlock propagate as an unhandled thread exception
    instead of the expected rejection.
    """
    trial_count = 30
    for trial in range(trial_count):
        with session_scope() as setup:
            guardian = _make_person()
            child = _make_person()
            setup.add_all([guardian, child])
            setup.commit()
            guardian_id, child_id = guardian.id, child.id

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, str] = {}

        def attempt(name: str, valid_from: datetime.datetime) -> None:
            with session_scope() as session:
                guardian_ref = Person(id=guardian_id)
                child_ref = Person(id=child_id)
                start_gate.wait()
                try:
                    session.add(
                        _make_relationship(
                            guardian_ref,
                            child_ref,
                            relationship_type="parent",
                            valid_from=valid_from,
                        )
                    )
                    session.commit()
                    result[name] = "succeeded"
                except (IntegrityError, OperationalError):
                    session.rollback()
                    result[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a", _utc(2024, 1, 1)))
        thread_b = threading.Thread(target=attempt, args=("b", _utc(2024, 6, 1)))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        with session_scope() as check:
            active_count = check.execute(
                text(
                    "SELECT count(*) FROM guardian_relationships "
                    "WHERE guardian_person_id = :g AND child_person_id = :c "
                    "AND relationship_type = 'parent' AND status = 'active'"
                ),
                {"g": str(guardian_id), "c": str(child_id)},
            ).scalar_one()

        assert active_count == 1, (
            f"trial {trial}: expected exactly one surviving active relationship, "
            f"got {active_count}; outcomes={result}"
        )


# --- concurrency: primary-contact race -------------------------------------


@requires_postgres
def test_concurrent_primary_contact_assignment_leaves_exactly_one() -> None:
    """Real-concurrency proof for the GiST exclusion constraint backing
    "at most one valid primary-contact relationship exists for a child
    at a time" (ADR-0023 §3): two transactions racing to assign an
    overlapping active primary-contact relationship for the same child
    (different guardians) must never both succeed.

    See test_concurrent_duplicate_active_relationship_leaves_exactly_one's
    docstring for why both IntegrityError (ExclusionViolation) and
    OperationalError (a genuine PostgreSQL deadlock between the two
    concurrent exclusion-constraint checks) are treated as "rejected"
    here.
    """
    trial_count = 30
    for trial in range(trial_count):
        with session_scope() as setup:
            guardian_a = _make_person()
            guardian_b = _make_person()
            child = _make_person()
            setup.add_all([guardian_a, guardian_b, child])
            setup.commit()
            guardian_a_id, guardian_b_id, child_id = guardian_a.id, guardian_b.id, child.id

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, str] = {}

        def attempt(
            name: str, guardian_id: uuid.UUID, relationship_type: str
        ) -> None:
            with session_scope() as session:
                guardian_ref = Person(id=guardian_id)
                child_ref = Person(id=child_id)
                start_gate.wait()
                try:
                    session.add(
                        _make_relationship(
                            guardian_ref,
                            child_ref,
                            relationship_type=relationship_type,
                            is_primary_contact=True,
                        )
                    )
                    session.commit()
                    result[name] = "succeeded"
                except (IntegrityError, OperationalError):
                    session.rollback()
                    result[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a", guardian_a_id, "mother"))
        thread_b = threading.Thread(target=attempt, args=("b", guardian_b_id, "father"))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        with session_scope() as check:
            primary_count = check.execute(
                text(
                    "SELECT count(*) FROM guardian_relationships "
                    "WHERE child_person_id = :c AND is_primary_contact = true "
                    "AND status = 'active'"
                ),
                {"c": str(child_id)},
            ).scalar_one()

        assert primary_count == 1, (
            f"trial {trial}: expected exactly one surviving primary contact, "
            f"got {primary_count}; outcomes={result}"
        )


# --- migration --------------------------------------------------------


@requires_postgres
def test_guardian_relationship_migration_creates_the_expected_table(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars().all()
    finally:
        engine.dispose()

    assert "guardian_relationships" in tables


@requires_postgres
def test_guardian_relationship_downgrade_then_upgrade_preserves_a_working_schema(
    database_url: str,
) -> None:
    # Pinned to the absolute pre-GuardianRelationship revision rather
    # than a relative "-1", so this test keeps targeting the right
    # migration once a later migration becomes the new head.
    downgrade = run_alembic("downgrade", "2ba3556a97ce", database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars().all()
    finally:
        engine.dispose()
    assert "guardian_relationships" not in tables

    upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        guardian = _make_person()
        child = _make_person()
        session.add_all([guardian, child])
        session.commit()
        session.add(_make_relationship(guardian, child))
        session.commit()  # must not raise: the re-created table works
