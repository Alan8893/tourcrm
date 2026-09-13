"""Real PostgreSQL integration tests for the Issue #41 Group persistence
foundation (Group, GroupMembership, GroupInstructorAssignment + migration).

These tests exercise the constraints that only PostgreSQL itself can
enforce (CHECK constraints, the exclusion constraint, foreign keys,
indexes) — there is no pure-Python domain module for this foundation
(unlike Event's app.events.lifecycle), since ADR-0021 defines no
lifecycle/status vocabulary to validate yet.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope

from .conftest import requires_postgres

API_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _migrated_schema(database_url: str) -> None:
    result = _run_alembic("upgrade", "head", database_url=database_url)
    assert result.returncode == 0, result.stderr


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


def _make_club_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "person_id": person.id,
        "membership_type": "regular",
        "status": "active",
        "joined_at": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Test Group {uuid.uuid4().hex[:8]}",
        "description": "A test group",
        "status": "active",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


def _make_group_membership(
    group: Group, club_membership: ClubMembership, **overrides: object
) -> GroupMembership:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": club_membership.id,
        "valid_from": _utc(2024, 1, 1),
        "membership_status": "active",
    }
    defaults.update(overrides)
    return GroupMembership(**defaults)  # type: ignore[arg-type]


def _make_group_instructor_assignment(
    group: Group, user: User, **overrides: object
) -> GroupInstructorAssignment:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "user_id": user.id,
        "role_in_group": "instructor",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return GroupInstructorAssignment(**defaults)  # type: ignore[arg-type]


# --- Group ------------------------------------------------------------


@requires_postgres
def test_creating_a_group_persists_all_fields() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        group = _make_group(club, valid_to=_utc(2025, 1, 1))
        session.add(group)
        session.commit()

        fetched = session.execute(select(Group).where(Group.id == group.id)).scalar_one()
        assert fetched.club_id == club.id
        assert fetched.name == group.name
        assert fetched.description == "A test group"
        assert fetched.status == "active"
        assert fetched.valid_from == _utc(2024, 1, 1)
        assert fetched.valid_to == _utc(2025, 1, 1)
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_group_valid_to_is_optional_for_an_ongoing_group() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        group = _make_group(club)
        session.add(group)
        session.commit()  # must not raise

        fetched = session.execute(select(Group).where(Group.id == group.id)).scalar_one()
        assert fetched.valid_to is None


@requires_postgres
def test_group_description_is_optional() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        group = _make_group(club, description=None)
        session.add(group)
        session.commit()  # must not raise


@requires_postgres
@pytest.mark.parametrize(
    "status_value", ["active", "closed", "archived", "some-made-up-status", "anything at all"]
)
def test_group_status_accepts_any_string_no_enum_exists(status_value: str) -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        group = _make_group(club, status=status_value)
        session.add(group)
        session.commit()  # must not raise: no CHECK/enum constraint exists

        fetched = session.execute(select(Group).where(Group.id == group.id)).scalar_one()
        assert fetched.status == status_value


@requires_postgres
def test_group_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        session.add(
            _make_group(club, valid_from=_utc(2024, 6, 1), valid_to=_utc(2024, 1, 1))
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_valid_to_equal_to_valid_from_is_accepted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        same = _utc(2024, 6, 1)
        session.add(_make_group(club, valid_from=same, valid_to=same))
        session.commit()  # must not raise: boundary is >=, not >


@requires_postgres
def test_group_club_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        bogus_club = Club(id=uuid.uuid4(), name="unused", status="active")
        session.add(_make_group(bogus_club))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_two_groups_receive_distinct_uuids() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        first = _make_group(club)
        second = _make_group(club)
        session.add_all([first, second])
        session.commit()

        assert first.id != second.id


@requires_postgres
def test_deleting_a_club_with_a_group_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        session.add(_make_group(club))
        session.commit()

        session.delete(club)
        with pytest.raises(IntegrityError):
            session.commit()


# --- GroupMembership -----------------------------------------------------


@requires_postgres
def test_group_membership_links_group_and_club_membership() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        membership = _make_group_membership(group, club_membership)
        session.add(membership)
        session.commit()

        fetched = session.execute(
            select(GroupMembership).where(GroupMembership.id == membership.id)
        ).scalar_one()
        assert fetched.group_id == group.id
        assert fetched.club_membership_id == club_membership.id
        assert fetched.membership_status == "active"
        assert fetched.created_at is not None
        assert fetched.updated_at is not None

        # The person is resolved through club_membership_id ->
        # ClubMembership.person_id, per ADR-0021 §2 — never a direct
        # person_id column on GroupMembership itself.
        resolved_person_id = session.execute(
            select(ClubMembership.person_id).where(
                ClubMembership.id == fetched.club_membership_id
            )
        ).scalar_one()
        assert resolved_person_id == person.id


@requires_postgres
def test_group_membership_has_no_person_id_column() -> None:
    assert "person_id" not in GroupMembership.__table__.columns


@requires_postgres
def test_group_membership_has_no_is_primary_column() -> None:
    assert "is_primary" not in GroupMembership.__table__.columns


@requires_postgres
def test_group_membership_has_no_assigned_by_column() -> None:
    assert "assigned_by" not in GroupMembership.__table__.columns


@requires_postgres
def test_group_membership_valid_to_is_optional() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        membership = _make_group_membership(group, club_membership)
        session.add(membership)
        session.commit()  # must not raise

        fetched = session.execute(
            select(GroupMembership).where(GroupMembership.id == membership.id)
        ).scalar_one()
        assert fetched.valid_to is None


@requires_postgres
def test_group_membership_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        session.add(
            _make_group_membership(
                group,
                club_membership,
                valid_from=_utc(2024, 6, 1),
                valid_to=_utc(2024, 1, 1),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_membership_group_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()

        bogus_group = Group(
            id=uuid.uuid4(), club_id=club.id, name="unused", status="active",
            valid_from=_utc(2024, 1, 1),
        )
        session.add(_make_group_membership(bogus_group, club_membership))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_membership_club_membership_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()

        bogus_club_membership = ClubMembership(
            id=uuid.uuid4(),
            club_id=club.id,
            person_id=uuid.uuid4(),
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        session.add(_make_group_membership(group, bogus_club_membership))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_overlapping_periods_for_same_club_membership_are_prohibited() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([club_membership, group_a, group_b])
        session.commit()

        first = _make_group_membership(group_a, club_membership, valid_from=_utc(2024, 1, 1))
        session.add(first)
        session.commit()

        # Same club_membership_id, overlapping (open-ended) period, even
        # though it names a *different* group — database-schema.md §8:
        # "one membership can move between groups over time" implies
        # sequential, not concurrent, group membership.
        second = _make_group_membership(group_b, club_membership, valid_from=_utc(2024, 6, 1))
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_sequential_non_overlapping_periods_for_same_club_membership_are_allowed() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([club_membership, group_a, group_b])
        session.commit()

        first = _make_group_membership(
            group_a, club_membership, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        session.add(first)
        session.commit()

        second = _make_group_membership(group_b, club_membership, valid_from=_utc(2024, 6, 1))
        session.add(second)
        session.commit()  # must not raise: closed period, then a new one

        rows = session.execute(
            select(GroupMembership).where(
                GroupMembership.club_membership_id == club_membership.id
            )
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_closing_a_group_membership_period_preserves_the_row() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        membership = _make_group_membership(group, club_membership)
        session.add(membership)
        session.commit()
        membership_id = membership.id

        membership.valid_to = _utc(2024, 12, 31)
        session.commit()

        fetched = session.execute(
            select(GroupMembership).where(GroupMembership.id == membership_id)
        ).scalar_one()
        assert fetched.id == membership_id
        assert fetched.valid_to == _utc(2024, 12, 31)


@requires_postgres
def test_deleting_a_group_with_a_group_membership_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()

        session.delete(group)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_club_membership_with_a_group_membership_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()

        session.delete(club_membership)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_cross_club_group_membership_is_persistable_but_detectable_via_join() -> None:
    """Documents the current, deliberate scope boundary (see
    app.db.groups module docstring): this foundation has no DB-level
    trigger/composite-FK rejecting a Group from one Club being linked, via
    GroupMembership, to a ClubMembership from a *different* Club — no
    such mechanism exists anywhere else in this codebase either. The join
    path to detect the mismatch is fully available, which is what a
    future service/API layer needs to enforce it (exactly like
    app.authorization.service.club_boundary_matches already does for
    UserRoleAssignment).
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()

        group_in_club_a = _make_group(club_a)
        club_membership_in_club_b = _make_club_membership(club_b, person)
        session.add_all([group_in_club_a, club_membership_in_club_b])
        session.commit()

        cross_club_membership = _make_group_membership(
            group_in_club_a, club_membership_in_club_b
        )
        session.add(cross_club_membership)
        session.commit()  # not rejected today — see docstring above

        # But the mismatch is fully detectable via a join:
        joined = session.execute(
            select(Group.club_id, ClubMembership.club_id)
            .join(GroupMembership, GroupMembership.group_id == Group.id)
            .join(
                ClubMembership,
                ClubMembership.id == GroupMembership.club_membership_id,
            )
            .where(GroupMembership.id == cross_club_membership.id)
        ).one()
        group_club_id, club_membership_club_id = joined
        assert group_club_id == club_a.id
        assert club_membership_club_id == club_b.id
        assert group_club_id != club_membership_club_id


