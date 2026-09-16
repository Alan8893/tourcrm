"""Real PostgreSQL integration tests for app.role_assignments.service
(Issue #74, implementing ADR-0026 and ADR-0027).

Run with a reachable PostgreSQL instance, matching
tests/integration/test_groups_service.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v

ADR-0027 (RoleAssignment and ClubMembership effectivity) settles what was
an open question during initial implementation: ending the target User's
ClubMembership is a creation-time integrity prerequisite only, never a
later effectivity condition. It does not delete, revoke, or otherwise
mutate the RoleAssignment row, and it does NOT make an otherwise
temporally-effective RoleAssignment ineffective — the shared
`app.authorization.service.applicable_assignments()`/`Authorizer` engine
deliberately gained no ClubMembership check (ADR-0027 §2: "MUST NOT
acquire a universal requirement"). `test_ending_target_club_membership_
leaves_the_role_assignment_effective` below asserts this directly via
the real authorization engine, not just "the row is unchanged".
"""

import datetime
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.audit.vocabulary import CANONICAL_AUDIT_ACTIONS
from app.authorization.context import ResourceContext
from app.authorization.service import can
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.role_assignments.lifecycle import (
    InvalidRoleAssignmentScopeError,
    InvalidRoleAssignmentTransitionError,
)
from app.role_assignments.service import (
    DuplicateRoleAssignmentError,
    RoleAssignmentClubMembershipMissingError,
    create_role_assignment,
    revoke_role_assignment,
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


def _make_club_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "person_id": person.id,
        "membership_type": "member",
        "status": "active",
        "joined_at": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


def _make_role(**overrides: object) -> Role:
    defaults: dict[str, object] = {"code": f"role-{uuid.uuid4().hex[:8]}", "name": "Test Role"}
    defaults.update(overrides)
    return Role(**defaults)  # type: ignore[arg-type]


def _latest_audit_actions(*, resource_id: uuid.UUID) -> list[str]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(AuditLog.action)
                .where(AuditLog.resource_id == resource_id)
                .order_by(AuditLog.occurred_at.asc())
            )
            .scalars()
            .all()
        )
        return list(rows)


# --- create_role_assignment: happy paths ------------------------------------


@requires_postgres
def test_create_club_scoped_all_scope_assignment_succeeds_and_is_audited() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([user, membership, role])
        session.commit()
        user_id, role_id, club_id = user.id, role.id, club.id

        assignment = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )
        assert assignment.club_id == club_id
        assert assignment.valid_from is not None
        assert assignment.valid_to is None
        assignment_id = assignment.id

    assert _latest_audit_actions(resource_id=assignment_id) == ["role_assignment.created"]
    assert "role_assignment.created" in CANONICAL_AUDIT_ACTIONS


