"""Issue #305 / ADR-0047: lesson automatic registration and the lesson
attendance workflow for the canonical seeded roles.

- `POST /events` with `event_type = lesson` and target Groups registers
  every Person with an active GroupMembership in a target Group at that
  moment (one `EventParticipation(registered)` per Person, atomically with
  the Event, no Attendance); every other Event type keeps ADR-0037's
  "targeting never registers" rule.
- The existing Attendance API (ADR-0032) works on that roster for the
  canonical roles through the grants seeded by migration a7c3e5f19d24
  (`instructor` attendance.read/update + event.read at own_groups/
  own_events, `member` attendance.read self, `guardian` attendance.read
  children), and stays fail-closed outside those scopes.
- Migration a7c3e5f19d24 upgrades/downgrades only its own grant scopes.

Against the REAL shipped app and a real PostgreSQL database. Self-contained
factories, per this codebase's convention of not importing helpers across
test files.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.attendance import Attendance
from app.db.audit import AuditLog
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.events import Event, EventParticipation
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_SEED_REVISION = "a7c3e5f19d24"
_BEFORE_SEED_REVISION = "d8e3b5f02a47"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories ----------------------------------------------------------------


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _new_person(session, first_name: str) -> Person:  # type: ignore[no-untyped-def]
    person = Person(last_name="Lesson", first_name=f"{first_name}-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.flush()
    return person


def _new_user(session, person: Person) -> User:  # type: ignore[no-untyped-def]
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _club_membership(session, club_id: uuid.UUID, person: Person) -> ClubMembership:  # type: ignore[no-untyped-def]
    membership = ClubMembership(
        club_id=club_id,
        person_id=person.id,
        membership_type="member",
        status="active",
        joined_at=_utc(2020, 1, 1),
    )
    session.add(membership)
    session.flush()
    return membership


def _group(session, club_id: uuid.UUID) -> Group:  # type: ignore[no-untyped-def]
    group = Group(
        club_id=club_id,
        name=f"Group {uuid.uuid4().hex[:8]}",
        status="active",
        valid_from=_utc(2020, 1, 1),
    )
    session.add(group)
    session.flush()
    return group


def _group_membership(  # type: ignore[no-untyped-def]
    session, group: Group, membership: ClubMembership, **overrides
) -> GroupMembership:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": membership.id,
        "membership_status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    row = GroupMembership(**defaults)  # type: ignore[arg-type]
    session.add(row)
    session.flush()
    return row


def _assign_role(user_id: uuid.UUID, role_code: str, club_id: uuid.UUID) -> None:
    with session_scope() as session:
        role_id = session.execute(select(Role.id).where(Role.code == role_code)).scalar_one()
        session.add(UserRoleAssignment(user_id=user_id, role_id=role_id, club_id=club_id))
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


@dataclass(frozen=True)
class _World:
    club_id: uuid.UUID
    admin_user_id: uuid.UUID
    group_a_id: uuid.UUID
    group_b_id: uuid.UUID
    # Persons with an active GroupMembership at lesson creation.
    only_a_id: uuid.UUID
    only_b_id: uuid.UUID
    both_id: uuid.UUID
    # Same Club, no GroupMembership in A/B; ended GroupMembership in A.
    outsider_id: uuid.UUID
    former_id: uuid.UUID
    # Club memberships used later by the snapshot tests.
    outsider_membership_id: uuid.UUID


def _world() -> _World:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        admin = _new_user(session, _new_person(session, "Admin"))
        group_a = _group(session, club.id)
        group_b = _group(session, club.id)

        only_a = _new_person(session, "OnlyA")
        _group_membership(session, group_a, _club_membership(session, club.id, only_a))
        only_b = _new_person(session, "OnlyB")
        _group_membership(session, group_b, _club_membership(session, club.id, only_b))
        both = _new_person(session, "Both")
        both_membership = _club_membership(session, club.id, both)
        _group_membership(session, group_a, both_membership)
        _group_membership(session, group_b, both_membership)
        outsider = _new_person(session, "Outsider")
        outsider_membership = _club_membership(session, club.id, outsider)
        former = _new_person(session, "Former")
        _group_membership(
            session,
            group_a,
            _club_membership(session, club.id, former),
            membership_status="ended",
            valid_to=_utc(2021, 1, 1),
        )
        session.commit()
        world = _World(
            club_id=club.id,
            admin_user_id=admin.id,
            group_a_id=group_a.id,
            group_b_id=group_b.id,
            only_a_id=only_a.id,
            only_b_id=only_b.id,
            both_id=both.id,
            outsider_id=outsider.id,
            former_id=former.id,
            outsider_membership_id=outsider_membership.id,
        )
    _assign_role(world.admin_user_id, "admin", world.club_id)
    return world


def _create_event(
    client: TestClient,
    world: _World,
    *,
    event_type: str = "lesson",
    group_ids: list[uuid.UUID] | None = None,
    instructor_ids: list[uuid.UUID] | None = None,
    title: str = "Lesson",
):  # type: ignore[no-untyped-def]
    _authenticate_as(world.admin_user_id)
    return client.post(
        "/api/v1/events",
        json={
            "club_id": str(world.club_id),
            "event_type": event_type,
            "title": title,
            "start_at": "2030-09-20T17:00:00+00:00",
            "end_at": "2030-09-20T19:00:00+00:00",
            "timezone": "Europe/Moscow",
            "group_ids": [str(g) for g in (group_ids or [])],
            "instructor_ids": [str(u) for u in (instructor_ids or [])],
        },
        headers=_csrf_headers(client),
    )


def _create_published_lesson(client: TestClient, world: _World, **kwargs) -> uuid.UUID:  # type: ignore[no-untyped-def]
    kwargs.setdefault("group_ids", [world.group_a_id, world.group_b_id])
    response = _create_event(client, world, **kwargs)
    assert response.status_code == 201, response.text
    event_id = uuid.UUID(response.json()["id"])
    _authenticate_as(world.admin_user_id)
    published = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "published"},
        headers=_csrf_headers(client),
    )
    assert published.status_code == 200, published.text
    return event_id


def _participations(event_id: uuid.UUID) -> dict[uuid.UUID, str]:
    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation.person_id, EventParticipation.registration_status).where(
                EventParticipation.event_id == event_id
            )
        ).all()
        return {person_id: status for person_id, status in rows}


def _attendance_count() -> int:
    with session_scope() as session:
        return len(session.execute(select(Attendance.id)).all())


def _get_attendance(client: TestClient, event_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return client.get(f"/api/v1/events/{event_id}/attendance")


def _mark(client: TestClient, event_id: uuid.UUID, person_id: uuid.UUID, status: str):  # type: ignore[no-untyped-def]
    return client.put(
        f"/api/v1/events/{event_id}/attendance/{person_id}",
        json={"status": status},
        headers=_csrf_headers(client),
    )


def _rows_by_person(response) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {item["person"]["id"]: item["status"] for item in response.json()["items"]}


# --- automatic registration (ADR-0047 §1) ------------------------------------


@requires_postgres
def test_lesson_creation_registers_current_group_participants_once(client: TestClient) -> None:
    world = _world()

    response = _create_event(client, world, group_ids=[world.group_a_id, world.group_b_id])

    assert response.status_code == 201, response.text
    event_id = uuid.UUID(response.json()["id"])
    # One row per Person — `both` is in two target Groups but gets one row;
    # the ended membership and the non-member are not registered.
    assert _participations(event_id) == {
        world.only_a_id: "registered",
        world.only_b_id: "registered",
        world.both_id: "registered",
    }


@requires_postgres
def test_lesson_registration_is_audited_with_the_existing_action(client: TestClient) -> None:
    world = _world()
    event_id = uuid.UUID(
        _create_event(client, world, group_ids=[world.group_a_id]).json()["id"]
    )

    with session_scope() as session:
        participation_ids = set(
            session.execute(
                select(EventParticipation.id).where(EventParticipation.event_id == event_id)
            ).scalars()
        )
        audited = session.execute(
            select(AuditLog).where(AuditLog.resource_id.in_(participation_ids))
        ).scalars().all()
        assert {row.action for row in audited} == {"event_participation.status_changed"}
        assert {row.resource_id for row in audited} == participation_ids
        assert {row.actor_user_id for row in audited} == {world.admin_user_id}


@requires_postgres
def test_lesson_registration_creates_no_attendance(client: TestClient) -> None:
    world = _world()
    before = _attendance_count()

    event_id = _create_published_lesson(client, world)

    assert _attendance_count() == before
    _authenticate_as(world.admin_user_id)
    response = _get_attendance(client, event_id)
    assert response.status_code == 200, response.text
    assert set(_rows_by_person(response).values()) == {None}
    assert response.json()["summary"] == {
        "total": 3,
        "marked": 0,
        "present": 0,
        "absent": 0,
        "unmarked": 3,
    }


@requires_postgres
def test_joining_group_after_lesson_creation_is_not_retroactive(client: TestClient) -> None:
    world = _world()
    event_id = uuid.UUID(
        _create_event(client, world, group_ids=[world.group_a_id]).json()["id"]
    )

    with session_scope() as session:
        group = session.get(Group, world.group_a_id)
        membership = session.get(ClubMembership, world.outsider_membership_id)
        _group_membership(session, group, membership, valid_from=_utc(2026, 1, 1))
        session.commit()

    assert world.outsider_id not in _participations(event_id)


@requires_postgres
def test_leaving_group_after_lesson_creation_keeps_registration(client: TestClient) -> None:
    world = _world()
    event_id = uuid.UUID(
        _create_event(client, world, group_ids=[world.group_a_id]).json()["id"]
    )

    with session_scope() as session:
        rows = session.execute(
            select(GroupMembership)
            .join(ClubMembership, ClubMembership.id == GroupMembership.club_membership_id)
            .where(
                ClubMembership.person_id == world.only_a_id,
                GroupMembership.group_id == world.group_a_id,
            )
        ).scalars().all()
        for row in rows:
            row.membership_status = "ended"
            row.valid_to = datetime.datetime.now(datetime.timezone.utc)
        session.commit()

    assert _participations(event_id)[world.only_a_id] == "registered"


@requires_postgres
@pytest.mark.parametrize("event_type", ["training", "competition", "meeting", "trip"])
def test_non_lesson_group_targeting_registers_nobody(client: TestClient, event_type: str) -> None:
    world = _world()

    response = _create_event(
        client, world, event_type=event_type, group_ids=[world.group_a_id, world.group_b_id]
    )

    assert response.status_code == 201, response.text
    assert _participations(uuid.UUID(response.json()["id"])) == {}


@requires_postgres
def test_club_wide_lesson_registers_nobody(client: TestClient) -> None:
    world = _world()

    response = _create_event(client, world, group_ids=[])

    assert response.status_code == 201, response.text
    assert _participations(uuid.UUID(response.json()["id"])) == {}


@requires_postgres
def test_lesson_with_invalid_target_creates_nothing(client: TestClient) -> None:
    world = _world()
    with session_scope() as session:
        other_club = Club(name=f"Other {uuid.uuid4().hex[:8]}", status="active")
        session.add(other_club)
        session.flush()
        foreign_group_id = _group(session, other_club.id).id
        session.commit()

    response = _create_event(
        client, world, group_ids=[world.group_a_id, foreign_group_id], title="Atomic lesson"
    )

    assert response.status_code == 422, response.text
    with session_scope() as session:
        assert session.execute(select(Event).where(Event.title == "Atomic lesson")).first() is None
        assert session.execute(select(EventParticipation.id)).first() is None


@requires_postgres
def test_lesson_registration_failure_rolls_back_event_creation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world()

    def _fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("participation audit failed")

    monkeypatch.setattr("app.events.crud.record_audit_event", _fail)

    with pytest.raises(RuntimeError):
        _create_event(client, world, group_ids=[world.group_a_id], title="Rolled back lesson")

    with session_scope() as session:
        assert (
            session.execute(select(Event).where(Event.title == "Rolled back lesson")).first()
            is None
        )
        assert session.execute(select(EventParticipation.id)).first() is None


# --- attendance workflow: admin (unchanged) ----------------------------------


@requires_postgres
def test_admin_marks_present_and_absent_and_unmarked_stays_distinct(client: TestClient) -> None:
    world = _world()
    event_id = _create_published_lesson(client, world)
    _authenticate_as(world.admin_user_id)

    assert _mark(client, event_id, world.only_a_id, "present").status_code == 200
    assert _mark(client, event_id, world.only_b_id, "absent").status_code == 200

    response = _get_attendance(client, event_id)
    assert response.status_code == 200, response.text
    assert _rows_by_person(response) == {
        str(world.only_a_id): "present",
        str(world.only_b_id): "absent",
        str(world.both_id): None,
    }
    assert response.json()["summary"] == {
        "total": 3,
        "marked": 2,
        "present": 1,
        "absent": 1,
        "unmarked": 1,
    }

    # Change a mark, then bulk-mark through the existing bulk endpoint.
    assert _mark(client, event_id, world.only_b_id, "present").status_code == 200
    bulk = client.put(
        f"/api/v1/events/{event_id}/attendance",
        json={"items": [{"person_id": str(world.both_id), "status": "absent"}]},
        headers=_csrf_headers(client),
    )
    assert bulk.status_code == 200, bulk.text
    summary = _get_attendance(client, event_id).json()["summary"]
    assert summary == {"total": 3, "marked": 3, "present": 2, "absent": 1, "unmarked": 0}


@requires_postgres
def test_attendance_for_non_participant_is_still_rejected(client: TestClient) -> None:
    world = _world()
    event_id = _create_published_lesson(client, world)
    _authenticate_as(world.admin_user_id)

    response = _mark(client, event_id, world.outsider_id, "present")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "participation_missing"


# --- attendance workflow: instructor (seeded own_groups / own_events) --------


def _instructor(world: _World, *, group_id: uuid.UUID | None) -> uuid.UUID:
    """A User holding the canonical `instructor` role (Club-scoped), with
    an active ClubMembership and — if `group_id` — an active
    GroupInstructorAssignment for that Group."""
    with session_scope() as session:
        person = _new_person(session, "Instructor")
        user = _new_user(session, person)
        _club_membership(session, world.club_id, person)
        if group_id is not None:
            session.add(
                GroupInstructorAssignment(
                    group_id=group_id,
                    user_id=user.id,
                    role_in_group="instructor",
                    valid_from=_utc(2020, 1, 1),
                )
            )
        session.commit()
        user_id = user.id
    _assign_role(user_id, "instructor", world.club_id)
    return user_id


@requires_postgres
def test_instructor_of_target_group_reads_and_marks_attendance(client: TestClient) -> None:
    world = _world()
    instructor_id = _instructor(world, group_id=world.group_a_id)
    event_id = _create_published_lesson(client, world)

    _authenticate_as(instructor_id)
    assert client.get(f"/api/v1/events/{event_id}").status_code == 200
    roster = _get_attendance(client, event_id)
    assert roster.status_code == 200, roster.text
    assert set(_rows_by_person(roster)) == {
        str(world.only_a_id),
        str(world.only_b_id),
        str(world.both_id),
    }
    assert _mark(client, event_id, world.only_b_id, "absent").status_code == 200
    assert _mark(client, event_id, world.only_b_id, "present").status_code == 200


@requires_postgres
def test_responsible_instructor_reads_and_marks_attendance(client: TestClient) -> None:
    world = _world()
    instructor_id = _instructor(world, group_id=None)
    event_id = _create_published_lesson(client, world, instructor_ids=[instructor_id])

    _authenticate_as(instructor_id)
    assert client.get(f"/api/v1/events/{event_id}").status_code == 200
    assert _get_attendance(client, event_id).status_code == 200
    assert _mark(client, event_id, world.only_a_id, "present").status_code == 200


@requires_postgres
def test_unrelated_instructor_gets_no_event_or_attendance_access(client: TestClient) -> None:
    world = _world()
    with session_scope() as session:
        other_group_id = _group(session, world.club_id).id
        session.commit()
    instructor_id = _instructor(world, group_id=other_group_id)
    event_id = _create_published_lesson(client, world)

    _authenticate_as(instructor_id)
    assert client.get(f"/api/v1/events/{event_id}").status_code == 404
    assert _get_attendance(client, event_id).status_code == 404
    assert _mark(client, event_id, world.only_a_id, "present").status_code == 404
    assert _attendance_count() == 0


# --- attendance read: member (self) and guardian (children) ------------------


def _user_for_person(person_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        user = _new_user(session, session.get(Person, person_id))
        session.commit()
        return user.id


@requires_postgres
def test_member_reads_only_own_attendance_and_cannot_mark(client: TestClient) -> None:
    world = _world()
    member_user_id = _user_for_person(world.only_a_id)
    _assign_role(member_user_id, "member", world.club_id)
    event_id = _create_published_lesson(client, world)
    _authenticate_as(world.admin_user_id)
    _mark(client, event_id, world.only_a_id, "present")
    _mark(client, event_id, world.only_b_id, "absent")

    _authenticate_as(member_user_id)
    response = _get_attendance(client, event_id)
    assert response.status_code == 200, response.text
    assert _rows_by_person(response) == {str(world.only_a_id): "present"}
    assert response.json()["summary"]["total"] == 1
    assert _mark(client, event_id, world.only_a_id, "absent").status_code == 404


@requires_postgres
def test_member_without_participation_gets_404(client: TestClient) -> None:
    world = _world()
    member_user_id = _user_for_person(world.outsider_id)
    _assign_role(member_user_id, "member", world.club_id)
    event_id = _create_published_lesson(client, world)

    _authenticate_as(member_user_id)
    assert _get_attendance(client, event_id).status_code == 404


def _guardian_of(world: _World, *child_ids: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        guardian = _new_person(session, "Guardian")
        user = _new_user(session, guardian)
        _club_membership(session, world.club_id, guardian)
        for child_id in child_ids:
            session.add(
                GuardianRelationship(
                    guardian_person_id=guardian.id,
                    child_person_id=child_id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_utc(2020, 1, 1),
                )
            )
        session.commit()
        user_id = user.id
    _assign_role(user_id, "guardian", world.club_id)
    return user_id


@requires_postgres
def test_guardian_reads_only_own_childrens_attendance(client: TestClient) -> None:
    world = _world()
    guardian_user_id = _guardian_of(world, world.only_a_id, world.both_id)
    event_id = _create_published_lesson(client, world)
    _authenticate_as(world.admin_user_id)
    _mark(client, event_id, world.only_a_id, "absent")
    _mark(client, event_id, world.only_b_id, "present")

    _authenticate_as(guardian_user_id)
    response = _get_attendance(client, event_id)
    assert response.status_code == 200, response.text
    # Only the two children; the unrelated participant (present) never leaks.
    assert _rows_by_person(response) == {
        str(world.only_a_id): "absent",
        str(world.both_id): None,
    }
    assert response.json()["summary"] == {
        "total": 2,
        "marked": 1,
        "present": 0,
        "absent": 1,
        "unmarked": 1,
    }
    assert _mark(client, event_id, world.only_a_id, "present").status_code == 404


@requires_postgres
def test_guardian_of_non_participant_gets_404(client: TestClient) -> None:
    world = _world()
    guardian_user_id = _guardian_of(world, world.outsider_id)
    event_id = _create_published_lesson(client, world)

    _authenticate_as(guardian_user_id)
    assert _get_attendance(client, event_id).status_code == 404


# --- migration a7c3e5f19d24 ---------------------------------------------------

_SEEDED = {
    ("instructor", "attendance.read", "own_groups"),
    ("instructor", "attendance.read", "own_events"),
    ("instructor", "attendance.update", "own_groups"),
    ("instructor", "attendance.update", "own_events"),
    ("instructor", "event.read", "own_groups"),
    ("instructor", "event.read", "own_events"),
    ("member", "attendance.read", "self"),
    ("guardian", "attendance.read", "children"),
}
# Seeded by a later migration (3b1fd730bb0d, Issue #312), so any downgrade
# below this revision removes them as well.
_LATER_SEEDED = {
    ("member", "person.read", "self"),
    ("member", "person.update", "self"),
}


def _grant_scopes() -> set[tuple[str, str, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code, RolePermissionScope.scope_type)
            .select_from(RolePermissionScope)
            .join(RolePermission, RolePermission.id == RolePermissionScope.role_permission_id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        ).all()
        return {(r, p, s) for r, p, s in rows}


def _grant_pairs() -> set[tuple[str, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code)
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        ).all()
        return {(r, p) for r, p in rows}


@requires_postgres
def test_seed_migration_downgrade_and_upgrade(database_url: str) -> None:
    at_head = _grant_scopes()
    assert _SEEDED <= at_head
    at_seed = at_head - _LATER_SEEDED
    # Deliberately not seeded (separate authorization GAP, outside #305).
    for code in ("event.create", "event.update", "event.cancel", "event.manage"):
        assert ("instructor", code) not in _grant_pairs()

    try:
        downgrade = run_alembic("downgrade", _BEFORE_SEED_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        # Exactly this migration's scopes and grants are gone; nothing else.
        assert _grant_scopes() == at_seed - _SEEDED
        assert not {(r, p) for r, p, _ in _SEEDED} & _grant_pairs()

        # A pre-existing extra scope on one of the same grants must survive
        # the migration's own upgrade/downgrade round trip.
        with session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO role_permissions (id, role_id, permission_id)
                    SELECT gen_random_uuid(), r.id, p.id FROM roles r, permissions p
                    WHERE r.code = 'member' AND p.code = 'attendance.read'
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO role_permission_scopes (id, role_permission_id, scope_type)
                    SELECT gen_random_uuid(), rp.id, 'none' FROM role_permissions rp
                    JOIN roles r ON r.id = rp.role_id
                    JOIN permissions p ON p.id = rp.permission_id
                    WHERE r.code = 'member' AND p.code = 'attendance.read'
                    """
                )
            )
            session.commit()

        upgrade = run_alembic("upgrade", _SEED_REVISION, database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
        assert _grant_scopes() == at_seed | {("member", "attendance.read", "none")}

        downgrade = run_alembic("downgrade", _BEFORE_SEED_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        assert ("member", "attendance.read", "none") in _grant_scopes()
        assert ("member", "attendance.read", "self") not in _grant_scopes()
        assert ("member", "attendance.read") in _grant_pairs()
    finally:
        restored = run_alembic("upgrade", "head", database_url=database_url)
        assert restored.returncode == 0, restored.stderr
        with session_scope() as session:
            session.execute(
                text(
                    """
                    DELETE FROM role_permission_scopes rps
                    USING role_permissions rp, roles r, permissions p
                    WHERE rps.role_permission_id = rp.id AND rp.role_id = r.id
                      AND rp.permission_id = p.id AND r.code = 'member'
                      AND p.code = 'attendance.read' AND rps.scope_type = 'none'
                    """
                )
            )
            session.commit()
    assert _grant_scopes() == at_head
