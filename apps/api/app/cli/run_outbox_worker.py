"""The outbox worker process (Issue #325, ADR-0046 §5.1).

Runs separately from FastAPI and from the scheduler (ADR-0044):

    python -m app.cli.run_outbox_worker

Consumes committed `outbox_jobs` rows until SIGTERM/SIGINT, then stops
claiming, finishes the job in progress and hands back claimed-but-not-
started jobs (ADR-0046 §5.6). Configuration: app.outbox.worker.WorkerConfig
(OUTBOX_WORKER_* environment variables) plus DATABASE_URL.

Registered handlers: `notification.delivery`. No channel adapter is
registered yet — Email/Telegram adapters are separate Issues — so a
Delivery is retried as `channel_adapter_unavailable` and ends as a
terminal failure once its attempts are exhausted.
"""

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Optional

from app.db.session import get_session_factory
from app.notifications.delivery import NotificationDeliveryHandler
from app.notifications.vocabulary import NOTIFICATION_DELIVERY_JOB_TYPE
from app.outbox.worker import OutboxWorker, WorkerConfig


def build_worker(config: WorkerConfig) -> OutboxWorker:
    return OutboxWorker(
        session_factory=get_session_factory(),
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler(adapters={})},
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
