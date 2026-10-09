"""HTTP + PostgreSQL tests for /api/v1/settings/notifications (Issue #333,
ADR-0048): authorization (admin allowed; instructor/member/guardian and
single-permission roles denied where appropriate; 401/CSRF), the Global
Admin Policy (default OFF, audited), installation-wide rules (enable/disable
only), write-only secrets (set/replace/clear, never returned, encrypted at
rest, fail closed without a usable key ring), non-secret settings, channel
status, and that no secret, ciphertext or key appears in responses, errors,
audit rows or logs. Test send has its own file."""

import logging
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import Club, Person, User
from app.db.notification_settings import IntegrationSecret
from app.db.notifications import NotificationRule
from app.db.session import session_scope
from app.main import app
from app.notification_settings.crypto import KeyRing, decrypt_secret
from tests.notification_settings_helpers import TEST_KEY_A, TEST_KEY_B, TEST_KEY_RING
from tests.telegram_fakes import BOT_TOKEN

from .conftest import requires_postgres

BASE = "/api/v1/settings/notifications"
SMTP_PASSWORD = "Sm7p-Pa55w0rd-UNIQUE-4711"
NEW_SMTP_PASSWORD = "An0ther-Pa55w0rd-UNIQUE-0815"
OTHER_TOKEN = "123456789:ZZReplacementFakeTokenForTestsOnly_99999"

NOTIFICATION_ENDPOINTS = [
    ("GET", "/policy", None),
    ("PUT", "/policy", {"email_enabled": True, "telegram_enabled": False}),
    ("GET", "/rules", None),
    ("PATCH", f"/rules/{uuid.uuid4()}", {"is_enabled": True}),
    ("GET", "/status", None),
    ("GET", "/telegram-destinations", None),
    ("POST", "/test-send", {"channel": "email", "destination_kind": "email_address",
                            "email": "admin@club.test"}),
]
SETTINGS_ENDPOINTS = [
    ("GET", "/integrations", None),
    ("PUT", "/integrations/email", {"smtp_host": "smtp.club.test"}),
    ("PUT", "/integrations/email/password", {"value": SMTP_PASSWORD}),
    ("DELETE", "/integrations/email/password", None),
    ("PUT", "/integrations/telegram", {"bot_username": "club_bot"}),
    ("PUT", "/integrations/telegram/bot-token", {"value": BOT_TOKEN}),
    ("DELETE", "/integrations/telegram/bot-token", None),
]


def _club() -> uuid.UUID:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.commit()
        return club.id


def _user() -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Adminova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test", status="active"
        )
        session.add(user)
        session.commit()
        return user.id


def _with_role(role_code: str, club_id: uuid.UUID) -> uuid.UUID:
    user_id = _user()
    with session_scope() as session:
        role = session.execute(sa.select(Role).where(Role.code == role_code)).scalar_one()
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all", club_id=club_id)
        )
        session.commit()
    return user_id


def _with_only(permission_code: str, club_id: uuid.UUID) -> uuid.UUID:
    """A test-only role holding exactly one permission."""
    user_id = _user()
    with session_scope() as session:
        permission = session.execute(
            sa.select(Permission).where(Permission.code == permission_code)
        ).scalar_one()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.flush()
        session.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                scopes=[RolePermissionScope(scope_type="all")],
            )
        )
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, club_id=club_id))
        session.commit()
    return user_id


@pytest.fixture
def client() -> Iterator[TestClient]:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture
def admin(client: TestClient) -> uuid.UUID:
    user_id = _with_role("admin", _club())
    _login(user_id)
    return user_id


def _login(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _call(client: TestClient, method: str, path: str, body: object = None):
    return client.request(method, f"{BASE}{path}", json=body, headers=_csrf(client))


def _audit_rows() -> list[AuditLog]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(AuditLog)
                .where(AuditLog.action.like("notification_%"))
                .order_by(AuditLog.occurred_at)
            ).scalars()
        )


def _ciphertexts() -> dict[str, str]:
    with session_scope() as session:
        rows = session.execute(sa.select(IntegrationSecret.name, IntegrationSecret.ciphertext))
        return {name: ciphertext for name, ciphertext in rows}


# --- Authorization --------------------------------------------------------------------------


@requires_postgres
def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    for method, path, body in NOTIFICATION_ENDPOINTS + SETTINGS_ENDPOINTS:
        assert _call(client, method, path, body).status_code == 401, (method, path)


