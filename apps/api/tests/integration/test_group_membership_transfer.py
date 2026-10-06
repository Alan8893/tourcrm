"""Atomic GroupMembership Transfer (Issue #286, people-api.md §15.3).

`POST /api/v1/group-memberships/{id}/transfer` ends the active source
membership and creates the participant's membership in the target Group
in ONE transaction, for an Administrator holding the existing
`group.manage` permission with an applicable scope. Every Administrator
test runs through the REAL, migration-seeded canonical `admin` role.

Covers the PO decision's cases: happy path (history kept, same Person and
ClubMembership), same Group, cross-Club, archived target, ended source,
nonexistent source/target, Member/Instructor/Guardian and wrong-scope
Administrator, atomic rollback when the second half fails, an existing
active membership in the target Group (the §15.2 conflict), other active
memberships left untouched, concurrent Transfers of one membership, and
the audit records.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration/test_group_membership_transfer.py -v
"""

import datetime
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal
from app.audit import service as audit_service
from app.db.audit import AuditLog
from app.db.authorization import Role, UserRoleAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.groups import service as group_service
from app.main import app

from .conftest import requires_postgres

_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories ----------------------------------------------------------------


def _club(session: Session) -> Club:
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _person(session: Session) -> Person:
    person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _user(session: Session, person: Person | None = None) -> User:
    user = User(
        person=person or _person(session),
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _club_membership(
    session: Session, club: Club, person: Person, *, status: str = "active"
) -> ClubMembership:
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="student",
        status=status,
        joined_at=_LONG_AGO,
    )
    session.add(membership)
    session.flush()
    return membership


def _group(session: Session, club: Club, *, status: str = "active") -> Group:
    group = Group(
        club_id=club.id,
        name=f"Group {uuid.uuid4().hex[:8]}",
        status=status,
        valid_from=_LONG_AGO,
    )
    session.add(group)
    session.flush()
    return group


def _group_membership(
    session: Session,
    group: Group,
    club_membership: ClubMembership,
    *,
    membership_status: str = "active",
    valid_to: datetime.datetime | None = None,
) -> GroupMembership:
    membership = GroupMembership(
        group_id=group.id,
        club_membership_id=club_membership.id,
        valid_from=_LONG_AGO,
        valid_to=valid_to,
        membership_status=membership_status,
    )
    session.add(membership)
    session.flush()
    return membership


def _assign_role(session: Session, user: User, role_code: str, club: Club | None) -> None:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=club.id if club is not None else None
        )
    )
    session.flush()


