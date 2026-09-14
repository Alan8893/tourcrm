"""Real PostgreSQL integration tests for the Issue #19 authorization
foundation (Role, Permission, RolePermission, UserRoleAssignment).

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.authorization import (
    BASELINE_ROLE_CODES,
    CANONICAL_SCOPE_TYPES,
    DOCUMENTED_PERMISSION_CODES,
    Permission,
    Role,
    RolePermission,
    UserRoleAssignment,
)
from app.db.identity import Club, Person, User
from app.db.session import session_scope

from ._schema_reset import run_alembic
from .conftest import requires_postgres


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Test Club {uuid.uuid4().hex[:8]}", "status": "active"}
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


def _make_role(**overrides: object) -> Role:
    defaults: dict[str, object] = {"code": f"role-{uuid.uuid4().hex[:8]}", "name": "Test Role"}
    defaults.update(overrides)
    return Role(**defaults)  # type: ignore[arg-type]


def _make_permission(**overrides: object) -> Permission:
    defaults: dict[str, object] = {"code": f"resource-{uuid.uuid4().hex[:8]}.read"}
    defaults.update(overrides)
    return Permission(**defaults)  # type: ignore[arg-type]


# --- Role -------------------------------------------------------------


@requires_postgres
def test_role_can_be_created() -> None:
    with session_scope() as session:
        role = _make_role(code="test-role", name="Test Role")
        session.add(role)
        session.commit()

        fetched = session.execute(select(Role).where(Role.id == role.id)).scalar_one()
        assert fetched.code == "test-role"
        assert fetched.is_system is False


@requires_postgres
def test_duplicate_role_code_is_rejected() -> None:
    with session_scope() as session:
        session.add(_make_role(code="dup-role"))
        session.commit()

        session.add(_make_role(code="dup-role"))
        with pytest.raises(IntegrityError):
            session.commit()


# --- Permission ---------------------------------------------------------


@requires_postgres
def test_permission_can_be_created() -> None:
    with session_scope() as session:
        permission = _make_permission(code="widget.read")
        session.add(permission)
        session.commit()

        fetched = session.execute(
            select(Permission).where(Permission.id == permission.id)
        ).scalar_one()
        assert fetched.code == "widget.read"


@requires_postgres
def test_duplicate_permission_code_is_rejected() -> None:
    with session_scope() as session:
        session.add(_make_permission(code="widget.manage"))
        session.commit()

        session.add(_make_permission(code="widget.manage"))
        with pytest.raises(IntegrityError):
            session.commit()


# --- RolePermission -------------------------------------------------------


@requires_postgres
def test_role_permission_link_can_be_created() -> None:
    with session_scope() as session:
        role = _make_role(code="rp-role")
        permission = _make_permission(code="rp.read")
        session.add_all([role, permission])
        session.commit()

        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.commit()

        fetched = session.execute(
            select(RolePermission).where(RolePermission.role_id == role.id)
        ).scalar_one()
        assert fetched.permission_id == permission.id


@requires_postgres
def test_duplicate_role_permission_pair_is_rejected() -> None:
    with session_scope() as session:
        role = _make_role(code="rp-dup-role")
        permission = _make_permission(code="rp-dup.read")
        session.add_all([role, permission])
        session.commit()

        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.commit()

        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_role_permission_foreign_keys_protect_integrity() -> None:
    with session_scope() as session:
        session.add(RolePermission(role_id=uuid.uuid4(), permission_id=uuid.uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_role_cascades_its_role_permission_links() -> None:
    with session_scope() as session:
        role = _make_role(code="cascade-role")
        permission = _make_permission(code="cascade.read")
        session.add_all([role, permission])
        session.commit()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.commit()

        session.delete(role)
        session.commit()

        remaining = session.execute(
            select(RolePermission).where(RolePermission.permission_id == permission.id)
        ).first()
        assert remaining is None
        # The permission itself is untouched by deleting the role.
        assert session.get(Permission, permission.id) is not None


# --- UserRoleAssignment -----------------------------------------------------


@requires_postgres
def test_user_role_assignment_can_be_created() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club = _make_club()
        role = _make_role(code="ura-role")
        session.add_all([person, user, club, role])
        session.commit()

        assignment = UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=club.id, scope_type="own_groups"
        )
        session.add(assignment)
        session.commit()

        fetched = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.id == assignment.id)
        ).scalar_one()
        assert fetched.user_id == user.id
        assert fetched.role_id == role.id
        assert fetched.club_id == club.id
        assert fetched.scope_type == "own_groups"
        assert fetched.scope_ref_id is None


@requires_postgres
def test_user_role_assignment_club_id_can_be_null_for_global_assignment() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="global-role")
        session.add_all([person, user, role])
        session.commit()

        assignment = UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all")
        session.add(assignment)
        session.commit()

        fetched = session.get(UserRoleAssignment, assignment.id)
        assert fetched is not None
        assert fetched.club_id is None


@requires_postgres
def test_user_role_assignment_user_foreign_key_protects_integrity() -> None:
    with session_scope() as session:
        role = _make_role(code="fk-user-role")
        session.add(role)
        session.commit()

        session.add(
            UserRoleAssignment(user_id=uuid.uuid4(), role_id=role.id, scope_type="all")
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_user_role_assignment_role_foreign_key_protects_integrity() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()

        session.add(
            UserRoleAssignment(user_id=user.id, role_id=uuid.uuid4(), scope_type="all")
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_user_role_assignment_club_foreign_key_protects_integrity() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="fk-club-role")
        session.add_all([person, user, role])
        session.commit()

        session.add(
            UserRoleAssignment(
                user_id=user.id, role_id=role.id, club_id=uuid.uuid4(), scope_type="all"
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
@pytest.mark.parametrize("scope_type", CANONICAL_SCOPE_TYPES)
def test_each_canonical_scope_type_is_accepted(scope_type: str) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code=f"scope-role-{scope_type}")
        session.add_all([person, user, role])
        session.commit()

        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type=scope_type))
        session.commit()


@requires_postgres
def test_invalid_scope_type_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="bad-scope-role")
        session.add_all([person, user, role])
        session.commit()

        session.add(
            UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="own_records")
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_duplicate_user_role_assignment_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club = _make_club()
        role = _make_role(code="dup-assignment-role")
        session.add_all([person, user, club, role])
        session.commit()

        session.add(
            UserRoleAssignment(
                user_id=user.id, role_id=role.id, club_id=club.id, scope_type="own_groups"
            )
        )
        session.commit()

        session.add(
            UserRoleAssignment(
                user_id=user.id, role_id=role.id, club_id=club.id, scope_type="own_groups"
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_duplicate_global_assignment_with_null_club_is_rejected() -> None:
    # `ck_user_role_assignments_no_overlapping_active` (a GiST EXCLUDE
    # constraint, Issue #74/ADR-0026 §1) COALESCEs the nullable club_id to
    # a sentinel UUID before comparing: two global (club_id=None)
    # assignments of the same role/scope to the same user must still
    # count as the same identity tuple (and, with both left open-ended,
    # overlap in time), unlike plain SQL NULL semantics. See
    # test_role_assignment_open_ended_overlap_is_rejected below for the
    # dedicated temporal-overlap tests.
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="dup-global-role")
        session.add_all([person, user, role])
        session.commit()

        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all"))
        session.commit()

        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_same_role_different_club_is_not_a_duplicate() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club_a = _make_club()
        club_b = _make_club()
        role = _make_role(code="multi-club-role")
        session.add_all([person, user, club_a, club_b, role])
        session.commit()

        session.add(
            UserRoleAssignment(
                user_id=user.id, role_id=role.id, club_id=club_a.id, scope_type="own_groups"
            )
        )
        session.add(
            UserRoleAssignment(
                user_id=user.id, role_id=role.id, club_id=club_b.id, scope_type="own_groups"
            )
        )
        session.commit()  # must not raise: different club_id, not a duplicate

        count = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
        ).scalars().all()
        assert len(count) == 2


@requires_postgres
def test_deleting_a_user_with_an_assignment_is_restricted() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="restrict-user-role")
        session.add_all([person, user, role])
        session.commit()
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all"))
        session.commit()

        session.delete(user)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_deleting_a_role_with_an_assignment_is_restricted() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="restrict-role-role")
        session.add_all([person, user, role])
        session.commit()
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all"))
        session.commit()

        session.delete(role)
        with pytest.raises(IntegrityError):
            session.commit()


# --- UserRoleAssignment temporal validity invariant (ADR-0026 §1, Issue #74) ---
#
# `ck_user_role_assignments_no_overlapping_active` (a GiST EXCLUDE
# constraint) is the DB-level enforcement of ADR-0026 §1's "no
# overlapping intervals for the same (user_id, role_id, club_id,
# scope_type, scope_ref_id)" invariant — it replaces the plain
# uniqueness constraint tested above once valid_from/valid_to exist.
# These tests are the DB-level proof of the interval-overlap formula,
# mirroring GroupInstructorAssignment's own is_primary invariant tests
# in test_groups.py exactly, including the two directly-required edge
# cases: a touching boundary is allowed (not an overlap — this is what
# makes "revoke, then re-assign" work), and two open-ended assignments
# always conflict.


def _make_role_assignment(user: User, role: Role, **overrides: object) -> UserRoleAssignment:
    defaults: dict[str, object] = {
        "user_id": user.id,
        "role_id": role.id,
        "scope_type": "all",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return UserRoleAssignment(**defaults)  # type: ignore[arg-type]


@requires_postgres
def test_role_assignment_full_overlap_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="temporal-full-overlap")
        session.add_all([person, user, role])
        session.commit()

        first = _make_role_assignment(
            user, role, valid_from=_utc(2024, 9, 1), valid_to=_utc(2024, 10, 1)
        )
        session.add(first)
        session.commit()

        second = _make_role_assignment(
            user, role, valid_from=_utc(2024, 9, 1), valid_to=_utc(2024, 10, 1)
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_role_assignment_partial_overlap_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="temporal-partial-overlap")
        session.add_all([person, user, role])
        session.commit()

        first = _make_role_assignment(
            user, role, valid_from=_utc(2024, 9, 1), valid_to=_utc(2024, 10, 1)
        )
        session.add(first)
        session.commit()

        second = _make_role_assignment(
            user, role, valid_from=_utc(2024, 9, 15), valid_to=_utc(2024, 10, 15)
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_role_assignment_open_ended_overlap_is_rejected() -> None:
    """Two open-ended (`valid_to=NULL`) assignments for the same identity
    tuple always overlap — NULL is an unbounded/ongoing end, not "no
    constraint"."""
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="temporal-open-overlap")
        session.add_all([person, user, role])
        session.commit()

        first = _make_role_assignment(user, role, valid_from=_utc(2024, 9, 1))
        session.add(first)
        session.commit()

        second = _make_role_assignment(user, role, valid_from=_utc(2024, 10, 1))
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_role_assignment_touching_boundary_is_allowed() -> None:
    """A shared boundary — one assignment's `valid_to` equals the next
    one's `valid_from` — is NOT an overlap under `[valid_from, valid_to)`
    semantics. This is exactly the "revoke, then re-assign" shape ADR-0026
    §1 requires: revoke closes the interval, and a new assignment may
    validly begin at (or after) that same instant."""
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="temporal-touching")
        session.add_all([person, user, role])
        session.commit()

        first = _make_role_assignment(
            user, role, valid_from=_utc(2024, 9, 1), valid_to=_utc(2024, 10, 1)
        )
        session.add(first)
        session.commit()

        second = _make_role_assignment(user, role, valid_from=_utc(2024, 10, 1))
        session.add(second)
        session.commit()  # must not raise: touching, not overlapping

        rows = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_role_assignment_sequential_historical_periods_are_allowed() -> None:
    """Two fully closed, non-overlapping historical periods (a gap between
    them, not just a touching boundary) are allowed."""
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="temporal-sequential")
        session.add_all([person, user, role])
        session.commit()

        first = _make_role_assignment(
            user, role, valid_from=_utc(2024, 1, 1), valid_to=_utc(2024, 6, 1)
        )
        session.add(first)
        session.commit()

        second = _make_role_assignment(
            user, role, valid_from=_utc(2024, 7, 1), valid_to=_utc(2024, 9, 1)
        )
        session.add(second)
        session.commit()  # must not raise: a gap, not an overlap

        rows = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_role_assignment_overlap_with_different_role_id_is_allowed() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role_a = _make_role(code="temporal-role-a")
        role_b = _make_role(code="temporal-role-b")
        session.add_all([person, user, role_a, role_b])
        session.commit()

        session.add(_make_role_assignment(user, role_a, valid_from=_utc(2024, 1, 1)))
        session.add(_make_role_assignment(user, role_b, valid_from=_utc(2024, 1, 1)))
        session.commit()  # must not raise: different role_id, not the same tuple

        rows = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_role_assignment_overlap_with_different_scope_type_is_allowed() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        club = _make_club()
        role = _make_role(code="temporal-scope-type")
        session.add_all([person, user, club, role])
        session.commit()

        session.add(
            _make_role_assignment(
                user, role, club_id=club.id, scope_type="own_groups", valid_from=_utc(2024, 1, 1)
            )
        )
        session.add(
            _make_role_assignment(
                user, role, club_id=club.id, scope_type="own_events", valid_from=_utc(2024, 1, 1)
            )
        )
        session.commit()  # must not raise: different scope_type, not the same tuple

        rows = session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_role_assignment_valid_to_before_valid_from_is_rejected() -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        role = _make_role(code="temporal-invalid-interval")
        session.add_all([person, user, role])
        session.commit()

        session.add(
            _make_role_assignment(
                user, role, valid_from=_utc(2024, 6, 1), valid_to=_utc(2024, 1, 1)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# --- Seed data ---------------------------------------------------------


@requires_postgres
def test_baseline_roles_are_seeded() -> None:
    with session_scope() as session:
        codes = set(
            session.execute(select(Role.code)).scalars().all()
        )
        assert codes == set(BASELINE_ROLE_CODES)
        assert "administrator" not in codes


@requires_postgres
def test_documented_permissions_are_seeded_and_only_those() -> None:
    with session_scope() as session:
        codes = set(session.execute(select(Permission.code)).scalars().all())
        assert codes == set(DOCUMENTED_PERMISSION_CODES)


@requires_postgres
def test_seed_creates_no_users_memberships_or_assignments() -> None:
    # Issue #19 §9: no bootstrap administrator, no auto-created users,
    # memberships or role assignments.
    with session_scope() as session:
        assert session.execute(select(User)).first() is None
        assert session.execute(select(UserRoleAssignment)).first() is None


@requires_postgres
def test_reapplying_seed_after_downgrade_and_upgrade_is_idempotent(database_url: str) -> None:
    downgrade = run_alembic("downgrade", "-1", database_url=database_url)
    assert downgrade.returncode == 0, downgrade.stderr

    upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        role_codes = session.execute(select(Role.code)).scalars().all()
        permission_codes = session.execute(select(Permission.code)).scalars().all()
        assert sorted(role_codes) == sorted(BASELINE_ROLE_CODES)
        assert sorted(permission_codes) == sorted(DOCUMENTED_PERMISSION_CODES)


@requires_postgres
def test_authorization_migration_applies_on_a_clean_database(database_url: str) -> None:
    from sqlalchemy import create_engine, text

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

    for expected in ("roles", "permissions", "role_permissions", "user_role_assignments"):
        assert expected in tables
