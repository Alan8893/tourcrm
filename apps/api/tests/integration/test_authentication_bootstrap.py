import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.authentication.bootstrap import (
    AdministratorAlreadyExistsError,
    AdminRoleInconsistentError,
    BootstrapResult,
    EmailAlreadyRegisteredError,
    bootstrap_initial_administrator,
    global_administrator_exists,
)
from app.authentication.passwords import WeakPasswordError, verify_password
from app.cli import bootstrap_admin
from app.db.audit import AuditLog
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

_STRONG_PASSWORD = "a-strong-bootstrap-password"
_CLUB_NAME = "Test Club"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _unique_email() -> str:
    return f"admin-{uuid.uuid4().hex[:8]}@example.com"


def _user_exists(email: str) -> bool:
    with session_scope() as session:
        return (
            session.execute(
                select(User.id).where(User.login_identifier == email)
            ).scalar_one_or_none()
            is not None
        )


def _bootstrap(session, email: str) -> BootstrapResult:
    return bootstrap_initial_administrator(
        session,
        club_name=_CLUB_NAME,
        email=email,
        password=_STRONG_PASSWORD,
    )


@requires_postgres
def test_clean_install_creates_person_user_and_global_admin_assignment() -> None:
    email = _unique_email()
    with session_scope() as session:
        result = _bootstrap(session, email)

    with session_scope() as session:
        person = session.get(Person, result.person_id)
        user = session.get(User, result.user_id)
        assignment = session.get(UserRoleAssignment, result.role_assignment_id)
        role = session.get(Role, result.role_id)
        club = session.get(Club, result.club_id)

        assert person is not None
        assert person.first_name == "Admin"
        assert person.last_name == "Admin"
        assert user is not None
        assert user.person_id == person.id
        assert user.status == "active"
        assert user.normalized_login_identifier == email.lower()
        assert role is not None
        assert role.code == "admin"
        assert club is not None
        assert club.name == _CLUB_NAME
        assert club.status == "active"
        assert assignment is not None
        assert assignment.user_id == user.id
        assert assignment.role_id == role.id
        assert assignment.scope_type == "all"
        assert assignment.club_id == club.id
        assert assignment.scope_ref_id is None
        assert assignment.valid_to is None


@requires_postgres
def test_global_administrator_exists_reports_true_after_bootstrap() -> None:
    with session_scope() as session:
        assert global_administrator_exists(session) is False
    with session_scope() as session:
        _bootstrap(session, _unique_email())
    with session_scope() as session:
        assert global_administrator_exists(session) is True


@requires_postgres
def test_bootstrap_never_leaks_the_password_into_the_hash_or_audit_details() -> None:
    email = _unique_email()
    with session_scope() as session:
        result = _bootstrap(session, email)

    with session_scope() as session:
        user = session.get(User, result.user_id)
        assert user is not None
        assert user.password_hash is not None
        assert _STRONG_PASSWORD not in user.password_hash
        assert verify_password(_STRONG_PASSWORD, user.password_hash) is True
        audit_rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.resource_id.in_(
                        [result.person_id, result.user_id, result.role_assignment_id]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {row.action for row in audit_rows} == {
            "person.created",
            "user.created",
            "role_assignment.created",
        }
        for row in audit_rows:
            assert row.actor_type == "system"
            assert row.actor_user_id is None
            serialized = repr(row.details)
            assert _STRONG_PASSWORD not in serialized
            assert user.password_hash not in serialized


@requires_postgres
def test_login_and_me_work_immediately_after_bootstrap_via_normal_endpoints(
    client: TestClient,
) -> None:
    email = _unique_email()
    with session_scope() as session:
        result = _bootstrap(session, email)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"identifier": email, "password": _STRONG_PASSWORD},
    )
    assert login_response.status_code == 200, login_response.text
    assert login_response.json()["user"]["id"] == str(result.user_id)
    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["id"] == str(result.user_id)


@requires_postgres
def test_second_bootstrap_attempt_is_rejected_without_mutation() -> None:
    with session_scope() as session:
        _bootstrap(session, _unique_email())
    with session_scope() as session:
        before_users = sorted(session.execute(select(User.id)).scalars().all())
        before_assignments = sorted(
            session.execute(select(UserRoleAssignment.id)).scalars().all()
        )
    second_email = _unique_email()
    with session_scope() as session:
        with pytest.raises(AdministratorAlreadyExistsError):
            bootstrap_initial_administrator(
                session,
                club_name=_CLUB_NAME,
                email=second_email,
                password=_STRONG_PASSWORD,
            )
    with session_scope() as session:
        after_users = sorted(session.execute(select(User.id)).scalars().all())
        after_assignments = sorted(
            session.execute(select(UserRoleAssignment.id)).scalars().all()
        )
    assert before_users == after_users
    assert before_assignments == after_assignments
    assert _user_exists(second_email) is False


@requires_postgres
def test_invalid_password_is_rejected_and_creates_nothing() -> None:
    email = _unique_email()
    with session_scope() as session:
        with pytest.raises(WeakPasswordError):
            bootstrap_initial_administrator(
                session,
                club_name=_CLUB_NAME,
                email=email,
                password="short",
            )
    assert _user_exists(email) is False
    with session_scope() as session:
        assert global_administrator_exists(session) is False


