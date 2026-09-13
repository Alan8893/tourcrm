"""Real PostgreSQL integration tests for the ADR-0022 Club-ownership
validation service (app.groups.service) — the canonical, shared
enforcement mechanism for GroupMembership and GroupInstructorAssignment
writes. See tests/integration/test_groups.py for the raw persistence-
layer tests (which deliberately do not enforce this invariant — see
that file's module docstring).

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
from sqlalchemy import select

from app.db.authorization import Role, UserRoleAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.groups.service import (
    GroupMembershipClubMismatchError,
    InstructorClubMembershipMissingError,
    create_group_instructor_assignment,
    create_group_membership,
)

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
        "status": "active",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


# --- GroupMembership --------------------------------------------------


@requires_postgres
def test_create_group_membership_succeeds_for_same_club() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        membership = create_group_membership(
            session,
            group_id=group.id,
            club_membership_id=club_membership.id,
            valid_from=_utc(2024, 1, 1),
            membership_status="active",
        )

        fetched = session.execute(
            select(GroupMembership).where(GroupMembership.id == membership.id)
        ).scalar_one()
        assert fetched.group_id == group.id
        assert fetched.club_membership_id == club_membership.id


@requires_postgres
def test_create_group_membership_rejects_cross_club_combination() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        # The ClubMembership belongs to Club B; the Group belongs to Club A.
        club_membership_in_b = _make_club_membership(club_b, person)
        group_in_a = _make_group(club_a)
        session.add_all([club_membership_in_b, group_in_a])
        session.commit()

        with pytest.raises(GroupMembershipClubMismatchError) as exc_info:
            create_group_membership(
                session,
                group_id=group_in_a.id,
                club_membership_id=club_membership_in_b.id,
                valid_from=_utc(2024, 1, 1),
                membership_status="active",
            )
        assert exc_info.value.group_club_id == club_a.id
        assert exc_info.value.club_membership_club_id == club_b.id

        rows = session.execute(
            select(GroupMembership).where(
                GroupMembership.club_membership_id == club_membership_in_b.id
            )
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_create_group_membership_rejects_when_source_membership_is_the_foreign_club() -> None:
    """Same invariant as the cross-Club test above, from the opposite
    direction: the ClubMembership being linked is the one that belongs
    to a *different* Club than the Group, not the other way around —
    the check must be symmetric regardless of which side is "foreign".
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person_a = _make_person()
        person_b = _make_person()
        session.add_all([club_a, club_b, person_a, person_b])
        session.commit()
        group_in_b = _make_group(club_b)
        club_membership_in_a = _make_club_membership(club_a, person_a)
        session.add_all([group_in_b, club_membership_in_a])
        session.commit()

        with pytest.raises(GroupMembershipClubMismatchError):
            create_group_membership(
                session,
                group_id=group_in_b.id,
                club_membership_id=club_membership_in_a.id,
                valid_from=_utc(2024, 1, 1),
                membership_status="active",
            )

        rows = session.execute(
            select(GroupMembership).where(
                GroupMembership.club_membership_id == club_membership_in_a.id
            )
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_valid_group_membership_still_works_after_a_rejected_attempt() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        club_membership_in_b = _make_club_membership(club_b, person)
        group_in_a = _make_group(club_a)
        group_in_b = _make_group(club_b)
        session.add_all([club_membership_in_b, group_in_a, group_in_b])
        session.commit()

        with pytest.raises(GroupMembershipClubMismatchError):
            create_group_membership(
                session,
                group_id=group_in_a.id,
                club_membership_id=club_membership_in_b.id,
                valid_from=_utc(2024, 1, 1),
                membership_status="active",
            )

        # The same session/club_membership can still be used correctly
        # afterward — the rollback on rejection did not corrupt the
        # session or leave it unusable.
        membership = create_group_membership(
            session,
            group_id=group_in_b.id,
            club_membership_id=club_membership_in_b.id,
            valid_from=_utc(2024, 1, 1),
            membership_status="active",
        )
        assert membership.group_id == group_in_b.id


# --- GroupInstructorAssignment ------------------------------------------


@requires_postgres
def test_create_group_instructor_assignment_succeeds_for_same_club_membership() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([user, club_membership, group])
        session.commit()

        assignment = create_group_instructor_assignment(
            session,
            group_id=group.id,
            user_id=user.id,
            role_in_group="instructor",
            valid_from=_utc(2024, 1, 1),
        )

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment.id
            )
        ).scalar_one()
        assert fetched.group_id == group.id
        assert fetched.user_id == user.id


