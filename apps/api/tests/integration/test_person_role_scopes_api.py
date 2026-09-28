"""AUTH-2A — one RoleAssignment per role for Person Detail and the Person
wizard; authorization scope belongs to each permission grant
(`UserRoleAssignment -> RolePermission -> RolePermissionScope`).

    admin / instructor / member / guardian -> exactly one assignment

Duplicate detection and revoke are independent of the legacy
`scope_type`; creation and revoke are atomic; one audit record per
assignment row. Legacy assignments (any old `scope_type`) count as "has
the role" and are revoked by the same remove flow.

The scope regressions below grant permissions on the *baseline* roles
test-locally (every integration test starts from the migrated baseline
snapshot): the migration itself grants no new permission to
instructor/member/guardian (AUTH-2A GAP-1).

Self-contained factories, per this codebase's convention of not importing
helpers across test files.
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app
from app.role_assignments import service as role_assignment_service

from .conftest import requires_postgres

ROLES = ["admin", "guardian", "instructor", "member"]
LEGACY_SCOPES = ["all", "self", "children", "own_groups", "own_events", "none"]


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _permission(session, code: str) -> Permission:  # type: ignore[no-untyped-def]
    permission = session.execute(
        select(Permission).where(Permission.code == code)
    ).scalar_one_or_none()
    if permission is None:
        permission = Permission(code=code)
        session.add(permission)
        session.flush()
    return permission


def _grant_admin_permissions(user_id: uuid.UUID, club_id: uuid.UUID) -> None:
    """Everything the Person Detail role endpoints and the wizard need."""
    with session_scope() as session:
        role = Role(code=f"tester-{uuid.uuid4().hex[:8]}", name="Tester")
        session.add(role)
        session.flush()
        for code in (
            "role.manage",
            "person.create",
            "account.manage",
            "group.manage",
            "group.read",
            "guardian_relationship.manage",
        ):
            session.add(
                RolePermission(
                    role_id=role.id,
                    permission_id=_permission(session, code).id,
                    scopes=[RolePermissionScope(scope_type="all")],
                )
            )
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, club_id=club_id)
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _new_person_with_user(session, club: Club, **person_fields) -> tuple[Person, User]:  # type: ignore[no-untyped-def]
    person = Person(
        last_name=person_fields.pop("last_name", "Target"),
        first_name=person_fields.pop("first_name", f"T-{uuid.uuid4().hex[:6]}"),
        **person_fields,
    )
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add_all([person, user])
    session.flush()
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="member",
            status="active",
            joined_at=_utc(2020, 1, 1),
        )
    )
    return person, user


def _setup() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """(club_id, admin_user_id, target_person_id, target_user_id) — the sole
    Club, an authorized caller, and a target Person with a User and an
    active ClubMembership."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        _, admin_user = _new_person_with_user(session, club, last_name="Admin")
        target, target_user = _new_person_with_user(session, club)
        session.commit()
        ids = club.id, admin_user.id, target.id, target_user.id
    _grant_admin_permissions(ids[1], ids[0])
    _authenticate_as(ids[1])
    return ids


def _role_id(code: str) -> uuid.UUID:
    with session_scope() as session:
        return session.execute(select(Role.id).where(Role.code == code)).scalar_one()


def _active_rows(user_id: uuid.UUID, role_code: str) -> list[UserRoleAssignment]:
    with session_scope() as session:
        now = datetime.datetime.now(datetime.timezone.utc)
        rows = (
            session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == user_id,
                    UserRoleAssignment.role_id == _role_id(role_code),
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return [r for r in rows if r.valid_to is None or r.valid_to > now]


def _all_rows(user_id: uuid.UUID) -> list[UserRoleAssignment]:
    with session_scope() as session:
        rows = (
            session.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id))
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return list(rows)


def _audit_count(action: str, resource_ids: list[uuid.UUID]) -> int:
    with session_scope() as session:
        return len(
            session.execute(
                select(AuditLog.id).where(
                    AuditLog.action == action, AuditLog.resource_id.in_(resource_ids)
                )
            ).all()
        )


def _add_role(client: TestClient, person_id: uuid.UUID, role_code: str):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/persons/{person_id}/role-assignments",
        json={"role_code": role_code},
        headers=_csrf_headers(client),
    )


def _remove_role(client: TestClient, person_id: uuid.UUID, role_code: str):  # type: ignore[no-untyped-def]
    return client.delete(
        f"/api/v1/persons/{person_id}/role-assignments/{role_code}",
        headers=_csrf_headers(client),
    )


