"""Real PostgreSQL integration tests for the ADR-0022/ADR-0023 Club-
ownership validation service (app.events.service) — the canonical,
shared enforcement mechanism for EventStaffAssignment writes. See
tests/integration/test_event_staff_assignments.py for the raw
persistence-layer tests (which deliberately do not enforce this
invariant — see that file's module docstring).

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.db.authorization import Role, UserRoleAssignment
from app.db.events import Event, EventStaffAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.events.service import (
    EventStaffAssignmentPrimaryConflictError,
    EventStaffClubMembershipMissingError,
    create_event_staff_assignment,
)

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


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Orienteering basics",
        "start_at": _utc(2026, 9, 20, 17, 0),
        "end_at": _utc(2026, 9, 20, 19, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


# --- 1. same Club: allowed -------------------------------------------------


@requires_postgres
def test_create_event_staff_assignment_succeeds_for_same_club() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        club_membership = _make_club_membership(club, person)
        event = _make_event(club)
        session.add_all([user, club_membership, event])
        session.commit()

        assignment = create_event_staff_assignment(
            session,
            event_id=event.id,
            user_id=user.id,
            role_in_event="instructor",
            valid_from=_utc(2024, 1, 1),
        )

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.event_id == event.id
        assert fetched.user_id == user.id


# --- 2. membership only in another Club: rejected --------------------------


@requires_postgres
def test_create_event_staff_assignment_rejects_membership_in_another_club_only() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        # The user's only ClubMembership is in Club B; the Event is in Club A.
        club_membership_in_b = _make_club_membership(club_b, person)
        event_in_a = _make_event(club_a)
        session.add_all([user, club_membership_in_b, event_in_a])
        session.commit()

        with pytest.raises(EventStaffClubMembershipMissingError) as exc_info:
            create_event_staff_assignment(
                session,
                event_id=event_in_a.id,
                user_id=user.id,
                role_in_event="instructor",
                valid_from=_utc(2024, 1, 1),
            )
        assert exc_info.value.user_id == user.id
        assert exc_info.value.club_id == club_a.id

        rows = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.user_id == user.id)
        ).scalars().all()
        assert rows == []


# --- 3. multiple Clubs including target: allowed ---------------------------


@requires_postgres
def test_create_event_staff_assignment_succeeds_with_multiple_clubs_including_target() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        # Distinct membership_type per club: ClubMembership's own
        # exclusion constraint (Issue #17) partitions by
        # (person_id, membership_type), not by club — see the identical
        # comment in tests/integration/test_groups_service.py.
        club_membership_in_a = _make_club_membership(
            club_a, person, membership_type="regular-a"
        )
        club_membership_in_b = _make_club_membership(
            club_b, person, membership_type="regular-b"
        )
        event_in_b = _make_event(club_b)
        session.add_all(
            [user, club_membership_in_a, club_membership_in_b, event_in_b]
        )
        session.commit()

        assignment = create_event_staff_assignment(
            session,
            event_id=event_in_b.id,
            user_id=user.id,
            role_in_event="instructor",
            valid_from=_utc(2024, 1, 1),
        )

        fetched = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.event_id == event_in_b.id


# --- 4. multiple Clubs NOT including target: rejected -----------------------


@requires_postgres
def test_create_event_staff_assignment_rejects_when_multiple_clubs_exclude_target() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        club_c = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, club_c, person])
        session.commit()
        user = _make_user(person)
        club_membership_in_a = _make_club_membership(
            club_a, person, membership_type="regular-a"
        )
        club_membership_in_b = _make_club_membership(
            club_b, person, membership_type="regular-b"
        )
        event_in_c = _make_event(club_c)
        session.add_all(
            [user, club_membership_in_a, club_membership_in_b, event_in_c]
        )
        session.commit()

        with pytest.raises(EventStaffClubMembershipMissingError):
            create_event_staff_assignment(
                session,
                event_id=event_in_c.id,
                user_id=user.id,
                role_in_event="instructor",
                valid_from=_utc(2024, 1, 1),
            )

        rows = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.user_id == user.id)
        ).scalars().all()
        assert rows == []


# --- 5. no active membership in target Club at all: rejected ---------------


@requires_postgres
def test_create_event_staff_assignment_rejects_no_membership_at_all() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        event = _make_event(club)
        session.add_all([user, event])
        session.commit()

        with pytest.raises(EventStaffClubMembershipMissingError):
            create_event_staff_assignment(
                session,
                event_id=event.id,
                user_id=user.id,
                role_in_event="instructor",
                valid_from=_utc(2024, 1, 1),
            )


# --- 6. membership exists but inactive: rejected ----------------------------


@requires_postgres
def test_create_event_staff_assignment_rejects_inactive_membership_in_target_club() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        inactive_membership = _make_club_membership(
            club, person, status="inactive", left_at=_utc(2024, 6, 1)
        )
        event = _make_event(club)
        session.add_all([user, inactive_membership, event])
        session.commit()

        with pytest.raises(EventStaffClubMembershipMissingError):
            create_event_staff_assignment(
                session,
                event_id=event.id,
                user_id=user.id,
                role_in_event="instructor",
                valid_from=_utc(2024, 1, 1),
            )


@requires_postgres
def test_global_instructor_role_without_target_club_membership_is_still_rejected() -> None:
    """ADR-0022 §5/§7: a global `instructor` role assignment never
    substitutes for the explicit Club-relationship invariant."""
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        club_membership_in_b = _make_club_membership(club_b, person)
        event_in_a = _make_event(club_a)
        role = Role(code=f"instructor-{uuid.uuid4().hex[:8]}", name="Instructor")
        session.add_all([user, club_membership_in_b, event_in_a, role])
        session.commit()
        session.add(
            UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all")
        )
        session.commit()

        with pytest.raises(EventStaffClubMembershipMissingError):
            create_event_staff_assignment(
                session,
                event_id=event_in_a.id,
                user_id=user.id,
                role_in_event="instructor",
                valid_from=_utc(2024, 1, 1),
            )

        rows = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.user_id == user.id)
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_valid_assignment_still_works_after_a_rejected_attempt() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        session.add_all([club_a, club_b, person])
        session.commit()
        user = _make_user(person)
        club_membership_in_b = _make_club_membership(club_b, person)
        event_in_a = _make_event(club_a)
        event_in_b = _make_event(club_b)
        session.add_all([user, club_membership_in_b, event_in_a, event_in_b])
        session.commit()

        with pytest.raises(EventStaffClubMembershipMissingError):
            create_event_staff_assignment(
                session,
                event_id=event_in_a.id,
                user_id=user.id,
                role_in_event="instructor",
                valid_from=_utc(2024, 1, 1),
            )

        # The rollback on rejection did not corrupt the session.
        assignment = create_event_staff_assignment(
            session,
            event_id=event_in_b.id,
            user_id=user.id,
            role_in_event="instructor",
            valid_from=_utc(2024, 1, 1),
        )
        assert assignment.event_id == event_in_b.id


# --- primary conflict raised as a typed error through the service ----------


@requires_postgres
def test_create_event_staff_assignment_raises_typed_error_on_primary_conflict() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Anna")
        person_b = _make_person(first_name="Boris")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a = _make_user(person_a)
        user_b = _make_user(person_b)
        club_membership_a = _make_club_membership(club, person_a)
        club_membership_b = _make_club_membership(club, person_b, membership_type="regular-b")
        event = _make_event(club)
        session.add_all([user_a, user_b, club_membership_a, club_membership_b, event])
        session.commit()

        create_event_staff_assignment(
            session,
            event_id=event.id,
            user_id=user_a.id,
            role_in_event="leader",
            valid_from=_utc(2024, 1, 1),
            is_primary=True,
        )

        with pytest.raises(EventStaffAssignmentPrimaryConflictError) as exc_info:
            create_event_staff_assignment(
                session,
                event_id=event.id,
                user_id=user_b.id,
                role_in_event="leader",
                valid_from=_utc(2024, 6, 1),
                is_primary=True,
            )
        assert exc_info.value.event_id == event.id

        # Only the first primary assignment was persisted.
        rows = session.execute(
            select(EventStaffAssignment).where(EventStaffAssignment.event_id == event.id)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == user_a.id

        # The session remains usable afterward (rollback did not corrupt it).
        second_non_primary = create_event_staff_assignment(
            session,
            event_id=event.id,
            user_id=user_b.id,
            role_in_event="assistant",
            valid_from=_utc(2024, 6, 1),
            is_primary=False,
        )
        assert second_non_primary.event_id == event.id
