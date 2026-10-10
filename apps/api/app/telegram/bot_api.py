"""Telegram Bot API client and error classification (Issue #329,
ADR-0047 §3.3/§5).

The only module that talks HTTP to Telegram. The bot token is part of
every request URL (`<base>/bot<token>/<method>`), so nothing raised or
returned here ever carries a URL, a urllib/socket exception, the response
description or the response body: transport failures become a
`TelegramApiError` with a stable code, raised outside the `except` block
so the original exception is not even kept as its `__context__`.

Bot API outcomes are classified conservatively into stable codes:

- retryable: `telegram_timeout`, `telegram_network_error`,
  `telegram_server_error` (HTTP 5xx), `telegram_rate_limited` (429, with
  the provider's `retry_after` seconds when given), and
  `telegram_malformed_response` (no Bot API JSON envelope);
- permanent: `telegram_configuration_invalid` (401/404 — invalid or
  revoked token, wrong API base URL), `telegram_tls_failed` (certificate
  verification), `telegram_bot_blocked` (403, the user blocked the bot or
  was deactivated), `telegram_chat_forbidden` (other 403 — e.g. the bot is
  not a member of the group), `telegram_chat_not_found`,
  `telegram_topic_unavailable` (thread not found, topic closed/deleted),
  `telegram_chat_migrated` (group upgraded to a supergroup),
  `telegram_request_rejected` (any other 4xx);
- `telegram_conflict` (409): another `getUpdates` consumer or a webhook is
  active for the same bot token — fatal for the poller (ADR-0047 §3.1).

The provider's description text is inspected in memory only to pick one of
these codes; it is never stored or logged.
"""

import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from app.core.config import TelegramSettings, validate_telegram_api_base_url

TELEGRAM_TIMEOUT = "telegram_timeout"
TELEGRAM_NETWORK_ERROR = "telegram_network_error"
TELEGRAM_TLS_FAILED = "telegram_tls_failed"
TELEGRAM_SERVER_ERROR = "telegram_server_error"
TELEGRAM_RATE_LIMITED = "telegram_rate_limited"
TELEGRAM_MALFORMED_RESPONSE = "telegram_malformed_response"
TELEGRAM_CONFIGURATION_INVALID = "telegram_configuration_invalid"
TELEGRAM_CONFLICT = "telegram_conflict"
TELEGRAM_BOT_BLOCKED = "telegram_bot_blocked"
TELEGRAM_CHAT_FORBIDDEN = "telegram_chat_forbidden"
TELEGRAM_CHAT_NOT_FOUND = "telegram_chat_not_found"
TELEGRAM_TOPIC_UNAVAILABLE = "telegram_topic_unavailable"
TELEGRAM_CHAT_MIGRATED = "telegram_chat_migrated"
TELEGRAM_REQUEST_REJECTED = "telegram_request_rejected"

# Upper bound on a response body read into memory (a getUpdates batch of
# 100 text messages is far smaller).
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

TransportFailure = Literal["timeout", "network", "tls"]


class TelegramTransportError(Exception):
    """The request did not produce an HTTP response. Carries only its
    kind — never a URL or the underlying exception."""

    def __init__(self, kind: TransportFailure) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class BotApiResponse:
    """One HTTP response: the status and the decoded JSON object (None when
    the body is not a JSON object)."""

    status: int
    body: Optional[dict[str, Any]]


class BotApiTransport(Protocol):
    """Performs one Bot API method call. Raises TelegramTransportError when
    no HTTP response was received."""

    def call(
        self, method: str, params: Mapping[str, Any], *, timeout: float
    ) -> BotApiResponse: ...