# --- GroupInstructorAssignment ---------------------------------------------


@requires_postgres
def test_creating_a_group_instructor_assignment_persists_all_fields() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        assignment = _make_group_instructor_assignment(
            group, user, role_in_group="leader", is_primary=True, valid_to=_utc(2025, 1, 1)
        )
        session.add(assignment)
        session.commit()

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment.id
            )
        ).scalar_one()
        assert fetched.group_id == group.id
        assert fetched.user_id == user.id
        assert fetched.role_in_group == "leader"
        assert fetched.is_primary is True
        assert fetched.valid_from == _utc(2024, 1, 1)
        assert fetched.valid_to == _utc(2025, 1, 1)
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


@requires_postgres
def test_is_primary_defaults_to_false_when_not_specified() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        assignment = GroupInstructorAssignment(
            group_id=group.id,
            user_id=user.id,
            role_in_group="assistant",
            valid_from=_utc(2024, 1, 1),
        )
        session.add(assignment)
        session.commit()

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment.id
            )
        ).scalar_one()
        assert fetched.is_primary is False


@requires_postgres
@pytest.mark.parametrize(
    "role_value", ["instructor", "leader", "assistant", "some-made-up-role", "anything at all"]
)
def test_role_in_group_accepts_any_string_no_enum_exists(role_value: str) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        assignment = _make_group_instructor_assignment(group, user, role_in_group=role_value)
        session.add(assignment)
        session.commit()  # must not raise: no CHECK/enum constraint exists

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment.id
            )
        ).scalar_one()
        assert fetched.role_in_group == role_value


