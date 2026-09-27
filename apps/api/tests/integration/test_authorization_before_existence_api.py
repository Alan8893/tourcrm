"""P1 GAP-3 — authorization before existence check (PO decision).

For every endpoint below the canonical order is authentication ->
authorization -> existence/visibility -> operation:

A/B. An authenticated caller WITHOUT the required permission gets the
     same 403 whether the addressed resource (Person, Club, guardian
     Person) exists or not — the response never discloses existence.
C.   An authorized caller addressing an existing resource keeps the
     previous successful result.
D.   An authorized caller addressing a missing resource keeps the
     previous canonical 404 (or 422 `invalid_*_id` for payload ids).

Self-contained factories, per this codebase's convention of not importing
helpers across test files.
"""

import datetime
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(code=permission_code)
            session.add(permission)
            session.commit()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.commit()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
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
    caller_id: uuid.UUID
    # Person with an active ClubMembership, a User account, a GroupMembership
    # and a guardian — every nested collection has something to return.
    target_person_id: uuid.UUID
    # Person with an email and an active ClubMembership but no User yet —
    # the valid target for `POST .../account`.
    accountless_person_id: uuid.UUID
    # A second Person usable as `guardian_person_id`.
    guardian_person_id: uuid.UUID


def _seed_world() -> _World:
    """One Club (the Person role/account endpoints resolve the sole Club),
    a caller with a User and no grants at all, and the target Persons."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        caller_person = Person(last_name="Caller", first_name=uuid.uuid4().hex[:8])
        target = Person(
            last_name="Target",
            first_name=uuid.uuid4().hex[:8],
            email=f"target-{uuid.uuid4().hex[:8]}@example.com",
        )
        accountless = Person(
            last_name="Accountless",
            first_name=uuid.uuid4().hex[:8],
            email=f"new-{uuid.uuid4().hex[:8]}@example.com",
        )
        guardian = Person(last_name="Guardian", first_name=uuid.uuid4().hex[:8])
        session.add_all([club, caller_person, target, accountless, guardian])
        session.flush()
        caller = User(
            person=caller_person,
            login_identifier=f"caller-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        target_user = User(person=target, login_identifier=target.email, status="active")
        target_cm = ClubMembership(
            club_id=club.id,
            person_id=target.id,
            membership_type="member",
            status="active",
            joined_at=_utc(2020, 1, 1),
        )
        accountless_cm = ClubMembership(
            club_id=club.id,
            person_id=accountless.id,
            membership_type="member",
            status="active",
            joined_at=_utc(2020, 1, 1),
        )
        group = Group(club_id=club.id, name="Group", status="active", valid_from=_utc(2020, 1, 1))
        session.add_all([caller, target_user, target_cm, accountless_cm, group])
        session.flush()
        session.add(
            GroupMembership(
                group_id=group.id,
                club_membership_id=target_cm.id,
                valid_from=_utc(2024, 1, 1),
                membership_status="active",
            )
        )
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian.id,
                child_person_id=target.id,
                relationship_type="parent",
                status="active",
                valid_from=_utc(2024, 1, 1),
            )
        )
        session.commit()
        return _World(
            club_id=club.id,
            caller_id=caller.id,
            target_person_id=target.id,
            accountless_person_id=accountless.id,
            guardian_person_id=guardian.id,
        )


# --- Case table ---------------------------------------------------------------
#
# `request(client, world, existing)` sends the request addressing either an
# existing resource (existing=True) or a random, nonexistent one.


RequestFn = Callable[[TestClient, _World, bool], object]


@dataclass(frozen=True)
class _Case:
    name: str
    permission: str
    # Club-scoped grants use the sole Club; None -> global assignment.
    club_scoped: bool
    request: RequestFn
    success_status: int
    missing_status: int
    missing_code: str | None


def _person(world: _World, existing: bool, *, accountless: bool = False) -> uuid.UUID:
    if not existing:
        return uuid.uuid4()
    return world.accountless_person_id if accountless else world.target_person_id


def _get(path: str, *, accountless: bool = False) -> RequestFn:
    def send(client: TestClient, world: _World, existing: bool):  # type: ignore[no-untyped-def]
        person_id = _person(world, existing, accountless=accountless)
        return client.get(path.format(person_id=person_id))

    return send


def _post(path: str, body: Callable[[_World], dict] | None = None, *, accountless: bool = False):  # type: ignore[no-untyped-def]
    def send(client: TestClient, world: _World, existing: bool):  # type: ignore[no-untyped-def]
        person_id = _person(world, existing, accountless=accountless)
        return client.post(
            path.format(person_id=person_id),
            json=body(world) if body is not None else None,
            headers=_csrf_headers(client),
        )

    return send


def _delete_role(client: TestClient, world: _World, existing: bool):  # type: ignore[no-untyped-def]
    person_id = _person(world, existing)
    return client.delete(
        f"/api/v1/persons/{person_id}/role-assignments/member", headers=_csrf_headers(client)
    )


def _post_membership(client: TestClient, world: _World, existing: bool):  # type: ignore[no-untyped-def]
    # The Club is the addressed resource here (`invalid_club_id`). The
    # caller's own Person has no ClubMembership yet, so creating one for it
    # is a valid success case (C).
    with session_scope() as session:
        caller_person_id = session.get(User, world.caller_id).person_id  # type: ignore[union-attr]
    return client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(caller_person_id),
            "club_id": str(world.club_id if existing else uuid.uuid4()),
            "membership_type": "member",
            "status": "active",
            "joined_at": _utc(2024, 1, 1).isoformat(),
        },
        headers=_csrf_headers(client),
    )


def _post_event(client: TestClient, world: _World, existing: bool):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/events",
        json={
            "club_id": str(world.club_id if existing else uuid.uuid4()),
            "event_type": "lesson",
            "title": "Lesson",
            "start_at": _utc(2030, 1, 1, 10).isoformat(),
            "end_at": _utc(2030, 1, 1, 12).isoformat(),
            "timezone": "Europe/Moscow",
        },
        headers=_csrf_headers(client),
    )


_CASES = [
    _Case(
        "get_role_assignments",
        "role.manage",
        True,
        _get("/api/v1/persons/{person_id}/role-assignments"),
        200,
        404,
        None,
    ),
    _Case(
        "post_role_assignments",
        "role.manage",
        True,
        _post("/api/v1/persons/{person_id}/role-assignments", lambda w: {"role_code": "member"}),
        201,
        404,
        None,
    ),
    _Case(
        # Existing Person without the role: the documented existence-hiding
        # 404 `role_assignment_not_found` is the previous result (C).
        "delete_role_assignment",
        "role.manage",
        True,
        _delete_role,
        404,
        404,
        None,
    ),
    _Case(
        "get_account",
        "account.manage",
        True,
        _get("/api/v1/persons/{person_id}/account"),
        200,
        404,
        None,
    ),
    _Case(
        "post_account",
        "account.manage",
        True,
        _post("/api/v1/persons/{person_id}/account", accountless=True),
        201,
        404,
        None,
    ),
    _Case(
        "post_account_password_reset",
        "account.manage",
        True,
        _post("/api/v1/persons/{person_id}/account/password-reset"),
        200,
        404,
        None,
    ),
    _Case(
        "get_groups",
        "group.read",
        True,
        _get("/api/v1/persons/{person_id}/groups"),
        200,
        404,
        None,
    ),
    _Case(
        "get_memberships",
        "membership.read",
        True,
        _get("/api/v1/persons/{person_id}/memberships"),
        200,
        404,
        None,
    ),
    _Case(
        "get_guardian_relationships",
        "guardian_relationship.read",
        False,
        _get("/api/v1/persons/{person_id}/guardian-relationships"),
        200,
        404,
        None,
    ),
    _Case(
        # GuardianRelationship is Club-neutral: its create context has no
        # club_id, so only a global `all` assignment can authorize it.
        "post_guardian_relationships",
        "guardian_relationship.manage",
        False,
        _post(
            "/api/v1/persons/{person_id}/guardian-relationships",
            lambda w: {
                "guardian_person_id": str(w.accountless_person_id),
                "relationship_type": "parent",
            },
        ),
        201,
        404,
        None,
    ),
    _Case(
        # Global `all` so an authorized caller's club boundary matches any
        # club_id and the previous 422 invalid_club_id is observable (D).
        "post_memberships",
        "membership.manage",
        False,
        _post_membership,
        201,
        422,
        "invalid_club_id",
    ),
    _Case(
        "post_events",
        "event.create",
        False,
        _post_event,
        201,
        422,
        "invalid_club_id",
    ),
]

_CASE_IDS = [case.name for case in _CASES]


def _public(response: object) -> dict:
    """The error envelope minus the per-request `request_id`."""
    body = response.json()  # type: ignore[attr-defined]
    body["error"].pop("request_id", None)
    return body


def _grant(world: _World, case: _Case) -> None:
    _grant_permission(
        world.caller_id,
        case.permission,
        scope_type="all",
        club_id=world.club_id if case.club_scoped else None,
    )


# --- A/B: no permission -> identical 403, existing or not ----------------------


@requires_postgres
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_caller_without_permission_gets_same_403_for_existing_and_missing(
    client: TestClient, case: _Case
) -> None:
    world = _seed_world()
    _authenticate_as(world.caller_id)  # authenticated, zero grants

    existing = case.request(client, world, True)
    missing = case.request(client, world, False)

    assert existing.status_code == 403, existing.text  # type: ignore[attr-defined]
    assert missing.status_code == 403, missing.text  # type: ignore[attr-defined]
    assert _public(existing) == _public(missing)


@requires_postgres
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_caller_with_unrelated_permission_gets_same_403_for_existing_and_missing(
    client: TestClient, case: _Case
) -> None:
    """Holding *some* grant is not enough — only the endpoint's own
    permission counts."""
    world = _seed_world()
    _grant_permission(world.caller_id, "knowledge.read", scope_type="all")
    _authenticate_as(world.caller_id)

    existing = case.request(client, world, True)
    missing = case.request(client, world, False)

    assert existing.status_code == missing.status_code == 403, (existing.text, missing.text)  # type: ignore[attr-defined]
    assert _public(existing) == _public(missing)


# --- C/D: after authorization, unchanged success and canonical 404/422 ----------


@requires_postgres
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_authorized_caller_existing_resource_keeps_previous_result(
    client: TestClient, case: _Case
) -> None:
    world = _seed_world()
    _grant(world, case)
    _authenticate_as(world.caller_id)

    response = case.request(client, world, True)

    assert response.status_code == case.success_status, response.text  # type: ignore[attr-defined]


@requires_postgres
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_authorized_caller_missing_resource_keeps_canonical_not_found(
    client: TestClient, case: _Case
) -> None:
    world = _seed_world()
    _grant(world, case)
    _authenticate_as(world.caller_id)

    response = case.request(client, world, False)

    assert response.status_code == case.missing_status, response.text  # type: ignore[attr-defined]
    if case.missing_code is not None:
        assert response.json()["error"]["code"] == case.missing_code  # type: ignore[attr-defined]


# --- POST payload ids: unauthorized caller never learns which ids exist -------


@requires_postgres
def test_post_guardian_relationship_unauthorized_hides_guardian_person_existence(
    client: TestClient,
) -> None:
    world = _seed_world()
    _authenticate_as(world.caller_id)
    path = f"/api/v1/persons/{world.target_person_id}/guardian-relationships"

    real = client.post(
        path,
        json={"guardian_person_id": str(world.guardian_person_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    random = client.post(
        path,
        json={"guardian_person_id": str(uuid.uuid4()), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )

    assert real.status_code == random.status_code == 403, (real.text, random.text)
    assert _public(real) == _public(random)


@requires_postgres
def test_post_memberships_unauthorized_hides_person_existence(client: TestClient) -> None:
    world = _seed_world()
    _authenticate_as(world.caller_id)

    def send(person_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return client.post(
            "/api/v1/memberships",
            json={
                "person_id": str(person_id),
                "club_id": str(world.club_id),
                "membership_type": "member",
                "status": "active",
                "joined_at": _utc(2024, 1, 1).isoformat(),
            },
            headers=_csrf_headers(client),
        )

    real = send(world.accountless_person_id)
    random = send(uuid.uuid4())

    assert real.status_code == random.status_code == 403, (real.text, random.text)
    assert _public(real) == _public(random)
