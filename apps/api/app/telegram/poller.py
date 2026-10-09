"""The Telegram long-polling runtime (Issue #329, ADR-0047 §3; dynamic
configuration Issue #333, ADR-0047 §6 / ADR-0048 §2.9).

A dedicated process (app.cli.run_telegram_poller) — not a FastAPI
background task and not part of the outbox worker. It never creates or
dispatches notification deliveries.

Two layers:

- `TelegramPoller` — one polling session for one bot token:
  1. `start`: `getMe` verifies the token and yields the bot's public
     numeric id (the checkpoint key); the configured bot username, when
     set, must match. Then the single-poller lock (ADR-0047 §3.1): a
     PostgreSQL session-level advisory lock keyed on the bot id, held on a
     dedicated AUTOCOMMIT connection, and the bot's
     `telegram_update_checkpoints` row is ensured. A second poller for the
     same bot is a fatal conflict (exit 3).
  2. `poll_once`: `getUpdates(offset = last_update_id + 1)`, then updates
     are processed **sequentially**, each in its own transaction that locks
     the checkpoint row, skips an `update_id` at or below the checkpoint
     (replay/duplicate — idempotent, no business effect), runs
     app.telegram.updates.handle_update and advances the checkpoint, then
     commits. The link and the checkpoint commit atomically (ADR-0047 §4.1
     step 6): a crash before the commit replays the update; a crash after
     it cannot repeat the effect. Telegram is acknowledged only by the next
     poll's offset, read from the committed checkpoint. The bot's reply is
     sent after the commit (at most once).
  3. `close`: releases the advisory lock.

- `TelegramPollerService` — the process loop. Between polls it re-reads the
  Telegram configuration from PostgreSQL (app.notification_settings.runtime,
  a short session closed before any Bot API call). When the token (or the
  bot username) changes, the current session finishes its update in
  progress and is closed — releasing the old bot's lock — before a new
  session verifies the new token and takes the new bot's lock, so two poll
  loops never run for one token and each bot continues from its own
  checkpoint. While Telegram is not configured, its token cannot be
  decrypted, Telegram rejects it (401/404) or the username does not match
  the token's bot, no lock is held, nothing is polled and the configuration
  is re-checked every `config_interval_seconds`. The Global Admin Policy's
  Telegram switch is not consulted: it governs delivery only.

Transient Bot API/transport failures, database errors and unexpected errors
back off exponentially, bounded by `backoff_max`; a 429 waits `retry_after`
(bounded the same way). Only a conflict (another poller for the bot, or
Telegram's `409 Conflict`) stops the process. SIGTERM/SIGINT set `stop`:
the poller finishes the update in progress and exits; unprocessed updates
of the batch are replayed by the next run.

Logs carry only safe fields — bot id, update id, state, outcome, error
code — never the token, the message text or a Telegram user/chat id.
"""

import hashlib
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.config import TelegramSettings
from app.db.telegram import TelegramUpdateCheckpoint
from app.notification_settings.runtime import TelegramRuntime, load_telegram_runtime
from app.notification_settings.vocabulary import CONFIG_CONFIGURED
from app.telegram.bot_api import (
    TELEGRAM_CONFIGURATION_INVALID,
    TELEGRAM_CONFLICT,
    BotApiClient,
    TelegramApiError,
    build_bot_api_client,
)
from app.telegram.updates import UpdateResult, handle_update

logger = logging.getLogger("tourcrm.telegram_poller")

EXIT_OK = 0
EXIT_CONFIGURATION = 2
EXIT_CONFLICT = 3

# Service states (logged on change; ADR-0047 §6).
STATE_POLLING = "polling"
STATE_TOKEN_REJECTED = "token_rejected"
STATE_USERNAME_MISMATCH = "username_mismatch"
STATE_BOT_API_UNAVAILABLE = "bot_api_unavailable"

