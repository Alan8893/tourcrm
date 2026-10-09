"""PostgreSQL integration tests for the Telegram long-polling runtime
(Issue #329, ADR-0047 §3; dynamic configuration #333, ADR-0047 §6):
checkpoint ordering and atomicity with linking, duplicate/replayed update
ids, crash-before-commit replay, private-chat only linking, the
single-poller lock, `409 Conflict`, configuration states read from
Settings (not configured, secret unavailable, token rejected, username
mismatch), token switching with lock handover and per-bot checkpoints,
bounded backoff, graceful stop and secret-free logs. The Bot API is the
scripted in-memory transport or a local fake server — never real
Telegram."""

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
    STATE_POLLING,
    STATE_TOKEN_REJECTED,
    STATE_USERNAME_MISMATCH,
    PollerConfig,
    TelegramPoller,
    TelegramPollerService,
)
from app.telegram.updates import REPLY_LINKED, REPLY_REJECTED
from tests.notification_settings_helpers import (
    TEST_KEY_B,
    store_policy,
    store_telegram_settings,
)
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


_FAST = PollerConfig(
    poll_timeout_seconds=1,
    backoff_base_seconds=0.01,
    backoff_max_seconds=0.05,
    config_interval_seconds=0.01,
)


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
        instance.start()
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


# --- Service loop: configuration, single poller, conflict, backoff, shutdown ---------------

OTHER_BOT_ID = 987654321
OTHER_BOT_TOKEN = "987654321:BBOtherFakeBotTokenForTestsOnly_987654xyz"


def _service(
    transports: "ScriptedTransport | dict[str, ScriptedTransport]",
    *,
    configure: bool = True,
    username: Optional[str] = BOT_USERNAME,
) -> TelegramPollerService:
    """A service whose Bot API client is chosen by the configured token."""
    if configure:
        store_telegram_settings(token=BOT_TOKEN, username=username)
    by_token = transports if isinstance(transports, dict) else {BOT_TOKEN: transports}
    return TelegramPollerService(
        engine=get_engine(),
        session_factory=get_session_factory(),
        config=_FAST,
        client_factory=lambda settings: client_for(by_token[settings.bot_token]),
    )


def _lock_is_free(bot_id: int) -> bool:
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        key = {"key": f"tourcrm.telegram_poller:{bot_id}"}
        acquired = connection.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), key
        ).scalar_one()
        if acquired:
            connection.execute(
                sa.text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), key
            )
        return bool(acquired)


@requires_postgres
def test_run_stops_gracefully_on_stop_event() -> None:
    stop = threading.Event()
    transport = _transport()
    _stop_when_drained(transport, stop)
    assert _service(transport).run(stop) == EXIT_OK
    # The lock was released: a new poller can start.
    assert _lock_is_free(BOT_ID)


@requires_postgres
def test_not_configured_idles_without_lock_then_starts_once_a_token_is_saved() -> None:
    transport = _transport().script("getUpdates", ok([]))
    service = _service(transport, configure=False)
    stop = threading.Event()
    assert service.run_once(stop) == _FAST.config_interval_seconds
    assert service.state == "not_configured"
    assert transport.calls == []
    assert _checkpoint() is None

    store_telegram_settings(token=BOT_TOKEN, username=BOT_USERNAME)
    assert service.run_once(stop) is None
    assert (service.state, service.active_bot_id) == (STATE_POLLING, BOT_ID)
    assert len(transport.calls_to("getUpdates")) == 1
    assert not _lock_is_free(BOT_ID)
    service._deactivate()


