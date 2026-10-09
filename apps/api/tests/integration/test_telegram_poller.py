"""PostgreSQL integration tests for the Telegram long-polling runtime
(Issue #329, ADR-0047 §3): checkpoint ordering and atomicity with linking,
duplicate/replayed update ids, crash-before-commit replay, private-chat
only linking, the single-poller lock, `409 Conflict`, configuration
failures, bounded backoff, graceful stop and secret-free logs. The Bot API
is the scripted in-memory transport — never real Telegram."""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import pytest
import sqlalchemy as sa

from app.cli import run_telegram_poller
from app.db.identity import Person, User
from app.db.session import get_engine, get_session_factory, session_scope
from app.db.telegram import TelegramIdentity, TelegramUpdateCheckpoint
from app.telegram import linking
from app.telegram import poller as poller_module
from app.telegram.bot_api import TelegramTransportError
from app.telegram.poller import (
    EXIT_CONFIGURATION,
    EXIT_CONFLICT,
    EXIT_OK,
    PollerConfig,
    TelegramPoller,
)
from app.telegram.updates import REPLY_LINKED, REPLY_REJECTED
from tests.telegram_fakes import (
    BOT_ID,
    BOT_TOKEN,
    BOT_USERNAME,
    FakeBotApiServer,
    FakeBotApiServerConfig,
    ScriptedTransport,
    ServerReply,
    api_error,
    client_for,
    get_me_ok,
    group_message,
    ok,
    private_start,
)

from .conftest import requires_postgres

ANNA_TG = 6_000_000_001
SECRET_TEXT = "my private message 4242"


def _user() -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Petrova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test", status="active"
        )
        session.add(user)
        session.commit()
        return user.id


def _issue(user_id: uuid.UUID) -> str:
    with session_scope() as session:
        issued = linking.create_link_challenge(
            session, user_id=user_id, bot_username=BOT_USERNAME
        )
        session.commit()
    return parse_qs(urlparse(issued.deep_link).query)["start"][0]


def _transport() -> ScriptedTransport:
    return ScriptedTransport(
        defaults={"getMe": get_me_ok(), "sendMessage": ok({"message_id": 1})}
    )


_FAST = PollerConfig(poll_timeout_seconds=1, backoff_base_seconds=0.01, backoff_max_seconds=0.05)


def _poller(transport: ScriptedTransport, **kwargs: Any) -> TelegramPoller:
    return TelegramPoller(
        client=client_for(transport),
        engine=get_engine(),
        session_factory=get_session_factory(),
        config=kwargs.pop("config", _FAST),
        **kwargs,
    )


def _checkpoint() -> Optional[int]:
    with session_scope() as session:
        return session.execute(
            sa.select(TelegramUpdateCheckpoint.last_update_id).where(
                TelegramUpdateCheckpoint.bot_id == BOT_ID
            )
        ).scalar_one_or_none()


def _active_identity(user_id: uuid.UUID) -> Optional[int]:
    with session_scope() as session:
        return session.execute(
            sa.select(TelegramIdentity.telegram_user_id).where(
                TelegramIdentity.user_id == user_id, TelegramIdentity.status == "active"
            )
        ).scalar_one_or_none()


def _stop_when_drained(transport: ScriptedTransport, stop: threading.Event) -> None:
    """Once the scripted getUpdates outcomes are used up, the next poll
    sets `stop` (a SIGTERM stand-in) and returns no updates."""

    def stopping(params: Any) -> Any:
        stop.set()
        return ok([])

    transport.defaults["getUpdates"] = stopping


@pytest.fixture
def started() -> Any:
    created: list[TelegramPoller] = []

    def start(transport: ScriptedTransport, **kwargs: Any) -> TelegramPoller:
        instance = _poller(transport, **kwargs)
        assert instance.start(threading.Event())
        created.append(instance)
        return instance

    yield start
    for instance in created:
        instance.close()


# --- Processing and checkpoint -------------------------------------------------------------


@requires_postgres
def test_start_link_commits_with_the_checkpoint_then_replies(started: Any) -> None:
    user_id = _user()
    token = _issue(user_id)
    transport = _transport().script(
        "getUpdates", ok([private_start(100, ANNA_TG, f"/start {token}")])
    )
    instance = started(transport)
    assert _checkpoint() is None

    assert instance.poll_once(threading.Event()) == 1
    assert _active_identity(user_id) == ANNA_TG
    assert _checkpoint() == 100
    (reply,) = transport.calls_to("sendMessage")
    assert reply.params == {"chat_id": ANNA_TG, "text": REPLY_LINKED}
    assert token not in str(reply.params)

    transport.script("getUpdates", ok([]))
    instance.poll_once(threading.Event())
    # The next poll acknowledges through the committed checkpoint.
    assert transport.calls_to("getUpdates")[-1].params["offset"] == 101