def _admin(session: Session, club: Club | None) -> User:
    """A real Administrator: the canonical, migration-seeded `admin` role
    (`group.manage` + `all`), scoped to `club` or global for None."""
    user = _user(session)
    _assign_role(session, user, "admin", club)
    return user


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _transfer(client: TestClient, membership_id: uuid.UUID, target_group_id: uuid.UUID):
    client.cookies.set("csrf_token", "test-csrf-token")
    return client.post(
        f"/api/v1/group-memberships/{membership_id}/transfer",
        json={"target_group_id": str(target_group_id)},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _memberships_of(club_membership_id: uuid.UUID) -> list[GroupMembership]:
    with session_scope() as session:
        return list(
            session.execute(
                select(GroupMembership)
                .where(GroupMembership.club_membership_id == club_membership_id)
                .order_by(GroupMembership.created_at)
            )
            .scalars()
            .all()
        )


def _state(membership_id: uuid.UUID) -> tuple[str, datetime.datetime | None]:
    with session_scope() as session:
        membership = session.get(GroupMembership, membership_id)
        assert membership is not None
        return membership.membership_status, membership.valid_to


def _world() -> dict[str, uuid.UUID]:
    """Club with Groups A (source), B (target), C; a participant with an
    active membership in A; a club-scoped Administrator."""
    with session_scope() as session:
        club = _club(session)
        group_a, group_b, group_c = (
            _group(session, club),
            _group(session, club),
            _group(session, club),
        )
        person = _person(session)
        club_membership = _club_membership(session, club, person)
        source = _group_membership(session, group_a, club_membership)
        admin = _admin(session, club)
        session.commit()
        return {
            "club": club.id,
            "a": group_a.id,
            "b": group_b.id,
            "c": group_c.id,
            "person": person.id,
            "club_membership": club_membership.id,
            "source": source.id,
            "admin": admin.id,
        }


# --- A. happy path --------------------------------------------------------------


@requires_postgres
def test_transfer_ends_source_and_creates_target_for_the_same_person(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world["admin"])

    response = _transfer(client, world["source"], world["b"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["group_id"] == str(world["b"])
    assert body["club_membership_id"] == str(world["club_membership"])
    assert body["membership_status"] == "active"
    assert body["valid_to"] is None

    rows = _memberships_of(world["club_membership"])
    assert len(rows) == 2  # the source row is kept, never deleted
    source, target = (
        next(r for r in rows if r.id == world["source"]),
        next(r for r in rows if r.id != world["source"]),
    )
    assert (source.group_id, source.membership_status) == (world["a"], "ended")
    assert source.valid_to is not None
    assert (target.group_id, target.membership_status) == (world["b"], "active")
    assert target.club_membership_id == world["club_membership"]
    assert target.valid_from == source.valid_to  # contiguous history
    with session_scope() as session:
        club_membership = session.get(ClubMembership, world["club_membership"])
        assert club_membership is not None
        assert (club_membership.person_id, club_membership.status) == (world["person"], "active")

    # The Group views reflect the move.
    in_a = client.get(
        f"/api/v1/groups/{world['a']}/members", params={"membership_status": "active"}
    )
    in_b = client.get(
        f"/api/v1/groups/{world['b']}/members", params={"membership_status": "active"}
    )
    assert in_a.json()["items"] == []
    assert [m["id"] for m in in_b.json()["items"]] == [str(target.id)]


@requires_postgres
def test_transfer_records_both_halves_in_the_existing_audit_vocabulary(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world["admin"])

    target_id = uuid.UUID(_transfer(client, world["source"], world["b"]).json()["id"])

    with session_scope() as session:
        ended = session.execute(
            select(AuditLog).where(
                AuditLog.action == "group_membership.ended",
                AuditLog.resource_id == world["source"],
            )
        ).scalar_one()
        created = session.execute(
            select(AuditLog).where(
                AuditLog.action == "group_membership.created",
                AuditLog.resource_id == target_id,
            )
        ).scalar_one()
    link = {
        "source_group_membership_id": str(world["source"]),
        "target_group_membership_id": str(target_id),
        "source_group_id": str(world["a"]),
        "target_group_id": str(world["b"]),
    }
    assert ended.actor_user_id == created.actor_user_id == world["admin"]
    assert ended.details["transfer"] == link
    assert ended.details["changes"] == {"membership_status": {"from": "active", "to": "ended"}}
    assert created.details["transfer"] == link


# --- M. other active memberships are untouched ------------------------------------


@requires_postgres
def test_transfer_leaves_other_active_memberships_untouched(client: TestClient) -> None:
    world = _world()
    with session_scope() as session:
        club_membership = session.get(ClubMembership, world["club_membership"])
        group_c = session.get(Group, world["c"])
        assert club_membership is not None and group_c is not None
        other = _group_membership(session, group_c, club_membership)
        session.commit()
        other_id = other.id
    _authenticate_as(world["admin"])

    assert _transfer(client, world["source"], world["b"]).status_code == 201

    active = {
        r.group_id
        for r in _memberships_of(world["club_membership"])
        if r.membership_status == "active"
    }
    assert active == {world["b"], world["c"]}
    assert _state(other_id) == ("active", None)


# --- B-H. rejected transfers change nothing -----------------------------------------


def _assert_unchanged(world: dict[str, uuid.UUID]) -> None:
    rows = _memberships_of(world["club_membership"])
    assert [(r.id, r.membership_status, r.valid_to) for r in rows] == [
        (world["source"], "active", None)
    ]


@requires_postgres
def test_same_group_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world["admin"])

    response = _transfer(client, world["source"], world["a"])

    assert (response.status_code, _error_code(response)) == (422, "invalid_group_transfer")
    _assert_unchanged(world)


@requires_postgres
def test_cross_club_target_is_rejected(client: TestClient) -> None:
    world = _world()
    with session_scope() as session:
        foreign_group = _group(session, _club(session))
        global_admin = _admin(session, None)  # authorized on both Clubs
        session.commit()
        foreign_group_id, global_admin_id = foreign_group.id, global_admin.id

    # A club-scoped Administrator cannot even see the other Club's Group.
    _authenticate_as(world["admin"])
    hidden = _transfer(client, world["source"], foreign_group_id)
    assert (hidden.status_code, _error_code(hidden)) == (404, "group_not_found")

    # An Administrator authorized on both Clubs hits the ADR-0022 check.
    _authenticate_as(global_admin_id)
    response = _transfer(client, world["source"], foreign_group_id)
    assert (response.status_code, _error_code(response)) == (422, "group_membership_club_mismatch")
    _assert_unchanged(world)


@requires_postgres
def test_archived_target_is_rejected(client: TestClient) -> None:
    # Group.status has exactly two values (people-api.md §14.1): `archived`
    # is the only non-active state a target Group can be in.
    world = _world()
    with session_scope() as session:
        club = session.get(Club, world["club"])
        assert club is not None
        archived = _group(session, club, status="archived")
        session.commit()
        archived_id = archived.id
    _authenticate_as(world["admin"])

    response = _transfer(client, world["source"], archived_id)

    assert (response.status_code, _error_code(response)) == (409, "group_archived")
    _assert_unchanged(world)


@requires_postgres
def test_ended_source_membership_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world["admin"])
    client.cookies.set("csrf_token", "test-csrf-token")
    ended = client.post(
        f"/api/v1/group-memberships/{world['source']}/end",
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert ended.status_code == 200

    response = _transfer(client, world["source"], world["b"])

    assert (response.status_code, _error_code(response)) == (
        409,
        "invalid_group_membership_transition",
    )
    rows = _memberships_of(world["club_membership"])
    assert [(r.id, r.membership_status) for r in rows] == [(world["source"], "ended")]


@requires_postgres
def test_inactive_club_membership_is_rejected(client: TestClient) -> None:
    # The existing creation rule (people-api.md §15 POST .../members): a
    # GroupMembership is created only through an active ClubMembership.
    world = _world()
    with session_scope() as session:
        club_membership = session.get(ClubMembership, world["club_membership"])
        assert club_membership is not None
        club_membership.status = "suspended"
        session.commit()
    _authenticate_as(world["admin"])

    response = _transfer(client, world["source"], world["b"])

    assert (response.status_code, _error_code(response)) == (422, "group_membership_club_mismatch")
    _assert_unchanged(world)


@requires_postgres
def test_nonexistent_target_and_source_are_404(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world["admin"])

    no_target = _transfer(client, world["source"], uuid.uuid4())
    assert (no_target.status_code, _error_code(no_target)) == (404, "group_not_found")

    no_source = _transfer(client, uuid.uuid4(), world["b"])
    assert (no_source.status_code, _error_code(no_source)) == (404, "group_membership_not_found")
    _assert_unchanged(world)


@requires_postgres
def test_unknown_request_body_shape_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world["admin"])
    client.cookies.set("csrf_token", "test-csrf-token")

    response = client.post(
        f"/api/v1/group-memberships/{world['source']}/transfer",
        json={},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )

    assert response.status_code == 422
    _assert_unchanged(world)


# --- I/J. authorization ------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ["member", "instructor", "guardian"])
def test_non_administrator_roles_cannot_transfer(client: TestClient, role_code: str) -> None:
    world = _world()
    with session_scope() as session:
        club = session.get(Club, world["club"])
        assert club is not None
        person = _person(session)
        actor_club_membership = _club_membership(session, club, person)
        actor = _user(session, person)
        _assign_role(session, actor, role_code, club)
        group_a = session.get(Group, world["a"])
        group_b = session.get(Group, world["b"])
        assert group_a is not None and group_b is not None
        if role_code == "member":
            _group_membership(session, group_a, actor_club_membership)
        if role_code == "instructor":
            for group in (group_a, group_b):
                session.add(
                    GroupInstructorAssignment(
                        group_id=group.id,
                        user_id=actor.id,
                        role_in_group="instructor",
                        valid_from=_LONG_AGO,
                    )
                )
        if role_code == "guardian":
            session.add(
                GuardianRelationship(
                    guardian_person_id=person.id,
                    child_person_id=world["person"],
                    relationship_type="parent",
                    status="active",
                    valid_from=_LONG_AGO,
                )
            )
        session.commit()
        actor_id = actor.id
    _authenticate_as(actor_id)

    response = _transfer(client, world["source"], world["b"])

    assert (response.status_code, _error_code(response)) == (404, "group_membership_not_found")
    _assert_unchanged(world)


@requires_postgres
def test_administrator_of_another_club_cannot_transfer(client: TestClient) -> None:
    world = _world()
    with session_scope() as session:
        other_admin = _admin(session, _club(session))
        session.commit()
        other_admin_id = other_admin.id
    _authenticate_as(other_admin_id)

    response = _transfer(client, world["source"], world["b"])

    assert (response.status_code, _error_code(response)) == (404, "group_membership_not_found")
    _assert_unchanged(world)


# --- K/L. atomicity ---------------------------------------------------------------


@requires_postgres
def test_existing_active_target_membership_is_a_conflict_and_rolls_back(
    client: TestClient,
) -> None:
    world = _world()
    with session_scope() as session:
        club_membership = session.get(ClubMembership, world["club_membership"])
        group_b = session.get(Group, world["b"])
        assert club_membership is not None and group_b is not None
        existing = _group_membership(session, group_b, club_membership)
        session.commit()
        existing_id = existing.id
    _authenticate_as(world["admin"])

    response = _transfer(client, world["source"], world["b"])

    # The §15.2 invariant fires on the target insert, after the source was
    # already set to ended in the same transaction: nothing is persisted.
    assert (response.status_code, _error_code(response)) == (409, "duplicate_group_membership")
    assert _state(world["source"]) == ("active", None)
    assert _state(existing_id) == ("active", None)
    assert len(_memberships_of(world["club_membership"])) == 2
    with session_scope() as session:
        audit_rows = session.execute(
            select(AuditLog).where(AuditLog.resource_id == world["source"])
        ).all()
    assert audit_rows == []


@requires_postgres
def test_failure_after_the_source_is_ended_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world()
    real_record = audit_service.record_audit_event

    def fail_on_target_creation(session: Session, **kwargs: object) -> AuditLog:
        if kwargs["action"] == "group_membership.created":
            raise RuntimeError("simulated failure while creating the target membership")
        return real_record(session, **kwargs)

    monkeypatch.setattr(group_service, "record_audit_event", fail_on_target_creation)
    _authenticate_as(world["admin"])
    failing_client = TestClient(app, raise_server_exceptions=False)

    response = _transfer(failing_client, world["source"], world["b"])

    assert response.status_code == 500
    _assert_unchanged(world)
    with session_scope() as session:
        audit_rows = session.execute(
            select(AuditLog).where(AuditLog.resource_id == world["source"])
        ).all()
    assert audit_rows == []


# --- concurrency -------------------------------------------------------------------


@requires_postgres
def test_concurrent_transfers_of_one_membership_create_exactly_one_target(
    client: TestClient,
) -> None:
    # Both requests queue on the source row's FOR UPDATE lock (held here
    # first so they really overlap); the one that runs second sees the
    # membership already ended and is rejected.
    world = _world()
    _authenticate_as(world["admin"])
    results: list[int] = []
    gate = threading.Barrier(2, timeout=10)

    def attempt(target: uuid.UUID) -> None:
        gate.wait()
        results.append(_transfer(client, world["source"], target).status_code)

    with session_scope() as blocker:
        blocker.execute(
            select(GroupMembership).where(GroupMembership.id == world["source"]).with_for_update()
        )
        threads = [
            threading.Thread(target=attempt, args=(world["b"],)),
            threading.Thread(target=attempt, args=(world["c"],)),
        ]
        for thread in threads:
            thread.start()
        threading.Event().wait(1.0)  # let both requests reach the lock
        blocker.rollback()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(results) == [201, 409]
    rows = _memberships_of(world["club_membership"])
    active = [r for r in rows if r.membership_status == "active"]
    assert len(rows) == 2 and len(active) == 1
    assert active[0].group_id in {world["b"], world["c"]}
    assert _state(world["source"])[0] == "ended"
