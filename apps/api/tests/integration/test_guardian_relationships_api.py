"""HTTP-level integration tests for /api/v1/persons/{id}/guardian-
relationships, /api/v1/guardian-relationships/{id}[/terminate], and
/api/v1/me/children (Issue #64): deterministic backend authorization,
canonical scope resolution (`all`/`self`/`children`/`none`), IDOR
regression coverage, Club-neutrality, read-time `inactive` derivation,
`terminate`-always-`revoked` semantics, and audit recording.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_people_api.py's pattern.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ----------------------------------------------------


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


def _make_guardian_relationship(
    guardian: Person, child: Person, **overrides: object
) -> GuardianRelationship:
    defaults: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "is_primary_contact": False,
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GuardianRelationship(**defaults)  # type: ignore[arg-type]


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


def _latest_audit_row(*, action: str, resource_id: uuid.UUID) -> AuditLog | None:
    with session_scope() as session:
        return (
            session.execute(
                select(AuditLog)
                .where(AuditLog.action == action, AuditLog.resource_id == resource_id)
                .order_by(AuditLog.occurred_at.desc())
            )
            .scalars()
            .first()
        )


def _make_guardian_child_requester(session):
    """Commit a guardian Person+User, a child Person, and a third
    requester Person+User, returning (guardian, guardian_user, child,
    requester_user)."""
    guardian = _make_person(first_name="Guardian")
    guardian_user = _make_user(guardian)
    child = _make_person(first_name="Child")
    requester = _make_person(first_name="Requester")
    requester_user = _make_user(requester)
    session.add_all([guardian, guardian_user, child, requester, requester_user])
    session.commit()
    return guardian, guardian_user, child, requester_user


# --- GET /persons/{person_id}/guardian-relationships ---------------------


@requires_postgres
def test_get_guardian_relationships_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        child_id, requester_user_id = child.id, requester_user.id
        relationship_id = relationship.id
    _grant_permission(requester_user_id, "guardian_relationship.read", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(relationship_id) in ids


@requires_postgres
def test_get_guardian_relationships_without_permission_returns_empty(client: TestClient) -> None:
    """List endpoints never 403 — an unauthorized/uninvolved requester
    simply sees an empty (filtered) list, matching Person/Membership.
    """
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        child_id, requester_user_id = child.id, requester_user.id
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_get_guardian_relationships_none_scope_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        child_id, requester_user_id = child.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.read", scope_type="none")
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@requires_postgres
def test_get_guardian_relationships_self_scope_sees_own_guardians(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        child_user = _make_user(child)
        session.add(child_user)
        session.commit()
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        child_id, child_user_id = child.id, child_user.id
        relationship_id = relationship.id
    _grant_permission(child_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(child_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(relationship_id) in ids


@requires_postgres
def test_get_guardian_relationships_self_scope_denies_other_persons_guardians(
    client: TestClient,
) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        other_person = _make_person(first_name="Other")
        other_user = _make_user(other_person)
        session.add_all([other_person, other_user])
        session.commit()
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        child_id, other_user_id = child.id, other_user.id
    _grant_permission(other_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(other_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@requires_postgres
def test_get_guardian_relationships_children_scope_sees_co_guardians(client: TestClient) -> None:
    """A co-guardian (someone who is ALSO an active guardian of the same
    child, under a different relationship_type/row) can see the child's
    full guardian-relationships list via the `children` scope.
    """
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        co_guardian = _make_person(first_name="CoGuardian")
        co_guardian_user = _make_user(co_guardian)
        session.add_all([co_guardian, co_guardian_user])
        session.commit()
        main_relationship = _make_guardian_relationship(guardian, child, relationship_type="parent")
        co_relationship = _make_guardian_relationship(
            co_guardian, child, relationship_type="grandparent"
        )
        session.add_all([main_relationship, co_relationship])
        session.commit()
        child_id, co_guardian_user_id = child.id, co_guardian_user.id
        main_relationship_id = main_relationship.id
    _grant_permission(co_guardian_user_id, "guardian_relationship.read", scope_type="children")
    _authenticate_as(co_guardian_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(main_relationship_id) in ids


@requires_postgres
def test_get_guardian_relationships_children_scope_denies_unrelated_child(
    client: TestClient,
) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        unrelated_child = _make_person(first_name="Unrelated")
        session.add(unrelated_child)
        session.commit()
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        unrelated_child_id, guardian_user_id = unrelated_child.id, guardian_user.id
    _grant_permission(guardian_user_id, "guardian_relationship.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/persons/{unrelated_child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@requires_postgres
def test_get_guardian_relationships_nonexistent_person_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        requester = _make_person()
        requester_user = _make_user(requester)
        session.add_all([requester, requester_user])
        session.commit()
        requester_user_id = requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.read", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{uuid.uuid4()}/guardian-relationships")
    assert response.status_code == 404, response.text


@requires_postgres
def test_get_guardian_relationships_expired_relationship_shown_as_inactive(
    client: TestClient,
) -> None:
    """An expired (stored `active`, `valid_to` in the past) relationship
    is still listed (history is preserved) but reported as `inactive` via
    read-time derivation, never as `active`.
    """
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(
            guardian,
            child,
            valid_from=_utc(2020, 1, 1),
            valid_to=_utc(2021, 1, 1),
        )
        session.add(relationship)
        session.commit()
        child_id, requester_user_id = child.id, requester_user.id
        relationship_id = relationship.id
    _grant_permission(requester_user_id, "guardian_relationship.read", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[str(relationship_id)]["status"] == "inactive"

    with session_scope() as session:
        stored = session.get(GuardianRelationship, relationship_id)
        assert stored.status == "active"  # never rewritten


@requires_postgres
def test_get_guardian_relationships_club_scoped_grant_never_matches(client: TestClient) -> None:
    """GuardianRelationship is Club-neutral: a club-scoped assignment of
    any scope_type never authorizes access to it, unlike Person's
    explicit club-scoped-`all` override (Issue #64 defines no analogous
    override for GuardianRelationship).
    """
    with session_scope() as session:
        club = _make_club()
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        session.add(club)
        session.commit()
        session.add(_make_club_membership(club, child))
        session.add(_make_club_membership(club, guardian))
        session.commit()
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        child_id, requester_user_id, club_id = child.id, requester_user.id, club.id
    _grant_permission(
        requester_user_id, "guardian_relationship.read", scope_type="all", club_id=club_id
    )
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@requires_postgres
def test_get_guardian_relationships_cross_club_guardian_and_child_unaffected(
    client: TestClient,
) -> None:
    """Guardian and child having ClubMemberships in *different* Clubs
    does not affect global `all`-scope visibility — GuardianRelationship
    never depends on either side's Club at all.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        session.add_all([club_a, club_b])
        session.commit()
        session.add(_make_club_membership(club_a, guardian))
        session.add(_make_club_membership(club_b, child))
        session.commit()
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        child_id, requester_user_id = child.id, requester_user.id
        relationship_id = relationship.id
    _grant_permission(requester_user_id, "guardian_relationship.read", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/guardian-relationships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(relationship_id) in ids


# --- POST /persons/{person_id}/guardian-relationships ---------------------


@requires_postgres
def test_create_guardian_relationship_succeeds_and_audits(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        child_id, guardian_id, requester_user_id = child.id, guardian.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={
            "guardian_person_id": str(guardian_id),
            "relationship_type": "parent",
            "is_primary_contact": True,
            "status": "active",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["guardian_person_id"] == str(guardian_id)
    assert body["child_person_id"] == str(child_id)
    assert body["status"] == "active"
    assert body["is_primary_contact"] is True

    audit_row = _latest_audit_row(
        action="guardian_relationship.created", resource_id=uuid.UUID(body["id"])
    )
    assert audit_row is not None
    assert audit_row.actor_user_id == requester_user_id
    assert audit_row.resource_type == "guardian_relationship"
    assert audit_row.outcome == "success"


@requires_postgres
def test_create_guardian_relationship_self_link_rejected(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        requester_user = _make_user(person)
        session.add_all([person, requester_user])
        session.commit()
        person_id, requester_user_id = person.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{person_id}/guardian-relationships",
        json={"guardian_person_id": str(person_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "guardian_link_not_allowed"


@requires_postgres
def test_create_guardian_relationship_duplicate_active_returns_422(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        session.add(_make_guardian_relationship(guardian, child, relationship_type="parent"))
        session.commit()
        child_id, guardian_id, requester_user_id = child.id, guardian.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={"guardian_person_id": str(guardian_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "guardian_link_not_allowed"


@requires_postgres
def test_create_guardian_relationship_duplicate_primary_contact_returns_422(
    client: TestClient,
) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        other_guardian = _make_person(first_name="OtherGuardian")
        session.add(other_guardian)
        session.commit()
        session.add(
            _make_guardian_relationship(
                guardian, child, relationship_type="parent", is_primary_contact=True
            )
        )
        session.commit()
        child_id, other_guardian_id, requester_user_id = (
            child.id,
            other_guardian.id,
            requester_user.id,
        )
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={
            "guardian_person_id": str(other_guardian_id),
            "relationship_type": "grandparent",
            "is_primary_contact": True,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "guardian_link_not_allowed"


@requires_postgres
def test_create_guardian_relationship_rejects_non_active_status(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        child_id, guardian_id, requester_user_id = child.id, guardian.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={
            "guardian_person_id": str(guardian_id),
            "relationship_type": "parent",
            "status": "pending",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text


@requires_postgres
def test_create_guardian_relationship_missing_guardian_person_returns_422(
    client: TestClient,
) -> None:
    with session_scope() as session:
        child = _make_person()
        requester_user = _make_user(child)
        session.add_all([child, requester_user])
        session.commit()
        child_id, requester_user_id = child.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={"guardian_person_id": str(uuid.uuid4()), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_person_id"


@requires_postgres
def test_create_guardian_relationship_missing_child_person_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        guardian = _make_person()
        requester_user = _make_user(guardian)
        session.add_all([guardian, requester_user])
        session.commit()
        guardian_id, requester_user_id = guardian.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{uuid.uuid4()}/guardian-relationships",
        json={"guardian_person_id": str(guardian_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


@requires_postgres
def test_create_guardian_relationship_without_permission_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        child_id, guardian_id, requester_user_id = child.id, guardian.id, requester_user.id
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={"guardian_person_id": str(guardian_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_create_guardian_relationship_club_scoped_grant_is_forbidden(client: TestClient) -> None:
    """Cross-club misuse guard: a club-scoped `guardian_relationship.manage`
    assignment must never authorize creation — GuardianRelationship is
    Club-neutral and defines no club-scoped override (unlike Person)."""
    with session_scope() as session:
        club = _make_club()
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        session.add(club)
        session.commit()
        club_id = club.id
        child_id, guardian_id, requester_user_id = child.id, guardian.id, requester_user.id
    _grant_permission(
        requester_user_id, "guardian_relationship.manage", scope_type="all", club_id=club_id
    )
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={"guardian_person_id": str(guardian_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_create_guardian_relationship_without_any_club_membership_succeeds(
    client: TestClient,
) -> None:
    """GuardianRelationship creation does not depend on either party
    having any ClubMembership at all — proving Club-neutrality, not just
    Club-independence."""
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        child_id, guardian_id, requester_user_id = child.id, guardian.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/persons/{child_id}/guardian-relationships",
        json={"guardian_person_id": str(guardian_id), "relationship_type": "parent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text


# --- PATCH /guardian-relationships/{relationship_id} ----------------------


@requires_postgres
def test_patch_guardian_relationship_type_succeeds_and_audits(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child, relationship_type="parent")
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["relationship_type"] == "grandparent"

    audit_row = _latest_audit_row(
        action="guardian_relationship.updated", resource_id=relationship_id
    )
    assert audit_row is not None
    assert audit_row.details["changes"]["relationship_type"] == {
        "from": "parent",
        "to": "grandparent",
    }


@requires_postgres
def test_patch_guardian_relationship_primary_contact_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child, is_primary_contact=False)
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"is_primary_contact": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_primary_contact"] is True


@requires_postgres
def test_patch_guardian_relationship_both_fields_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(
            guardian, child, relationship_type="parent", is_primary_contact=False
        )
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"relationship_type": "aunt_uncle", "is_primary_contact": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["relationship_type"] == "aunt_uncle"
    assert body["is_primary_contact"] is True


@requires_postgres
def test_patch_guardian_relationship_cannot_change_status(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child, status="active")
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"relationship_type": "parent", "status": "revoked"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"

    with session_scope() as session:
        stored = session.get(GuardianRelationship, relationship_id)
        assert stored.status == "active"


@requires_postgres
def test_patch_guardian_relationship_unauthorized_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _authenticate_as(requester_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "guardian_relationship_not_found"


@requires_postgres
def test_patch_nonexistent_guardian_relationship_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        requester = _make_person()
        requester_user = _make_user(requester)
        session.add_all([requester, requester_user])
        session.commit()
        requester_user_id = requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{uuid.uuid4()}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "guardian_relationship_not_found"


@requires_postgres
def test_patch_guardian_relationship_idor_unrelated_self_scope_returns_404(
    client: TestClient,
) -> None:
    """`self` scope only lets a party to the relationship (either side)
    manage it — an unrelated, merely-authenticated guardian must not."""
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        unrelated_person = _make_person(first_name="Unrelated")
        unrelated_user = _make_user(unrelated_person)
        session.add_all([unrelated_person, unrelated_user])
        session.commit()
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        relationship_id, unrelated_user_id = relationship.id, unrelated_user.id
    _grant_permission(unrelated_user_id, "guardian_relationship.manage", scope_type="self")
    _authenticate_as(unrelated_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "guardian_relationship_not_found"
    with session_scope() as session:
        victim = session.get(GuardianRelationship, relationship_id)
        assert victim.relationship_type != "grandparent"


@requires_postgres
def test_patch_guardian_relationship_missing_and_unauthorized_are_indistinguishable(
    client: TestClient,
) -> None:
    """Existence-hiding: a PATCH on a relationship that does not exist and
    one that exists but the requester cannot access must be identical
    from the public API's point of view — same status, same code, same
    message (Issue #64 error-contract fix)."""
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        denied_person = _make_person(first_name="Denied")
        denied_user = _make_user(denied_person)
        session.add_all([denied_person, denied_user])
        session.commit()
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        existing_id, denied_user_id = relationship.id, denied_user.id
    _grant_permission(denied_user_id, "guardian_relationship.manage", scope_type="none")
    _authenticate_as(denied_user_id)

    missing_response = client.patch(
        f"/api/v1/guardian-relationships/{uuid.uuid4()}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    denied_response = client.patch(
        f"/api/v1/guardian-relationships/{existing_id}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    assert missing_response.status_code == denied_response.status_code == 404
    assert (
        missing_response.json()["error"]["code"]
        == denied_response.json()["error"]["code"]
        == "guardian_relationship_not_found"
    )
    assert missing_response.json()["error"]["message"] == denied_response.json()["error"]["message"]


@requires_postgres
def test_patch_guardian_relationship_party_guardian_self_side_succeeds(
    client: TestClient,
) -> None:
    """The guardian side is a "party to the relationship" too (Issue #64
    §13), authorized via `children` scope (guardian's own active link)."""
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        relationship_id, guardian_user_id = relationship.id, guardian_user.id
    _grant_permission(guardian_user_id, "guardian_relationship.manage", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.patch(
        f"/api/v1/guardian-relationships/{relationship_id}",
        json={"relationship_type": "grandparent"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text


# --- POST /guardian-relationships/{relationship_id}/terminate -------------


@requires_postgres
def test_terminate_guardian_relationship_active_to_revoked(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child, status="active")
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/guardian-relationships/{relationship_id}/terminate",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "revoked"

    audit_row = _latest_audit_row(
        action="guardian_relationship.revoked", resource_id=relationship_id
    )
    assert audit_row is not None
    assert audit_row.details["changes"]["status"] == {"from": "active", "to": "revoked"}


@requires_postgres
def test_terminate_guardian_relationship_never_yields_inactive(client: TestClient) -> None:
    """Even an already-expired (effectively `inactive`) relationship
    terminates to `revoked`, never `inactive` — ADR-0025 §3: no
    alternative outcome."""
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(
            guardian, child, valid_from=_utc(2020, 1, 1), valid_to=_utc(2021, 1, 1)
        )
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/guardian-relationships/{relationship_id}/terminate",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "revoked"


@requires_postgres
def test_terminate_already_revoked_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child, status="revoked")
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/guardian-relationships/{relationship_id}/terminate",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "guardian_link_not_allowed"


@requires_postgres
def test_terminate_guardian_relationship_without_permission_returns_404(
    client: TestClient,
) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, requester_user = _make_guardian_child_requester(session)
        relationship = _make_guardian_relationship(guardian, child)
        session.add(relationship)
        session.commit()
        relationship_id, requester_user_id = relationship.id, requester_user.id
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/guardian-relationships/{relationship_id}/terminate",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "guardian_relationship_not_found"


@requires_postgres
def test_terminate_nonexistent_guardian_relationship_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        requester = _make_person()
        requester_user = _make_user(requester)
        session.add_all([requester, requester_user])
        session.commit()
        requester_user_id = requester_user.id
    _grant_permission(requester_user_id, "guardian_relationship.manage", scope_type="all")
    _authenticate_as(requester_user_id)

    response = client.post(
        f"/api/v1/guardian-relationships/{uuid.uuid4()}/terminate",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "guardian_relationship_not_found"


@requires_postgres
def test_terminate_guardian_relationship_idor_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        unrelated_person = _make_person(first_name="Unrelated")
        unrelated_user = _make_user(unrelated_person)
        session.add_all([unrelated_person, unrelated_user])
        session.commit()
        relationship = _make_guardian_relationship(guardian, child, status="active")
        session.add(relationship)
        session.commit()
        relationship_id, unrelated_user_id = relationship.id, unrelated_user.id
    _grant_permission(unrelated_user_id, "guardian_relationship.manage", scope_type="self")
    _authenticate_as(unrelated_user_id)

    response = client.post(
        f"/api/v1/guardian-relationships/{relationship_id}/terminate",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "guardian_relationship_not_found"
    with session_scope() as session:
        victim = session.get(GuardianRelationship, relationship_id)
        assert victim.status == "active"


@requires_postgres
def test_terminate_guardian_relationship_missing_and_unauthorized_are_indistinguishable(
    client: TestClient,
) -> None:
    """Terminate-side equivalent of the PATCH existence-hiding test above."""
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        denied_person = _make_person(first_name="Denied")
        denied_user = _make_user(denied_person)
        session.add_all([denied_person, denied_user])
        session.commit()
        relationship = _make_guardian_relationship(guardian, child, status="active")
        session.add(relationship)
        session.commit()
        existing_id, denied_user_id = relationship.id, denied_user.id
    _grant_permission(denied_user_id, "guardian_relationship.manage", scope_type="none")
    _authenticate_as(denied_user_id)

    missing_response = client.post(
        f"/api/v1/guardian-relationships/{uuid.uuid4()}/terminate",
        headers=_csrf_headers(client),
    )
    denied_response = client.post(
        f"/api/v1/guardian-relationships/{existing_id}/terminate",
        headers=_csrf_headers(client),
    )
    assert missing_response.status_code == denied_response.status_code == 404
    assert (
        missing_response.json()["error"]["code"]
        == denied_response.json()["error"]["code"]
        == "guardian_relationship_not_found"
    )
    assert missing_response.json()["error"]["message"] == denied_response.json()["error"]["message"]


# --- GET /me/children ------------------------------------------------------


@requires_postgres
def test_me_children_authenticated_guardian_sees_own_children(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        guardian_user_id, child_id = guardian_user.id, child.id
    _grant_permission(guardian_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/me/children")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(child_id) in ids
    for item in response.json()["items"]:
        assert set(item.keys()) == {"id", "full_name", "birth_date", "photo_file_id"}


@requires_postgres
def test_me_children_never_substitutes_client_supplied_id(client: TestClient) -> None:
    """No query/path parameter can redirect /me/children to a different
    guardian's children — verified by asserting an unrelated guardian's
    children never leak into the requester's own response regardless of
    what's passed."""
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        other_guardian = _make_person(first_name="OtherGuardian")
        other_guardian_user = _make_user(other_guardian)
        other_child = _make_person(first_name="OtherChild")
        session.add_all([other_guardian, other_guardian_user, other_child])
        session.commit()
        session.add(_make_guardian_relationship(guardian, child))
        session.add(_make_guardian_relationship(other_guardian, other_child))
        session.commit()
        guardian_user_id, other_child_id = guardian_user.id, other_child.id
    _grant_permission(guardian_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(guardian_user_id)

    response = client.get(
        "/api/v1/me/children",
        params={"guardian_id": str(other_guardian.id), "person_id": str(other_child_id)},
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(other_child_id) not in ids


@requires_postgres
def test_me_children_expired_relationship_excluded(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        session.add(
            _make_guardian_relationship(
                guardian, child, valid_from=_utc(2020, 1, 1), valid_to=_utc(2021, 1, 1)
            )
        )
        session.commit()
        guardian_user_id, child_id = guardian_user.id, child.id
    _grant_permission(guardian_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/me/children")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(child_id) not in ids


@requires_postgres
def test_me_children_revoked_relationship_excluded(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        session.add(_make_guardian_relationship(guardian, child, status="revoked"))
        session.commit()
        guardian_user_id, child_id = guardian_user.id, child.id
    _grant_permission(guardian_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/me/children")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(child_id) not in ids


@requires_postgres
def test_me_children_without_permission_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        guardian, guardian_user, child, _ = _make_guardian_child_requester(session)
        session.add(_make_guardian_relationship(guardian, child))
        session.commit()
        guardian_user_id = guardian_user.id
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/me/children")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


# --- Audit: fail-closed transaction behavior ------------------------------


@requires_postgres
def test_create_guardian_relationship_rolls_back_when_audit_insert_fails() -> None:
    """ADR-0024 §5 fail-closed contract, mirroring
    test_people_api.py's Person equivalent: if the audit insert fails,
    the GuardianRelationship row must not survive either."""
    from app.people.guardian_service import create_guardian_relationship

    with session_scope() as session:
        guardian = _make_person(first_name="AuditGuardian")
        child = _make_person(first_name="AuditChild")
        session.add_all([guardian, child])
        session.commit()
        guardian_id, child_id = guardian.id, child.id

    bogus_actor_id = uuid.uuid4()
    with session_scope() as session:
        with pytest.raises(IntegrityError):
            create_guardian_relationship(
                session,
                guardian_person_id=guardian_id,
                child_person_id=child_id,
                relationship_type="parent",
                is_primary_contact=False,
                actor_user_id=bogus_actor_id,
            )

    with session_scope() as verify_session:
        remaining = verify_session.execute(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == guardian_id,
                GuardianRelationship.child_person_id == child_id,
            )
        ).scalar_one_or_none()
        assert remaining is None, "GuardianRelationship must not survive a rolled-back audit insert"