@requires_postgres
def test_first_poll_has_no_offset(started: Any) -> None:
    transport = _transport().script("getUpdates", ok([]))
    started(transport).poll_once(threading.Event())
    assert "offset" not in transport.calls_to("getUpdates")[0].params


@requires_postgres
def test_duplicate_and_replayed_updates_have_no_second_effect(started: Any) -> None:
    user_id = _user()
    token = _issue(user_id)
    update = private_start(200, ANNA_TG, f"/start {token}")
    transport = _transport().script("getUpdates", ok([update, update]), ok([update]))
    instance = started(transport)

    assert instance.poll_once(threading.Event()) == 1
    assert instance.poll_once(threading.Event()) == 0
    assert _checkpoint() == 200
    # One reply only, and it was the success reply (no "token used" reply).
    assert [call.params["text"] for call in transport.calls_to("sendMessage")] == [REPLY_LINKED]
    with session_scope() as session:
        count = session.execute(
            sa.select(sa.func.count()).select_from(TelegramIdentity)
        ).scalar_one()
    assert count == 1


@requires_postgres
def test_updates_are_processed_in_update_id_order(started: Any) -> None:
    user_id = _user()
    token = _issue(user_id)
    transport = _transport().script(
        "getUpdates",
        ok(
            [
                private_start(302, ANNA_TG, f"/start {token}"),
                private_start(301, ANNA_TG, "hello"),
            ]
        ),
    )
    started(transport).poll_once(threading.Event())
    assert _checkpoint() == 302
    assert _active_identity(user_id) == ANNA_TG


@requires_postgres
def test_group_and_non_command_updates_advance_the_checkpoint_without_reply(
    started: Any,
) -> None:
    user_id = _user()
    token = _issue(user_id)
    transport = _transport().script(
        "getUpdates",
        ok(
            [
                group_message(400, ANNA_TG, f"/start {token}"),
                private_start(401, ANNA_TG, SECRET_TEXT),
                {"update_id": 402, "my_chat_member": {}},
            ]
        ),
    )
    started(transport).poll_once(threading.Event())
    assert _checkpoint() == 402
    assert _active_identity(user_id) is None
    assert transport.calls_to("sendMessage") == []


@requires_postgres
def test_rejected_token_gets_the_generic_reply(started: Any) -> None:
    transport = _transport().script(
        "getUpdates", ok([private_start(500, ANNA_TG, "/start " + "Z" * 43)])
    )
    started(transport).poll_once(threading.Event())
    assert [call.params["text"] for call in transport.calls_to("sendMessage")] == [REPLY_REJECTED]
    assert _checkpoint() == 500


@requires_postgres
def test_crash_before_commit_rolls_back_and_the_update_is_replayed(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = _user()
    token = _issue(user_id)
    update = private_start(600, ANNA_TG, f"/start {token}")
    transport = _transport().script("getUpdates", ok([update]), ok([update]))
    instance = started(transport)

    real_handle = poller_module.handle_update

    def crash_after_linking(session: Any, item: Any) -> Any:
        real_handle(session, item)
        session.flush()
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("connection lost"))

    monkeypatch.setattr(poller_module, "handle_update", crash_after_linking)
    with pytest.raises(sa.exc.OperationalError):
        instance.poll_once(threading.Event())
    assert _active_identity(user_id) is None
    assert _checkpoint() is None
    assert transport.calls_to("sendMessage") == []

    monkeypatch.setattr(poller_module, "handle_update", real_handle)
    assert instance.poll_once(threading.Event()) == 1
    assert _active_identity(user_id) == ANNA_TG
    assert _checkpoint() == 600
    # Telegram is re-asked from the last committed checkpoint (none yet).
    assert "offset" not in transport.calls_to("getUpdates")[1].params


