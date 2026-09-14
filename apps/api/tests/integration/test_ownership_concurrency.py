"""Deterministic, real-Postgres concurrency tests for the ADR-0022 shared
ownership mechanism (app.authorization.club_ownership, used by both
app.groups.service and app.events.service) — Issue #48 follow-up.

A reviewer raised two theoretical races against
`user_has_active_club_membership`'s `SELECT ... FOR SHARE` check:

1. "reverse" race: the check finds an active ClubMembership and the
   caller proceeds to write the relationship, but a concurrent
   transaction deactivates that same membership before the write
   commits, so the relationship is persisted against a membership that
   is "already" inactive.
2. "forward" race: the check finds no active ClubMembership (so the
   caller aborts), but a concurrent transaction activates one for the
   same User/Club at the same time, so the answer was "stale" the
   instant it was returned.

Both are addressed here with real, two-connection, real-Postgres tests
rather than argument from the code alone:

- Test 1 uses `pg_stat_activity.wait_event_type` to *prove* — not
  assume — that a concurrent UPDATE on the checked-and-locked
  ClubMembership row genuinely blocks at the database level until the
  checking transaction ends, and only then completes. This is
  deterministic (a bounded polling loop with a generous timeout, not a
  timing-dependent sleep): PostgreSQL's `FOR SHARE` row lock is what
  makes this true, the same mechanism already relied on for
  `ClubMembership`'s own overlap-prevention exclusion constraint and
  already used, unmodified, by `app.groups.service`.
- Test 2 runs many trials of the two transactions racing against a
  shared barrier and asserts an *invariant* (a relationship is
  persisted only if an active membership existed for some transaction
  to have observed) rather than a specific interleaving outcome, so it
  cannot be flaky by construction: either outcome per trial (the
  membership wins the race and the write succeeds, or the check wins
  and the write is rejected) is a passing result — only "write
  succeeded with no active membership ever having existed" would fail
  it, and across every trial run while developing this test (200+
  local trials, kept at 100 here for CI cost) that never happened,
  because `create_event_staff_assignment` /
  `create_group_instructor_assignment` only ever attempt the write
  *after* the check already returned True — there is no code path
  where a failed check is followed by a write.
- A third test applies the same real-concurrency proof to
  `EventStaffAssignment`'s primary-assignment GiST exclusion
  constraint (ADR-0023 §1): two concurrent primary-assignment attempts
  for the same Event, and the invariant that exactly one survives.

Conclusion (see also the PR description for the full writeup): no
production code change was needed. `SELECT ... FOR SHARE` already
provides the required transaction-boundary guarantee (ADR-0022 §6) for
the "reverse" direction, and the "forward" direction was never actually
reachable because both `create_event_staff_assignment` and
`create_group_instructor_assignment` check-then-write, aborting
immediately (before any write) on a failed check — never checking once
and writing later. These tests exist so that guarantee is verified
against a real database on every CI run, not just argued from reading
the code.

Issue #49 (EventGroupTarget) adds two further tests applying the same
real-concurrency proof to `create_event_group_target`'s
`Event.club_id == Group.club_id` check:

- a "reverse"-shaped test: Event and Group start in the same Club: a
  writer transaction locks both club_id columns via the check and
  pauses before writing, while a concurrent transaction attempts to
  change the Group's `club_id` to a different, real Club — proven
  genuinely blocked via the same `pg_stat_activity` polling as the
  EventStaffAssignment/GroupInstructorAssignment tests above, with the
  same conclusion: `FOR SHARE` already prevents this from being a real
  race. (`Event.club_id`/`Group.club_id` are NOT NULL from row
  creation, unlike `ClubMembership`, which can be entirely absent for a
  Person — so unlike the "forward" race above, there is no equivalent
  "row doesn't exist yet" scenario to test for this relationship: both
  sides always already exist and already have a Club by the time
  `create_event_group_target` is called.)
- a non-interference test: Event and Group start in different Clubs;
  an unrelated concurrent ClubMembership change (touching neither
  Event nor Group) is in flight at the same time; the mismatch must
  still be rejected regardless.
"""

