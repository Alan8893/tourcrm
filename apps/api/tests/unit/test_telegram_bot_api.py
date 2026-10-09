"""Unit tests for the Telegram Bot API boundary and configuration (Issue
#329, ADR-0047 §3.3/§5/§6): classification of every provider/transport
outcome, the typed client methods, the real urllib transport against a
local fake server, and that the bot token never leaks through config
errors, repr, exceptions or tracebacks. No database, no real Telegram."""

import traceback
from typing import Any

import pytest

from app.core.config import (
    ConfigurationError,
    TelegramSettings,
    get_telegram_bot_username,
    get_telegram_settings,
    validate_telegram_api_base_url,
)
from app.telegram import bot_api
from app.telegram.bot_api import (
    BotApiClient,
    BotApiResponse,
    TelegramApiError,
    TelegramTransportError,
    UrllibBotApiTransport,
    classify_error_response,
)
from tests.telegram_fakes import (
    BOT_TOKEN,
    FakeBotApiServer,
    FakeBotApiServerConfig,
    ScriptedTransport,
    ServerReply,
    api_error,
    get_me_ok,
    ok,
)

# --- Configuration ------------------------------------------------------------------------


def test_settings_absent_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert get_telegram_settings() is None


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@TourCRM_Test_Bot")
    monkeypatch.setenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "7.5")
    monkeypatch.delenv("TELEGRAM_API_BASE_URL", raising=False)
    settings = get_telegram_settings()
    assert settings is not None
    assert settings.bot_username == "TourCRM_Test_Bot"
    assert settings.api_base_url == "https://api.telegram.org"
    assert settings.request_timeout_seconds == 7.5


@pytest.mark.parametrize(
    ("base_url", "normalized"),
    [
        ("https://api.telegram.org", "https://api.telegram.org"),
        ("https://api.telegram.org/", "https://api.telegram.org"),
        ("https://api.telegram.org:443", "https://api.telegram.org"),
        ("HTTPS://API.TELEGRAM.ORG", "https://api.telegram.org"),
        ("http://127.0.0.1:8081", "http://127.0.0.1:8081"),
        ("http://localhost:9000/", "http://localhost:9000"),
        ("http://[::1]:8081", "http://[::1]:8081"),
    ],
)
def test_allowed_api_base_urls_are_normalized(base_url: str, normalized: str) -> None:
    settings = TelegramSettings(bot_token=BOT_TOKEN, api_base_url=base_url)
    assert settings.api_base_url == normalized
    assert validate_telegram_api_base_url(base_url) == normalized