@requires_postgres
def test_create_group_instructor_assignment_rejects_membership_in_another_club_only() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        # The user's only ClubMembership is in Club B; the Group is in Club A.
        club_membership_in_b = _make_club_membership(club_b, person)
        group_in_a = _make_group(club_a)
        session.add_all([user, club_membership_in_b, group_in_a])
        session.commit()

        with pytest.raises(InstructorClubMembershipMissingError) as exc_info:
            create_group_instructor_assignment(
                session,
                group_id=group_in_a.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_utc(2024, 1, 1),
            )
        assert exc_info.value.user_id == user.id
        assert exc_info.value.club_id == club_a.id

        rows = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.user_id == user.id
            )
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_create_group_instructor_assignment_succeeds_with_multiple_clubs_including_target() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        # Different membership_type per club: ClubMembership's own
        # exclusion constraint (Issue #17) partitions by
        # (person_id, membership_type), not by club — so the same person
        # can only hold two *simultaneously active* memberships of the
        # *same* membership_type if they don't overlap in time. Using
        # distinct types here is a test-data choice, not a new business
        # rule, and matches ADR-0022's own "a User may be associated with
        # multiple Clubs" statement.
        club_membership_in_a = _make_club_membership(
            club_a, person, membership_type="regular-a"
        )
        club_membership_in_b = _make_club_membership(
            club_b, person, membership_type="regular-b"
        )
        group_in_b = _make_group(club_b)
        session.add_all(
            [user, club_membership_in_a, club_membership_in_b, group_in_b]
        )
        session.commit()

        assignment = create_group_instructor_assignment(
            session,
            group_id=group_in_b.id,
            user_id=user.id,
            role_in_group="instructor",
            valid_from=_utc(2024, 1, 1),
        )

        fetched = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.id == assignment.id
            )
        ).scalar_one()
        assert fetched.group_id == group_in_b.id


@requires_postgres
def test_create_group_instructor_assignment_rejects_when_multiple_clubs_exclude_target() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        club_c = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, club_c, person])
        session.commit()
        user = _make_user(person)
        # Distinct membership_type per club — see the comment in the
        # "succeeds_with_multiple_clubs" test above for why.
        club_membership_in_a = _make_club_membership(
            club_a, person, membership_type="regular-a"
        )
        club_membership_in_b = _make_club_membership(
            club_b, person, membership_type="regular-b"
        )
        group_in_c = _make_group(club_c)
        session.add_all(
            [user, club_membership_in_a, club_membership_in_b, group_in_c]
        )
        session.commit()

        with pytest.raises(InstructorClubMembershipMissingError):
            create_group_instructor_assignment(
                session,
                group_id=group_in_c.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_utc(2024, 1, 1),
            )

        rows = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.user_id == user.id
            )
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_create_group_instructor_assignment_rejects_inactive_membership_in_target_club() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        # Membership in the *target* club exists but is not active.
        inactive_membership = _make_club_membership(
            club, person, status="inactive", left_at=_utc(2024, 6, 1)
        )
        group = _make_group(club)
        session.add_all([user, inactive_membership, group])
        session.commit()

        with pytest.raises(InstructorClubMembershipMissingError):
            create_group_instructor_assignment(
                session,
                group_id=group.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_utc(2024, 1, 1),
            )


@requires_postgres
def test_global_instructor_role_without_target_club_membership_is_still_rejected() -> None:
    """ADR-0022 §5: a global `instructor` role assignment never
    substitutes for the explicit Club-relationship invariant."""
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        # Active membership only in Club B; the role grant below is
        # global (club_id=None), which must still not satisfy Club A.
        club_membership_in_b = _make_club_membership(club_b, person)
        group_in_a = _make_group(club_a)
        role = Role(code=f"instructor-{uuid.uuid4().hex[:8]}", name="Instructor")
        session.add_all([user, club_membership_in_b, group_in_a, role])
        session.commit()
        session.add(
            UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all")
        )
        session.commit()

        with pytest.raises(InstructorClubMembershipMissingError):
            create_group_instructor_assignment(
                session,
                group_id=group_in_a.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_utc(2024, 1, 1),
            )

        rows = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.user_id == user.id
            )
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_valid_instructor_assignment_still_works_after_a_rejected_attempt() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        club_membership_in_b = _make_club_membership(club_b, person)
        group_in_a = _make_group(club_a)
        group_in_b = _make_group(club_b)
        session.add_all([user, club_membership_in_b, group_in_a, group_in_b])
        session.commit()

        with pytest.raises(InstructorClubMembershipMissingError):
            create_group_instructor_assignment(
                session,
                group_id=group_in_a.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_utc(2024, 1, 1),
            )

        assignment = create_group_instructor_assignment(
            session,
            group_id=group_in_b.id,
            user_id=user.id,
            role_in_group="instructor",
            valid_from=_utc(2024, 1, 1),
        )
        assert assignment.group_id == group_in_b.id