@requires_postgres
def test_create_all_scope_assignment_without_club_id_succeeds_as_global() -> None:
    """ADR-0026 §2 originally required club_id even for scope `all`,
    while §5 (`role.manage`, same ADR) already described `all +
    club_id=NULL` as valid ("the holder may manage RoleAssignments in
    any Club") — an internal inconsistency surfaced and resolved by the
    TH-0089 / Issue #99 amendment (see ADR-0026's own amendment section
    and app.role_assignments.lifecycle's module docstring): `all` is now
    the one scope whose club_id may be either NULL (installation-wide)
    or a specific Club. This is exactly the combination
    app.authentication.bootstrap.bootstrap_initial_administrator uses
    for the global installation administrator."""
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role()
        session.add_all([person, user, role])
        session.commit()
        user_id, role_id = user.id, role.id

        assignment = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            actor_user_id=user_id,
        )
        assert assignment.club_id is None
        assert assignment.scope_type == "all"

        rows = (
            session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
def test_create_club_scoped_assignment_succeeds_with_active_membership() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        target = _make_user(person)
        actor_person = _make_person(first_name="Actor")
        session.add(actor_person)
        session.commit()
        actor = _make_user(actor_person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([target, actor, membership, role])
        session.commit()
        target_id, actor_id, club_id, role_id = target.id, actor.id, club.id, role.id

        assignment = create_role_assignment(
            session,
            user_id=target_id,
            role_id=role_id,
            scope_type="own_groups",
            club_id=club_id,
            actor_user_id=actor_id,
        )
        assert assignment.club_id == club_id
        assert assignment.scope_type == "own_groups"


@requires_postgres
def test_create_club_scoped_assignment_without_active_membership_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        target = _make_user(person)
        role = _make_role()
        session.add_all([target, role])
        session.commit()
        target_id, club_id, role_id = target.id, club.id, role.id

        with pytest.raises(RoleAssignmentClubMembershipMissingError) as exc_info:
            create_role_assignment(
                session,
                user_id=target_id,
                role_id=role_id,
                scope_type="own_events",
                club_id=club_id,
                actor_user_id=target_id,
            )
        assert exc_info.value.user_id == target_id
        assert exc_info.value.club_id == club_id

        rows = (
            session.execute(
                select(UserRoleAssignment).where(UserRoleAssignment.user_id == target_id)
            )
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_create_assignment_with_invalid_scope_combination_is_rejected() -> None:
    """`none` requires club_id=NULL — supplying one is an invalid
    combination (ADR-0026 §2), rejected before any DB write."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        role = _make_role()
        session.add_all([user, role])
        session.commit()
        user_id, club_id, role_id = user.id, club.id, role.id

        with pytest.raises(InvalidRoleAssignmentScopeError):
            create_role_assignment(
                session,
                user_id=user_id,
                role_id=role_id,
                scope_type="none",
                club_id=club_id,
                actor_user_id=user_id,
            )

        rows = (
            session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id))
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_create_assignment_with_non_null_scope_ref_id_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role()
        session.add_all([person, user, role])
        session.commit()
        user_id, role_id = user.id, role.id

        with pytest.raises(InvalidRoleAssignmentScopeError):
            create_role_assignment(
                session,
                user_id=user_id,
                role_id=role_id,
                scope_type="all",
                scope_ref_id=uuid.uuid4(),
                actor_user_id=user_id,
            )


@requires_postgres
def test_create_assignment_rejects_duplicate_overlapping() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([user, membership, role])
        session.commit()
        user_id, role_id, club_id = user.id, role.id, club.id

        create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )

        with pytest.raises(DuplicateRoleAssignmentError):
            create_role_assignment(
                session,
                user_id=user_id,
                role_id=role_id,
                scope_type="all",
                club_id=club_id,
                actor_user_id=user_id,
            )

        rows = (
            session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
def test_reassignment_after_revoke_creates_a_new_row_not_reopening_the_old_one() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([user, membership, role])
        session.commit()
        user_id, role_id, club_id = user.id, role.id, club.id

        first = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )
        first_id = first.id
        revoke_role_assignment(session, assignment=first, actor_user_id=user_id)

        second = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )
        assert second.id != first_id
        assert second.valid_to is None

        rows = (
            session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        # The original row is untouched (still ended, not reopened).
        original = session.get(UserRoleAssignment, first_id)
        assert original is not None
        assert original.valid_to is not None


# --- revoke_role_assignment --------------------------------------------


@requires_postgres
def test_revoke_sets_valid_to_and_is_audited() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([user, membership, role])
        session.commit()
        user_id, role_id, club_id = user.id, role.id, club.id

        assignment = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )
        assignment_id = assignment.id
        assert assignment.valid_to is None

        revoked = revoke_role_assignment(session, assignment=assignment, actor_user_id=user_id)
        assert revoked.valid_to is not None

    actions = _latest_audit_actions(resource_id=assignment_id)
    assert actions == ["role_assignment.created", "role_assignment.revoked"]
    assert "role_assignment.revoked" in CANONICAL_AUDIT_ACTIONS


@requires_postgres
def test_revoke_twice_is_rejected_deterministically() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([user, membership, role])
        session.commit()
        user_id, role_id, club_id = user.id, role.id, club.id

        assignment = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )
        revoke_role_assignment(session, assignment=assignment, actor_user_id=user_id)

        with pytest.raises(InvalidRoleAssignmentTransitionError) as exc_info:
            revoke_role_assignment(session, assignment=assignment, actor_user_id=user_id)
        assert exc_info.value.assignment_id == assignment.id


@requires_postgres
def test_revoke_audit_details_contain_no_secrets_or_orm_dump() -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        user = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        session.add_all([user, membership, role])
        session.commit()
        user_id, role_id, club_id = user.id, role.id, club.id

        assignment = create_role_assignment(
            session,
            user_id=user_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=user_id,
        )
        assignment_id = assignment.id
        revoke_role_assignment(session, assignment=assignment, actor_user_id=user_id)

    with session_scope() as session:
        row = session.execute(
            select(AuditLog).where(
                AuditLog.action == "role_assignment.revoked", AuditLog.resource_id == assignment_id
            )
        ).scalar_one()
        assert row.outcome == "success"
        details = row.details or {}
        blob = str(details).lower()
        assert "password" not in blob
        assert "token" not in blob
        assert "secret" not in blob


# --- Known-true half of the ClubMembership-lifecycle requirement -----------


@requires_postgres
def test_ending_target_club_membership_leaves_the_role_assignment_effective() -> None:
    """ADR-0027 §1/§2: ending the target User's ClubMembership is a
    creation-time integrity prerequisite only — it never deletes,
    revokes, or otherwise mutates the RoleAssignment row, and it does
    NOT make an otherwise temporally-effective RoleAssignment
    ineffective. Asserted two ways: the row itself is untouched, AND the
    real authorization engine (`app.authorization.service.can()`, the
    same function every `Authorizer`/`role.manage` check goes through)
    still grants the permission afterward — proving the shared engine
    genuinely has no ClubMembership check, not merely that the row
    wasn't deleted."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        target = _make_user(person)
        membership = _make_club_membership(club, person)
        role = _make_role()
        permission = Permission(code=f"resource-{uuid.uuid4().hex[:8]}.read")
        session.add_all([target, membership, role, permission])
        session.commit()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.commit()
        target_id, club_id, role_id, membership_id, permission_code = (
            target.id,
            club.id,
            role.id,
            membership.id,
            permission.code,
        )

        assignment = create_role_assignment(
            session,
            user_id=target_id,
            role_id=role_id,
            scope_type="all",
            club_id=club_id,
            actor_user_id=target_id,
        )
        assignment_id = assignment.id

        assert can(session, target_id, permission_code, ResourceContext(club_id=club_id))

    with session_scope() as session:
        membership = session.get(ClubMembership, membership_id)
        assert membership is not None
        membership.status = "inactive"
        membership.left_at = _utc(2024, 6, 1)
        session.commit()

    with session_scope() as session:
        row = session.get(UserRoleAssignment, assignment_id)
        assert row is not None
        assert row.valid_to is None
        assert row.club_id == club_id

        # The crux of ADR-0027: the shared authorization engine still
        # grants the permission — the ended ClubMembership did not
        # silently revoke effective access.
        assert can(session, target_id, permission_code, ResourceContext(club_id=club_id))