@requires_postgres
def test_failure_mid_batch_keeps_committed_progress(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    anna, boris = _user(), _user()
    anna_token, boris_token = _issue(anna), _issue(boris)
    batch = [
        private_start(700, ANNA_TG, f"/start {anna_token}"),
        private_start(701, ANNA_TG + 1, f"/start {boris_token}"),
    ]
    transport = _transport().script("getUpdates", ok(batch), ok(batch[1:]))
    instance = started(transport)
    real_handle = poller_module.handle_update

    def fail_on_second(session: Any, item: Any) -> Any:
        if item["update_id"] == 701:
            raise sa.exc.OperationalError("SELECT 1", {}, Exception("db down"))
        return real_handle(session, item)

    monkeypatch.setattr(poller_module, "handle_update", fail_on_second)
    with pytest.raises(sa.exc.OperationalError):
        instance.poll_once(threading.Event())
    assert (_checkpoint(), _active_identity(anna), _active_identity(boris)) == (
        700,
        ANNA_TG,
        None,
    )
    monkeypatch.setattr(poller_module, "handle_update", real_handle)
    instance.poll_once(threading.Event())
    assert transport.calls_to("getUpdates")[1].params["offset"] == 701
    assert (_checkpoint(), _active_identity(boris)) == (701, ANNA_TG + 1)


@requires_postgres
def test_reply_failure_does_not_undo_the_committed_link(started: Any) -> None:
    user_id = _user()
    token = _issue(user_id)
    transport = _transport().script(
        "getUpdates", ok([private_start(800, ANNA_TG, f"/start {token}")])
    )
    transport.script("sendMessage", api_error(403, "Forbidden: bot was blocked by the user"))
    started(transport).poll_once(threading.Event())
    assert (_checkpoint(), _active_identity(user_id)) == (800, ANNA_TG)


@requires_postgres
def test_stop_between_updates_leaves_the_rest_for_replay(started: Any) -> None:
    stop = threading.Event()
    transport = _transport()
    transport.script(
        "getUpdates",
        ok([private_start(900, ANNA_TG, "hello"), private_start(901, ANNA_TG, "hello")]),
    )
    instance = started(transport)
    real_process = instance.process_update

    def process_then_stop(update: Any) -> Any:
        result = real_process(update)
        stop.set()
        return result

    instance.process_update = process_then_stop  # type: ignore[method-assign]
    assert instance.poll_once(stop) == 1
    assert _checkpoint() == 900


# --- Run loop: single poller, conflict, configuration, backoff, shutdown -------------------


@requires_postgres
def test_run_stops_gracefully_on_stop_event() -> None:
    stop = threading.Event()
    transport = _transport()
    _stop_when_drained(transport, stop)
    assert _poller(transport).run(stop) == EXIT_OK
    # The lock was released: a new poller can start.
    other = _poller(_transport())
    assert other.start(threading.Event())
    other.close()


@requires_postgres
def test_second_poller_for_the_same_bot_exits_without_polling(started: Any) -> None:
    started(_transport())
    second_transport = _transport()
    assert _poller(second_transport).run(threading.Event()) == EXIT_CONFLICT
    assert second_transport.calls_to("getUpdates") == []


@requires_postgres
def test_telegram_409_conflict_stops_with_a_safe_operational_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    transport = _transport().script(
        "getUpdates", api_error(409, "Conflict: terminated by other getUpdates request")
    )
    assert _poller(transport).run(threading.Event()) == EXIT_CONFLICT
    assert len(transport.calls_to("getUpdates")) == 1
    assert "conflict" in caplog.text.lower()
    assert BOT_TOKEN not in caplog.text


@requires_postgres
def test_rejected_token_at_startup_is_a_configuration_exit() -> None:
    transport = ScriptedTransport().script("getMe", api_error(401, "Unauthorized"))
    assert _poller(transport).run(threading.Event()) == EXIT_CONFIGURATION
    assert transport.calls_to("getUpdates") == []


@requires_postgres
def test_username_mismatch_is_a_configuration_exit() -> None:
    transport = _transport()
    assert (
        _poller(transport, expected_username="other_club_bot").run(threading.Event())
        == EXIT_CONFIGURATION
    )
    assert transport.calls_to("getUpdates") == []


@requires_postgres
def test_transient_failures_back_off_boundedly_then_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = threading.Event()
    waits: list[float] = []
    real_wait = stop.wait

    def recording_wait(timeout: Optional[float] = None) -> bool:
        waits.append(timeout or 0)
        return real_wait(0)

    monkeypatch.setattr(stop, "wait", recording_wait)
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    transport.script("getMe", TelegramTransportError("network"), get_me_ok())
    transport.script(
        "getUpdates",
        TelegramTransportError("timeout"),
        api_error(502, "Bad Gateway"),
        api_error(429, "Too Many Requests", {"retry_after": 30}),
        TelegramTransportError("network"),
        TelegramTransportError("network"),
        TelegramTransportError("network"),
        ok([private_start(1000, ANNA_TG, "hello")]),
    )
    _stop_when_drained(transport, stop)
    assert _poller(transport).run(stop) == EXIT_OK
    assert _checkpoint() == 1000
    # getMe retry + 6 getUpdates failures; all bounded by backoff_max (0.05s),
    # the 429's retry_after included.
    assert len(waits) == 7
    assert max(waits) <= _FAST.backoff_max_seconds
    # Exponential, reset by nothing until success; the 429 waits its
    # retry_after, capped at backoff_max.
    assert waits[1:3] == [0.01, 0.02]
    assert waits[3] == _FAST.backoff_max_seconds


@requires_postgres
def test_database_failure_in_the_loop_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    transport = _transport()
    instance = _poller(transport)
    calls = {"n": 0}
    real_checkpoint = instance.checkpoint

    def flaky_checkpoint() -> Optional[int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sa.exc.OperationalError("SELECT 1", {}, Exception("db restarting"))
        return real_checkpoint()

    monkeypatch.setattr(instance, "checkpoint", flaky_checkpoint)
    _stop_when_drained(transport, stop)
    assert instance.run(stop) == EXIT_OK
    assert calls["n"] == 2


@requires_postgres
def test_logs_never_contain_token_or_message_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    user_id = _user()
    token = _issue(user_id)
    stop = threading.Event()
    transport = _transport().script(
        "getUpdates",
        ok(
            [
                private_start(1100, ANNA_TG, f"/start {token}"),
                private_start(1101, ANNA_TG, SECRET_TEXT),
            ]
        ),
    )
    _stop_when_drained(transport, stop)
    assert _poller(transport).run(stop) == EXIT_OK
    for secret in (BOT_TOKEN, token, SECRET_TEXT, str(ANNA_TG)):
        assert secret not in caplog.text
    assert "update_id=1100 outcome=linked" in caplog.text


# --- CLI -----------------------------------------------------------------------------------


@requires_postgres
def test_cli_without_token_exits_with_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert run_telegram_poller.main() == EXIT_CONFIGURATION


@requires_postgres
def test_cli_invalid_token_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bad = "this-is-not-a-valid-token-but-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bad)
    assert run_telegram_poller.main() == EXIT_CONFIGURATION
    assert bad not in caplog.text


@requires_postgres
def test_cli_process_shuts_down_gracefully_on_sigterm(database_url: str) -> None:
    me = {"ok": True, "result": {"id": BOT_ID, "is_bot": True, "username": BOT_USERNAME}}
    config = FakeBotApiServerConfig(
        by_method={
            "getMe": ServerReply(body=json.dumps(me).encode()),
            "getUpdates": ServerReply(body=b'{"ok": true, "result": []}', delay_seconds=0.2),
        }
    )
    with FakeBotApiServer(config) as server:
        env = {
            **os.environ,
            "DATABASE_URL": database_url,
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TELEGRAM_BOT_USERNAME": BOT_USERNAME,
            "TELEGRAM_API_BASE_URL": server.base_url,
            "TELEGRAM_POLL_TIMEOUT_SECONDS": "1",
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "app.cli.run_telegram_poller"],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not any(
                path.endswith("/getUpdates") for path, _ in config.received
            ):
                time.sleep(0.05)
            assert any(path.endswith("/getUpdates") for path, _ in config.received)
            process.send_signal(signal.SIGTERM)
            output, _ = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
    assert process.returncode == EXIT_OK
    text = output.decode()
    assert "telegram poller started" in text and "telegram poller stopped" in text
    assert BOT_TOKEN not in text


@requires_postgres
def test_unexpected_error_backs_off_and_logs_only_its_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stop = threading.Event()
    transport = _transport().script(
        "getUpdates", ok([private_start(1200, ANNA_TG, SECRET_TEXT)])
    )
    calls = {"n": 0}
    real_handle = poller_module.handle_update

    def broken_once(session: Any, item: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyError(SECRET_TEXT)
        return real_handle(session, item)

    monkeypatch.setattr(poller_module, "handle_update", broken_once)
    transport.script("getUpdates", ok([private_start(1200, ANNA_TG, SECRET_TEXT)]))
    _stop_when_drained(transport, stop)
    assert _poller(transport).run(stop) == EXIT_OK
    assert _checkpoint() == 1200
    assert "type=KeyError" in caplog.text
    assert SECRET_TEXT not in caplog.text
