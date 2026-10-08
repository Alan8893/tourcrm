"""The outbox worker process (Issue #325, ADR-0046 §5.1).

Runs separately from FastAPI and from the scheduler (ADR-0044):

    python -m app.cli.run_outbox_worker

Consumes committed `outbox_jobs` rows until SIGTERM/SIGINT, then stops
claiming, finishes the job in progress and hands back claimed-but-not-
started jobs (ADR-0046 §5.6). Configuration: app.outbox.worker.WorkerConfig
(OUTBOX_WORKER_* environment variables) plus DATABASE_URL.

Registered handlers: `notification.delivery`, with the Email channel
adapter when SMTP is configured (SMTP_* environment, Issue #327; invalid
SMTP configuration stops the worker at startup). A channel without an
adapter (Telegram — a separate Issue — or Email without SMTP_HOST) is
retried as `channel_adapter_unavailable` and ends as a terminal failure
once its attempts are exhausted.
"""

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Optional

from app.core.config import SmtpSettings, get_smtp_settings
from app.db.session import get_session_factory
from app.notifications.delivery import ChannelAdapter, NotificationDeliveryHandler
from app.notifications.email_adapter import EmailChannelAdapter
from app.notifications.smtp import SmtplibTransport
from app.notifications.vocabulary import CHANNEL_EMAIL, NOTIFICATION_DELIVERY_JOB_TYPE
from app.outbox.worker import OutboxWorker, WorkerConfig


def build_worker(
    config: WorkerConfig, smtp_settings: Optional[SmtpSettings] = None
) -> OutboxWorker:
    session_factory = get_session_factory()
    adapters: dict[str, ChannelAdapter] = {}
    if smtp_settings is not None:
        adapters[CHANNEL_EMAIL] = EmailChannelAdapter(
            settings=smtp_settings,
            transport=SmtplibTransport(smtp_settings),
            session_factory=session_factory,
        )
    return OutboxWorker(
        session_factory=session_factory,
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler(adapters)},
        config=config,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = build_worker(WorkerConfig.from_env(), get_smtp_settings())
    stop = threading.Event()

    def request_stop(signum: int, frame: Optional[FrameType]) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