@pytest.mark.parametrize(
    "base_url",
    [
        # Foreign HTTPS hosts, look-alikes and suffix/prefix tricks.
        "https://evil.example",
        "https://api.telegram.org.evil.example",
        "https://evilapi.telegram.org",
        "https://telegram.org",
        "https://api.telegram.org.",
        "https://xn--api-telegram-org.example",
        "https://api.telegram.org%2eevil.example",
        # Userinfo that would make another host the real target.
        "https://api.telegram.org@evil.example",
        "https://api.telegram.org:443@evil.example",
        "https://user:pass@api.telegram.org",
        "https://evil.example#@api.telegram.org",
        "https://evil.example?@api.telegram.org",
        "https://evil.example\\@api.telegram.org",
        # Unexpected ports, paths, query, fragment.
        "https://api.telegram.org:8443",
        "https://api.telegram.org:0",
        "https://api.telegram.org:99999",
        "https://api.telegram.org:abc",
        "https://api.telegram.org/bot",
        "https://api.telegram.org/../x",
        "https://api.telegram.org?x=1",
        "https://api.telegram.org#frag",
        # Plain HTTP anywhere but loopback; loopback without a port.
        "http://api.telegram.org",
        "http://10.0.0.5:8080",
        "http://127.0.0.2:8080",
        "http://localhost.evil.example:8080",
        "http://127.0.0.1",
        "http://[::2]:8080",
        # HTTPS to loopback is not an official endpoint either.
        "https://127.0.0.1:8443",
        # Other schemes and malformed values.
        "ftp://api.telegram.org",
        "//api.telegram.org",
        "api.telegram.org",
        "",
        " https://api.telegram.org",
        "https://api.telegram.org\t",
        "https://api.telegram\n.org",
        "https://evil.example\r\n@api.telegram.org",
    ],
)
def test_untrusted_api_base_urls_are_rejected(base_url: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        TelegramSettings(bot_token=BOT_TOKEN, api_base_url=base_url)
    assert BOT_TOKEN not in str(raised.value)


def test_untrusted_api_base_url_from_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org.evil.example")
    with pytest.raises(ConfigurationError) as raised:
        get_telegram_settings()
    assert BOT_TOKEN not in str(raised.value)


def test_transport_never_sends_the_token_to_a_foreign_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if a settings object were forced past its own validation, the
    transport re-validates before building any URL, and no request is made."""
    settings = TelegramSettings(bot_token=BOT_TOKEN)
    object.__setattr__(settings, "api_base_url", "https://evil.example")
    opened: list[object] = []
    monkeypatch.setattr(bot_api.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    with pytest.raises(ConfigurationError):
        UrllibBotApiTransport(settings)
    assert opened == []


def test_transport_url_is_built_from_the_normalized_base() -> None:
    settings = TelegramSettings(bot_token=BOT_TOKEN, api_base_url="https://api.telegram.org:443/")
    transport = UrllibBotApiTransport(settings)
    assert "https://api.telegram.org'" in repr(transport)


def test_settings_repr_never_contains_the_token() -> None:
    settings = TelegramSettings(bot_token=BOT_TOKEN, bot_username="tourcrm_test_bot")
    assert BOT_TOKEN not in repr(settings)
    assert BOT_TOKEN.split(":")[1] not in repr(settings)
    transport = UrllibBotApiTransport(settings)
    assert BOT_TOKEN not in repr(transport)
    assert BOT_TOKEN not in repr(BotApiClient(transport, request_timeout=1))


@pytest.mark.parametrize("token", ["not-a-token", "123:short", ":AAAAAAAAAAAAAAAAAAAAAAAA", " "])
def test_malformed_token_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token + "x")
    with pytest.raises(ConfigurationError) as raised:
        get_telegram_settings()
    assert token.strip() + "x" not in str(raised.value)


@pytest.mark.parametrize("username", ["ab", "club_notifier", "1startsdigitbot", "x" * 40 + "bot"])
def test_invalid_bot_username_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, username: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", username)
    with pytest.raises(ConfigurationError):
        get_telegram_bot_username()


def test_bot_username_alone_needs_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "tourcrm_test_bot")
    assert get_telegram_bot_username() == "tourcrm_test_bot"
    monkeypatch.delenv("TELEGRAM_BOT_USERNAME")
    assert get_telegram_bot_username() is None


def test_app_imports_and_starts_without_any_telegram_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME"):
        monkeypatch.delenv(name, raising=False)
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/health/live").status_code == 200


# --- Classification -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (api_error(429, "Too Many Requests: retry after 5", {"retry_after": 5}),
         bot_api.TELEGRAM_RATE_LIMITED, True),
        (api_error(500, "Internal Server Error"), bot_api.TELEGRAM_SERVER_ERROR, True),
        (api_error(502, "Bad Gateway"), bot_api.TELEGRAM_SERVER_ERROR, True),
        (BotApiResponse(502, None), bot_api.TELEGRAM_SERVER_ERROR, True),
        (BotApiResponse(200, None), bot_api.TELEGRAM_MALFORMED_RESPONSE, True),
        (BotApiResponse(200, {"unexpected": 1}), bot_api.TELEGRAM_MALFORMED_RESPONSE, True),
        (api_error(401, "Unauthorized"), bot_api.TELEGRAM_CONFIGURATION_INVALID, False),
        (api_error(404, "Not Found"), bot_api.TELEGRAM_CONFIGURATION_INVALID, False),
        (api_error(409, "Conflict: terminated by other getUpdates request"),
         bot_api.TELEGRAM_CONFLICT, False),
        (api_error(403, "Forbidden: bot was blocked by the user"),
         bot_api.TELEGRAM_BOT_BLOCKED, False),
        (api_error(403, "Forbidden: user is deactivated"), bot_api.TELEGRAM_BOT_BLOCKED, False),
        (api_error(403, "Forbidden: bot is not a member of the supergroup chat"),
         bot_api.TELEGRAM_CHAT_FORBIDDEN, False),
        (api_error(400, "Bad Request: chat not found"), bot_api.TELEGRAM_CHAT_NOT_FOUND, False),
        (api_error(400, "Bad Request: message thread not found"),
         bot_api.TELEGRAM_TOPIC_UNAVAILABLE, False),
        (api_error(400, "Bad Request: TOPIC_CLOSED"), bot_api.TELEGRAM_TOPIC_UNAVAILABLE, False),
        (api_error(400, "Bad Request: group chat was upgraded to a supergroup chat",
                   {"migrate_to_chat_id": -100987}), bot_api.TELEGRAM_CHAT_MIGRATED, False),
        (api_error(400, "Bad Request: message text is empty"),
         bot_api.TELEGRAM_REQUEST_REJECTED, False),
    ],
)
def test_error_responses_map_to_stable_codes(
    response: BotApiResponse, code: str, retryable: bool
) -> None:
    error = classify_error_response(response)
    assert (error.code, error.retryable) == (code, retryable)
    assert str(error) == code


def test_rate_limit_keeps_only_the_numeric_retry_after() -> None:
    error = classify_error_response(api_error(429, "Too Many Requests", {"retry_after": 17}))
    assert error.retry_after == 17
    assert error.safe_message == "retry_after=17s"
    assert classify_error_response(api_error(429, "x", {"retry_after": "soon"})).retry_after is None


@pytest.mark.parametrize(
    ("kind", "code", "retryable"),
    [
        ("timeout", bot_api.TELEGRAM_TIMEOUT, True),
        ("network", bot_api.TELEGRAM_NETWORK_ERROR, True),
        ("tls", bot_api.TELEGRAM_TLS_FAILED, False),
    ],
)
def test_transport_failures_map_to_stable_codes(kind: Any, code: str, retryable: bool) -> None:
    transport = ScriptedTransport().script("sendMessage", TelegramTransportError(kind))
    with pytest.raises(TelegramApiError) as raised:
        BotApiClient(transport, request_timeout=1).send_message(chat_id=1, text="x")
    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    assert raised.value.__context__ is None and raised.value.__cause__ is None


# --- Client methods -----------------------------------------------------------------------


def test_send_message_is_plain_text_with_optional_thread() -> None:
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 42})})
    client = BotApiClient(transport, request_timeout=3)
    assert client.send_message(chat_id=-100, text="Сбор", message_thread_id=7) == 42
    assert client.send_message(chat_id=5, text="Сбор") == 42
    first, second = transport.calls
    assert first.params == {"chat_id": -100, "text": "Сбор", "message_thread_id": 7}
    assert second.params == {"chat_id": 5, "text": "Сбор"}
    assert "parse_mode" not in first.params
    assert first.timeout == 3


@pytest.mark.parametrize(
    "result", [{"message_id": "42"}, {}, [], None, {"message_id": True}]
)
def test_send_message_malformed_success_is_retryable(result: Any) -> None:
    transport = ScriptedTransport(defaults={"sendMessage": ok(result)})
    with pytest.raises(TelegramApiError) as raised:
        BotApiClient(transport, request_timeout=1).send_message(chat_id=1, text="x")
    assert (raised.value.code, raised.value.retryable) == (
        bot_api.TELEGRAM_MALFORMED_RESPONSE,
        True,
    )


def test_ok_envelope_without_result_is_malformed() -> None:
    transport = ScriptedTransport(defaults={"getMe": BotApiResponse(200, {"ok": True})})
    with pytest.raises(TelegramApiError) as raised:
        BotApiClient(transport, request_timeout=1).get_me()
    assert raised.value.code == bot_api.TELEGRAM_MALFORMED_RESPONSE


def test_get_me_and_get_updates() -> None:
    transport = ScriptedTransport(
        defaults={"getMe": get_me_ok(), "getUpdates": ok([{"update_id": 1}])}
    )
    client = BotApiClient(transport, request_timeout=4)
    identity = client.get_me()
    assert (identity.bot_id, identity.username) == (123456789, "tourcrm_test_bot")
    assert client.get_updates(offset=11, poll_timeout=20, limit=50) == [{"update_id": 1}]
    assert client.get_updates(offset=None, poll_timeout=20) == [{"update_id": 1}]
    with_offset, without_offset = transport.calls_to("getUpdates")
    assert with_offset.params == {
        "offset": 11,
        "timeout": 20,
        "limit": 50,
        "allowed_updates": ["message"],
    }
    assert "offset" not in without_offset.params
    # Long-poll wait plus the ordinary request timeout.
    assert with_offset.timeout == 24


@pytest.mark.parametrize("result", [{"id": "1"}, {"id": 0}, [], {"username": "x"}])
def test_get_me_malformed(result: Any) -> None:
    transport = ScriptedTransport(defaults={"getMe": ok(result)})
    with pytest.raises(TelegramApiError):
        BotApiClient(transport, request_timeout=1).get_me()


def test_get_updates_malformed() -> None:
    transport = ScriptedTransport(defaults={"getUpdates": ok({"not": "a list"})})
    with pytest.raises(TelegramApiError) as raised:
        BotApiClient(transport, request_timeout=1).get_updates(offset=None, poll_timeout=1)
    assert raised.value.code == bot_api.TELEGRAM_MALFORMED_RESPONSE


# --- Real urllib transport against a local fake server -------------------------------------


def _client(base_url: str, timeout: float = 2.0) -> BotApiClient:
    settings = TelegramSettings(
        bot_token=BOT_TOKEN, api_base_url=base_url, request_timeout_seconds=timeout
    )
    return bot_api.build_bot_api_client(settings)


def _assert_no_token(error: BaseException) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert BOT_TOKEN not in rendered
    assert BOT_TOKEN.split(":")[1] not in rendered
    assert BOT_TOKEN not in repr(error)


def test_urllib_transport_success_uses_the_bot_api_url_shape() -> None:
    config = FakeBotApiServerConfig()
    config.replies.append(ServerReply(body=b'{"ok": true, "result": {"message_id": 9}}'))
    with FakeBotApiServer(config) as server:
        assert _client(server.base_url).send_message(chat_id=5, text="Привет") == 9
    ((path, params),) = config.received
    assert path == f"/bot{BOT_TOKEN}/sendMessage"
    assert params == {"chat_id": 5, "text": "Привет"}


def test_urllib_transport_http_error_body_is_classified_without_leaking() -> None:
    config = FakeBotApiServerConfig()
    config.replies.append(
        ServerReply(
            status=403,
            body=b'{"ok": false, "error_code": 403, '
            b'"description": "Forbidden: bot was blocked by the user"}',
        )
    )
    with FakeBotApiServer(config) as server:
        with pytest.raises(TelegramApiError) as raised:
            _client(server.base_url).send_message(chat_id=5, text="x")
    assert raised.value.code == bot_api.TELEGRAM_BOT_BLOCKED
    _assert_no_token(raised.value)


def test_urllib_transport_non_json_gateway_error_is_retryable() -> None:
    config = FakeBotApiServerConfig()
    config.replies.append(ServerReply(status=502, body=b"<html>Bad Gateway</html>"))
    with FakeBotApiServer(config) as server:
        with pytest.raises(TelegramApiError) as raised:
            _client(server.base_url).get_me()
    assert (raised.value.code, raised.value.retryable) == (bot_api.TELEGRAM_SERVER_ERROR, True)


def test_urllib_transport_unauthorized_token_is_a_configuration_error() -> None:
    config = FakeBotApiServerConfig()
    config.replies.append(
        ServerReply(
            status=401, body=b'{"ok": false, "error_code": 401, "description": "Unauthorized"}'
        )
    )
    with FakeBotApiServer(config) as server:
        with pytest.raises(TelegramApiError) as raised:
            _client(server.base_url).get_me()
    assert raised.value.code == bot_api.TELEGRAM_CONFIGURATION_INVALID
    _assert_no_token(raised.value)


def test_urllib_transport_timeout_is_bounded_and_retryable() -> None:
    config = FakeBotApiServerConfig()
    config.replies.append(ServerReply(delay_seconds=1.5))
    with FakeBotApiServer(config) as server:
        with pytest.raises(TelegramApiError) as raised:
            _client(server.base_url, timeout=0.3).send_message(chat_id=1, text="x")
    assert (raised.value.code, raised.value.retryable) == (bot_api.TELEGRAM_TIMEOUT, True)
    _assert_no_token(raised.value)


def test_urllib_transport_connection_refused_is_a_network_error() -> None:
    with FakeBotApiServer(FakeBotApiServerConfig()) as server:
        base_url = server.base_url
    # The server is closed now: nothing listens on that port.
    with pytest.raises(TelegramApiError) as raised:
        _client(base_url, timeout=1).get_me()
    assert (raised.value.code, raised.value.retryable) == (bot_api.TELEGRAM_NETWORK_ERROR, True)
    _assert_no_token(raised.value)
