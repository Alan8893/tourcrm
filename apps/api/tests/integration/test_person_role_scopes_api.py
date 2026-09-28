"""AUTH-2 — canonical RoleAssignment scopes for Person Detail and the Person
wizard (PO decision, GAP-4 option B):

    admin      -> all
    instructor -> own_groups + self   (two assignments)
    member     -> self
    guardian   -> children + self     (two assignments)

Duplicate detection and revoke consider every active assignment of the role
regardless of scope (historical `all` assignments included, no data
migration); composite roles are created/revoked atomically. The generic
`POST /role-assignments` API is unchanged.

Accepted limitation (pinned below, deliberately not "fixed"): every
permission of a two-assignment role is effective through both scopes.

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
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app
from app.role_assignments import service as role_assignment_service
from app.role_assignments.person_roles import CANONICAL_ROLE_SCOPE_TYPES

from .conftest import requires_postgres

EXPECTED_SCOPES = {
    "admin": {"all"},
    "instructor": {"own_groups", "self"},
    "member": {"self"},
    "guardian": {"children", "self"},
}
ROLES = sorted(EXPECTED_SCOPES)


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
                RolePermission(role_id=role.id, permission_id=_permission(session, code).id)
            )
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all", club_id=club_id)
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


def _insert_legacy_all(user_id: uuid.UUID, role_code: str, club_id: uuid.UUID) -> uuid.UUID:
    """A pre-AUTH-2 assignment: same role, `scope_type='all'`."""
    with session_scope() as session:
        row = UserRoleAssignment(
            user_id=user_id,
            role_id=_role_id(role_code),
            scope_type="all",
            club_id=club_id,
            valid_from=_utc(2024, 1, 1),
        )
        session.add(row)
        session.commit()
        return row.id


# --- 1. Person Detail: canonical scopes ------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_person_detail_assigns_canonical_scopes(client: TestClient, role_code: str) -> None:
    club_id, _, person_id, user_id = _setup()

    response = _add_role(client, person_id, role_code)

    assert response.status_code == 201, response.text
    assert response.json()["role_code"] == role_code
    rows = _active_rows(user_id, role_code)
    assert {r.scope_type for r in rows} == EXPECTED_SCOPES[role_code]
    assert len(rows) == len(EXPECTED_SCOPES[role_code])
    assert all(r.club_id == club_id for r in rows)
    # One role_assignment.created audit record per assignment row (ADR-0026 §6).
    assert _audit_count("role_assignment.created", [r.id for r in rows]) == len(rows)


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_person_detail_lists_each_role_once(client: TestClient, role_code: str) -> None:
    _, _, person_id, _ = _setup()
    assert _add_role(client, person_id, role_code).status_code == 201

    response = client.get(f"/api/v1/persons/{person_id}/role-assignments")

    assert response.status_code == 200, response.text
    assert [item["role_code"] for item in response.json()["items"]] == [role_code]


def test_canonical_scope_table_is_the_po_decision() -> None:
    assert {code: set(scopes) for code, scopes in CANONICAL_ROLE_SCOPE_TYPES.items()} == (
        EXPECTED_SCOPES
    )


# --- 2. Wizard: identical scopes ------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_wizard_assigns_the_same_canonical_scopes(client: TestClient, role_code: str) -> None:
    from app.db.groups import Group

    club_id, _, _, _ = _setup()
    with session_scope() as session:
        group = Group(club_id=club_id, name="G", status="active", valid_from=_utc(2020, 1, 1))
        child = Person(last_name="Child", first_name="C")
        session.add_all([group, child])
        session.commit()
        group_id, child_id = group.id, child.id
    payload: dict = {
        "first_name": "Anna",
        "last_name": f"W-{role_code}",
        "email": f"{role_code}-{uuid.uuid4().hex[:6]}@example.com",
        "role_code": role_code,
        "group_ids": [str(group_id)] if role_code == "member" else [],
        "child_person_ids": [str(child_id)] if role_code == "guardian" else [],
    }

    response = client.post("/api/v1/persons/wizard", json=payload, headers=_csrf_headers(client))

    assert response.status_code == 201, response.text
    person_id = uuid.UUID(response.json()["person"]["id"])
    with session_scope() as session:
        user_id = session.execute(select(User.id).where(User.person_id == person_id)).scalar_one()
    rows = _active_rows(user_id, role_code)
    assert {r.scope_type for r in rows} == EXPECTED_SCOPES[role_code]
    assert len(rows) == len(EXPECTED_SCOPES[role_code])


# --- 3. Duplicate --------------------------------------------------------------------


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
@pytest.mark.parametrize("role_code", ["instructor", "member", "guardian"])
def test_legacy_all_assignment_counts_as_duplicate(client: TestClient, role_code: str) -> None:
    club_id, _, person_id, user_id = _setup()
    legacy_id = _insert_legacy_all(user_id, role_code, club_id)

    response = _add_role(client, person_id, role_code)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_role_assignment"
    assert {r.id for r in _all_rows(user_id)} == {legacy_id}


# --- 4. Revoke -----------------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("role_code", ROLES)
def test_remove_role_revokes_every_assignment_of_the_role(
    client: TestClient, role_code: str
) -> None:
    _, _, person_id, user_id = _setup()
    assert _add_role(client, person_id, role_code).status_code == 201
    created = _active_rows(user_id, role_code)

    response = _remove_role(client, person_id, role_code)

    assert response.status_code == 204, response.text
    assert _active_rows(user_id, role_code) == []
    # Rows are kept (valid_to set), never deleted; one revoked audit per row.
    assert {r.id for r in _all_rows(user_id)} == {r.id for r in created}
    assert _audit_count("role_assignment.revoked", [r.id for r in created]) == len(created)
    # Nothing left to revoke.
    assert _remove_role(client, person_id, role_code).status_code == 404


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "member", "guardian"])
def test_remove_role_revokes_legacy_all_assignment(client: TestClient, role_code: str) -> None:
    club_id, _, person_id, user_id = _setup()
    _insert_legacy_all(user_id, role_code, club_id)

    response = _remove_role(client, person_id, role_code)

    assert response.status_code == 204, response.text
    assert _active_rows(user_id, role_code) == []


# --- 5. Atomicity -----------------------------------------------------------------------


def _fail_on_second_call(monkeypatch, attribute: str) -> None:  # type: ignore[no-untyped-def]
    real = getattr(role_assignment_service, attribute)
    calls = {"n": 0}

    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise role_assignment_service.RoleAssignmentClubMembershipMissingError(
                user_id=uuid.uuid4(), club_id=uuid.uuid4()
            )
        return real(*args, **kwargs)

    monkeypatch.setattr(role_assignment_service, attribute, wrapper)


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "guardian"])
def test_composite_role_creation_is_atomic(client: TestClient, monkeypatch, role_code: str) -> None:
    _, _, person_id, user_id = _setup()
    _fail_on_second_call(monkeypatch, "create_role_assignment")

    response = _add_role(client, person_id, role_code)

    assert response.status_code == 422, response.text
    assert _all_rows(user_id) == []


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "guardian"])
def test_composite_role_revoke_is_atomic(client: TestClient, monkeypatch, role_code: str) -> None:
    _, _, person_id, user_id = _setup()
    assert _add_role(client, person_id, role_code).status_code == 201
    _fail_on_second_call(monkeypatch, "revoke_role_assignment")

    with pytest.raises(role_assignment_service.RoleAssignmentClubMembershipMissingError):
        _remove_role(client, person_id, role_code)

    assert len(_active_rows(user_id, role_code)) == 2


@requires_postgres
def test_wizard_composite_role_creation_is_atomic(client: TestClient, monkeypatch) -> None:
    _setup()
    _fail_on_second_call(monkeypatch, "create_role_assignment")
    email = f"instr-{uuid.uuid4().hex[:6]}@example.com"

    response = client.post(
        "/api/v1/persons/wizard",
        json={
            "first_name": "Ivan",
            "last_name": "Atomic",
            "email": email,
            "role_code": "instructor",
            "group_ids": [],
            "child_person_ids": [],
        },
        headers=_csrf_headers(client),
    )

    assert response.status_code == 422, response.text
    with session_scope() as session:
        assert session.execute(select(User).where(User.login_identifier == email)).first() is None
        assert session.execute(select(Person).where(Person.last_name == "Atomic")).first() is None


# --- 6. Accepted limitation: every permission works through both scopes -----------


@requires_postgres
def test_composite_role_permissions_apply_through_both_scopes(client: TestClient) -> None:
    """PO-accepted limitation of the current model (scope lives on the
    assignment, not on RolePermission): a guardian's `person.read` grant is
    effective through `children` AND `self`. Pinned on purpose — do not
    "fix" here."""
    club_id, _, person_id, user_id = _setup()
    with session_scope() as session:
        guardian_role = session.execute(select(Role).where(Role.code == "guardian")).scalar_one()
        session.add(
            RolePermission(
                role_id=guardian_role.id, permission_id=_permission(session, "person.read").id
            )
        )
        child = Person(last_name="Child", first_name="Kid")
        stranger = Person(last_name="Stranger", first_name="S")
        session.add_all([child, stranger])
        session.flush()
        session.add(
            GuardianRelationship(
                guardian_person_id=person_id,
                child_person_id=child.id,
                relationship_type="parent",
                status="active",
                valid_from=_utc(2020, 1, 1),
            )
        )
        session.commit()
        child_id, stranger_id = child.id, stranger.id
    assert _add_role(client, person_id, "guardian").status_code == 201
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/persons/{child_id}").status_code == 200  # children
    assert client.get(f"/api/v1/persons/{person_id}").status_code == 200  # self
    assert client.get(f"/api/v1/persons/{stranger_id}").status_code == 404


# --- 7. Generic API unchanged; UNION of roles intact ----------------------------------


@requires_postgres
def test_generic_role_assignment_api_still_uses_caller_scope(client: TestClient) -> None:
    club_id, _, _, user_id = _setup()

    response = client.post(
        "/api/v1/role-assignments",
        json={
            "user_id": str(user_id),
            "role_id": str(_role_id("member")),
            "scope_type": "all",
            "club_id": str(club_id),
        },
        headers=_csrf_headers(client),
    )

    assert response.status_code == 201, response.text
    assert response.json()["scope_type"] == "all"
    assert [r.scope_type for r in _active_rows(user_id, "member")] == ["all"]


@requires_postgres
def test_roles_stay_independent_union(client: TestClient) -> None:
    _, _, person_id, user_id = _setup()
    assert _add_role(client, person_id, "member").status_code == 201
    assert _add_role(client, person_id, "guardian").status_code == 201

    listed = client.get(f"/api/v1/persons/{person_id}/role-assignments").json()["items"]
    assert sorted(item["role_code"] for item in listed) == ["guardian", "member"]

    assert _remove_role(client, person_id, "guardian").status_code == 204
    assert [r.scope_type for r in _active_rows(user_id, "member")] == ["self"]
    assert _active_rows(user_id, "guardian") == []
