"""HTTP tests for /api/v1/me/notification-preferences (Issue #336,
ADR-0049 §2.1): authentication and CSRF, unset values reported as OFF,
mandatory types read-only, the master switch preserving per-event values,
rejected types changing nothing, self-scope (no other User is touched),
the Telegram link state, and that the stored values are the ones the
Engine enforces."""

import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.identity import Person, User
from app.db.notifications import CommunicationChannelPreference, CommunicationPreference
from app.db.session import session_scope
from app.db.telegram import TelegramIdentity
from app.main import app

from .conftest import requires_postgres

BASE = "/api/v1/me/notification-preferences"
OPTIONAL = [
    "event.created",
    "event.updated",
    "registration.created",
    "registration.cancelled",
    "attendance.changed",
    "news.published",
    "achievement.awarded",
]


def _user(*, linked: bool = False) -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Orlova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test", status="active"
        )
        session.add(user)
        session.flush()
        if linked:
            session.add(
                TelegramIdentity(user_id=user.id, telegram_user_id=7_200_000_001, status="active")
            )
        session.commit()
        return user.id


@pytest.fixture
def client() -> Iterator[TestClient]:
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


def _events(body: dict) -> dict[str, tuple[bool, bool]]:
    return {e["event_type"]: (e["mandatory"], e["enabled"]) for e in body["events"]}


def _stored(user_id: uuid.UUID) -> tuple[bool | None, dict[str, bool]]:
    with session_scope() as session:
        master = session.execute(
            sa.select(CommunicationChannelPreference.enabled).where(
                CommunicationChannelPreference.user_id == user_id
            )
        ).scalar_one_or_none()
        events = dict(
            session.execute(
                sa.select(
                    CommunicationPreference.notification_type, CommunicationPreference.enabled
                ).where(CommunicationPreference.user_id == user_id)
            ).tuples().all()
        )
    return master, events


@requires_postgres
def test_unauthenticated_and_missing_csrf_are_rejected(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
    assert client.put(BASE, json={}, headers=_csrf(client)).status_code == 401
    _login(_user())
    client.cookies.clear()
    assert client.put(BASE, json={"personal_enabled": True}).status_code == 403


@requires_postgres
def test_defaults_are_off_and_mandatory_types_are_listed_read_only(client: TestClient) -> None:
    _login(_user())
    body = client.get(BASE).json()
    assert (body["channel"], body["personal_enabled"], body["telegram_linked"]) == (
        "telegram",
        False,
        False,
    )
    events = _events(body)
    assert "membership.approved" not in events
    assert events == {
        **{key: (False, False) for key in OPTIONAL},
        "event.cancelled": (True, True),
        "event.rescheduled": (True, True),
    }


@requires_postgres
def test_linked_state_is_reported(client: TestClient) -> None:
    _login(_user(linked=True))
    assert client.get(BASE).json()["telegram_linked"] is True


@requires_postgres
def test_master_switch_toggle_preserves_event_preferences(client: TestClient) -> None:
    user_id = _user()
    _login(user_id)
    response = client.put(
        BASE,
        json={"personal_enabled": True, "events": {"news.published": True, "event.created": False}},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert response.json()["personal_enabled"] is True
    assert _events(response.json())["news.published"] == (False, True)

    off = client.put(BASE, json={"personal_enabled": False}, headers=_csrf(client)).json()
    assert off["personal_enabled"] is False
    assert _events(off)["news.published"] == (False, True)
    assert _stored(user_id) == (False, {"news.published": True, "event.created": False})

    on = client.put(BASE, json={"personal_enabled": True}, headers=_csrf(client)).json()
    assert _events(on)["news.published"] == (False, True)


@pytest.mark.parametrize(
    "events",
    [
        {"event.cancelled": False},
        {"event.rescheduled": False},
        {"membership.approved": True},
        {"unknown.type": True},
        {"news.published": True, "event.cancelled": False},
    ],
)
@requires_postgres
def test_non_switchable_types_are_rejected_and_nothing_changes(
    client: TestClient, events: dict
) -> None:
    user_id = _user()
    _login(user_id)
    response = client.put(
        BASE, json={"personal_enabled": True, "events": events}, headers=_csrf(client)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_notification_type"
    assert _stored(user_id) == (None, {})


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": str(uuid.uuid4())},
        {"destination_type": "telegram_destination"},
        {"personal_enabled": "maybe"},
        {"events": {"news.published": "yes"}},
    ],
)
@requires_postgres
def test_malformed_body_is_rejected(client: TestClient, body: dict) -> None:
    user_id = _user()
    _login(user_id)
    assert client.put(BASE, json=body, headers=_csrf(client)).status_code == 422
    assert _stored(user_id) == (None, {})


@requires_postgres
def test_only_the_callers_own_preferences_change(client: TestClient) -> None:
    other = _user()
    caller = _user()
    _login(caller)
    client.put(
        BASE,
        json={"personal_enabled": True, "events": {"news.published": True}},
        headers=_csrf(client),
    )
    assert _stored(other) == (None, {})
    assert _stored(caller) == (True, {"news.published": True})
