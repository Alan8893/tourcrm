"""Unit tests for update interpretation and poller configuration (Issue
#329, ADR-0047 §3.3/§4.3). Every update that must not reach the linking
service is checked with a session double that fails if it is touched; the
linking path itself is covered against PostgreSQL in
tests/integration/test_telegram_linking.py."""

from typing import Any

import pytest

from app.telegram import updates
from app.telegram.poller import PollerConfig, PollerConfigurationError, backoff_delay
from app.telegram.updates import handle_update
from tests.telegram_fakes import group_message, private_start

TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcd"


class _UntouchableSession:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"session.{name} used for an update that must be ignored")


def _handle(update: dict[str, Any]) -> updates.UpdateResult:
    return handle_update(_UntouchableSession(), update)  # type: ignore[arg-type]


def _with(update: dict[str, Any], **message_changes: Any) -> dict[str, Any]:
    update["message"].update(message_changes)
    return update


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": 1},
        {"update_id": 1, "edited_message": private_start(1, 5, f"/start {TOKEN}")["message"]},
        {"update_id": 1, "message": "not an object"},
        group_message(1, 5, f"/start {TOKEN}"),
        group_message(1, 5, f"/start@tourcrm_test_bot {TOKEN}"),
        _with(private_start(1, 5, f"/start {TOKEN}"), chat={"id": 5, "type": "channel"}),
        _with(
            private_start(1, 5, f"/start {TOKEN}"),
            **{"from": {"id": 5, "is_bot": True}},
        ),
        _with(private_start(1, 5, f"/start {TOKEN}"), **{"from": {"id": 5}}),
        # A private chat whose id is not the sender's own id.
        _with(private_start(1, 5, f"/start {TOKEN}"), chat={"id": 6, "type": "private"}),
        _with(private_start(1, 5, f"/start {TOKEN}"), **{"from": {"id": "5", "is_bot": False}}),
        _with(private_start(1, 5, f"/start {TOKEN}"), text=None),
        private_start(1, 5, "hello"),
        private_start(1, 5, f"/help {TOKEN}"),
        private_start(1, 5, f"/started {TOKEN}"),
    ],
)
def test_updates_that_cannot_link_are_ignored_without_reply(update: dict[str, Any]) -> None:
    result = _handle(update)
    assert result == updates.UpdateResult("ignored")


def test_bare_start_gets_instructions_without_touching_linking() -> None:
    result = _handle(private_start(1, 5, "/start"))
    assert result.outcome == "start_without_link"
    assert (result.reply_chat_id, result.reply_text) == (5, updates.REPLY_START_WITHOUT_LINK)


def test_replies_never_contain_message_content() -> None:
    for text in (updates.REPLY_LINKED, updates.REPLY_REJECTED, updates.REPLY_START_WITHOUT_LINK):
        assert "{" not in text and TOKEN not in text


def test_poller_config_bounds() -> None:
    assert PollerConfig().poll_timeout_seconds == 20
    with pytest.raises(PollerConfigurationError):
        PollerConfig(poll_timeout_seconds=0)
    with pytest.raises(PollerConfigurationError):
        PollerConfig(poll_timeout_seconds=51)
    with pytest.raises(PollerConfigurationError):
        PollerConfig(backoff_base_seconds=0)
    with pytest.raises(PollerConfigurationError):
        PollerConfig(backoff_base_seconds=10, backoff_max_seconds=5)


def test_poller_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("TELEGRAM_POLL_BACKOFF_MAX_SECONDS", "15")
    config = PollerConfig.from_env()
    assert (config.poll_timeout_seconds, config.backoff_max_seconds) == (30, 15.0)
    monkeypatch.setenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "soon")
    with pytest.raises(PollerConfigurationError):
        PollerConfig.from_env()


def test_backoff_is_exponential_and_bounded() -> None:
    config = PollerConfig(backoff_base_seconds=1, backoff_max_seconds=60)
    assert [backoff_delay(n, config) for n in range(1, 9)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert backoff_delay(10_000, config) == 60