@requires_postgres
@pytest.mark.parametrize("role_code", ["instructor", "member", "guardian"])
def test_non_admin_roles_are_denied_everywhere(client: TestClient, role_code: str) -> None:
    _login(_with_role(role_code, _club()))
    for method, path, body in NOTIFICATION_ENDPOINTS + SETTINGS_ENDPOINTS:
        response = _call(client, method, path, body)
        assert response.status_code == 403, (role_code, method, path)
        assert response.json()["error"]["code"] == "forbidden"
    assert _ciphertexts() == {}
    assert _audit_rows() == []


@requires_postgres
def test_notification_manage_alone_cannot_touch_integrations(client: TestClient) -> None:
    _login(_with_only("notification.manage", _club()))
    assert _call(client, "GET", "/policy").status_code == 200
    for method, path, body in SETTINGS_ENDPOINTS:
        assert _call(client, method, path, body).status_code == 403, (method, path)


@requires_postgres
def test_settings_manage_alone_cannot_change_policy_or_send_tests(client: TestClient) -> None:
    _login(_with_only("settings.manage", _club()))
    assert _call(client, "GET", "/integrations").status_code == 200
    for method, path, body in NOTIFICATION_ENDPOINTS:
        assert _call(client, method, path, body).status_code == 403, (method, path)


@requires_postgres
def test_admin_of_another_club_or_without_a_club_is_denied(client: TestClient) -> None:
    _club()
    other_club_admin = _user()
    _login(other_club_admin)
    # No role at all.
    assert _call(client, "GET", "/policy").status_code == 403
    # A second Club breaks the single-club contract: fail closed.
    admin_id = _with_role("admin", _club())
    _login(admin_id)
    assert _call(client, "GET", "/policy").status_code == 403


@requires_postgres
def test_state_changes_require_csrf(client: TestClient, admin: uuid.UUID) -> None:
    client.cookies.clear()
    for method, path, body in NOTIFICATION_ENDPOINTS + SETTINGS_ENDPOINTS:
        if method == "GET":
            continue
        response = client.request(method, f"{BASE}{path}", json=body)
        assert response.status_code == 403, (method, path)


# --- Global Admin Policy --------------------------------------------------------------------


@requires_postgres
def test_policy_defaults_off_and_updates_are_audited(client: TestClient, admin: uuid.UUID) -> None:
    assert client.get(f"{BASE}/policy").json() == {
        "email_enabled": False,
        "telegram_enabled": False,
        "saved": False,
    }
    response = _call(client, "PUT", "/policy", {"email_enabled": True, "telegram_enabled": False})
    assert response.status_code == 200
    assert response.json() == {"email_enabled": True, "telegram_enabled": False, "saved": True}
    _call(client, "PUT", "/policy", {"email_enabled": True, "telegram_enabled": True})
    # Unchanged save: no extra audit row.
    _call(client, "PUT", "/policy", {"email_enabled": True, "telegram_enabled": True})
    rows = [row for row in _audit_rows() if row.action == "notification_policy.updated"]
    assert [row.details for row in rows] == [
        {"changes": {"email": {"from": False, "to": True}}, "first_saved": True},
        {"changes": {"telegram": {"from": False, "to": True}}, "first_saved": False},
    ]
    assert all(row.actor_user_id == admin and row.outcome == "success" for row in rows)


@requires_postgres
@pytest.mark.parametrize(
    "body",
    [
        {"email_enabled": "yes", "telegram_enabled": False},
        {"email_enabled": True},
        {"email_enabled": True, "telegram_enabled": True, "club_id": str(uuid.uuid4())},
        {"email_enabled": True, "telegram_enabled": True, "max_enabled": True},
    ],
)
def test_policy_rejects_invalid_or_club_level_input(
    client: TestClient, admin: uuid.UUID, body: dict
) -> None:
    assert _call(client, "PUT", "/policy", body).status_code == 422


# --- Rules ------------------------------------------------------------------------------------


def _rule(club_id: uuid.UUID | None, *, enabled: bool = True) -> uuid.UUID:
    with session_scope() as session:
        rule = NotificationRule(
            club_id=club_id,
            event_type="spec.gated_event",
            channel="email",
            recipient_scope="participants",
            is_enabled=enabled,
        )
        session.add(rule)
        session.commit()
        return rule.id


@requires_postgres
def test_empty_rule_list_is_valid(client: TestClient, admin: uuid.UUID) -> None:
    body = client.get(f"{BASE}/rules").json()
    assert body["items"] == [] and body["pagination"]["total"] == 0