@requires_postgres
def test_token_change_hands_the_lock_over_and_uses_the_new_bots_checkpoint() -> None:
    user_id = _user()
    old = _transport().script("getUpdates", ok([private_start(10, ANNA_TG, "hello")]))
    new = ScriptedTransport(
        defaults={
            "getMe": get_me_ok(bot_id=OTHER_BOT_ID, username="other_club_bot"),
            "sendMessage": ok({"message_id": 1}),
        }
    ).script("getUpdates", ok([private_start(3, ANNA_TG, f"/start {_issue(user_id)}")]))
    service = _service({BOT_TOKEN: old, OTHER_BOT_TOKEN: new})
    stop = threading.Event()
    service.run_once(stop)
    assert (service.active_bot_id, _checkpoint()) == (BOT_ID, 10)

    # The administrator replaces the token and username in Settings.
    store_telegram_settings(token=OTHER_BOT_TOKEN, username="other_club_bot")
    assert service.run_once(stop) is None
    assert service.active_bot_id == OTHER_BOT_ID
    # Old bot's lock released, new bot's lock held — never both loops.
    assert _lock_is_free(BOT_ID)
    assert not _lock_is_free(OTHER_BOT_ID)
    # The new bot polls from its own checkpoint (none yet), not the old one.
    assert "offset" not in new.calls_to("getUpdates")[0].params
    with session_scope() as session:
        checkpoints = dict(
            session.execute(
                sa.select(
                    TelegramUpdateCheckpoint.bot_id, TelegramUpdateCheckpoint.last_update_id
                )
            ).all()
        )
    assert checkpoints == {BOT_ID: 10, OTHER_BOT_ID: 3}
    assert _active_identity(user_id) == ANNA_TG
    # The old bot is no longer polled.
    assert len(old.calls_to("getUpdates")) == 1
    service._deactivate()
    assert _lock_is_free(OTHER_BOT_ID)


@requires_postgres
def test_cleared_token_stops_polling_and_releases_the_lock() -> None:
    transport = _transport().script("getUpdates", ok([]))
    service = _service(transport)
    stop = threading.Event()
    service.run_once(stop)
    assert not _lock_is_free(BOT_ID)
    with session_scope() as session:
        session.execute(sa.text("DELETE FROM integration_secrets"))
        session.commit()
    assert service.run_once(stop) == _FAST.config_interval_seconds
    assert (service.state, service.active_bot_id) == ("not_configured", None)
    assert _lock_is_free(BOT_ID)


@requires_postgres
def test_undecryptable_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _transport()
    service = _service(transport)
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEYS", f"other:{TEST_KEY_B}")
    assert service.run_once(threading.Event()) == _FAST.config_interval_seconds
    assert service.state == "secret_unavailable"
    assert transport.calls == []
    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEYS")
    service.run_once(threading.Event())
    assert service.state == "secret_unavailable"
    assert transport.calls == []


@requires_postgres
def test_rejected_token_waits_for_a_configuration_change() -> None:
    transport = ScriptedTransport(defaults={"getMe": api_error(401, "Unauthorized")})
    service = _service(transport)
    stop = threading.Event()
    for _ in range(3):
        assert service.run_once(stop) == _FAST.config_interval_seconds
    assert service.state == STATE_TOKEN_REJECTED
    # getMe is not retried with the same rejected token.
    assert len(transport.calls_to("getMe")) == 1
    assert transport.calls_to("getUpdates") == []
    assert _lock_is_free(BOT_ID)


@requires_postgres
def test_username_mismatch_waits_and_recovers_when_the_username_is_fixed() -> None:
    transport = _transport().script("getUpdates", ok([]))
    service = _service(transport, username="other_club_bot")
    stop = threading.Event()
    assert service.run_once(stop) == _FAST.config_interval_seconds
    assert service.state == STATE_USERNAME_MISMATCH
    assert transport.calls_to("getUpdates") == []
    assert _lock_is_free(BOT_ID)

    store_telegram_settings(token=None, username=BOT_USERNAME)
    assert service.run_once(stop) is None
    assert service.state == STATE_POLLING
    service._deactivate()


@requires_postgres
def test_telegram_delivery_policy_off_does_not_stop_the_poller() -> None:
    store_policy(email=False, telegram=False)
    transport = _transport().script("getUpdates", ok([private_start(40, ANNA_TG, "hello")]))
    service = _service(transport)
    service.run_once(threading.Event())
    assert (service.state, _checkpoint()) == (STATE_POLLING, 40)
    service._deactivate()