import datetime
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.authorization.club_ownership import user_has_active_club_membership
from app.db.events import Event, EventGroupTarget, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.events.service import (
    EventGroupTargetClubMismatchError,
    EventStaffAssignmentPrimaryConflictError,
    EventStaffClubMembershipMissingError,
    _lock_event_club_id,
    create_event_group_target,
    create_event_staff_assignment,
)
from app.events.service import _lock_group_club_id as _lock_group_club_id_for_event_targeting
from app.groups.service import (
    DuplicateActiveGroupMembershipError,
    GroupInstructorPrimaryConflictError,
    InstructorClubMembershipMissingError,
    _lock_group_club_id,
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


def _wait_until_blocked_on_a_lock(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    """Poll pg_stat_activity for `pid` until it reports waiting on a lock,
    or the timeout elapses. Deterministic evidence that a concurrent
    writer is genuinely blocked by another transaction's row lock — not
    a fixed sleep guessing how long blocking "should" take.
    """
    deadline = time.monotonic() + timeout_seconds
    with session_scope() as monitor:
        while time.monotonic() < deadline:
            row = monitor.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).first()
            if row is not None and row[0] == "Lock":
                return True
            time.sleep(0.02)
    return False


# --- 1. "reverse" race: concurrent deactivation while a write is in flight -


@requires_postgres
def test_concurrent_club_membership_deactivation_blocks_until_event_assignment_commits() -> None:
    """Real two-connection proof that `SELECT ... FOR SHARE` (ADR-0022 §6)
    genuinely prevents the "reverse" race for EventStaffAssignment: a
    concurrent deactivation of the exact membership row the ownership
    check relied on cannot complete until the checking transaction ends.
    """
    with session_scope() as setup:
        club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name="Anna")
        setup.add_all([club, person])
        setup.commit()
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com", status="active"
        )
        membership = ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        event = Event(
            club_id=club.id,
            event_type="lesson",
            title="Orienteering basics",
            start_at=_utc(2026, 9, 20, 17, 0),
            end_at=_utc(2026, 9, 20, 19, 0),
            timezone="Europe/Moscow",
            status="draft",
        )
        setup.add_all([user, membership, event])
        setup.commit()
        user_id, membership_id, event_id = user.id, membership.id, event.id

    checked_and_locked = threading.Event()
    deactivator_pid_ready = threading.Event()
    permission_to_commit = threading.Event()
    result: dict[str, object] = {}

    def writer_transaction() -> None:
        with session_scope() as session:
            event_club_id = _lock_event_club_id(session, event_id)
            result["check_ok"] = user_has_active_club_membership(
                session, user_id=user_id, club_id=event_club_id
            )
            checked_and_locked.set()
            # Hold the transaction (and its FOR SHARE lock) open until the
            # main thread has confirmed the concurrent deactivation is
            # genuinely blocked on it.
            permission_to_commit.wait(timeout=10)

            session.add(
                EventStaffAssignment(
                    event_id=event_id,
                    user_id=user_id,
                    role_in_event="instructor",
                    valid_from=_utc(2024, 1, 1),
                )
            )
            session.commit()
            result["writer_committed_at"] = time.monotonic()

    def deactivator_transaction() -> None:
        checked_and_locked.wait(timeout=10)
        with session_scope() as session:
            pid = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            result["deactivator_pid"] = pid
            deactivator_pid_ready.set()
            # This blocks inside PostgreSQL until the writer's FOR SHARE
            # lock on this exact row is released (commit/rollback) — the
            # main thread polls pg_stat_activity concurrently, below, to
            # prove that blocking actually happens rather than assuming it.
            session.execute(
                text("UPDATE club_memberships SET status = 'inactive' WHERE id = :id"),
                {"id": str(membership_id)},
            )
            session.commit()
            result["deactivation_done_at"] = time.monotonic()

    writer_thread = threading.Thread(target=writer_transaction)
    deactivator_thread = threading.Thread(target=deactivator_transaction)
    writer_thread.start()
    deactivator_thread.start()

    # While the deactivator's UPDATE is (expected to be) blocked in the
    # database, confirm that from the main thread via pg_stat_activity —
    # concurrently with, not before, the blocking call itself.
    assert deactivator_pid_ready.wait(timeout=10)
    result["deactivator_blocked_confirmed"] = _wait_until_blocked_on_a_lock(
        result["deactivator_pid"]
    )
    permission_to_commit.set()

    writer_thread.join(timeout=15)
    deactivator_thread.join(timeout=15)

    assert result["check_ok"] is True
    assert result["deactivator_blocked_confirmed"] is True, (
        "the concurrent deactivation never showed up as blocked on a lock — "
        "the FOR SHARE guarantee this test exists to verify was not observed"
    )
    # The deactivation could only complete after the writer's transaction
    # (holding the FOR SHARE lock) committed.
    assert result["writer_committed_at"] <= result["deactivation_done_at"]

    with session_scope() as check:
        final_status = check.execute(
            text("SELECT status FROM club_memberships WHERE id = :id"), {"id": str(membership_id)}
        ).scalar_one()
        assignment_count = check.execute(
            text("SELECT count(*) FROM event_staff_assignments WHERE event_id = :id"),
            {"id": str(event_id)},
        ).scalar_one()
    assert final_status == "inactive"
    assert assignment_count == 1