@requires_postgres
def test_only_installation_wide_rules_are_listed_and_toggled(
    client: TestClient, admin: uuid.UUID
) -> None:
    installation = _rule(None)
    with session_scope() as session:
        club_id = session.execute(sa.select(Club.id)).scalar_one()
    club_rule = _rule(club_id)
    items = client.get(f"{BASE}/rules").json()["items"]
    assert [item["id"] for item in items] == [str(installation)]

    response = _call(client, "PATCH", f"/rules/{installation}", {"is_enabled": False})
    assert response.status_code == 200 and response.json()["is_enabled"] is False
    assert _call(client, "PATCH", f"/rules/{club_rule}", {"is_enabled": False}).status_code == 404
    with session_scope() as session:
        assert session.get(NotificationRule, club_rule).is_enabled is True  # type: ignore[union-attr]
    (row,) = [row for row in _audit_rows() if row.action == "notification_rule.updated"]
    assert row.resource_id == installation
    assert row.details == {"is_enabled": {"from": True, "to": False}}
    # No create/delete through this API.
    assert client.post(f"{BASE}/rules", json={}, headers=_csrf(client)).status_code == 405
    assert _call(client, "DELETE", f"/rules/{installation}").status_code == 405
    # Rule fields other than the switch are not editable.
    assert (
        _call(client, "PATCH", f"/rules/{installation}", {"is_enabled": True, "event_type": "x"})
        .status_code
        == 422
    )


# --- Integrations and secrets ---------------------------------------------------------------


@requires_postgres
def test_integrations_never_return_secret_values(client: TestClient, admin: uuid.UUID) -> None:
    assert _call(client, "PUT", "/integrations/email", {
        "smtp_host": "smtp.club.test", "smtp_port": 2525, "smtp_security": "ssl",
        "smtp_username": "mailer", "sender_email": "noreply@club.test", "sender_name": "Клуб",
    }).status_code == 200
    response = _call(client, "PUT", "/integrations/email/password", {"value": SMTP_PASSWORD})
    assert response.status_code == 200 and response.json() == {"configured": True}
    _call(client, "PUT", "/integrations/telegram", {"bot_username": "@Club_Bot"})
    _call(client, "PUT", "/integrations/telegram/bot-token", {"value": BOT_TOKEN})

    response = client.get(f"{BASE}/integrations")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body == {
        "encryption": "available",
        "email": {
            "smtp_host": "smtp.club.test",
            "smtp_port": 2525,
            "smtp_security": "ssl",
            "smtp_username": "mailer",
            "sender_email": "noreply@club.test",
            "sender_name": "Клуб",
            "password_configured": True,
        },
        "telegram": {"bot_username": "Club_Bot", "bot_token_configured": True},
    }
    stored = _ciphertexts()
    for text in (response.text, client.get(f"{BASE}/status").text):
        for secret in (SMTP_PASSWORD, BOT_TOKEN, BOT_TOKEN.split(":")[1], TEST_KEY_A,
                       *stored.values()):
            assert secret not in text
    # Encrypted at rest: no plaintext in the table, decryptable with the key.
    ring = KeyRing.parse(TEST_KEY_RING)
    assert SMTP_PASSWORD not in stored["smtp_password"]
    assert decrypt_secret(ring, "smtp_password", stored["smtp_password"]) == SMTP_PASSWORD
    assert decrypt_secret(ring, "telegram_bot_token", stored["telegram_bot_token"]) == BOT_TOKEN


@requires_postgres
def test_saving_non_secret_settings_leaves_the_secret_unchanged(
    client: TestClient, admin: uuid.UUID
) -> None:
    _call(client, "PUT", "/integrations/email/password", {"value": SMTP_PASSWORD})
    before = _ciphertexts()["smtp_password"]
    _call(client, "PUT", "/integrations/email", {"smtp_host": "smtp2.club.test"})
    _call(client, "PUT", "/integrations/email", {"smtp_host": None, "sender_email": None})
    assert _ciphertexts()["smtp_password"] == before
    # The placeholder mask is not a value the API would accept as "unchanged":
    # a non-secret body carrying a password field is rejected outright.
    response = _call(
        client, "PUT", "/integrations/email", {"smtp_host": "x.test", "password": "••••••••"}
    )
    assert response.status_code == 422
    assert _ciphertexts()["smtp_password"] == before