_ADVISORY_LOCK_NAMESPACE = "tourcrm.telegram_poller:"


class PollerConfigurationError(ValueError):
    """A poller setting is invalid."""


class UsernameMismatch(Exception):
    """The configured bot username is not the token's bot."""


class PollerFatalError(Exception):
    """The poller must stop. `exit_code` is the process exit status;
    `str()` is a safe operational message."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class PollerConfig:
    """Environment (all optional): TELEGRAM_POLL_TIMEOUT_SECONDS,
    TELEGRAM_POLL_BACKOFF_BASE_SECONDS, TELEGRAM_POLL_BACKOFF_MAX_SECONDS,
    TELEGRAM_POLL_CONFIG_INTERVAL_SECONDS. Process tuning only — the bot
    token and username come from Settings (ADR-0048)."""

    # Telegram long-poll wait per getUpdates request.
    poll_timeout_seconds: int = 20
    batch_limit: int = 100
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    # Re-check interval while Telegram is not usable.
    config_interval_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not 1 <= self.poll_timeout_seconds <= 50:
            raise PollerConfigurationError("TELEGRAM_POLL_TIMEOUT_SECONDS must be in [1, 50]")
        if not 1 <= self.batch_limit <= 100:
            raise PollerConfigurationError("batch_limit must be in [1, 100]")
        if self.backoff_base_seconds <= 0:
            raise PollerConfigurationError("TELEGRAM_POLL_BACKOFF_BASE_SECONDS must be positive")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise PollerConfigurationError(
                "TELEGRAM_POLL_BACKOFF_MAX_SECONDS must not be below the base"
            )
        if not 0 < self.config_interval_seconds <= 3600:
            raise PollerConfigurationError(
                "TELEGRAM_POLL_CONFIG_INTERVAL_SECONDS must be in (0, 3600]"
            )

    @classmethod
    def from_env(cls) -> "PollerConfig":
        def number(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None or not raw.strip():
                return default
            try:
                return float(raw)
            except ValueError:
                raise PollerConfigurationError(f"{name} must be a number") from None

        defaults = cls()
        return cls(
            poll_timeout_seconds=int(
                number("TELEGRAM_POLL_TIMEOUT_SECONDS", defaults.poll_timeout_seconds)
            ),
            backoff_base_seconds=number(
                "TELEGRAM_POLL_BACKOFF_BASE_SECONDS", defaults.backoff_base_seconds
            ),
            backoff_max_seconds=number(
                "TELEGRAM_POLL_BACKOFF_MAX_SECONDS", defaults.backoff_max_seconds
            ),
            config_interval_seconds=number(
                "TELEGRAM_POLL_CONFIG_INTERVAL_SECONDS", defaults.config_interval_seconds
            ),
        )


def backoff_delay(failures: int, config: PollerConfig) -> float:
    """Delay after the `failures`-th consecutive failure (1-based)."""
    delay = config.backoff_base_seconds * (2 ** min(failures - 1, 30))
    return min(delay, config.backoff_max_seconds)


def _update_id(update: dict[str, Any]) -> Optional[int]:
    value = update.get("update_id")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


class TelegramPoller:
    """One polling session for one bot token (see the module docstring)."""

    def __init__(
        self,
        *,
        client: BotApiClient,
        engine: Engine,
        session_factory: sessionmaker,
        config: PollerConfig,
        expected_username: Optional[str] = None,
    ) -> None:
        self._client = client
        self._engine = engine
        self._session_factory = session_factory
        self._config = config
        self._expected_username = expected_username
        self._lock_connection: Optional[Connection] = None
        self.bot_id: Optional[int] = None

    def __repr__(self) -> str:
        return f"TelegramPoller(bot_id={self.bot_id!r})"

    # --- startup / shutdown --------------------------------------------------

    def start(self) -> None:
        """Identify the bot, take the single-poller lock and ensure the
        checkpoint row. Raises TelegramApiError (getMe failed),
        UsernameMismatch, or PollerFatalError (another poller holds the
        bot's lock)."""
        identity = self._client.get_me()
        if self._expected_username is not None and (
            identity.username is None
            or identity.username.lower() != self._expected_username.lower()
        ):
            raise UsernameMismatch()
        self.bot_id = identity.bot_id
        connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = connection.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": f"{_ADVISORY_LOCK_NAMESPACE}{identity.bot_id}"},
            ).scalar_one()
        except BaseException:
            connection.close()
            raise
        if not acquired:
            connection.close()
            raise PollerFatalError(
                f"another telegram poller is already active for bot_id={identity.bot_id}",
                EXIT_CONFLICT,
            )
        self._lock_connection = connection
        with self._session_factory() as session:
            session.execute(
                pg_insert(TelegramUpdateCheckpoint)
                .values(bot_id=identity.bot_id, last_update_id=None)
                .on_conflict_do_nothing(index_elements=["bot_id"])
            )
            session.commit()
        logger.info("telegram poller started bot_id=%d", identity.bot_id)

    def close(self) -> None:
        connection, self._lock_connection = self._lock_connection, None
        if connection is None:
            return
        try:
            connection.execute(
                sa.text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": f"{_ADVISORY_LOCK_NAMESPACE}{self.bot_id}"},
            )
        except SQLAlchemyError:
            pass
        finally:
            connection.close()
        logger.info("telegram poller released bot_id=%s", self.bot_id)

    def _ensure_lock_held(self) -> None:
        connection = self._lock_connection
        try:
            if connection is None:
                raise SQLAlchemyError("no lock connection")
            connection.execute(sa.text("SELECT 1"))
        except SQLAlchemyError:
            # The session-level lock ended with its connection; another
            # poller may have taken over.
            raise PollerFatalError(
                "telegram poller lost its single-poller lock", EXIT_CONFLICT
            ) from None

    # --- processing --------------------------------------------------------

    def checkpoint(self) -> Optional[int]:
        with self._session_factory() as session:
            return session.execute(
                sa.select(TelegramUpdateCheckpoint.last_update_id).where(
                    TelegramUpdateCheckpoint.bot_id == self.bot_id
                )
            ).scalar_one()

    def process_update(self, update: dict[str, Any]) -> Optional[UpdateResult]:
        """Process one update and advance the checkpoint in one
        transaction. None when the update was skipped (already processed or
        without a usable update_id). Database errors propagate after the
        rollback, leaving the update eligible for replay."""
        update_id = _update_id(update)
        if update_id is None:
            logger.warning("telegram update without a valid update_id ignored")
            return None
        with self._session_factory() as session:
            try:
                checkpoint = session.execute(
                    sa.select(TelegramUpdateCheckpoint)
                    .where(TelegramUpdateCheckpoint.bot_id == self.bot_id)
                    .with_for_update()
                ).scalar_one()
                if checkpoint.last_update_id is not None and update_id <= checkpoint.last_update_id:
                    session.rollback()
                    logger.info("telegram update update_id=%d outcome=duplicate", update_id)
                    return None
                result = handle_update(session, update)
                checkpoint.last_update_id = update_id
                session.commit()
            except BaseException:
                session.rollback()
                raise
        logger.info("telegram update update_id=%d outcome=%s", update_id, result.outcome)
        return result

    def _reply(self, result: UpdateResult) -> None:
        if result.reply_chat_id is None or result.reply_text is None:
            return
        try:
            self._client.send_message(chat_id=result.reply_chat_id, text=result.reply_text)
        except TelegramApiError as exc:
            logger.warning(
                "telegram reply failed outcome=%s error_code=%s", result.outcome, exc.code
            )

    def poll_once(self, stop: threading.Event) -> int:
        """One getUpdates round. Returns the number of updates processed.
        Raises TelegramApiError/SQLAlchemyError for the service loop."""
        self._ensure_lock_held()
        last = self.checkpoint()
        updates = self._client.get_updates(
            offset=None if last is None else last + 1,
            poll_timeout=self._config.poll_timeout_seconds,
            limit=self._config.batch_limit,
        )
        processed = 0
        for update in sorted(updates, key=lambda item: _update_id(item) or -1):
            if stop.is_set():
                break
            try:
                result = self.process_update(update)
            except SQLAlchemyError:
                logger.error(
                    "telegram update processing failed update_id=%s error_code=database_error",
                    _update_id(update),
                )
                raise
            if result is not None:
                processed += 1
                self._reply(result)
        return processed