class UrllibBotApiTransport:
    """The production transport over the standard library (HTTPS with the
    default verified TLS context; honours the standard proxy environment)."""

    def __init__(self, settings: TelegramSettings) -> None:
        # Re-checked here too: the token is appended to this URL.
        self._base_url = validate_telegram_api_base_url(settings.api_base_url)
        self._token = settings.bot_token

    def __repr__(self) -> str:
        return f"UrllibBotApiTransport(base_url={self._base_url!r})"

    def call(self, method: str, params: Mapping[str, Any], *, timeout: float) -> BotApiResponse:
        request = urllib.request.Request(
            f"{self._base_url}/bot{self._token}/{method}",
            data=json.dumps(dict(params)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        failure: Optional[TransportFailure] = None
        status = 0
        raw = b""
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                status = response.status
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(_MAX_RESPONSE_BYTES + 1)
            except (OSError, http.client.HTTPException):
                raw = b""
            finally:
                exc.close()
        except (socket.timeout, TimeoutError):
            failure = "timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                failure = "timeout"
            elif isinstance(exc.reason, ssl.SSLCertVerificationError):
                failure = "tls"
            else:
                failure = "network"
        except ssl.SSLCertVerificationError:
            failure = "tls"
        except (OSError, http.client.HTTPException, ValueError):
            failure = "network"
        if failure is not None:
            # Raised here, outside the handler: no exception context (whose
            # text may contain the request URL and so the token) survives.
            raise TelegramTransportError(failure)
        return BotApiResponse(status=status, body=_decode(raw))


def _decode(raw: bytes) -> Optional[dict[str, Any]]:
    if len(raw) > _MAX_RESPONSE_BYTES:
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


class TelegramApiError(Exception):
    """A classified Bot API failure. `str()` is the stable code only."""

    def __init__(self, code: str, *, retryable: bool, retry_after: Optional[int] = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def safe_message(self) -> Optional[str]:
        return None if self.retry_after is None else f"retry_after={self.retry_after}s"


def _transport_error(kind: TransportFailure) -> TelegramApiError:
    if kind == "timeout":
        return TelegramApiError(TELEGRAM_TIMEOUT, retryable=True)
    if kind == "tls":
        return TelegramApiError(TELEGRAM_TLS_FAILED, retryable=False)
    return TelegramApiError(TELEGRAM_NETWORK_ERROR, retryable=True)


def classify_error_response(response: BotApiResponse) -> TelegramApiError:
    """Classify a response that is not a successful Bot API result."""
    body = response.body
    if body is None or not isinstance(body.get("ok"), bool):
        if response.status >= 500:
            return TelegramApiError(TELEGRAM_SERVER_ERROR, retryable=True)
        return TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)
    error_code = body.get("error_code")
    status = error_code if isinstance(error_code, int) else response.status
    raw_description = body.get("description")
    description = raw_description.lower() if isinstance(raw_description, str) else ""
    parameters = body.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}

    if status == 429:
        retry_after = parameters.get("retry_after")
        return TelegramApiError(
            TELEGRAM_RATE_LIMITED,
            retryable=True,
            retry_after=retry_after if isinstance(retry_after, int) and retry_after >= 0 else None,
        )
    if status >= 500:
        return TelegramApiError(TELEGRAM_SERVER_ERROR, retryable=True)
    if status == 409:
        return TelegramApiError(TELEGRAM_CONFLICT, retryable=False)
    if status in (401, 404):
        return TelegramApiError(TELEGRAM_CONFIGURATION_INVALID, retryable=False)
    if status == 403:
        if "blocked by the user" in description or "user is deactivated" in description:
            return TelegramApiError(TELEGRAM_BOT_BLOCKED, retryable=False)
        return TelegramApiError(TELEGRAM_CHAT_FORBIDDEN, retryable=False)
    if status == 400:
        if "migrate_to_chat_id" in parameters or "upgraded to a supergroup" in description:
            return TelegramApiError(TELEGRAM_CHAT_MIGRATED, retryable=False)
        if "chat not found" in description:
            return TelegramApiError(TELEGRAM_CHAT_NOT_FOUND, retryable=False)
        if (
            "thread not found" in description
            or "topic_closed" in description
            or "topic_deleted" in description
        ):
            return TelegramApiError(TELEGRAM_TOPIC_UNAVAILABLE, retryable=False)
    if 400 <= status < 500:
        return TelegramApiError(TELEGRAM_REQUEST_REJECTED, retryable=False)
    return TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)


@dataclass(frozen=True)
class BotIdentity:
    """The bot's own public identity from `getMe`."""

    bot_id: int
    username: Optional[str]


class BotApiClient:
    """Typed Bot API methods over a transport. Every failure is a
    TelegramApiError."""

    def __init__(self, transport: BotApiTransport, *, request_timeout: float) -> None:
        self._transport = transport
        self._request_timeout = request_timeout

    def __repr__(self) -> str:
        return f"BotApiClient(transport={self._transport!r})"

    def _call(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        try:
            response = self._transport.call(method, params, timeout=timeout)
        except TelegramTransportError as exc:
            kind = exc.kind
        else:
            body = response.body
            if response.status == 200 and body is not None and body.get("ok") is True:
                if "result" not in body:
                    raise TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)
                return body["result"]
            raise classify_error_response(response)
        raise _transport_error(kind)

    def get_me(self) -> BotIdentity:
        result = self._call("getMe", {}, timeout=self._request_timeout)
        if not isinstance(result, dict):
            raise TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)
        bot_id = result.get("id")
        username = result.get("username")
        if not isinstance(bot_id, int) or isinstance(bot_id, bool) or bot_id <= 0:
            raise TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)
        return BotIdentity(bot_id=bot_id, username=username if isinstance(username, str) else None)

    def get_updates(
        self, *, offset: Optional[int], poll_timeout: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Long poll. The HTTP timeout is the poll timeout plus the ordinary
        request timeout, so a healthy empty poll never times out locally."""
        params: dict[str, Any] = {
            "timeout": poll_timeout,
            "limit": limit,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            params["offset"] = offset
        result = self._call(
            "getUpdates", params, timeout=poll_timeout + self._request_timeout
        )
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)
        return result

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        message_thread_id: Optional[int] = None,
        parse_mode: Optional[str] = None,
    ) -> int:
        """Send `text` — plain text unless `parse_mode` is given (the
        notification renderer produces escaped `HTML`). Returns the Telegram
        message id."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        result = self._call("sendMessage", params, timeout=self._request_timeout)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise TelegramApiError(TELEGRAM_MALFORMED_RESPONSE, retryable=True)
        return message_id


def build_bot_api_client(settings: TelegramSettings) -> BotApiClient:
    return BotApiClient(
        UrllibBotApiTransport(settings), request_timeout=settings.request_timeout_seconds
    )


__all__ = [
    "TELEGRAM_TIMEOUT",
    "TELEGRAM_NETWORK_ERROR",
    "TELEGRAM_TLS_FAILED",
    "TELEGRAM_SERVER_ERROR",
    "TELEGRAM_RATE_LIMITED",
    "TELEGRAM_MALFORMED_RESPONSE",
    "TELEGRAM_CONFIGURATION_INVALID",
    "TELEGRAM_CONFLICT",
    "TELEGRAM_BOT_BLOCKED",
    "TELEGRAM_CHAT_FORBIDDEN",
    "TELEGRAM_CHAT_NOT_FOUND",
    "TELEGRAM_TOPIC_UNAVAILABLE",
    "TELEGRAM_CHAT_MIGRATED",
    "TELEGRAM_REQUEST_REJECTED",
    "TelegramTransportError",
    "BotApiResponse",
    "BotApiTransport",
    "UrllibBotApiTransport",
    "TelegramApiError",
    "classify_error_response",
    "BotIdentity",
    "BotApiClient",
    "build_bot_api_client",
]