def _insert_legacy_assignment(
    user_id: uuid.UUID,
    role_code: str,
    club_id: uuid.UUID,
    scope_type: str,
    valid_to: datetime.datetime | None = None,
) -> uuid.UUID:
    """A pre-AUTH-2A assignment carrying a legacy `scope_type`."""
    with session_scope() as session:
        row = UserRoleAssignment(
            user_id=user_id,
            role_id=_role_id(role_code),
            scope_type=scope_type,
            club_id=club_id,
            valid_from=_utc(2024, 1, 1),
            valid_to=valid_to,
        )
        session.add(row)
        session.commit()
        return row.id


def _grant_on_baseline_role(role_code: str, permission_code: str, *scope_types: str) -> None:
    """Test-local grant of `permission_code` to a baseline role, carrying
    exactly `scope_types` on that one grant."""
    with session_scope() as session:
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
        session.add(
            RolePermission(
                role_id=role.id,
                permission_id=_permission(session, permission_code).id,
                scopes=[RolePermissionScope(scope_type=scope_type) for scope_type in scope_types],
            )
        )
        session.commit()


def _child_and_stranger(guardian_person_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        child = Person(last_name="Child", first_name="Kid")
        stranger = Person(last_name="Stranger", first_name="S")
        session.add_all([child, stranger])
        session.flush()
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian_person_id,
                child_person_id=child.id,
                relationship_type="parent",
                status="active",
                valid_from=_utc(2020, 1, 1),
            )
        )
        session.commit()
        return child.id, stranger.id


def _wizard_payload(role_code: str, club_id: uuid.UUID) -> dict:
    from app.db.groups import Group

    with session_scope() as session:
        group = Group(club_id=club_id, name="G", status="active", valid_from=_utc(2020, 1, 1))
        child = Person(last_name="Child", first_name="C")
        session.add_all([group, child])
        session.commit()
        group_id, child_id = group.id, child.id
    return {
        "first_name": "Anna",
        "last_name": f"W-{role_code}",
        "email": f"{role_code}-{uuid.uuid4().hex[:6]}@example.com",
        "role_code": role_code,
        "group_ids": [str(group_id)] if role_code == "member" else [],
        "child_person_ids": [str(child_id)] if role_code == "guardian" else [],
    }


# --- 1. One RoleAssignment per role -----------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_person_detail_creates_exactly_one_assignment_per_role(
    client: TestClient, role_code: str
) -> None:
    club_id, _, person_id, user_id = _setup()

    response = _add_role(client, person_id, role_code)

    assert response.status_code == 201, response.text
    assert response.json()["role_code"] == role_code
    rows = _active_rows(user_id, role_code)
    assert len(rows) == 1
    assert rows[0].club_id == club_id
    # Legacy column, fixed value — not a permission scope.
    assert rows[0].scope_type == "all"
    assert str(rows[0].id) == response.json()["id"]
    # One role_assignment.created audit record for the one row (ADR-0026 §6).
    assert _audit_count("role_assignment.created", [rows[0].id]) == 1


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_person_detail_lists_each_role_once(client: TestClient, role_code: str) -> None:
    _, _, person_id, _ = _setup()
    assert _add_role(client, person_id, role_code).status_code == 201

    response = client.get(f"/api/v1/persons/{person_id}/role-assignments")

    assert response.status_code == 200, response.text
    assert [item["role_code"] for item in response.json()["items"]] == [role_code]


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_wizard_creates_the_same_single_assignment(client: TestClient, role_code: str) -> None:
    club_id, _, _, _ = _setup()

    response = client.post(
        "/api/v1/persons/wizard",
        json=_wizard_payload(role_code, club_id),
        headers=_csrf_headers(client),
    )

    assert response.status_code == 201, response.text
    person_id = uuid.UUID(response.json()["person"]["id"])
    with session_scope() as session:
        user_id = session.execute(select(User.id).where(User.person_id == person_id)).scalar_one()
    rows = _active_rows(user_id, role_code)
    assert len(rows) == 1
    assert rows[0].club_id == club_id
    assert rows[0].scope_type == "all"
    assert _audit_count("role_assignment.created", [rows[0].id]) == 1


# --- 2. Duplicate is independent of scope -------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_reassigning_a_role_is_duplicate_and_adds_nothing(
    client: TestClient, role_code: str
) -> None:
    _, _, person_id, user_id = _setup()
    assert _add_role(client, person_id, role_code).status_code == 201
    before = {r.id for r in _all_rows(user_id)}

    response = _add_role(client, person_id, role_code)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_role_assignment"
    assert {r.id for r in _all_rows(user_id)} == before