ClientFactory = Callable[[TelegramSettings], BotApiClient]


def _fingerprint(settings: TelegramSettings) -> bytes:
    """In-memory identity of a configuration (never logged or stored)."""
    return hashlib.sha256(
        f"{settings.bot_token}\0{settings.bot_username or ''}".encode("utf-8")
    ).digest()


class TelegramPollerService:
    """The poller process loop with dynamic configuration (module docstring)."""

    def __init__(
        self,
        *,
        engine: Engine,
        session_factory: sessionmaker,
        config: PollerConfig,
        client_factory: ClientFactory = build_bot_api_client,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._config = config
        self._client_factory = client_factory
        self._active: Optional[TelegramPoller] = None
        self._active_fingerprint: Optional[bytes] = None
        # A configuration Telegram rejected or whose username mismatched:
        # not retried until the configuration changes.
        self._rejected_fingerprint: Optional[bytes] = None
        self.state: Optional[str] = None

    def __repr__(self) -> str:
        bot_id = self._active.bot_id if self._active is not None else None
        return f"TelegramPollerService(state={self.state!r}, bot_id={bot_id!r})"

    @property
    def active_bot_id(self) -> Optional[int]:
        return self._active.bot_id if self._active is not None else None

    def _set_state(self, state: str) -> None:
        if state != self.state:
            logger.info("telegram poller state=%s bot_id=%s", state, self.active_bot_id)
            self.state = state

    def _load(self) -> TelegramRuntime:
        with self._session_factory() as session:
            runtime = load_telegram_runtime(session)
            session.rollback()
        return runtime

    def _deactivate(self) -> None:
        active, self._active, self._active_fingerprint = self._active, None, None
        if active is not None:
            active.close()

    def _delay(self, failures: int, exc: Optional[TelegramApiError] = None) -> float:
        delay = backoff_delay(failures, self._config)
        if exc is not None and exc.retry_after is not None:
            delay = min(max(float(exc.retry_after), delay), self._config.backoff_max_seconds)
        return delay

    def _activate(self, settings: TelegramSettings, fingerprint: bytes) -> Optional[str]:
        """Start a session for `settings`. Returns None when polling, or the
        state to wait in. Raises TelegramApiError (retryable) and
        PollerFatalError."""
        poller = TelegramPoller(
            client=self._client_factory(settings),
            engine=self._engine,
            session_factory=self._session_factory,
            config=self._config,
            expected_username=settings.bot_username,
        )
        try:
            poller.start()
        except UsernameMismatch:
            poller.close()
            self._rejected_fingerprint = fingerprint
            return STATE_USERNAME_MISMATCH
        except TelegramApiError as exc:
            poller.close()
            if exc.code == TELEGRAM_CONFLICT:
                raise PollerFatalError(
                    "telegram getUpdates conflict: another poller or a webhook is active; "
                    "stopping",
                    EXIT_CONFLICT,
                ) from None
            if exc.code == TELEGRAM_CONFIGURATION_INVALID:
                self._rejected_fingerprint = fingerprint
                return STATE_TOKEN_REJECTED
            if not exc.retryable:
                logger.warning("telegram getMe failed error_code=%s", exc.code)
                return STATE_BOT_API_UNAVAILABLE
            raise
        except BaseException:
            poller.close()
            raise
        self._active, self._active_fingerprint = poller, fingerprint
        self._rejected_fingerprint = None
        return None

    def run_once(self, stop: threading.Event) -> Optional[float]:
        """One supervision step. Returns how long to wait before the next
        step (None: continue at once). Raises TelegramApiError,
        SQLAlchemyError and PollerFatalError for `run`."""
        runtime = self._load()
        settings = runtime.settings if runtime.state == CONFIG_CONFIGURED else None
        fingerprint = _fingerprint(settings) if settings is not None else None
        if self._active is not None and fingerprint != self._active_fingerprint:
            logger.info("telegram poller configuration changed bot_id=%s", self.active_bot_id)
            self._deactivate()
        if settings is None or fingerprint is None:
            self._set_state(runtime.state)
            return self._config.config_interval_seconds
        if fingerprint == self._rejected_fingerprint:
            return self._config.config_interval_seconds
        if self._active is None:
            waiting = self._activate(settings, fingerprint)
            if waiting is not None:
                self._set_state(waiting)
                return self._config.config_interval_seconds
            self._set_state(STATE_POLLING)
        assert self._active is not None
        try:
            self._active.poll_once(stop)
        except TelegramApiError as exc:
            if exc.code == TELEGRAM_CONFLICT:
                raise PollerFatalError(
                    "telegram getUpdates conflict: another poller or a webhook is active for "
                    f"bot_id={self.active_bot_id}; stopping",
                    EXIT_CONFLICT,
                ) from None
            if not exc.retryable:
                if exc.code == TELEGRAM_CONFIGURATION_INVALID:
                    self._rejected_fingerprint = fingerprint
                    state = STATE_TOKEN_REJECTED
                else:
                    logger.warning("telegram getUpdates failed error_code=%s", exc.code)
                    state = STATE_BOT_API_UNAVAILABLE
                self._deactivate()
                self._set_state(state)
                return self._config.config_interval_seconds
            raise
        return None

    def run(self, stop: threading.Event) -> int:
        """Run until `stop` or a conflict. Returns the exit status."""
        failures = 0
        try:
            while not stop.is_set():
                try:
                    wait = self.run_once(stop)
                    failures = 0
                except TelegramApiError as exc:
                    failures += 1
                    wait = self._delay(failures, exc)
                    logger.warning(
                        "telegram getUpdates failed error_code=%s retry_in_s=%.1f",
                        exc.code,
                        wait,
                    )
                except SQLAlchemyError:
                    failures += 1
                    wait = self._delay(failures)
                    logger.warning(
                        "telegram poller database error error_code=database_error "
                        "retry_in_s=%.1f",
                        wait,
                    )
                except PollerFatalError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep polling, bounded
                    # Only the type: exception text could carry update content.
                    failures += 1
                    wait = self._delay(failures)
                    logger.error(
                        "telegram poller unexpected error error_code=internal_error "
                        "type=%s retry_in_s=%.1f",
                        type(exc).__name__,
                        wait,
                    )
                if wait is not None:
                    stop.wait(wait)
            logger.info("telegram poller stopped bot_id=%s", self.active_bot_id)
            return EXIT_OK
        except PollerFatalError as exc:
            logger.error("%s", exc)
            return exc.exit_code
        finally:
            self._deactivate()


__all__ = [
    "EXIT_OK",
    "EXIT_CONFIGURATION",
    "EXIT_CONFLICT",
    "STATE_POLLING",
    "STATE_TOKEN_REJECTED",
    "STATE_USERNAME_MISMATCH",
    "STATE_BOT_API_UNAVAILABLE",
    "PollerConfigurationError",
    "PollerFatalError",
    "UsernameMismatch",
    "PollerConfig",
    "backoff_delay",
    "TelegramPoller",
    "ClientFactory",
    "TelegramPollerService",
]