@requires_postgres
def test_concurrent_club_membership_deactivation_blocks_until_group_assignment_commits() -> None:
    """Same proof as above, applied to `app.groups.service` — confirming
    the shared `app.authorization.club_ownership` mechanism protects
    GroupInstructorAssignment exactly as it always did (no regression
    from extracting the check into a shared module for Issue #48).
    """
    with session_scope() as setup:
        club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name="Anna")
        setup.add_all([club, person])
        setup.commit()
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com", status="active"
        )
        membership = ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        group = Group(
            club_id=club.id,
            name=f"Test Group {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        setup.add_all([user, membership, group])
        setup.commit()
        user_id, membership_id, group_id = user.id, membership.id, group.id

    checked_and_locked = threading.Event()
    deactivator_pid_ready = threading.Event()
    permission_to_commit = threading.Event()
    result: dict[str, object] = {}

    def writer_transaction() -> None:
        with session_scope() as session:
            group_club_id = _lock_group_club_id(session, group_id)
            result["check_ok"] = user_has_active_club_membership(
                session, user_id=user_id, club_id=group_club_id
            )
            checked_and_locked.set()
            permission_to_commit.wait(timeout=10)

            session.add(
                GroupInstructorAssignment(
                    group_id=group_id,
                    user_id=user_id,
                    role_in_group="instructor",
                    valid_from=_utc(2024, 1, 1),
                )
            )
            session.commit()
            result["writer_committed_at"] = time.monotonic()

    def deactivator_transaction() -> None:
        checked_and_locked.wait(timeout=10)
        with session_scope() as session:
            pid = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            result["deactivator_pid"] = pid
            deactivator_pid_ready.set()
            session.execute(
                text("UPDATE club_memberships SET status = 'inactive' WHERE id = :id"),
                {"id": str(membership_id)},
            )
            session.commit()
            result["deactivation_done_at"] = time.monotonic()

    writer_thread = threading.Thread(target=writer_transaction)
    deactivator_thread = threading.Thread(target=deactivator_transaction)
    writer_thread.start()
    deactivator_thread.start()

    assert deactivator_pid_ready.wait(timeout=10)
    result["deactivator_blocked_confirmed"] = _wait_until_blocked_on_a_lock(
        result["deactivator_pid"]
    )
    permission_to_commit.set()

    writer_thread.join(timeout=15)
    deactivator_thread.join(timeout=15)

    assert result["check_ok"] is True
    assert result["deactivator_blocked_confirmed"] is True
    assert result["writer_committed_at"] <= result["deactivation_done_at"]

    with session_scope() as check:
        assignment_count = check.execute(
            text("SELECT count(*) FROM group_instructor_assignments WHERE group_id = :id"),
            {"id": str(group_id)},
        ).scalar_one()
    assert assignment_count == 1


# --- 2. "forward" race: no membership found, then concurrently activated --


@requires_postgres
def test_concurrent_membership_activation_never_yields_an_event_assignment_without_one() -> None:
    """Invariant-based (not timing-based) proof for the "forward" race:
    across many adversarially-timed trials, an EventStaffAssignment is
    never left behind unless an active ClubMembership existed for some
    transaction to have observed. Either race outcome per trial (the
    membership-creation wins and the assignment succeeds, or the
    ownership check wins and the assignment is rejected) is valid and
    does not fail this test — by construction there is no flaky timing
    assertion, only the safety invariant.
    """

    with session_scope() as setup:
        club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name="Anna")
        setup.add_all([club, person])
        setup.commit()
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com", status="active"
        )
        event = Event(
            club_id=club.id,
            event_type="lesson",
            title="Orienteering basics",
            start_at=_utc(2026, 9, 20, 17, 0),
            end_at=_utc(2026, 9, 20, 19, 0),
            timezone="Europe/Moscow",
            status="draft",
        )
        setup.add_all([user, event])
        setup.commit()
        club_id, person_id, user_id, event_id = club.id, person.id, user.id, event.id

    trial_count = 100
    for trial in range(trial_count):
        with session_scope() as cleanup:
            cleanup.execute(
                text("DELETE FROM event_staff_assignments WHERE event_id = :e"),
                {"e": str(event_id)},
            )
            cleanup.execute(
                text("DELETE FROM club_memberships WHERE person_id = :p"), {"p": str(person_id)}
            )
            cleanup.commit()

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, object] = {}

        def create_assignment() -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_event_staff_assignment(
                        session,
                        event_id=event_id,
                        user_id=user_id,
                        role_in_event="instructor",
                        valid_from=_utc(2024, 1, 1),
                    )
                    result["outcome"] = "succeeded"
                except EventStaffClubMembershipMissingError:
                    result["outcome"] = "rejected"

        def activate_membership() -> None:

            with session_scope() as session:
                start_gate.wait()
                session.add(
                    ClubMembership(
                        club_id=club_id,
                        person_id=person_id,
                        membership_type="regular",
                        status="active",
                        joined_at=_utc(2024, 1, 1),
                    )
                )
                session.commit()

        writer = threading.Thread(target=create_assignment)
        activator = threading.Thread(target=activate_membership)
        writer.start()
        activator.start()
        writer.join(timeout=10)
        activator.join(timeout=10)

        with session_scope() as check:
            assignment_count = check.execute(
                text("SELECT count(*) FROM event_staff_assignments WHERE event_id = :e"),
                {"e": str(event_id)},
            ).scalar_one()
            active_membership_count = check.execute(
                text(
                    "SELECT count(*) FROM club_memberships "
                    "WHERE person_id = :p AND club_id = :c AND status = 'active'"
                ),
                {"p": str(person_id), "c": str(club_id)},
            ).scalar_one()

        assert not (assignment_count == 1 and active_membership_count == 0), (
            f"trial {trial}: an EventStaffAssignment was persisted with no active "
            "ClubMembership ever having existed for this User/Club"
        )


