"""The Telegram long-polling process (Issue #329, ADR-0047 §3):

    python -m app.cli.run_telegram_poller

A separate deployable process — not part of FastAPI and not part of the
outbox worker. Run exactly ONE replica per bot token: a second one exits
at once (PostgreSQL advisory lock), and Telegram's `409 Conflict` (another
consumer elsewhere, or a webhook) stops the process with exit status 3.

Configuration: DATABASE_URL, TELEGRAM_BOT_TOKEN (secret, required),
TELEGRAM_BOT_USERNAME (when set, must match the token's bot),
TELEGRAM_API_BASE_URL, TELEGRAM_REQUEST_TIMEOUT_SECONDS and
app.telegram.poller.PollerConfig (TELEGRAM_POLL_* variables).

Exit status: 0 after SIGTERM/SIGINT, 1 database unavailable at startup,
2 missing/invalid configuration (including a rejected token),
3 another poller/webhook is active for the bot.
"""

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Optional

from app.core.config import ConfigurationError, get_telegram_settings
from app.db.session import get_engine, get_session_factory
from app.telegram.bot_api import build_bot_api_client
from app.telegram.poller import (
    EXIT_CONFIGURATION,
    PollerConfig,
    PollerConfigurationError,
    TelegramPoller,
)

logger = logging.getLogger("tourcrm.telegram_poller")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = get_telegram_settings()
        config = PollerConfig.from_env()
    except (ConfigurationError, PollerConfigurationError) as exc:
        # These messages name the variable, never its value.
        logger.error("telegram poller configuration invalid: %s", exc)
        return EXIT_CONFIGURATION
    if settings is None:
        logger.error("telegram poller configuration invalid: TELEGRAM_BOT_TOKEN is not set")
        return EXIT_CONFIGURATION

    poller = TelegramPoller(
        client=build_bot_api_client(settings),
        engine=get_engine(),
        session_factory=get_session_factory(),
        config=config,
        expected_username=settings.bot_username,
    )
    stop = threading.Event()

    def request_stop(signum: int, frame: Optional[FrameType]) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    return poller.run(stop)


if __name__ == "__main__":
    sys.exit(main())