@requires_postgres
@pytest.mark.parametrize("legacy_scope", LEGACY_SCOPES)
@pytest.mark.parametrize("role_code", ["instructor", "guardian"])
def test_legacy_assignment_of_any_scope_counts_as_duplicate(
    client: TestClient, role_code: str, legacy_scope: str
) -> None:
    club_id, _, person_id, user_id = _setup()
    legacy_id = _insert_legacy_assignment(user_id, role_code, club_id, legacy_scope)

    response = _add_role(client, person_id, role_code)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_role_assignment"
    assert {r.id for r in _all_rows(user_id)} == {legacy_id}


# --- 3. Revoke is independent of scope ----------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_remove_role_revokes_the_one_assignment(client: TestClient, role_code: str) -> None:
    _, _, person_id, user_id = _setup()
    assert _add_role(client, person_id, role_code).status_code == 201
    [created] = _active_rows(user_id, role_code)

    response = _remove_role(client, person_id, role_code)

    assert response.status_code == 204, response.text
    assert _active_rows(user_id, role_code) == []
    # The row is kept (valid_to set), never deleted; one revoked audit.
    assert {r.id for r in _all_rows(user_id)} == {created.id}
    assert _audit_count("role_assignment.revoked", [created.id]) == 1
    # Nothing left to revoke.
    assert _remove_role(client, person_id, role_code).status_code == 404


@requires_postgres
@pytest.mark.parametrize("legacy_scope", LEGACY_SCOPES)
def test_remove_role_revokes_a_legacy_assignment_of_any_scope(
    client: TestClient, legacy_scope: str
) -> None:
    club_id, _, person_id, user_id = _setup()
    _insert_legacy_assignment(user_id, "guardian", club_id, legacy_scope)

    response = _remove_role(client, person_id, "guardian")

    assert response.status_code == 204, response.text
    assert _active_rows(user_id, "guardian") == []


@requires_postgres
def test_revoked_role_stops_granting_every_permission_of_the_role(client: TestClient) -> None:
    club_id, _, person_id, user_id = _setup()
    _grant_on_baseline_role("guardian", "person.read", "children", "self")
    _grant_on_baseline_role("guardian", "person.update", "children", "self")
    child_id, _ = _child_and_stranger(person_id)
    assert _add_role(client, person_id, "guardian").status_code == 201
    assert _remove_role(client, person_id, "guardian").status_code == 204

    _authenticate_as(user_id)
    assert client.get(f"/api/v1/persons/{child_id}").status_code == 404
    assert client.get(f"/api/v1/persons/{person_id}").status_code == 404
    response = client.patch(
        f"/api/v1/persons/{child_id}", json={"phone": "+100"}, headers=_csrf_headers(client)
    )
    assert response.status_code == 404


# --- 4. Atomicity ----------------------------------------------------------------------


def _fail_on_call(monkeypatch, attribute: str, call_number: int) -> None:  # type: ignore[no-untyped-def]
    real = getattr(role_assignment_service, attribute)
    calls = {"n": 0}

    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == call_number:
            raise role_assignment_service.RoleAssignmentClubMembershipMissingError(
                user_id=uuid.uuid4(), club_id=uuid.uuid4()
            )
        return real(*args, **kwargs)

    monkeypatch.setattr(role_assignment_service, attribute, wrapper)


@requires_postgres
def test_role_revoke_of_legacy_rows_is_atomic(client: TestClient, monkeypatch) -> None:
    """A role still effective through two legacy rows (one open-ended, one
    with a future `valid_to`) is revoked as one unit: a failure on the
    second row persists nothing."""
    club_id, _, person_id, user_id = _setup()
    _insert_legacy_assignment(user_id, "guardian", club_id, "children")
    _insert_legacy_assignment(
        user_id, "guardian", club_id, "self", valid_to=_utc(2999, 1, 1)
    )
    _fail_on_call(monkeypatch, "revoke_role_assignment", 2)

    with pytest.raises(role_assignment_service.RoleAssignmentClubMembershipMissingError):
        _remove_role(client, person_id, "guardian")

    assert len(_active_rows(user_id, "guardian")) == 2