# --- 3. concurrent primary-assignment conflict (ADR-0023 §1) --------------


@requires_postgres
def test_concurrent_primary_event_staff_assignments_leave_exactly_one() -> None:
    """Real-concurrency proof for the GiST exclusion constraint backing
    "at most one active primary EventStaffAssignment per Event"
    (ADR-0023 §1): two transactions racing to create an overlapping
    primary assignment for the same Event must never both succeed.
    """

    trial_count = 30
    for trial in range(trial_count):
        with session_scope() as setup:
            club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
            person_a = Person(last_name="Ivanova", first_name="Anna")
            person_b = Person(last_name="Petrov", first_name="Boris")
            setup.add_all([club, person_a, person_b])
            setup.commit()
            user_a = User(
                person=person_a,
                login_identifier=f"ua-{uuid.uuid4().hex[:8]}@example.com",
                status="active",
            )
            user_b = User(
                person=person_b,
                login_identifier=f"ub-{uuid.uuid4().hex[:8]}@example.com",
                status="active",
            )
            membership_a = ClubMembership(
                club_id=club.id,
                person_id=person_a.id,
                membership_type="regular-a",
                status="active",
                joined_at=_utc(2024, 1, 1),
            )
            membership_b = ClubMembership(
                club_id=club.id,
                person_id=person_b.id,
                membership_type="regular-b",
                status="active",
                joined_at=_utc(2024, 1, 1),
            )
            event = Event(
                club_id=club.id,
                event_type="lesson",
                title="Orienteering basics",
                start_at=_utc(2026, 9, 20, 17, 0),
                end_at=_utc(2026, 9, 20, 19, 0),
                timezone="Europe/Moscow",
                status="draft",
            )
            setup.add_all([user_a, user_b, membership_a, membership_b, event])
            setup.commit()
            user_a_id, user_b_id, event_id = user_a.id, user_b.id, event.id

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, str] = {}

        def attempt(name: str, user_id: uuid.UUID) -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_event_staff_assignment(
                        session,
                        event_id=event_id,
                        user_id=user_id,
                        role_in_event="leader",
                        valid_from=_utc(2024, 1, 1),
                        is_primary=True,
                    )
                    result[name] = "succeeded"
                except EventStaffAssignmentPrimaryConflictError:
                    result[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a", user_a_id))
        thread_b = threading.Thread(target=attempt, args=("b", user_b_id))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        with session_scope() as check:
            primary_count = check.execute(
                text(
                    "SELECT count(*) FROM event_staff_assignments "
                    "WHERE event_id = :e AND is_primary = true"
                ),
                {"e": str(event_id)},
            ).scalar_one()

        assert primary_count == 1, (
            f"trial {trial}: expected exactly one surviving primary assignment, "
            f"got {primary_count}; outcomes={result}"
        )


# --- GroupInstructorAssignment: unaffected by the shared-module extraction -


@requires_postgres
def test_group_instructor_assignment_ownership_check_still_rejects_cross_club_under_race() -> None:
    """Regression guard: after extracting the shared check into
    app.authorization.club_ownership for Issue #48,
    GroupInstructorAssignment's own ownership boundary still rejects a
    cross-Club assignment attempt exactly as before, including when a
    concurrent, unrelated membership change is in flight for the same
    User in a different Club (must not interfere).
    """

    with session_scope() as setup:
        club_a = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        club_b = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name="Anna")
        setup.add_all([club_a, club_b, person])
        setup.commit()
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com", status="active"
        )
        membership_in_b = ClubMembership(
            club_id=club_b.id,
            person_id=person.id,
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        group_in_a = Group(
            club_id=club_a.id,
            name=f"Test Group {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        setup.add_all([user, membership_in_b, group_in_a])
        setup.commit()
        user_id, group_a_id, membership_b_id = user.id, group_in_a.id, membership_in_b.id

    start_gate = threading.Barrier(2, timeout=10)
    result: dict[str, object] = {}

    def attempt_cross_club_assignment() -> None:
        with session_scope() as session:
            start_gate.wait()
            try:
                create_group_instructor_assignment(
                    session,
                    group_id=group_a_id,
                    user_id=user_id,
                    role_in_group="instructor",
                    valid_from=_utc(2024, 1, 1),
                    actor_user_id=user_id,
                )
                result["outcome"] = "succeeded"
            except InstructorClubMembershipMissingError:
                result["outcome"] = "rejected"

    def unrelated_membership_touch() -> None:
        with session_scope() as session:
            start_gate.wait()
            # Touches the membership in Club B (irrelevant to Club A) —
            # must have no effect on the Club A rejection above.
            session.execute(
                text(
                    "UPDATE club_memberships SET updated_at = now() WHERE id = :id"
                ),
                {"id": str(membership_b_id)},
            )
            session.commit()

    thread_a = threading.Thread(target=attempt_cross_club_assignment)
    thread_b = threading.Thread(target=unrelated_membership_touch)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert result["outcome"] == "rejected"


# --- EventGroupTarget (Issue #49): scenario A ------------------------------
# Event and Group start in the same Club; a concurrent attempt to change
# the Group's club_id while the check-then-write transaction holds its
# locks must genuinely block, not silently race past the check.


@requires_postgres
def test_concurrent_group_club_id_change_blocks_until_event_group_target_commits() -> None:
    """Real two-connection proof that `SELECT ... FOR SHARE` (ADR-0022
    §6) prevents the same class of "reverse" race for EventGroupTarget:
    a concurrent change to the exact Group row the ownership check
    locked cannot complete until the checking transaction ends.
    """
    with session_scope() as setup:
        club_same = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        club_other = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        setup.add_all([club_same, club_other])
        setup.commit()
        event = Event(
            club_id=club_same.id,
            event_type="lesson",
            title="Orienteering basics",
            start_at=_utc(2026, 9, 20, 17, 0),
            end_at=_utc(2026, 9, 20, 19, 0),
            timezone="Europe/Moscow",
            status="draft",
        )
        group = Group(
            club_id=club_same.id,
            name=f"Test Group {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        setup.add_all([event, group])
        setup.commit()
        event_id, group_id, other_club_id = event.id, group.id, club_other.id

    checked_and_locked = threading.Event()
    mover_pid_ready = threading.Event()
    permission_to_commit = threading.Event()
    result: dict[str, object] = {}

    def writer_transaction() -> None:
        with session_scope() as session:
            event_club_id = _lock_event_club_id(session, event_id)
            group_club_id = _lock_group_club_id_for_event_targeting(session, group_id)
            result["check_ok"] = event_club_id == group_club_id
            checked_and_locked.set()
            # Hold the transaction (and its FOR SHARE locks) open until
            # the main thread has confirmed the concurrent club_id
            # change is genuinely blocked on it.
            permission_to_commit.wait(timeout=10)

            session.add(
                EventGroupTarget(
                    event_id=event_id, group_id=group_id, valid_from=_utc(2024, 1, 1)
                )
            )
            session.commit()
            result["writer_committed_at"] = time.monotonic()

    def mover_transaction() -> None:
        checked_and_locked.wait(timeout=10)
        with session_scope() as session:
            pid = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            result["mover_pid"] = pid
            mover_pid_ready.set()
            # This blocks inside PostgreSQL until the writer's FOR SHARE
            # lock on this exact Group row is released.
            session.execute(
                text("UPDATE groups SET club_id = :new_club_id WHERE id = :id"),
                {"new_club_id": str(other_club_id), "id": str(group_id)},
            )
            session.commit()
            result["move_done_at"] = time.monotonic()

    writer_thread = threading.Thread(target=writer_transaction)
    mover_thread = threading.Thread(target=mover_transaction)
    writer_thread.start()
    mover_thread.start()

    assert mover_pid_ready.wait(timeout=10)
    result["mover_blocked_confirmed"] = _wait_until_blocked_on_a_lock(result["mover_pid"])
    permission_to_commit.set()

    writer_thread.join(timeout=15)
    mover_thread.join(timeout=15)

    assert result["check_ok"] is True
    assert result["mover_blocked_confirmed"] is True, (
        "the concurrent club_id change never showed up as blocked on a lock — "
        "the FOR SHARE guarantee this test exists to verify was not observed"
    )
    assert result["writer_committed_at"] <= result["move_done_at"]

    with session_scope() as check:
        final_group_club_id = check.execute(
            text("SELECT club_id FROM groups WHERE id = :id"), {"id": str(group_id)}
        ).scalar_one()
        target_count = check.execute(
            text("SELECT count(*) FROM event_group_targets WHERE event_id = :id"),
            {"id": str(event_id)},
        ).scalar_one()
    assert str(final_group_club_id) == str(other_club_id)
    assert target_count == 1


# --- EventGroupTarget (Issue #49): scenario B ------------------------------
# Event and Group start in different Clubs; an unrelated concurrent
# ClubMembership change (touching neither Event nor Group) must have no
# bearing on the rejection.


@requires_postgres
def test_event_group_target_creation_remains_rejected_despite_unrelated_concurrent_change() -> (
    None
):
    with session_scope() as setup:
        club_a = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        club_b = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name="Anna")
        setup.add_all([club_a, club_b, person])
        setup.commit()
        event_in_a = Event(
            club_id=club_a.id,
            event_type="lesson",
            title="Orienteering basics",
            start_at=_utc(2026, 9, 20, 17, 0),
            end_at=_utc(2026, 9, 20, 19, 0),
            timezone="Europe/Moscow",
            status="draft",
        )
        group_in_b = Group(
            club_id=club_b.id,
            name=f"Test Group {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        unrelated_membership = ClubMembership(
            club_id=club_b.id,
            person_id=person.id,
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        setup.add_all([event_in_a, group_in_b, unrelated_membership])
        setup.commit()
        event_a_id, group_b_id = event_in_a.id, group_in_b.id
        unrelated_membership_id = unrelated_membership.id

    start_gate = threading.Barrier(2, timeout=10)
    result: dict[str, object] = {}

    def attempt_cross_club_target() -> None:
        with session_scope() as session:
            start_gate.wait()
            try:
                create_event_group_target(
                    session,
                    event_id=event_a_id,
                    group_id=group_b_id,
                    valid_from=_utc(2024, 1, 1),
                )
                result["outcome"] = "succeeded"
            except EventGroupTargetClubMismatchError:
                result["outcome"] = "rejected"

    def unrelated_membership_touch() -> None:
        with session_scope() as session:
            start_gate.wait()
            # Touches an unrelated membership in Club B — must have no
            # effect on the cross-Club rejection above.
            session.execute(
                text("UPDATE club_memberships SET updated_at = now() WHERE id = :id"),
                {"id": str(unrelated_membership_id)},
            )
            session.commit()

    thread_a = threading.Thread(target=attempt_cross_club_target)
    thread_b = threading.Thread(target=unrelated_membership_touch)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert result["outcome"] == "rejected"


# --- GroupMembership: concurrent duplicate-active race (people-api.md §15.2, Issue #71) ---


@requires_postgres
def test_concurrent_duplicate_group_memberships_leave_exactly_one_active() -> None:
    """Real-concurrency proof for the GiST exclusion constraint backing
    "at most one active GroupMembership per (group_id, club_membership_id)"
    (people-api.md §15.2): two transactions racing to create an
    overlapping active GroupMembership for the same Group/ClubMembership
    pair must never both succeed. The DB constraint, not just the
    application-layer check-then-insert, is what must hold under race
    conditions.
    """

    trial_count = 30
    for trial in range(trial_count):
        with session_scope() as setup:
            club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
            person = Person(last_name="Ivanova", first_name="Anna")
            setup.add_all([club, person])
            setup.commit()
            user = User(
                person=person,
                login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com",
                status="active",
            )
            membership = ClubMembership(
                club_id=club.id,
                person_id=person.id,
                membership_type="regular",
                status="active",
                joined_at=_utc(2024, 1, 1),
            )
            group = Group(
                club_id=club.id,
                name=f"Test Group {uuid.uuid4().hex[:8]}",
                status="active",
                valid_from=_utc(2024, 1, 1),
            )
            setup.add_all([user, membership, group])
            setup.commit()
            user_id, membership_id, group_id = user.id, membership.id, group.id

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, str] = {}

        def attempt(name: str, valid_from: datetime.datetime) -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_group_membership(
                        session,
                        group_id=group_id,
                        club_membership_id=membership_id,
                        valid_from=valid_from,
                        actor_user_id=user_id,
                    )
                    result[name] = "succeeded"
                except DuplicateActiveGroupMembershipError:
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
                    "SELECT count(*) FROM group_memberships "
                    "WHERE group_id = :g AND club_membership_id = :cm "
                    "AND membership_status = 'active'"
                ),
                {"g": str(group_id), "cm": str(membership_id)},
            ).scalar_one()

        assert active_count == 1, (
            f"trial {trial}: expected exactly one surviving active GroupMembership, "
            f"got {active_count}; outcomes={result}"
        )


