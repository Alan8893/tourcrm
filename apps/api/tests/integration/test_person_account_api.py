"""HTTP-level integration tests for Person-scoped account management
(TH-0113 / ADR-0038):

    GET  /api/v1/persons/{person_id}/account
    POST /api/v1/persons/{person_id}/account
    POST /api/v1/persons/{person_id}/account/password-reset

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_person_role_assignments_api.py's
fixture/factory conventions (local, duplicated helpers rather than
importing across test files, per this codebase's own established
convention).

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.authentication.tokens import hash_token
from app.db.audit import AuditLog
from app.db.authentication import PasswordResetChallenge
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ---------------------------------------------------


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


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    """Ad hoc grant via a throwaway role, isolated from the real admin
    RolePermission wiring — mirrors test_users_api.py's/
    test_person_role_assignments_api.py's identical helper."""
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


def _setup_admin_and_target(
    *, target_email: str | None = "target@example.com", target_has_user: bool = False
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, admin_user_id, target_person_id). The admin holds
    `account.manage` (scope_type="all") for the one Club; the target
    Person has an active ClubMembership in that Club and the given
    `email`, with no User unless `target_has_user=True`."""
    with session_scope() as session:
        club = _make_club()
        admin_person = _make_person()
        admin_user = _make_user(admin_person)
        target_person = _make_person(last_name="Petrova", first_name="Olga", email=target_email)
        session.add_all([club, admin_person, admin_user, target_person])
        session.flush()
        session.add(_make_club_membership(club, target_person))
        if target_has_user:
            session.add(_make_user(target_person, login_identifier=target_email))
        session.commit()
        club_id, admin_id, target_id = club.id, admin_user.id, target_person.id
    _grant_permission(admin_id, "account.manage", scope_type="all", club_id=club_id)
    return club_id, admin_id, target_id


# --- 1. admin can create account for Person with email -----------------------


@requires_postgres
def test_admin_can_create_account_for_person_with_email(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="new@example.com")
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["account"]["login_identifier"] == "new@example.com"
    assert body["account"]["status"] == "active"
    assert body["account"]["person_id"] == str(target_person_id)
    assert isinstance(body["temporary_credential"], str) and len(body["temporary_credential"]) > 0


# --- 2. Person without email cannot create account ----------------------------


@requires_postgres
def test_person_without_email_cannot_create_account(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email=None)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "person_email_missing"


# --- 3. Person with existing User cannot create second account ---------------


@requires_postgres
def test_person_with_existing_user_cannot_create_second_account(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(
        target_email="dup@example.com", target_has_user=True
    )
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "account_already_exists"


# --- 4. duplicate login identifier is rejected --------------------------------


@requires_postgres
def test_duplicate_login_identifier_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        admin_person = _make_person()
        admin_user = _make_user(admin_person)
        existing_person = _make_person(email="shared@example.com")
        existing_user = _make_user(existing_person, login_identifier="shared@example.com")
        target_person = _make_person(email="shared@example.com")
        session.add_all(
            [club, admin_person, admin_user, existing_person, existing_user, target_person]
        )
        session.flush()
        session.add(_make_club_membership(club, target_person))
        session.commit()
        club_id, admin_id, target_person_id = club.id, admin_user.id, target_person.id
    _grant_permission(admin_id, "account.manage", scope_type="all", club_id=club_id)
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_login_identifier"


# --- 5. non-admin cannot create account ---------------------------------------


@requires_postgres
def test_non_admin_cannot_create_account(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        bystander_person = _make_person()
        bystander_user = _make_user(bystander_person)
        target_person = _make_person(email="target@example.com")
        session.add_all([club, bystander_person, bystander_user, target_person])
        session.flush()
        session.add(_make_club_membership(club, target_person))
        session.commit()
        target_person_id, bystander_id = target_person.id, bystander_user.id
    _authenticate_as(bystander_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 403, response.text


# --- 6-7. initial password setup challenge / no persisted raw secret ---------


@requires_postgres
def test_create_account_issues_a_challenge_whose_raw_secret_is_never_persisted(
    client: TestClient,
) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="setup@example.com")
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    raw_credential = response.json()["temporary_credential"]
    user_id = uuid.UUID(response.json()["account"]["id"])

    with session_scope() as session:
        challenge = session.execute(
            select(PasswordResetChallenge).where(PasswordResetChallenge.user_id == user_id)
        ).scalar_one()
        assert challenge.consumed_at is None
        assert challenge.revoked_at is None
        # The raw credential is never itself stored — only its hash, and
        # the hash never equals the raw value.
        assert challenge.token_hash == hash_token(raw_credential)
        assert challenge.token_hash != raw_credential


# --- 8. challenge expires ------------------------------------------------------


@requires_postgres
def test_expired_challenge_cannot_be_confirmed(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="exp@example.com")
    _authenticate_as(admin_id)
    create_response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    raw_credential = create_response.json()["temporary_credential"]

    with session_scope() as session:
        challenge = session.execute(
            select(PasswordResetChallenge).where(
                PasswordResetChallenge.token_hash == hash_token(raw_credential)
            )
        ).scalar_one()
        challenge.expires_at = _utc(2000, 1, 1)
        session.commit()

    app.dependency_overrides.clear()
    confirm_response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_credential, "new_password": "brandnewpassword1"},
    )
    assert confirm_response.status_code == 400, confirm_response.text
    assert confirm_response.json()["error"]["code"] == "invalid_or_expired_token"


# --- 9. challenge is single-use ------------------------------------------------


@requires_postgres
def test_challenge_is_single_use(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(
        target_email="singleuse@example.com"
    )
    _authenticate_as(admin_id)
    create_response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    raw_credential = create_response.json()["temporary_credential"]

    app.dependency_overrides.clear()
    first = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_credential, "new_password": "brandnewpassword1"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_credential, "new_password": "anothernewpassword2"},
    )
    assert second.status_code == 400, second.text
    assert second.json()["error"]["code"] == "invalid_or_expired_token"


# --- 10-11. successful setup changes password and consumes challenge --------


@requires_postgres
def test_successful_setup_changes_password_and_consumes_challenge(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="setup2@example.com")
    _authenticate_as(admin_id)
    create_response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    raw_credential = create_response.json()["temporary_credential"]

    app.dependency_overrides.clear()
    confirm_response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_credential, "new_password": "brandnewpassword1"},
    )
    assert confirm_response.status_code == 200, confirm_response.text

    login_response = client.post(
        "/api/v1/auth/login",
        json={"identifier": "setup2@example.com", "password": "brandnewpassword1"},
    )
    assert login_response.status_code == 200, login_response.text

    with session_scope() as session:
        challenge = session.execute(
            select(PasswordResetChallenge).where(
                PasswordResetChallenge.token_hash == hash_token(raw_credential)
            )
        ).scalar_one()
        assert challenge.consumed_at is not None


# --- 12. successful setup invalidates sessions --------------------------------


@requires_postgres
def test_admin_reset_confirmation_invalidates_existing_sessions(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(
        target_email="hasaccount@example.com", target_has_user=True
    )
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.person_id == target_person_id)
        ).scalar_one()
        from app.authentication.passwords import hash_password

        user.password_hash = hash_password("originalpassword1")
        session.commit()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"identifier": "hasaccount@example.com", "password": "originalpassword1"},
    )
    assert login_response.status_code == 200, login_response.text

    _authenticate_as(admin_id)
    reset_response = client.post(
        f"/api/v1/persons/{target_person_id}/account/password-reset",
        headers=_csrf_headers(client),
    )
    assert reset_response.status_code == 200, reset_response.text
    raw_credential = reset_response.json()["temporary_credential"]

    app.dependency_overrides.clear()
    confirm_response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_credential, "new_password": "brandnewpassword2"},
    )
    assert confirm_response.status_code == 200, confirm_response.text

    # The session created by the original login must no longer resolve.
    old_session_cookie = login_response.cookies.get("session_token")
    assert old_session_cookie is not None
    client.cookies.set("session_token", old_session_cookie)
    whoami = client.get("/api/v1/auth/me")
    assert whoami.status_code == 401, whoami.text


# --- 13. admin can initiate reset for existing User ---------------------------


@requires_postgres
def test_admin_can_initiate_reset_for_existing_user(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(
        target_email="reset@example.com", target_has_user=True
    )
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account/password-reset",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account"]["login_identifier"] == "reset@example.com"
    assert isinstance(body["temporary_credential"], str) and len(body["temporary_credential"]) > 0


# --- 14. reset for Person without User is rejected ----------------------------


@requires_postgres
def test_reset_for_person_without_user_is_rejected(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="nouser@example.com")
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account/password-reset",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "person_has_no_account"


# --- 15. non-admin cannot initiate reset --------------------------------------


@requires_postgres
def test_non_admin_cannot_initiate_reset(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        bystander_person = _make_person()
        bystander_user = _make_user(bystander_person)
        target_person = _make_person(email="target2@example.com")
        session.add_all([club, bystander_person, bystander_user, target_person])
        session.flush()
        session.add(_make_club_membership(club, target_person))
        session.add(_make_user(target_person, login_identifier="target2@example.com"))
        session.commit()
        target_person_id, bystander_id = target_person.id, bystander_user.id
    _authenticate_as(bystander_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account/password-reset",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


# --- 16. old reset challenge cannot be reused ---------------------------------


@requires_postgres
def test_old_reset_challenge_cannot_be_reused_after_a_second_reset(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(
        target_email="supersede@example.com", target_has_user=True
    )
    _authenticate_as(admin_id)

    first = client.post(
        f"/api/v1/persons/{target_person_id}/account/password-reset",
        headers=_csrf_headers(client),
    )
    old_credential = first.json()["temporary_credential"]

    second = client.post(
        f"/api/v1/persons/{target_person_id}/account/password-reset",
        headers=_csrf_headers(client),
    )
    assert second.status_code == 200, second.text

    app.dependency_overrides.clear()
    confirm_with_old = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": old_credential, "new_password": "brandnewpassword3"},
    )
    assert confirm_with_old.status_code == 400, confirm_with_old.text
    assert confirm_with_old.json()["error"]["code"] == "invalid_or_expired_token"


# --- 17-18. audit records are created, with no secret/hash -------------------


@requires_postgres
def test_audit_records_are_created_without_secrets(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="audit@example.com")
    _authenticate_as(admin_id)

    response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    raw_credential = response.json()["temporary_credential"]
    user_id = uuid.UUID(response.json()["account"]["id"])

    with session_scope() as session:
        user_created_rows = session.execute(
            select(AuditLog).where(
                AuditLog.action == "user.created",
                AuditLog.resource_type == "user",
                AuditLog.resource_id == user_id,
            )
        ).scalars().all()
        assert len(user_created_rows) == 1
        assert user_created_rows[0].actor_user_id == admin_id

        challenge_created_rows = session.execute(
            select(AuditLog).where(AuditLog.action == "password_reset_challenge.created")
        ).scalars().all()
        assert len(challenge_created_rows) == 1
        assert challenge_created_rows[0].actor_user_id == admin_id

        for row in (*user_created_rows, *challenge_created_rows):
            serialized = str(row.details)
            assert raw_credential not in serialized

    app.dependency_overrides.clear()
    client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_credential, "new_password": "brandnewpassword4"},
    )
    with session_scope() as session:
        completed_rows = session.execute(
            select(AuditLog).where(AuditLog.action == "password_reset_challenge.completed")
        ).scalars().all()
        assert len(completed_rows) == 1
        assert completed_rows[0].actor_user_id == user_id
        assert raw_credential not in str(completed_rows[0].details)
        assert "sessions_revoked" in (completed_rows[0].details or {})


# --- 19. API responses contain no password/hash -------------------------------


@requires_postgres
def test_api_responses_never_contain_password_or_hash_fields(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="safe@example.com")
    _authenticate_as(admin_id)

    create_response = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert create_response.status_code == 201, create_response.text
    account_body = create_response.json()["account"]
    forbidden_substrings = ("password", "hash")
    for key in account_body:
        assert not any(s in key.lower() for s in forbidden_substrings), key

    get_response = client.get(f"/api/v1/persons/{target_person_id}/account")
    assert get_response.status_code == 200, get_response.text
    for key in get_response.json():
        assert not any(s in key.lower() for s in forbidden_substrings), key
    assert set(get_response.json().keys()) == {
        "id",
        "person_id",
        "login_identifier",
        "status",
        "email_verified_at",
        "last_login_at",
    }


# --- 20. account.manage permission is enforced backend-side ------------------


@requires_postgres
def test_account_manage_permission_is_enforced(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_perm_person = _make_person()
        other_perm_user = _make_user(other_perm_person)
        target_person = _make_person(email="enforced@example.com")
        session.add_all([club, other_perm_person, other_perm_user, target_person])
        session.flush()
        session.add(_make_club_membership(club, target_person))
        session.commit()
        club_id, other_perm_user_id, target_person_id = (
            club.id,
            other_perm_user.id,
            target_person.id,
        )
    # Holds a different, unrelated permission — must not be sufficient.
    _grant_permission(other_perm_user_id, "person.update", scope_type="all", club_id=club_id)
    _authenticate_as(other_perm_user_id)

    denied = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert denied.status_code == 403, denied.text

    # Granting the actual permission to the same user now succeeds.
    _grant_permission(other_perm_user_id, "account.manage", scope_type="all", club_id=club_id)
    allowed = client.post(
        f"/api/v1/persons/{target_person_id}/account", headers=_csrf_headers(client)
    )
    assert allowed.status_code == 201, allowed.text


# --- GET /account ---------------------------------------------------------


@requires_postgres
def test_get_account_returns_404_for_person_with_no_user(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(target_email="ghost@example.com")
    _authenticate_as(admin_id)

    response = client.get(f"/api/v1/persons/{target_person_id}/account")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "account_not_found"


@requires_postgres
def test_get_account_returns_safe_fields_for_person_with_user(client: TestClient) -> None:
    club_id, admin_id, target_person_id = _setup_admin_and_target(
        target_email="present@example.com", target_has_user=True
    )
    _authenticate_as(admin_id)

    response = client.get(f"/api/v1/persons/{target_person_id}/account")
    assert response.status_code == 200, response.text
    assert response.json()["login_identifier"] == "present@example.com"
    assert response.json()["status"] == "active"


@requires_postgres
def test_account_endpoints_404_for_nonexistent_person(client: TestClient) -> None:
    club_id, admin_id, _target_person_id = _setup_admin_and_target()
    _authenticate_as(admin_id)

    response = client.get(f"/api/v1/persons/{uuid.uuid4()}/account")
    assert response.status_code == 404, response.text