@requires_postgres
def test_group_instructor_assignment_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        session.add(
            _make_group_instructor_assignment(
                group, user, valid_from=_utc(2024, 6, 1), valid_to=_utc(2024, 1, 1)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_instructor_assignment_valid_to_is_optional() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        assignment = _make_group_instructor_assignment(group, user)
        session.add(assignment)
        session.commit()  # must not raise

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment.id
            )
        ).scalar_one()
        assert fetched.valid_to is None


@requires_postgres
def test_group_instructor_assignment_group_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        session.add(user)
        session.commit()

        bogus_group = Group(
            id=uuid.uuid4(), club_id=club.id, name="unused", status="active",
            valid_from=_utc(2024, 1, 1),
        )
        session.add(_make_group_instructor_assignment(bogus_group, user))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_instructor_assignment_user_id_foreign_key_integrity() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()

        bogus_user = User(
            id=uuid.uuid4(),
            person_id=uuid.uuid4(),
            login_identifier="unused@example.com",
            status="active",
        )
        session.add(_make_group_instructor_assignment(group, bogus_user))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_group_with_an_instructor_assignment_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()

        session.delete(group)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_user_with_an_instructor_assignment_is_restricted() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()

        session.delete(user)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_closing_an_instructor_assignment_period_preserves_the_row() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        assignment = _make_group_instructor_assignment(group, user)
        session.add(assignment)
        session.commit()
        assignment_id = assignment.id

        assignment.valid_to = _utc(2024, 12, 31)
        session.commit()

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment_id
            )
        ).scalar_one()
        assert fetched.id == assignment_id
        assert fetched.valid_to == _utc(2024, 12, 31)


@requires_postgres
def test_two_instructor_assignments_receive_distinct_uuids() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        group = _make_group(club)
        session.add_all([user, group])
        session.commit()

        first = _make_group_instructor_assignment(group, user, role_in_group="instructor")
        second = _make_group_instructor_assignment(group, user, role_in_group="assistant")
        session.add_all([first, second])
        session.commit()

        assert first.id != second.id


@requires_postgres
def test_cross_club_group_instructor_assignment_is_persistable_but_detectable_via_join() -> None:
    """Same documented boundary as the GroupMembership cross-Club test
    above: a User whose only ClubMembership is in Club B can currently be
    assigned as instructor of a Group owned by Club A. No DB-level
    mechanism rejects this anywhere in this codebase; the join path to
    detect it is available for a future service/API layer.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user_in_club_b = _make_user(person)
        club_membership_in_club_b = _make_club_membership(club_b, person)
        group_in_club_a = _make_group(club_a)
        session.add_all([user_in_club_b, club_membership_in_club_b, group_in_club_a])
        session.commit()

        cross_club_assignment = _make_group_instructor_assignment(
            group_in_club_a, user_in_club_b
        )
        session.add(cross_club_assignment)
        session.commit()  # not rejected today — see docstring above

        joined = session.execute(
            select(Group.club_id, ClubMembership.club_id)
            .join(
                GroupInstructorAssignment,
                GroupInstructorAssignment.group_id == Group.id,
            )
            .join(
                ClubMembership,
                ClubMembership.person_id == person.id,
            )
            .where(GroupInstructorAssignment.id == cross_club_assignment.id)
        ).one()
        group_club_id, instructor_club_membership_club_id = joined
        assert group_club_id == club_a.id
        assert instructor_club_membership_club_id == club_b.id
        assert group_club_id != instructor_club_membership_club_id


# --- migration ----------------------------------------------------------


@requires_postgres
def test_group_migration_creates_the_expected_tables(database_url: str) -> None:
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

    for expected in ("groups", "group_memberships", "group_instructor_assignments"):
        assert expected in tables


@requires_postgres
def test_group_downgrade_then_upgrade_preserves_a_working_schema(database_url: str) -> None:
    downgrade = _run_alembic("downgrade", "-1", database_url=database_url)
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
    for removed in ("groups", "group_memberships", "group_instructor_assignments"):
        assert removed not in tables

    upgrade = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        session.add(_make_group(club))
        session.commit()  # must not raise: the re-created table works
