"""Backend authorization boundary regression tests (Issue #184).

Issue #184 started from a frontend observation: a Guardian deep-linking to
`/people` still gets the People page rendered. Hidden navigation is not a
security boundary (ADR-0005, auth-and-authorization.md §11/§24) — what
matters is what the backend returns when that page (or any client) calls
the API directly. These tests pin that behavior against the REAL shipped
app and a real PostgreSQL database:

- every protected route answers an anonymous request with 401 (the route
  set comes from the live route table, shared with
  tests/api/test_authorization_boundary.py — never a hand-kept list);
- a Guardian without People/Group permissions gets no Person/Group rows,
  counts or objects, and cannot mutate anything (no row, no audit entry);
- a revoked RoleAssignment stops granting access on the very next request.

Every scenario creates the RolePermission/UserRoleAssignment rows it needs
explicitly; nothing relies on role names or frontend behavior.

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.groups import Group
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from ..api.test_authorization_boundary import protected_routes
from .conftest import requires_postgres

_CSRF_TOKEN = "test-csrf-token"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": "Ivanova",
        "first_name": f"P-{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_user(person: Person) -> User:
    return User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )


def _make_club_membership(club: Club, person: Person) -> ClubMembership:
    return ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="member",
        status="active",
        joined_at=_utc(2020, 1, 1),
    )


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    *,
    scope_type: str,
    club_id: uuid.UUID | None,
) -> None:
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one()
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


def _assign_canonical_admin_role(
    user_id: uuid.UUID, club_id: uuid.UUID, *, valid_to: datetime.datetime | None = None
) -> None:
    with session_scope() as session:
        admin_role = session.execute(
            select(Role).where(Role.code == "admin", Role.is_system.is_(True))
        ).scalar_one()
        session.add(
            UserRoleAssignment(
                user_id=user_id,
                role_id=admin_role.id,
                scope_type="all",
                club_id=club_id,
                valid_from=_utcnow() - datetime.timedelta(days=30),
                valid_to=valid_to,
            )
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", _CSRF_TOKEN)
    return {"X-CSRF-Token": _CSRF_TOKEN}


def _count(model: type) -> int:
    with session_scope() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- anonymous -> 401 on every protected route -----------------------------


def _concrete_path(path: str) -> str:
    # `{role_code}` is the only non-UUID path parameter in the route table.
    path = path.replace("{role_code}", "admin")
    return re.sub(r"\{[^}]+\}", lambda _match: str(uuid.uuid4()), path)


@requires_postgres
@pytest.mark.parametrize(("method", "path"), protected_routes())
def test_anonymous_request_to_protected_route_is_401_and_mutates_nothing(
    client: TestClient, method: str, path: str
) -> None:
    """auth-and-authorization.md §18: unauthenticated -> 401. A valid CSRF
    pair is sent on purpose, so the only thing this request lacks is a
    session — the 401 comes from authentication, not from CSRF or body
    validation, and no audit row is ever written for it."""
    audit_rows_before = _count(AuditLog)

    response = client.request(method, _concrete_path(path), headers=_csrf_headers(client), json={})

    assert response.status_code == 401, response.text
    body = response.json()["error"]
    assert body["code"] == "unauthorized"
    assert body["details"] == {}
    assert _count(AuditLog) == audit_rows_before


# --- Issue #184: Guardian calling People/Group APIs directly ---------------


@requires_postgres
def test_guardian_direct_api_access_gets_no_people_or_group_data_and_cannot_mutate(
    client: TestClient,
) -> None:
    """The deep-link scenario from Issue #184, at the API boundary.

    The Guardian holds exactly what the canonical role matrix gives them
    for their children (`guardian_relationship.read`, `children` scope) and
    nothing for People/Groups. Direct API calls must yield no Person/Group
    rows or counts, existence-hidden objects, and no mutation or audit —
    while the Guardian's own legitimate endpoint (`/me/children`) keeps
    working."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        guardian = _make_person(first_name="Guardian")
        child = _make_person(first_name="Child", phone="+70000000000")
        unrelated = _make_person(first_name="Unrelated")
        session.add_all([club, guardian, child, unrelated])
        session.commit()
        guardian_user = _make_user(guardian)
        group = Group(club_id=club.id, name="Group", status="active", valid_from=_utc(2020, 1, 1))
        session.add_all(
            [
                guardian_user,
                group,
                _make_club_membership(club, guardian),
                _make_club_membership(club, child),
                _make_club_membership(club, unrelated),
                GuardianRelationship(
                    guardian_person_id=guardian.id,
                    child_person_id=child.id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_utc(2020, 1, 1),
                ),
            ]
        )
        session.commit()
        club_id, guardian_user_id = club.id, guardian_user.id
        child_id, unrelated_id, group_id = child.id, unrelated.id, group.id
    _grant_permission(
        guardian_user_id, "guardian_relationship.read", scope_type="children", club_id=None
    )
    _authenticate_as(guardian_user_id)

    # Collections: no rows and no count leak, even when a filter is supplied.
    for url, params in (
        ("/api/v1/persons", {}),
        ("/api/v1/persons", {"search": "Child"}),
        ("/api/v1/persons", {"club_id": str(club_id)}),
        ("/api/v1/groups", {}),
        ("/api/v1/memberships", {"person_id": str(child_id)}),
    ):
        response = client.get(url, params=params)
        assert response.status_code == 200, (url, response.text)
        assert response.json()["items"] == [], url
        assert response.json()["pagination"]["total"] == 0, url

    # Items: the child, an unrelated Person and a random UUID are
    # indistinguishable — knowing a UUID grants nothing.
    missing_person = client.get(f"/api/v1/persons/{uuid.uuid4()}")
    for person_id in (child_id, unrelated_id):
        response = client.get(f"/api/v1/persons/{person_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == missing_person.json()["error"]["code"]
        assert response.json()["error"]["message"] == missing_person.json()["error"]["message"]
    assert client.get(f"/api/v1/groups/{group_id}").status_code == 404
    assert client.get("/api/v1/users").status_code == 403

    # Mutations: denied before any write — no new row, no changed row, no audit.
    persons_before, groups_before = _count(Person), _count(Group)
    audit_before = _count(AuditLog)

    created_person = client.post(
        "/api/v1/persons",
        json={"first_name": "New", "last_name": "Person"},
        headers=_csrf_headers(client),
    )
    assert created_person.status_code == 403
    created_group = client.post(
        "/api/v1/groups",
        json={"club_id": str(club_id), "name": "Hijack", "valid_from": "2026-01-01T00:00:00Z"},
        headers=_csrf_headers(client),
    )
    assert created_group.status_code == 403
    patched_child = client.patch(
        f"/api/v1/persons/{child_id}",
        json={"first_name": "Changed"},
        headers=_csrf_headers(client),
    )
    assert patched_child.status_code == 404

    assert _count(Person) == persons_before
    assert _count(Group) == groups_before
    assert _count(AuditLog) == audit_before
    with session_scope() as session:
        assert session.get(Person, child_id).first_name == "Child"  # type: ignore[union-attr]

    # Positive control: the Guardian's own canonical projection still works
    # and never includes the child's contact fields.
    children = client.get("/api/v1/me/children")
    assert children.status_code == 200, children.text
    assert [item["id"] for item in children.json()["items"]] == [str(child_id)]
    assert "phone" not in children.json()["items"][0]


# --- revoked RoleAssignment stops granting on the next request -------------


@requires_postgres
def test_revoked_admin_assignment_loses_people_and_group_access_immediately(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        admin_person = _make_person()
        target = _make_person()
        session.add_all([club, admin_person, target])
        session.commit()
        active_admin = _make_user(admin_person)
        revoked_person = _make_person()
        session.add(revoked_person)
        session.commit()
        revoked_admin = _make_user(revoked_person)
        session.add_all([active_admin, revoked_admin, _make_club_membership(club, target)])
        session.commit()
        club_id, target_id = club.id, target.id
        active_admin_id, revoked_admin_id = active_admin.id, revoked_admin.id
    _assign_canonical_admin_role(active_admin_id, club_id)
    _assign_canonical_admin_role(
        revoked_admin_id, club_id, valid_to=_utcnow() - datetime.timedelta(seconds=1)
    )

    # Positive control: the identical, still-effective assignment is allowed.
    _authenticate_as(active_admin_id)
    assert client.get(f"/api/v1/persons/{target_id}").status_code == 200
    assert client.get("/api/v1/persons").json()["pagination"]["total"] >= 1

    _authenticate_as(revoked_admin_id)
    assert client.get(f"/api/v1/persons/{target_id}").status_code == 404
    listed = client.get("/api/v1/persons")
    assert listed.json()["items"] == []
    assert listed.json()["pagination"]["total"] == 0
    groups_before = _count(Group)
    denied = client.post(
        "/api/v1/groups",
        json={
            "club_id": str(club_id),
            "name": "After revoke",
            "valid_from": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert denied.status_code == 403
    assert _count(Group) == groups_before