@requires_postgres
def test_role_revoke_of_legacy_rows_revokes_all_of_them(client: TestClient) -> None:
    club_id, _, person_id, user_id = _setup()
    _insert_legacy_assignment(user_id, "guardian", club_id, "children")
    _insert_legacy_assignment(
        user_id, "guardian", club_id, "self", valid_to=_utc(2999, 1, 1)
    )

    assert _remove_role(client, person_id, "guardian").status_code == 204
    assert _active_rows(user_id, "guardian") == []


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "guardian"])
def test_wizard_role_assignment_failure_persists_nothing(
    client: TestClient, monkeypatch, role_code: str
) -> None:
    club_id, _, _, _ = _setup()
    payload = _wizard_payload(role_code, club_id)
    _fail_on_call(monkeypatch, "create_role_assignment", 1)

    response = client.post("/api/v1/persons/wizard", json=payload, headers=_csrf_headers(client))

    assert response.status_code == 422, response.text
    with session_scope() as session:
        assert (
            session.execute(select(User).where(User.login_identifier == payload["email"])).first()
            is None
        )
        assert (
            session.execute(select(Person).where(Person.last_name == payload["last_name"])).first()
            is None
        )


# --- 5. Permission-level scopes through the real Person endpoints -------------------


@requires_postgres
def test_guardian_person_read_works_through_both_of_its_scopes(client: TestClient) -> None:
    _, _, person_id, user_id = _setup()
    _grant_on_baseline_role("guardian", "person.read", "children", "self")
    child_id, stranger_id = _child_and_stranger(person_id)
    assert _add_role(client, person_id, "guardian").status_code == 201
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/persons/{child_id}").status_code == 200  # children
    assert client.get(f"/api/v1/persons/{person_id}").status_code == 200  # self
    assert client.get(f"/api/v1/persons/{stranger_id}").status_code == 404


@requires_postgres
def test_guardian_person_update_works_through_both_of_its_scopes(client: TestClient) -> None:
    """person.update with `children` + `self`: a child's allowed fields and
    the guardian's own record are both updatable; field rules unchanged
    (`children` keeps its restricted field set, `self` its own)."""
    _, _, person_id, user_id = _setup()
    _grant_on_baseline_role("guardian", "person.read", "children", "self")
    _grant_on_baseline_role("guardian", "person.update", "children", "self")
    child_id, stranger_id = _child_and_stranger(person_id)
    assert _add_role(client, person_id, "guardian").status_code == 201
    _authenticate_as(user_id)

    def patch(target: uuid.UUID, body: dict) -> int:
        return client.patch(
            f"/api/v1/persons/{target}", json=body, headers=_csrf_headers(client)
        ).status_code

    assert patch(child_id, {"phone": "+111"}) == 200
    assert patch(child_id, {"email": "kid@example.com"}) == 403
    assert patch(person_id, {"phone": "+222"}) == 200
    assert patch(stranger_id, {"phone": "+333"}) == 404


@requires_postgres
def test_scopes_of_one_permission_do_not_leak_into_another(client: TestClient) -> None:
    """Scope escalation through another permission is impossible: the
    guardian's `person.read` reaches the child (`children`), but its
    `person.update` grant carries only `self` — so the child can be read
    and not updated. Under the pre-AUTH-2A model (scope on the
    assignment) the second assignment's `children` scope leaked into every
    permission of the role."""
    _, _, person_id, user_id = _setup()
    _grant_on_baseline_role("guardian", "person.read", "children", "self")
    _grant_on_baseline_role("guardian", "person.update", "self")
    child_id, _ = _child_and_stranger(person_id)
    assert _add_role(client, person_id, "guardian").status_code == 201
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/persons/{child_id}").status_code == 200
    response = client.patch(
        f"/api/v1/persons/{child_id}", json={"phone": "+100"}, headers=_csrf_headers(client)
    )
    assert response.status_code == 404
    response = client.patch(
        f"/api/v1/persons/{person_id}", json={"phone": "+200"}, headers=_csrf_headers(client)
    )
    assert response.status_code == 200


# --- 6. Roles stay independent (UNION) ------------------------------------------------


@requires_postgres
def test_roles_stay_independent_union(client: TestClient) -> None:
    _, _, person_id, user_id = _setup()
    assert _add_role(client, person_id, "member").status_code == 201
    assert _add_role(client, person_id, "guardian").status_code == 201

    listed = client.get(f"/api/v1/persons/{person_id}/role-assignments").json()["items"]
    assert sorted(item["role_code"] for item in listed) == ["guardian", "member"]

    assert _remove_role(client, person_id, "guardian").status_code == 204
    assert len(_active_rows(user_id, "member")) == 1
    assert _active_rows(user_id, "guardian") == []