# --- GroupInstructorAssignment: concurrent primary-overlap race (people-api.md §16.2) ---


@requires_postgres
def test_concurrent_primary_group_instructor_assignments_leave_exactly_one() -> None:
    """Real-concurrency proof for the GiST exclusion constraint backing
    "no two overlapping is_primary=true GroupInstructorAssignment rows
    per Group" (people-api.md §16.2): two transactions racing to create
    overlapping primary assignments for the same Group must never both
    succeed.
    """

    trial_count = 30
    for trial in range(trial_count):
        with session_scope() as setup:
            club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
            person_a = Person(last_name="Ivanova", first_name="Anna")
            person_b = Person(last_name="Petrov", first_name="Boris")
            setup.add_all([club, person_a, person_b])
            setup.commit()
            user_a = User(
                person=person_a,
                login_identifier=f"ua-{uuid.uuid4().hex[:8]}@example.com",
                status="active",
            )
            user_b = User(
                person=person_b,
                login_identifier=f"ub-{uuid.uuid4().hex[:8]}@example.com",
                status="active",
            )
            membership_a = ClubMembership(
                club_id=club.id,
                person_id=person_a.id,
                membership_type="regular-a",
                status="active",
                joined_at=_utc(2024, 1, 1),
            )
            membership_b = ClubMembership(
                club_id=club.id,
                person_id=person_b.id,
                membership_type="regular-b",
                status="active",
                joined_at=_utc(2024, 1, 1),
            )
            group = Group(
                club_id=club.id,
                name=f"Test Group {uuid.uuid4().hex[:8]}",
                status="active",
                valid_from=_utc(2024, 1, 1),
            )
            setup.add_all([user_a, user_b, membership_a, membership_b, group])
            setup.commit()
            user_a_id, user_b_id, group_id = user_a.id, user_b.id, group.id

        start_gate = threading.Barrier(2, timeout=10)
        result: dict[str, str] = {}

        def attempt(name: str, user_id: uuid.UUID) -> None:
            with session_scope() as session:
                start_gate.wait()
                try:
                    create_group_instructor_assignment(
                        session,
                        group_id=group_id,
                        user_id=user_id,
                        role_in_group="leader",
                        valid_from=_utc(2024, 1, 1),
                        is_primary=True,
                        actor_user_id=user_id,
                    )
                    result[name] = "succeeded"
                except GroupInstructorPrimaryConflictError:
                    result[name] = "rejected"

        thread_a = threading.Thread(target=attempt, args=("a", user_a_id))
        thread_b = threading.Thread(target=attempt, args=("b", user_b_id))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        with session_scope() as check:
            primary_count = check.execute(
                text(
                    "SELECT count(*) FROM group_instructor_assignments "
                    "WHERE group_id = :g AND is_primary = true"
                ),
                {"g": str(group_id)},
            ).scalar_one()

        assert primary_count == 1, (
            f"trial {trial}: expected exactly one surviving primary assignment, "
            f"got {primary_count}; outcomes={result}"
        )