@requires_postgres
def test_second_poller_for_the_same_bot_exits_without_polling(started: Any) -> None:
    started(_transport())
    second_transport = _transport()
    assert _service(second_transport).run(threading.Event()) == EXIT_CONFLICT
    assert second_transport.calls_to("getUpdates") == []


@requires_postgres
def test_telegram_409_conflict_stops_with_a_safe_operational_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    transport = _transport().script(
        "getUpdates", api_error(409, "Conflict: terminated by other getUpdates request")
    )
    assert _service(transport).run(threading.Event()) == EXIT_CONFLICT
    assert len(transport.calls_to("getUpdates")) == 1
    assert "conflict" in caplog.text.lower()
    assert BOT_TOKEN not in caplog.text
    assert _lock_is_free(BOT_ID)


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
    assert _service(transport).run(stop) == EXIT_OK
    assert _checkpoint() == 1000
    # getMe failure + 6 getUpdates failures: exponential, bounded by
    # backoff_max (0.05s) — the 429's retry_after included.
    assert waits == [0.01, 0.02, 0.04, 0.05, 0.05, 0.05, 0.05]


@requires_postgres
def test_database_failure_in_the_loop_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    transport = _transport()
    calls = {"n": 0}
    real_checkpoint = TelegramPoller.checkpoint

    def flaky_checkpoint(self: TelegramPoller) -> Optional[int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sa.exc.OperationalError("SELECT 1", {}, Exception("db restarting"))
        return real_checkpoint(self)

    monkeypatch.setattr(TelegramPoller, "checkpoint", flaky_checkpoint)
    _stop_when_drained(transport, stop)
    assert _service(transport).run(stop) == EXIT_OK
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
    assert _service(transport).run(stop) == EXIT_OK
    for secret in (BOT_TOKEN, BOT_TOKEN.split(":")[1], token, SECRET_TEXT, str(ANNA_TG)):
        assert secret not in caplog.text
    assert "update_id=1100 outcome=linked" in caplog.text


@requires_postgres
def test_unexpected_error_backs_off_and_logs_only_its_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stop = threading.Event()
    transport = _transport().script(
        "getUpdates",
        ok([private_start(1200, ANNA_TG, SECRET_TEXT)]),
        ok([private_start(1200, ANNA_TG, SECRET_TEXT)]),
    )
    calls = {"n": 0}
    real_handle = poller_module.handle_update

    def broken_once(session: Any, item: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyError(SECRET_TEXT)
        return real_handle(session, item)

    monkeypatch.setattr(poller_module, "handle_update", broken_once)
    _stop_when_drained(transport, stop)
    assert _service(transport).run(stop) == EXIT_OK
    assert _checkpoint() == 1200
    assert "type=KeyError" in caplog.text
    assert SECRET_TEXT not in caplog.text


# --- CLI -----------------------------------------------------------------------------------


@requires_postgres
def test_cli_invalid_poller_tuning_exits_with_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "0")
    assert run_telegram_poller.main() == EXIT_CONFIGURATION


@requires_postgres
def test_cli_process_reads_the_token_from_settings_and_stops_on_sigterm(
    database_url: str,
) -> None:
    store_telegram_settings(token=BOT_TOKEN, username=BOT_USERNAME)
    me = {"ok": True, "result": {"id": BOT_ID, "is_bot": True, "username": BOT_USERNAME}}
    config = FakeBotApiServerConfig(
        by_method={
            "getMe": ServerReply(body=json.dumps(me).encode()),
            "getUpdates": ServerReply(body=b'{"ok": true, "result": []}', delay_seconds=0.2),
        }
    )
    with FakeBotApiServer(config) as server:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME")
        }
        env.update(
            {
                "DATABASE_URL": database_url,
                "TELEGRAM_API_BASE_URL": server.base_url,
                "TELEGRAM_POLL_TIMEOUT_SECONDS": "1",
            }
        )
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
    assert "state=polling" in text
    assert BOT_TOKEN not in text and BOT_TOKEN.split(":")[1] not in text