@requires_postgres
def test_replace_and_clear_are_explicit_and_audited_without_values(
    client: TestClient, admin: uuid.UUID
) -> None:
    _call(client, "PUT", "/integrations/email/password", {"value": SMTP_PASSWORD})
    first = _ciphertexts()["smtp_password"]
    _call(client, "PUT", "/integrations/email/password", {"value": NEW_SMTP_PASSWORD})
    second = _ciphertexts()["smtp_password"]
    assert first != second
    ring = KeyRing.parse(TEST_KEY_RING)
    assert decrypt_secret(ring, "smtp_password", second) == NEW_SMTP_PASSWORD

    assert _call(client, "DELETE", "/integrations/email/password").status_code == 204
    assert "smtp_password" not in _ciphertexts()
    assert client.get(f"{BASE}/integrations").json()["email"]["password_configured"] is False
    # Clearing again is idempotent.
    assert _call(client, "DELETE", "/integrations/email/password").status_code == 204

    rows = [
        (row.action, row.details)
        for row in _audit_rows()
        if row.action.startswith("notification_secret.")
    ]
    assert rows == [
        ("notification_secret.set", {"setting": "smtp_password", "channel": "email",
                                     "replaced": False}),
        ("notification_secret.set", {"setting": "smtp_password", "channel": "email",
                                     "replaced": True}),
        ("notification_secret.cleared", {"setting": "smtp_password", "channel": "email",
                                         "existed": True}),
        ("notification_secret.cleared", {"setting": "smtp_password", "channel": "email",
                                         "existed": False}),
    ]
    with session_scope() as session:
        dumped = " ".join(
            str(value)
            for record in session.execute(sa.text("SELECT * FROM audit_logs")).all()
            for value in record
        )
    for secret in (SMTP_PASSWORD, NEW_SMTP_PASSWORD, first, second):
        assert secret not in dumped


@requires_postgres
def test_non_secret_changes_are_audited_with_field_names_only(
    client: TestClient, admin: uuid.UUID
) -> None:
    _call(client, "PUT", "/integrations/email", {
        "smtp_host": "smtp.club.test", "sender_email": "noreply@club.test",
    })
    _call(client, "PUT", "/integrations/telegram", {"bot_username": "club_bot"})
    rows = [
        row.details for row in _audit_rows() if row.action == "notification_integration.updated"
    ]
    assert rows == [
        {"channel": "email", "changed_fields": ["sender_email", "smtp_host"]},
        {"channel": "telegram", "changed_fields": ["bot_username"]},
    ]
    assert "smtp.club.test" not in str(rows) and "club_bot" not in str(rows)


@requires_postgres
@pytest.mark.parametrize("key_ring", [None, "garbage", f"a:{TEST_KEY_A[:-8]}"])
def test_secret_writes_fail_closed_without_a_usable_key_ring(
    client: TestClient,
    admin: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    key_ring: str | None,
) -> None:
    if key_ring is None:
        monkeypatch.delenv("SETTINGS_ENCRYPTION_KEYS")
    else:
        monkeypatch.setenv("SETTINGS_ENCRYPTION_KEYS", key_ring)
    for path, value in (
        ("/integrations/email/password", SMTP_PASSWORD),
        ("/integrations/telegram/bot-token", BOT_TOKEN),
    ):
        response = _call(client, "PUT", path, {"value": value})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "settings_encryption_unavailable"
        assert value not in response.text
    assert _ciphertexts() == {}
    assert client.get(f"{BASE}/integrations").json()["encryption"] == (
        "missing" if key_ring is None else "invalid"
    )
    assert not [row for row in _audit_rows() if row.action == "notification_secret.set"]