@requires_postgres
def test_duplicate_email_is_rejected_without_mutation() -> None:
    email = _unique_email()
    with session_scope() as session:
        person = Person(first_name="Existing", last_name="Person")
        user = User(
            person=person,
            login_identifier=email,
            password_hash="not-a-real-hash",
            status="pending",
        )
        session.add_all([person, user])
        session.commit()
    with session_scope() as session:
        with pytest.raises(EmailAlreadyRegisteredError):
            bootstrap_initial_administrator(
                session,
                club_name=_CLUB_NAME,
                email=email,
                password=_STRONG_PASSWORD,
            )
    with session_scope() as session:
        assert global_administrator_exists(session) is False


@requires_postgres
def test_missing_admin_role_fails_safely_without_mutation() -> None:
    email = _unique_email()
    with session_scope() as session:
        session.execute(delete(Role).where(Role.code == "admin"))
        session.commit()
    with session_scope() as session:
        with pytest.raises(AdminRoleInconsistentError):
            bootstrap_initial_administrator(
                session,
                club_name=_CLUB_NAME,
                email=email,
                password=_STRONG_PASSWORD,
            )
    assert _user_exists(email) is False


@requires_postgres
def test_non_system_admin_role_fails_safely_without_mutation() -> None:
    email = _unique_email()
    with session_scope() as session:
        role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
        role.is_system = False
        session.commit()
    with session_scope() as session:
        with pytest.raises(AdminRoleInconsistentError):
            bootstrap_initial_administrator(
                session,
                club_name=_CLUB_NAME,
                email=email,
                password=_STRONG_PASSWORD,
            )
    assert _user_exists(email) is False


@requires_postgres
def test_rollback_on_induced_failure_leaves_no_half_created_account(monkeypatch) -> None:
    from app.authentication import bootstrap as bootstrap_module

    email = _unique_email()

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("induced failure after Person/User were staged")

    monkeypatch.setattr(bootstrap_module, "record_audit_event", _boom)
    with session_scope() as session:
        with pytest.raises(RuntimeError):
            bootstrap_initial_administrator(
                session,
                club_name=_CLUB_NAME,
                email=email,
                password=_STRONG_PASSWORD,
            )
    assert _user_exists(email) is False
    with session_scope() as session:
        assert global_administrator_exists(session) is False


@requires_postgres
def test_concurrent_bootstrap_attempts_produce_exactly_one_administrator() -> None:
    barrier = threading.Barrier(2)
    results: list[object] = []
    results_lock = threading.Lock()

    def _attempt(email: str) -> None:
        barrier.wait()
        try:
            with session_scope() as session:
                result = bootstrap_initial_administrator(
                    session,
                    club_name=_CLUB_NAME,
                    email=email,
                    password=_STRONG_PASSWORD,
                )
            with results_lock:
                results.append(result)
        except AdministratorAlreadyExistsError as exc:
            with results_lock:
                results.append(exc)
        except Exception as exc:  # noqa: BLE001
            with results_lock:
                results.append(exc)

    emails = [_unique_email(), _unique_email()]
    threads = [
        threading.Thread(target=_attempt, args=(emails[0],)),
        threading.Thread(target=_attempt, args=(emails[1],)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 2
    successes = [r for r in results if isinstance(r, BootstrapResult)]
    conflicts = [r for r in results if isinstance(r, AdministratorAlreadyExistsError)]
    crashes = [
        r
        for r in results
        if not isinstance(r, (BootstrapResult, AdministratorAlreadyExistsError))
    ]
    assert crashes == []
    assert len(successes) == 1
    assert len(conflicts) == 1

    with session_scope() as session:
        rows = (
            session.execute(
                select(UserRoleAssignment.id)
                .join(Role, Role.id == UserRoleAssignment.role_id)
                .where(
                    Role.code == "admin",
                    UserRoleAssignment.scope_type == "all",
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
def test_cli_password_mismatch_creates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _unique_email()
    inputs = iter([_CLUB_NAME, email])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    passwords = iter(["password-one-long-enough", "password-two-long-enough"])
    monkeypatch.setattr(
        bootstrap_admin.getpass,
        "getpass",
        lambda prompt="": next(passwords),
    )
    exit_code = bootstrap_admin.main()
    assert exit_code == 1
    assert _user_exists(email) is False


@requires_postgres
def test_cli_user_cancellation_creates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _unique_email()
    inputs = iter([_CLUB_NAME, email, "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr(
        bootstrap_admin.getpass,
        "getpass",
        lambda prompt="": _STRONG_PASSWORD,
    )
    exit_code = bootstrap_admin.main()
    assert exit_code == 0
    assert _user_exists(email) is False


@requires_postgres
def test_cli_confirmed_run_creates_the_administrator(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _unique_email()
    inputs = iter([_CLUB_NAME, email, "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr(
        bootstrap_admin.getpass,
        "getpass",
        lambda prompt="": _STRONG_PASSWORD,
    )
    exit_code = bootstrap_admin.main()
    assert exit_code == 0
    assert _user_exists(email) is True


@requires_postgres
def test_cli_refuses_a_second_run_before_asking_for_a_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_scope() as session:
        _bootstrap(session, _unique_email())

    def _fail_if_called(prompt: str = "") -> str:
        raise AssertionError(
            "input()/getpass() must not be called when an admin already exists"
        )

    monkeypatch.setattr("builtins.input", _fail_if_called)
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", _fail_if_called)
    exit_code = bootstrap_admin.main()
    assert exit_code == 1
