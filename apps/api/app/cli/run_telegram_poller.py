"""The Telegram long-polling process (Issue #329, ADR-0047 §3; dynamic
configuration Issue #333, ADR-0047 §6):

    python -m app.cli.run_telegram_poller

A separate deployable process — not part of FastAPI and not part of the
outbox worker. Run exactly ONE replica: a second poller for the same bot
exits at once (PostgreSQL advisory lock), and Telegram's `409 Conflict`
(another consumer elsewhere, or a webhook) stops the process with exit
status 3.

The bot token and username are read from Settings → Notifications
(PostgreSQL) between polls, never from the environment; a changed token is
picked up without a restart. Environment: DATABASE_URL, the settings key
ring SETTINGS_ENCRYPTION_KEYS (to decrypt the token), the
app.telegram.poller.PollerConfig tuning (TELEGRAM_POLL_*) and, for local
fake servers only, TELEGRAM_API_BASE_URL.

Exit status: 0 after SIGTERM/SIGINT, 2 invalid poller tuning
configuration, 3 another poller/webhook is active for the bot.
"""

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Optional

from app.db.session import get_engine, get_session_factory
from app.telegram.poller import (
    EXIT_CONFIGURATION,
    PollerConfig,
    PollerConfigurationError,
    TelegramPollerService,
)

logger = logging.getLogger("tourcrm.telegram_poller")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = PollerConfig.from_env()
    except PollerConfigurationError as exc:
        # These messages name the variable, never a secret.
        logger.error("telegram poller configuration invalid: %s", exc)
        return EXIT_CONFIGURATION

    service = TelegramPollerService(
        engine=get_engine(), session_factory=get_session_factory(), config=config
    )
    stop = threading.Event()

    def request_stop(signum: int, frame: Optional[FrameType]) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    return service.run(stop)


if __name__ == "__main__":
    sys.exit(main())