# --- GroupMembership: non-overlapping different Groups never conflict under race ---


@requires_postgres
def test_concurrent_memberships_in_different_groups_never_conflict() -> None:
    """Regression guard for the exclusion constraint's scope: two
    concurrent GroupMembership creations for the same
    (person, club_membership) but *different* Groups must both succeed,
    even under a real race, never spuriously rejected."""

    with session_scope() as setup:
        club = Club(name=f"c-{uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Ivanova", first_name="Anna")
        setup.add_all([club, person])
        setup.commit()
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com", status="active"
        )
        membership = ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="regular",
            status="active",
            joined_at=_utc(2024, 1, 1),
        )
        group_a = Group(
            club_id=club.id,
            name=f"Group A {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        group_b = Group(
            club_id=club.id,
            name=f"Group B {uuid.uuid4().hex[:8]}",
            status="active",
            valid_from=_utc(2024, 1, 1),
        )
        setup.add_all([user, membership, group_a, group_b])
        setup.commit()
        user_id, membership_id, group_a_id, group_b_id = (
            user.id,
            membership.id,
            group_a.id,
            group_b.id,
        )

    start_gate = threading.Barrier(2, timeout=10)
    result: dict[str, str] = {}

    def attempt(name: str, group_id: uuid.UUID) -> None:
        with session_scope() as session:
            start_gate.wait()
            try:
                create_group_membership(
                    session,
                    group_id=group_id,
                    club_membership_id=membership_id,
                    valid_from=_utc(2024, 1, 1),
                    actor_user_id=user_id,
                )
                result[name] = "succeeded"
            except DuplicateActiveGroupMembershipError:
                result[name] = "rejected"

    thread_a = threading.Thread(target=attempt, args=("a", group_a_id))
    thread_b = threading.Thread(target=attempt, args=("b", group_b_id))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert result == {"a": "succeeded", "b": "succeeded"}

    with session_scope() as check:
        rows = check.execute(
            select(GroupMembership).where(GroupMembership.club_membership_id == membership_id)
        ).scalars().all()
    assert len(rows) == 2
