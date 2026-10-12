"""The outbox worker process (Issue #325, ADR-0046 §5.1).

Runs separately from FastAPI and from the scheduler (ADR-0044):

    python -m app.cli.run_outbox_worker

Consumes committed `outbox_jobs` rows until SIGTERM/SIGINT, then stops
claiming, finishes the job in progress and hands back claimed-but-not-
started jobs (ADR-0046 §5.6). Configuration: app.outbox.worker.WorkerConfig
(OUTBOX_WORKER_* environment variables), DATABASE_URL and the settings key
ring SETTINGS_ENCRYPTION_KEYS, and the optional public frontend address
APP_PUBLIC_BASE_URL used for links in notification messages (unset: no
links; invalid: the worker does not start).

Registered handlers: `notification.delivery`, with the settings-driven
Email and Telegram adapters (app.notifications.configured_adapters, Issue
#333, ADR-0048). SMTP and the Telegram bot are configured in Settings →
Notifications and read for every delivery attempt — never from the
environment and without restarting the worker. A channel disabled by the
Global Admin Policy is PAUSED: its Deliveries are not sent and no attempt is
counted; the job is re-checked every OUTBOX_WORKER_PAUSE_RECHECK_SECONDS and
resumes once the channel is switched back on. A channel that is not (yet)
configured is retried as `channel_adapter_unavailable` and ends as a
terminal failure once its attempts are exhausted. The worker only sends
Telegram messages; it never polls for Telegram updates (that is
app.cli.run_telegram_poller).

Password-reset code emails (Issue #342) need AUTH_DELIVERY_ENCRYPTION_KEYS
to decrypt the code in memory right before sending
(app.authentication.password_reset_delivery); without it such a Delivery
is retried as `delivery_encryption_unavailable` and then fails. The same
component discards the encrypted copy when the Delivery finishes.

At start the worker logs whether each key ring is usable (variable name and
state only — never a key), so a missing key is visible before the first
delivery fails. Exit status: 0 after SIGTERM/SIGINT, 2 invalid worker
configuration (OUTBOX_WORKER_*, APP_PUBLIC_BASE_URL), logged as one line
without a traceback. A database that is briefly unreachable does not end
the process (app.outbox.worker.OutboxWorker.run).
"""

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Optional

from app.authentication.password_reset_delivery import (
    AUTH_DELIVERY_KEY_RING_ENV,
    PASSWORD_RESET_EVENT_TYPE,
    PasswordResetCodeDelivery,
)
from app.core.config import ConfigurationError, get_public_base_url
from app.db.session import get_session_factory
from app.notification_settings.crypto import KEY_RING_ENV, KeyRing, KeyRingUnavailable
from app.notification_settings.policy import GlobalAdminPolicy
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
from app.outbox.worker import OutboxWorker, WorkerConfig, WorkerConfigurationError
from app.telegram.bot_api import build_bot_api_client

logger = logging.getLogger("tourcrm.worker")

EXIT_CONFIGURATION = 2

# What each key ring is needed for, for the startup diagnostics.
_KEY_RING_PURPOSES = (
    (KEY_RING_ENV, "SMTP password / Telegram bot token from Settings → Notifications"),
    (AUTH_DELIVERY_KEY_RING_ENV, "password-reset code emails (same value as the API)"),
)


def log_key_ring_status() -> None:
    """One line per key ring: its variable name and state, never a key."""
    for env_name, purpose in _KEY_RING_PURPOSES:
        try:
            KeyRing.from_env(env_name)
        except KeyRingUnavailable as exc:
            logger.warning(
                "outbox worker key_ring=%s state=%s: %s cannot be used until it is set",
                env_name,
                exc.reason,
                purpose,
            )
            continue
        logger.info("outbox worker key_ring=%s state=available", env_name)


def build_worker(
    config: WorkerConfig,
    *,
    smtp_transport_factory: SmtpTransportFactory = SmtplibTransport,
    telegram_client_factory: BotApiClientFactory = build_bot_api_client,
) -> OutboxWorker:
    session_factory = get_session_factory()
    password_reset_codes = PasswordResetCodeDelivery()
    adapters: dict[str, ChannelAdapter] = {
        CHANNEL_EMAIL: ConfiguredEmailAdapter(
            session_factory=session_factory,
            transport_factory=smtp_transport_factory,
            secret_resolvers={PASSWORD_RESET_EVENT_TYPE: password_reset_codes},
        ),
        CHANNEL_TELEGRAM: ConfiguredTelegramAdapter(
            session_factory=session_factory,
            client_factory=telegram_client_factory,
            # ADR-0049 §2.7: an invalid value stops the worker at start.
            public_base_url=get_public_base_url(),
        ),
    }
    handler = NotificationDeliveryHandler(
        adapters,
        admin_policy=GlobalAdminPolicy(),
        pause_recheck=config.pause_recheck,
        secret_cleanup=password_reset_codes,
    )
    return OutboxWorker(
        session_factory=session_factory,
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: handler},
        config=config,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        worker = build_worker(WorkerConfig.from_env())
    except (WorkerConfigurationError, ConfigurationError) as exc:
        # These messages name the setting, never its value or a secret.
        logger.error("outbox worker configuration invalid: %s", exc)
        return EXIT_CONFIGURATION
    log_key_ring_status()
    stop = threading.Event()

    def request_stop(signum: int, frame: Optional[FrameType]) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
