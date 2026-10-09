"""HTTP tests for /api/v1/me/telegram-link (Issue #329, ADR-0047 §4.1):
authentication and CSRF, issuance response (deep link only, no-store,
nothing secret beyond the one-time link), 503 without bot configuration,
429 rate limit, link status, unlink, and that no endpoint accepts a
client-supplied Telegram id."""

import uuid
from typing import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.deps import CurrentPrincipal, get_current_principal
from app.authentication.tokens import hash_token
from app.db.identity import Person, User
from app.db.session import session_scope
from app.db.telegram import TelegramIdentity, TelegramLinkChallenge
from app.main import app
from app.telegram import linking
from app.telegram.vocabulary import CHALLENGE_RATE_LIMIT_COUNT
from tests.notification_settings_helpers import store_telegram_settings
from tests.telegram_fakes import BOT_TOKEN, BOT_USERNAME

from .conftest import requires_postgres

BASE = "/api/v1/me/telegram-link"


def _user() -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Sidorova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test", status="active"
        )
        session.add(user)
        session.commit()
        return user.id


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # ADR-0048: the username comes from Settings (PostgreSQL), never env.
    store_telegram_settings(token=None, username=BOT_USERNAME)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.pop(get_current_principal, None)


def _login(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


@requires_postgres
def test_unauthenticated_and_missing_csrf_are_rejected(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/challenges", headers=_csrf(client)).status_code == 401
    assert client.delete(BASE, headers=_csrf(client)).status_code == 401
    _login(_user())
    client.cookies.clear()
    assert client.post(f"{BASE}/challenges").status_code == 403
    assert client.delete(BASE).status_code == 403


@requires_postgres
def test_issue_returns_only_the_deep_link_and_expiry(client: TestClient) -> None:
    user_id = _user()
    _login(user_id)
    response = client.post(f"{BASE}/challenges", headers=_csrf(client))
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"deep_link", "expires_at"}
    parsed = urlparse(body["deep_link"])
    assert f"{parsed.netloc}{parsed.path}" == f"t.me/{BOT_USERNAME}"
    (token,) = parse_qs(parsed.query)["start"]
    with session_scope() as session:
        (row,) = session.execute(
            sa.select(TelegramLinkChallenge).where(TelegramLinkChallenge.user_id == user_id)
        ).scalars()
    assert row.token_hash == hash_token(token)
    assert row.token_hash not in response.text
    assert BOT_TOKEN not in response.text


@requires_postgres
def test_issue_without_bot_configuration_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(_user())
    # An environment value is not a fallback source (ADR-0048 §2.6).
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "env_only_bot")
    store_telegram_settings(token=None, username=None)
    response = client.post(f"{BASE}/challenges", headers=_csrf(client))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "telegram_linking_unavailable"
    # Status still works: missing Telegram config never breaks the API.
    assert client.get(BASE).json() == {"linked": False, "linked_at": None}


@requires_postgres
def test_username_change_applies_to_the_next_link_without_restart(client: TestClient) -> None:
    _login(_user())
    store_telegram_settings(token=None, username="other_club_bot")
    deep_link = client.post(f"{BASE}/challenges", headers=_csrf(client)).json()["deep_link"]
    assert urlparse(deep_link).path == "/other_club_bot"


@requires_postgres
def test_issue_is_rate_limited(client: TestClient) -> None:
    user_id = _user()
    _login(user_id)
    for _ in range(CHALLENGE_RATE_LIMIT_COUNT):
        assert client.post(f"{BASE}/challenges", headers=_csrf(client)).status_code == 201
    response = client.post(f"{BASE}/challenges", headers=_csrf(client))
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_requests"
    with session_scope() as session:
        count = session.execute(
            sa.select(sa.func.count())
            .select_from(TelegramLinkChallenge)
            .where(TelegramLinkChallenge.user_id == user_id)
        ).scalar_one()
    assert count == CHALLENGE_RATE_LIMIT_COUNT


@requires_postgres
def test_client_supplied_telegram_id_is_ignored(client: TestClient) -> None:
    user_id = _user()
    _login(user_id)
    response = client.post(
        f"{BASE}/challenges",
        json={"telegram_user_id": 12345, "user_id": str(uuid.uuid4())},
        headers=_csrf(client),
    )
    assert response.status_code == 201
    assert client.get(BASE).json()["linked"] is False
    with session_scope() as session:
        assert session.execute(sa.select(TelegramIdentity)).first() is None


@requires_postgres
def test_status_and_unlink(client: TestClient) -> None:
    user_id = _user()
    _login(user_id)
    deep_link = client.post(f"{BASE}/challenges", headers=_csrf(client)).json()["deep_link"]
    (token,) = parse_qs(urlparse(deep_link).query)["start"]
    with session_scope() as session:
        assert (
            linking.consume_link_challenge(session, raw_token=token, telegram_user_id=8_000_001)
            == linking.LINKED
        )
        session.commit()

    status = client.get(BASE).json()
    assert status["linked"] is True and status["linked_at"] is not None
    assert "8000001" not in client.get(BASE).text

    assert client.delete(BASE, headers=_csrf(client)).status_code == 204
    assert client.get(BASE).json() == {"linked": False, "linked_at": None}
    # Idempotent.
    assert client.delete(BASE, headers=_csrf(client)).status_code == 204
    with session_scope() as session:
        (identity,) = session.execute(sa.select(TelegramIdentity)).scalars()
    assert identity.status == "unlinked"


@requires_postgres
def test_openapi_documents_the_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths[BASE]) == {"get", "delete"}
    assert set(paths[f"{BASE}/challenges"]) == {"post"}