@requires_postgres
def test_wrong_key_makes_the_channel_secret_unavailable(
    client: TestClient, admin: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _call(client, "PUT", "/integrations/email", {
        "smtp_host": "smtp.club.test", "smtp_username": "mailer",
        "sender_email": "noreply@club.test",
    })
    _call(client, "PUT", "/integrations/email/password", {"value": SMTP_PASSWORD})
    _call(client, "PUT", "/integrations/telegram/bot-token", {"value": BOT_TOKEN})
    status = client.get(f"{BASE}/status").json()
    assert status["email"]["configuration"] == "configured"
    assert status["telegram"]["configuration"] == "configured"

    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEYS", f"other:{TEST_KEY_B}")
    status = client.get(f"{BASE}/status").json()
    assert status["email"]["configuration"] == "secret_unavailable"
    assert status["telegram"]["configuration"] == "secret_unavailable"
    assert status["email"]["ready"] is False
    # Still reported as configured (stored), never revealed.
    assert client.get(f"{BASE}/integrations").json()["email"]["password_configured"] is True


@requires_postgres
def test_corrupted_ciphertext_fails_closed(client: TestClient, admin: uuid.UUID) -> None:
    _call(client, "PUT", "/integrations/telegram/bot-token", {"value": BOT_TOKEN})
    with session_scope() as session:
        row = session.get(IntegrationSecret, "telegram_bot_token")
        assert row is not None
        version, key_id, nonce, ciphertext = row.ciphertext.split(".")
        row.ciphertext = ".".join((version, key_id, nonce, ciphertext[:-2] + "AA"))
        session.commit()
    assert client.get(f"{BASE}/status").json()["telegram"]["configuration"] == (
        "secret_unavailable"
    )


@requires_postgres
@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        ("/integrations/telegram/bot-token", "not-a-token-SECRETISH-12345", "invalid_secret"),
        ("/integrations/telegram/bot-token", "   ", "invalid_secret"),
        ("/integrations/email/password", "line\r\nbreak-SECRETISH", "invalid_secret"),
        ("/integrations/email/password", "", "validation_error"),
        ("/integrations/email/password", "SECRETISH" * 200, "validation_error"),
    ],
)
def test_secret_validation_errors_never_echo_the_value(
    client: TestClient, admin: uuid.UUID, path: str, value: str, code: str
) -> None:
    response = _call(client, "PUT", path, {"value": value})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert "SECRETISH" not in response.text
    assert _ciphertexts() == {}


@requires_postgres
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"smtp_host": "smtp host with spaces"}, "invalid_smtp_host"),
        ({"smtp_host": "user@smtp.test"}, "invalid_smtp_host"),
        ({"smtp_host": "smtp.test\r\nX: y"}, "invalid_smtp_host"),
        ({"sender_email": "not-an-address"}, "invalid_sender_email"),
        ({"sender_name": "Name\r\nBcc: x@y.z"}, "invalid_sender_name"),
        ({"smtp_port": 0}, "validation_error"),
        ({"smtp_security": "tls13"}, "validation_error"),
    ],
)
def test_email_settings_validation(
    client: TestClient, admin: uuid.UUID, body: dict, code: str
) -> None:
    response = _call(client, "PUT", "/integrations/email", body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


@requires_postgres
def test_invalid_bot_username_is_rejected(client: TestClient, admin: uuid.UUID) -> None:
    response = _call(client, "PUT", "/integrations/telegram", {"bot_username": "not a bot"})
    assert (response.status_code, response.json()["error"]["code"]) == (
        422,
        "invalid_bot_username",
    )


# --- Status -------------------------------------------------------------------------------


@requires_postgres
def test_status_combines_policy_and_configuration(client: TestClient, admin: uuid.UUID) -> None:
    status = client.get(f"{BASE}/status").json()
    assert status == {
        "encryption": "available",
        "email": {"policy_enabled": False, "configuration": "not_configured", "ready": False},
        "telegram": {
            "policy_enabled": False,
            "configuration": "not_configured",
            "ready": False,
            "linking_available": False,
        },
    }
    _call(client, "PUT", "/integrations/email", {"smtp_host": "smtp.club.test",
                                                  "smtp_username": "mailer"})
    assert client.get(f"{BASE}/status").json()["email"]["configuration"] == "incomplete"
    _call(client, "PUT", "/integrations/email", {"smtp_host": "smtp.club.test",
                                                  "sender_email": "noreply@club.test"})
    assert client.get(f"{BASE}/status").json()["email"]["configuration"] == "configured"
    _call(client, "PUT", "/policy", {"email_enabled": True, "telegram_enabled": True})
    _call(client, "PUT", "/integrations/telegram", {"bot_username": "club_bot"})
    status = client.get(f"{BASE}/status").json()
    assert status["email"] == {"policy_enabled": True, "configuration": "configured",
                               "ready": True}
    assert status["telegram"] == {"policy_enabled": True, "configuration": "not_configured",
                                  "ready": False, "linking_available": True}


@requires_postgres
def test_secrets_never_reach_logs(
    client: TestClient, admin: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _call(client, "PUT", "/integrations/email/password", {"value": SMTP_PASSWORD})
    _call(client, "PUT", "/integrations/telegram/bot-token", {"value": OTHER_TOKEN})
    _call(client, "PUT", "/integrations/telegram/bot-token", {"value": "bad-SECRETISH"})
    client.get(f"{BASE}/integrations")
    client.get(f"{BASE}/status")
    for secret in (SMTP_PASSWORD, OTHER_TOKEN, OTHER_TOKEN.split(":")[1], "SECRETISH",
                   TEST_KEY_A, *_ciphertexts().values()):
        assert secret not in caplog.text
