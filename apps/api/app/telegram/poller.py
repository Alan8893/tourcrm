"""The Telegram long-polling runtime (Issue #329, ADR-0047 §3).

A dedicated process (app.cli.run_telegram_poller) — not a FastAPI
background task and not part of the outbox worker. It never creates or
dispatches notification deliveries.

Startup (`start`):

1. `getMe` — verifies the token and yields the bot's public numeric id
   (the checkpoint key) and username. When TELEGRAM_BOT_USERNAME is set it
   must match, otherwise the API's deep links would point at another bot.
2. Single poller per bot (ADR-0047 §3.1): a PostgreSQL session-level
   advisory lock keyed on the bot id, held on a dedicated AUTOCOMMIT
   connection for the process lifetime. A second poller against the same
   database exits at once. Telegram's own `409 Conflict` (another
   `getUpdates` consumer elsewhere, or a webhook) also stops the process
   with a secret-free error instead of competing.
3. Ensure the bot's `telegram_update_checkpoints` row exists.

Each cycle (`poll_once`): `getUpdates(offset = last_update_id + 1)`, then
updates are processed **sequentially**, each in its own transaction that
locks the checkpoint row, skips an `update_id` at or below the checkpoint
(replay/duplicate — idempotent, no business effect), runs
app.telegram.updates.handle_update and advances the checkpoint to that
`update_id`, then commits. The link and the checkpoint therefore commit
atomically (ADR-0047 §4.1 step 6): a crash before the commit replays the
update; a crash after it cannot repeat the effect. Telegram itself is
only acknowledged by the next poll's offset, which is read from the
committed checkpoint. A processing error rolls back and stops the batch;
the update is retried after a bounded backoff. The bot's reply is sent
after the commit (at most once; a lost reply is not re-sent).

Transient Bot API/transport failures (and database or unexpected errors) back off
exponentially, bounded by `backoff_max`; a 429 waits `retry_after`
(bounded the same way). SIGTERM/SIGINT set `stop`: the poller finishes
the update in progress and exits; unprocessed updates of the batch are
replayed by the next run.

Logs carry only safe fields — update id, outcome, error code — never the
token, the message text or a Telegram user/chat id.
"""

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.db.telegram import TelegramUpdateCheckpoint
from app.telegram.bot_api import (
    TELEGRAM_CONFLICT,
    BotApiClient,
    BotIdentity,
    TelegramApiError,
)
from app.telegram.updates import UpdateResult, handle_update

logger = logging.getLogger("tourcrm.telegram_poller")

EXIT_OK = 0
EXIT_UNAVAILABLE = 1
EXIT_CONFIGURATION = 2
EXIT_CONFLICT = 3

_ADVISORY_LOCK_NAMESPACE = "tourcrm.telegram_poller:"


class PollerConfigurationError(ValueError):
    """A poller setting is invalid."""


class PollerFatalError(Exception):
    """The poller must stop. `exit_code` is the process exit status;
    `str()` is a safe operational message."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class PollerConfig:
    """Environment (all optional): TELEGRAM_POLL_TIMEOUT_SECONDS,
    TELEGRAM_POLL_BACKOFF_BASE_SECONDS, TELEGRAM_POLL_BACKOFF_MAX_SECONDS."""

    # Telegram long-poll wait per getUpdates request.
    poll_timeout_seconds: int = 20
    batch_limit: int = 100
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0

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

    def _identify(self, stop: threading.Event) -> Optional[BotIdentity]:
        failures = 0
        while not stop.is_set():
            try:
                return self._client.get_me()
            except TelegramApiError as exc:
                if not exc.retryable:
                    raise PollerFatalError(
                        f"telegram poller cannot start error_code={exc.code}",
                        EXIT_CONFLICT if exc.code == TELEGRAM_CONFLICT else EXIT_CONFIGURATION,
                    ) from None
                failures += 1
                delay = self._delay(failures, exc)
                logger.warning(
                    "telegram getMe failed error_code=%s retry_in_s=%.1f", exc.code, delay
                )
                stop.wait(delay)
        return None

    def start(self, stop: threading.Event) -> bool:
        """Identify the bot, take the single-poller lock and ensure the
        checkpoint row. False when stopped before identification."""
        identity = self._identify(stop)
        if identity is None:
            return False
        if self._expected_username is not None and (
            identity.username is None
            or identity.username.lower() != self._expected_username.lower()
        ):
            raise PollerFatalError(
                "TELEGRAM_BOT_USERNAME does not match the bot of TELEGRAM_BOT_TOKEN",
                EXIT_CONFIGURATION,
            )
        self.bot_id = identity.bot_id
        connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        acquired = connection.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
            {"key": f"{_ADVISORY_LOCK_NAMESPACE}{identity.bot_id}"},
        ).scalar_one()
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
        return True

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
        Raises TelegramApiError/SQLAlchemyError for the run loop."""
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

    def _delay(self, failures: int, exc: Optional[TelegramApiError] = None) -> float:
        delay = backoff_delay(failures, self._config)
        if exc is not None and exc.retry_after is not None:
            delay = min(max(float(exc.retry_after), delay), self._config.backoff_max_seconds)
        return delay

    def run(self, stop: threading.Event) -> int:
        """Run until `stop` or a fatal error. Returns the exit status."""
        try:
            if not self.start(stop):
                return EXIT_OK
            failures = 0
            while not stop.is_set():
                try:
                    self.poll_once(stop)
                    failures = 0
                except TelegramApiError as exc:
                    if exc.code == TELEGRAM_CONFLICT:
                        raise PollerFatalError(
                            "telegram getUpdates conflict: another poller or a webhook is "
                            f"active for bot_id={self.bot_id}; stopping",
                            EXIT_CONFLICT,
                        ) from None
                    if not exc.retryable:
                        raise PollerFatalError(
                            f"telegram poller stopped error_code={exc.code}", EXIT_CONFIGURATION
                        ) from None
                    failures += 1
                    delay = self._delay(failures, exc)
                    logger.warning(
                        "telegram getUpdates failed error_code=%s retry_in_s=%.1f",
                        exc.code,
                        delay,
                    )
                    stop.wait(delay)
                except SQLAlchemyError:
                    failures += 1
                    delay = self._delay(failures)
                    logger.warning(
                        "telegram poller database error error_code=database_error "
                        "retry_in_s=%.1f",
                        delay,
                    )
                    stop.wait(delay)
                except Exception as exc:  # noqa: BLE001 - keep polling, bounded
                    # Only the type: exception text could carry update content.
                    failures += 1
                    delay = self._delay(failures)
                    logger.error(
                        "telegram poller unexpected error error_code=internal_error "
                        "type=%s retry_in_s=%.1f",
                        type(exc).__name__,
                        delay,
                    )
                    stop.wait(delay)
            logger.info("telegram poller stopped bot_id=%s", self.bot_id)
            return EXIT_OK
        except PollerFatalError as exc:
            logger.error("%s", exc)
            return exc.exit_code
        except SQLAlchemyError:
            # Only reachable from startup (the loop handles its own).
            logger.error("telegram poller cannot start error_code=database_error")
            return EXIT_UNAVAILABLE
        finally:
            self.close()


__all__ = [
    "EXIT_OK",
    "EXIT_UNAVAILABLE",
    "EXIT_CONFIGURATION",
    "EXIT_CONFLICT",
    "PollerConfigurationError",
    "PollerFatalError",
    "PollerConfig",
    "backoff_delay",
    "TelegramPoller",
]
