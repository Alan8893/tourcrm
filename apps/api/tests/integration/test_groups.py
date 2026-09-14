"""Real PostgreSQL integration tests for the Issue #41 Group persistence
foundation (Group, GroupMembership, GroupInstructorAssignment + migration).

These tests exercise the constraints that only PostgreSQL itself can
enforce (CHECK constraints, foreign keys, indexes) at the raw ORM/DB
layer — Club-ownership validation is a separate, application/service-
layer concern (ADR-0022) covered in
tests/integration/test_groups_service.py, not here; the cross-Club tests
in this file document that the raw persistence layer deliberately does
not enforce it (ADR-0022 §3/§8).

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
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
@pytest.mark.parametrize("status_value", ["active", "archived"])
def test_group_status_accepts_canonical_values(status_value: str) -> None:
    """people-api.md §14.1 (PO decision, Issue #69) closes the vocabulary
    ADR-0021 §1 originally left open — superseding the previous revision
    of this test (`test_group_status_accepts_any_string_no_enum_exists`),
    which documented the pre-Issue-#69 unconstrained-string behavior.
    """
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        group = _make_group(club, status=status_value)
        session.add(group)
        session.commit()  # must not raise: both are canonical values

        fetched = session.execute(select(Group).where(Group.id == group.id)).scalar_one()
        assert fetched.status == status_value


@requires_postgres
@pytest.mark.parametrize(
    "status_value", ["closed", "some-made-up-status", "anything at all", "pending"]
)
def test_group_status_rejects_non_canonical_values(status_value: str) -> None:
    """people-api.md §14.1: the `ck_groups_status_valid` CHECK constraint
    enforces the closed `active`/`archived` vocabulary — a value that is
    neither is rejected at the database layer."""
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

        group = _make_group(club, status=status_value)
        session.add(group)
        with pytest.raises(IntegrityError):
            session.commit()


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
@pytest.mark.parametrize("status_value", ["active", "ended"])
def test_group_membership_status_accepts_canonical_values(status_value: str) -> None:
    """people-api.md §15.1 (PO decision, Issue #69) closes the
    `membership_status` vocabulary ADR-0021 §2 originally left open."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        membership = _make_group_membership(
            group, club_membership, membership_status=status_value
        )
        session.add(membership)
        session.commit()  # must not raise: both are canonical values

        fetched = session.execute(
            select(GroupMembership).where(GroupMembership.id == membership.id)
        ).scalar_one()
        assert fetched.membership_status == status_value


@requires_postgres
@pytest.mark.parametrize("status_value", ["pending", "closed", "some-made-up-status"])
def test_group_membership_status_rejects_non_canonical_values(status_value: str) -> None:
    """people-api.md §15.1: the
    `ck_group_memberships_membership_status_valid` CHECK constraint
    enforces the closed `active`/`ended` vocabulary."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        membership = _make_group_membership(
            group, club_membership, membership_status=status_value
        )
        session.add(membership)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_membership_duplicate_active_in_same_group_is_rejected() -> None:
    """people-api.md §15.2 (PO decision, Issue #69): at most one active
    `GroupMembership` may exist for the same `(group_id,
    club_membership_id)` pair at a time — enforced by the
    `ck_group_memberships_no_duplicate_active` GiST exclusion constraint.
    Contrast with `test_simultaneous_membership_in_multiple_groups_is_allowed`
    below: the restriction is scoped to the *same* Group only."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        first = _make_group_membership(group, club_membership, valid_from=_utc(2024, 1, 1))
        session.add(first)
        session.commit()

        second = _make_group_membership(group, club_membership, valid_from=_utc(2024, 6, 1))
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_group_membership_sequential_active_periods_in_same_group_are_allowed() -> None:
    """A closed (`membership_status='ended'`) period never blocks a new
    active one for the same `(group_id, club_membership_id)` pair — the
    exclusion constraint is scoped to `membership_status = 'active'`
    rows only."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        club_membership = _make_club_membership(club, person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()

        first = _make_group_membership(
            group,
            club_membership,
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
            membership_status="ended",
        )
        session.add(first)
        session.commit()

        second = _make_group_membership(group, club_membership, valid_from=_utc(2024, 6, 1))
        session.add(second)
        session.commit()  # must not raise: the first period is not active

        rows = session.execute(
            select(GroupMembership).where(GroupMembership.group_id == group.id)
        ).scalars().all()
        assert len(rows) == 2


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
def test_simultaneous_membership_in_multiple_groups_is_allowed() -> None:
    """ADR-0021 §2 deliberately leaves "whether a person may belong to
    multiple groups simultaneously" as a separate, not-yet-made
    business-policy question — this persistence foundation must not
    invent a restriction the canonical documents do not require. An
    earlier revision of this model added a GiST exclusion constraint
    that *did* invent such a restriction; it has been removed (see
    app.db.groups module docstring), and this test is its replacement:
    the same club_membership_id may have two simultaneously open,
    overlapping periods in two different groups.
    """
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
        second = _make_group_membership(group_b, club_membership, valid_from=_utc(2024, 1, 1))
        session.add_all([first, second])
        session.commit()  # must not raise

        rows = session.execute(
            select(GroupMembership).where(
                GroupMembership.club_membership_id == club_membership.id
            )
        ).scalars().all()
        assert len(rows) == 2
        assert {row.valid_to for row in rows} == {None}


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
    """ADR-0022 §3/§8: Club ownership for GroupMembership is deliberately
    an application/service-layer invariant, not a database constraint —
    constructing the ORM row directly (bypassing
    app.groups.service.create_group_membership) does not validate Club
    ownership, by design. See tests/integration/test_groups_service.py
    for the actual enforcement. This test documents that the raw
    persistence layer remains structurally independent (as ADR-0022 §3
    explicitly allows) and that the mismatch is still fully detectable
    via a join, which is exactly what app.groups.service relies on.
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
        session.commit()  # not rejected at this layer, by design — see docstring above

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


# --- is_primary temporal-overlap invariant (people-api.md §16.2) ----------
#
# Review of an earlier draft of the canonical contract flagged that the
# invariant must be expressed as an *interval overlap*
# (`[valid_from, valid_to)`), not as "is_primary=true AND currently
# active" — see people-api.md §16.2 and Issue #71 §11.2 for the exact
# formula. The tests below are the DB-level proof of that formula,
# including the two directly-requested edge cases: a touching boundary
# is allowed (not an overlap), and two open-ended primaries always
# conflict.


def _two_users(person_a: Person, person_b: Person) -> tuple[User, User]:
    return _make_user(person_a), _make_user(person_b)


@requires_postgres
def test_primary_instructor_full_overlap_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group = _make_group(club)
        session.add_all([user_a, user_b, group])
        session.commit()

        first = _make_group_instructor_assignment(
            group,
            user_a,
            is_primary=True,
            valid_from=_utc(2024, 9, 1),
            valid_to=_utc(2024, 10, 1),
        )
        session.add(first)
        session.commit()

        second = _make_group_instructor_assignment(
            group,
            user_b,
            is_primary=True,
            valid_from=_utc(2024, 9, 1),
            valid_to=_utc(2024, 10, 1),
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_primary_instructor_partial_overlap_is_rejected() -> None:
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group = _make_group(club)
        session.add_all([user_a, user_b, group])
        session.commit()

        first = _make_group_instructor_assignment(
            group,
            user_a,
            is_primary=True,
            valid_from=_utc(2024, 9, 1),
            valid_to=_utc(2024, 10, 1),
        )
        session.add(first)
        session.commit()

        second = _make_group_instructor_assignment(
            group,
            user_b,
            is_primary=True,
            valid_from=_utc(2024, 9, 15),
            valid_to=_utc(2024, 10, 15),
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_primary_instructor_open_ended_overlap_is_rejected() -> None:
    """Two open-ended (`valid_to=NULL`) primaries always overlap — NULL
    is treated as an unbounded/ongoing end, not "no constraint"."""
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group = _make_group(club)
        session.add_all([user_a, user_b, group])
        session.commit()

        first = _make_group_instructor_assignment(
            group, user_a, is_primary=True, valid_from=_utc(2024, 9, 1)
        )
        session.add(first)
        session.commit()

        second = _make_group_instructor_assignment(
            group, user_b, is_primary=True, valid_from=_utc(2024, 10, 1)
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_primary_instructor_touching_boundary_is_allowed() -> None:
    """A shared boundary — one assignment's `valid_to` equals the next
    one's `valid_from` — is NOT an overlap under `[valid_from, valid_to)`
    semantics (people-api.md §16.2's explicit example)."""
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group = _make_group(club)
        session.add_all([user_a, user_b, group])
        session.commit()

        first = _make_group_instructor_assignment(
            group,
            user_a,
            is_primary=True,
            valid_from=_utc(2024, 9, 1),
            valid_to=_utc(2024, 10, 1),
        )
        session.add(first)
        session.commit()

        second = _make_group_instructor_assignment(
            group, user_b, is_primary=True, valid_from=_utc(2024, 10, 1)
        )
        session.add(second)
        session.commit()  # must not raise: touching, not overlapping

        rows = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.group_id == group.id
            )
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_primary_instructor_sequential_historical_periods_are_allowed() -> None:
    """Two fully closed, non-overlapping historical primary periods for
    the same Group are allowed (not just the touching-boundary case)."""
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group = _make_group(club)
        session.add_all([user_a, user_b, group])
        session.commit()

        first = _make_group_instructor_assignment(
            group,
            user_a,
            is_primary=True,
            valid_from=_utc(2024, 1, 1),
            valid_to=_utc(2024, 6, 1),
        )
        session.add(first)
        session.commit()

        second = _make_group_instructor_assignment(
            group,
            user_b,
            is_primary=True,
            valid_from=_utc(2024, 7, 1),
            valid_to=_utc(2024, 9, 1),
        )
        session.add(second)
        session.commit()  # must not raise: a gap, not an overlap

        rows = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.group_id == group.id
            )
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_non_primary_overlapping_assignments_are_unrestricted() -> None:
    """The exclusion constraint is scoped to `is_primary = true` rows
    only — overlapping non-primary assignments for the same Group are
    never restricted, including many simultaneous non-primary
    instructors."""
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group = _make_group(club)
        session.add_all([user_a, user_b, group])
        session.commit()

        first = _make_group_instructor_assignment(
            group, user_a, is_primary=False, valid_from=_utc(2024, 1, 1)
        )
        second = _make_group_instructor_assignment(
            group, user_b, is_primary=False, valid_from=_utc(2024, 1, 1)
        )
        session.add_all([first, second])
        session.commit()  # must not raise: is_primary=false is unrestricted

        rows = session.execute(
            select(GroupInstructorAssignment).where(
                GroupInstructorAssignment.group_id == group.id
            )
        ).scalars().all()
        assert len(rows) == 2


@requires_postgres
def test_primary_instructor_overlap_across_different_groups_is_allowed() -> None:
    """The invariant is scoped per-`group_id` — two different Groups may
    each have their own, independently overlapping-in-time primary
    instructor."""
    with session_scope() as session:
        club = _make_club()
        person_a = _make_person(first_name="Timofey")
        person_b = _make_person(first_name="Anastasia")
        session.add_all([club, person_a, person_b])
        session.commit()
        user_a, user_b = _two_users(person_a, person_b)
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([user_a, user_b, group_a, group_b])
        session.commit()

        first = _make_group_instructor_assignment(
            group_a, user_a, is_primary=True, valid_from=_utc(2024, 1, 1)
        )
        second = _make_group_instructor_assignment(
            group_b, user_b, is_primary=True, valid_from=_utc(2024, 1, 1)
        )
        session.add_all([first, second])
        session.commit()  # must not raise: different groups


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
    """Same documented ADR-0022 §3/§8 boundary as the GroupMembership
    cross-Club test above: constructing the ORM row directly (bypassing
    app.groups.service.create_group_instructor_assignment) does not
    validate that the assigned User has an active ClubMembership in the
    Group's Club — by design, at this layer. See
    tests/integration/test_groups_service.py for the actual enforcement.
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
        session.commit()  # not rejected at this layer, by design — see docstring above

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
    # Pinned to the absolute pre-Group revision rather than a relative
    # "-1": a relative downgrade silently targets the wrong migration
    # once a later migration (e.g. Issue #48's EventStaffAssignment)
    # becomes the new head.
    downgrade = run_alembic("downgrade", "a86214bd3bc4", database_url=database_url)
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

    upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr

    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        session.add(_make_group(club))
        session.commit()  # must not raise: the re-created table works
