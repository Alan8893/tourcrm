"""The outbox worker process (Issue #325, ADR-0046 §5.1).

Runs separately from FastAPI and from the scheduler (ADR-0044):

    python -m app.cli.run_outbox_worker

Consumes committed `outbox_jobs` rows until SIGTERM/SIGINT, then stops
claiming, finishes the job in progress and hands back claimed-but-not-
started jobs (ADR-0046 §5.6). Configuration: app.outbox.worker.WorkerConfig
(OUTBOX_WORKER_* environment variables), DATABASE_URL and the settings key
ring SETTINGS_ENCRYPTION_KEYS.

Registered handlers: `notification.delivery`, with the settings-driven
Email and Telegram adapters (app.notifications.configured_adapters, Issue
#333, ADR-0048). SMTP and the Telegram bot are configured in Settings →
Notifications and read for every delivery attempt — never from the
environment and without restarting the worker. A channel disabled by the
Global Admin Policy ends as a terminal `channel_disabled_by_policy`; one that
is not (yet) configured is retried as `channel_adapter_unavailable` and ends
as a terminal failure once its attempts are exhausted. The worker only sends
Telegram messages; it never polls for Telegram updates (that is
app.cli.run_telegram_poller).
"""

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Optional

from app.db.session import get_session_factory
from app.notifications.configured_adapters import (
    BotApiClientFactory,
    ConfiguredEmailAdapter,
    ConfiguredTelegramAdapter,
    SmtpTransportFactory,
)
from app.notifications.delivery import ChannelAdapter, NotificationDeliveryHandler
from app.notifications.smtp import SmtplibTransport
from app.notifications.vocabulary import (
    CHANNEL_EMAIL,
    CHANNEL_TELEGRAM,
    NOTIFICATION_DELIVERY_JOB_TYPE,
)
from app.outbox.worker import OutboxWorker, WorkerConfig
from app.telegram.bot_api import build_bot_api_client


def build_worker(
    config: WorkerConfig,
    *,
    smtp_transport_factory: SmtpTransportFactory = SmtplibTransport,
    telegram_client_factory: BotApiClientFactory = build_bot_api_client,
) -> OutboxWorker:
    session_factory = get_session_factory()
    adapters: dict[str, ChannelAdapter] = {
        CHANNEL_EMAIL: ConfiguredEmailAdapter(
            session_factory=session_factory, transport_factory=smtp_transport_factory
        ),
        CHANNEL_TELEGRAM: ConfiguredTelegramAdapter(
            session_factory=session_factory, client_factory=telegram_client_factory
        ),
    }
    return OutboxWorker(
        session_factory=session_factory,
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler(adapters)},
        config=config,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = build_worker(WorkerConfig.from_env())
    stop = threading.Event()

    def request_stop(signum: int, frame: Optional[FrameType]) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
