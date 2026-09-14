"""Real PostgreSQL integration tests for the Issue #17 identity foundation
(Club, Person, User, ClubMembership).

These tests exercise the constraints that only PostgreSQL itself can
enforce (foreign keys, uniqueness, the exclusion constraint, the check
constraints) — see tests/unit/test_identity.py for the pure-Python
normalize_login_identifier() tests that need no database.

Run with a reachable PostgreSQL instance, matching tests/integration/test_database.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope

from ._schema_reset import run_alembic
from .conftest import requires_postgres


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


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


def _make_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club": club,
        "person": person,
        "membership_type": "regular",
        "status": "active",
        "joined_at": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


@requires_postgres
def test_person_can_exist_without_a_user() -> None:
    with session_scope() as session:
        person = _make_person()
        session.add(person)
        session.commit()

        fetched = session.execute(select(Person).where(Person.id == person.id)).scalar_one()
        assert fetched.user is None


@requires_postgres
def test_person_and_user_are_created_correctly_and_linked() -> None:
    with session_scope() as session:
        person = _make_person(first_name="Boris")
        user = _make_user(person, login_identifier="boris@example.com")
        session.add_all([person, user])
        session.commit()

        fetched_user = session.execute(select(User).where(User.id == user.id)).scalar_one()
        assert fetched_user.person_id == person.id
        assert fetched_user.person.first_name == "Boris"
        assert fetched_user.password_hash is None


@requires_postgres
def test_cannot_create_a_second_user_for_the_same_person() -> None:
    with session_scope() as session:
        person = _make_person()
        session.add(person)
        session.add(_make_user(person, login_identifier="first@example.com"))
        session.commit()

        session.add(_make_user(person, login_identifier="second@example.com"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_login_identifier_normalizes_equivalent_values_identically() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person, login_identifier="  Mixed.Case@Example.COM  ")
        session.add_all([person, user])
        session.commit()

        session.refresh(user)
        assert user.normalized_login_identifier == "mixed.case@example.com"


@requires_postgres
def test_cannot_create_duplicate_normalized_login_identifier() -> None:
    with session_scope() as session:
        person_a = _make_person()
        session.add(person_a)
        session.add(_make_user(person_a, login_identifier="Someone@Example.com"))
        session.commit()

        person_b = _make_person(first_name="Other")
        session.add(person_b)
        session.add(_make_user(person_b, login_identifier=" someone@example.com "))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_club_membership_links_club_and_person() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        membership = _make_membership(club, person)
        session.add_all([club, person, membership])
        session.commit()

        fetched = session.execute(
            select(ClubMembership).where(ClubMembership.id == membership.id)
        ).scalar_one()
        assert fetched.club_id == club.id
        assert fetched.person_id == person.id


@requires_postgres
def test_membership_status_change_preserves_history_as_a_new_row() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        original = _make_membership(
            club, person, status="active", joined_at=_utc(2020, 1, 1)
        )
        session.add_all([club, person, original])
        session.commit()

        original.status = "inactive"
        original.left_at = _utc(2021, 1, 1)
        session.commit()
        original_id = original.id

        rejoin = _make_membership(club, person, status="active", joined_at=_utc(2022, 1, 1))
        session.add(rejoin)
        session.commit()

        rows = session.execute(
            select(ClubMembership)
            .where(ClubMembership.person_id == person.id)
            .order_by(ClubMembership.joined_at)
        ).scalars().all()
        assert [row.id for row in rows] == [original_id, rejoin.id]
        assert rows[0].status == "inactive"
        assert rows[0].left_at == _utc(2021, 1, 1)
        assert rows[1].status == "active"
        assert rows[1].left_at is None


@requires_postgres
def test_rejoin_after_inactive_membership_is_allowed_and_history_kept() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        first = _make_membership(
            club,
            person,
            status="inactive",
            joined_at=_utc(2019, 1, 1),
            left_at=_utc(2019, 6, 1),
        )
        session.add_all([club, person, first])
        session.commit()

        second = _make_membership(club, person, status="active", joined_at=_utc(2023, 1, 1))
        session.add(second)
        session.commit()

        rows = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == person.id)
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_overlapping_active_memberships_of_same_type_are_prohibited() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        first = _make_membership(club, person, status="active", joined_at=_utc(2024, 1, 1))
        session.add_all([club, person, first])
        session.commit()

        second = _make_membership(club, person, status="active", joined_at=_utc(2024, 6, 1))
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_left_at_before_joined_at_is_forbidden() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()

        bad = _make_membership(
            club,
            person,
            status="inactive",
            joined_at=_utc(2024, 6, 1),
            left_at=_utc(2024, 1, 1),
        )
        session.add(bad)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_foreign_keys_protect_membership_integrity() -> None:
    with session_scope() as session:
        bogus_membership = ClubMembership(
            club_id=uuid.uuid4(),
            person_id=uuid.uuid4(),
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        session.add(bogus_membership)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_user_does_not_cascade_delete_the_person() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id = person.id

        session.delete(user)
        session.commit()

        remaining_person = session.execute(
            select(Person).where(Person.id == person_id)
        ).scalar_one()
        assert remaining_person is not None
        assert session.execute(select(User).where(User.person_id == person_id)).first() is None


@requires_postgres
def test_membership_history_is_unaffected_by_user_deletion() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        membership = _make_membership(club, person)
        session.add_all([club, person, user, membership])
        session.commit()
        membership_id = membership.id

        session.delete(user)
        session.commit()

        remaining_membership = session.execute(
            select(ClubMembership).where(ClubMembership.id == membership_id)
        ).scalar_one()
        assert remaining_membership.person_id == person.id


@requires_postgres
def test_deleting_a_person_with_a_membership_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        membership = _make_membership(club, person)
        session.add_all([club, person, membership])
        session.commit()

        session.delete(person)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_club_name_must_be_unique() -> None:
    with session_scope() as session:
        session.add(_make_club(name="Same Club Name"))
        session.commit()

        session.add(_make_club(name="Same Club Name"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_identity_migration_applies_on_a_clean_database(database_url: str) -> None:
    result = run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr

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

    for expected in ("clubs", "persons", "users", "club_memberships"):
        assert expected in tables
