"""Telegram Bot API test doubles (Issue #329) — never a real provider.

- `ScriptedTransport`: an in-memory `BotApiTransport`; per method, a queue
  of scripted outcomes (a `BotApiResponse` or a `TelegramTransportError`),
  falling back to a per-method default. Records every call.
- `FakeBotApiServer`: a local HTTP server speaking the Bot API URL shape
  (`/bot<token>/<method>`), used to exercise the real urllib transport
  end-to-end (status codes, non-JSON bodies, slow responses).

Shared by tests/unit and tests/integration, like tests/smtp_server.py.
"""

import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Union

from app.telegram.bot_api import BotApiClient, BotApiResponse, TelegramTransportError

# Format-valid, obviously fake bot token used by every Telegram test.
BOT_TOKEN = "123456789:AAFakeBotTokenForTestsOnly_0123456789xyz"
BOT_ID = 123456789
BOT_USERNAME = "tourcrm_test_bot"

Outcome = Union[BotApiResponse, TelegramTransportError, Callable[[Mapping[str, Any]], Any]]


def ok(result: Any) -> BotApiResponse:
    return BotApiResponse(200, {"ok": True, "result": result})


def api_error(
    status: int, description: str, parameters: Optional[dict[str, Any]] = None
) -> BotApiResponse:
    body: dict[str, Any] = {"ok": False, "error_code": status, "description": description}
    if parameters is not None:
        body["parameters"] = parameters
    return BotApiResponse(status, body)


@dataclass
class Call:
    method: str
    params: dict[str, Any]
    timeout: float


@dataclass
class ScriptedTransport:
    defaults: dict[str, Outcome] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    _queues: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def script(self, method: str, *outcomes: Outcome) -> "ScriptedTransport":
        self._queues[method].extend(outcomes)
        return self

    def call(self, method: str, params: Mapping[str, Any], *, timeout: float) -> BotApiResponse:
        self.calls.append(Call(method, dict(params), timeout))
        queue = self._queues[method]
        outcome = queue.popleft() if queue else self.defaults.get(method)
        if outcome is None:
            raise AssertionError(f"unscripted Bot API call {method}")
        if callable(outcome):
            outcome = outcome(params)
        if isinstance(outcome, TelegramTransportError):
            raise outcome
        assert isinstance(outcome, BotApiResponse)
        return outcome

    def calls_to(self, method: str) -> list[Call]:
        return [call for call in self.calls if call.method == method]


def get_me_ok(username: str = BOT_USERNAME, bot_id: int = BOT_ID) -> BotApiResponse:
    return ok({"id": bot_id, "is_bot": True, "first_name": "TourCRM", "username": username})


def client_for(transport: ScriptedTransport, *, request_timeout: float = 5.0) -> BotApiClient:
    return BotApiClient(transport, request_timeout=request_timeout)


def private_start(update_id: int, telegram_user_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": telegram_user_id, "type": "private", "first_name": "Anna"},
            "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Anna"},
            "text": text,
        },
    }


def group_message(update_id: int, telegram_user_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": -1001234567890, "type": "supergroup", "title": "Club"},
            "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Anna"},
            "text": text,
        },
    }


@dataclass
class ServerReply:
    status: int = 200
    body: bytes = b'{"ok": true, "result": true}'
    delay_seconds: float = 0.0


@dataclass
class FakeBotApiServerConfig:
    # Queued replies are used first, in order; then the per-method default
    # (keyed by Bot API method name); then a generic `ok: true`.
    replies: deque = field(default_factory=deque)
    by_method: dict[str, ServerReply] = field(default_factory=dict)
    received: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


class FakeBotApiServer:
    """`with FakeBotApiServer(config) as server: server.base_url`."""

    def __init__(self, config: FakeBotApiServerConfig) -> None:
        self.config = config
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    params = json.loads(raw or b"{}")
                except ValueError:
                    params = {}
                outer.config.received.append((self.path, params))
                method = self.path.rsplit("/", 1)[-1]
                if outer.config.replies:
                    reply = outer.config.replies.popleft()
                else:
                    reply = outer.config.by_method.get(method, ServerReply())
                if reply.delay_seconds:
                    time.sleep(reply.delay_seconds)
                try:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(reply.body)))
                    self.end_headers()
                    self.wfile.write(reply.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeBotApiServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
