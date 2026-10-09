"""Settings-driven channel adapters of the outbox worker (Issue #333,
ADR-0048 §2.8/§2.9, ADR-0047 §6).

The worker registers one of these per channel. For every delivery attempt
each one, in a short read-only session closed before any network I/O:

1. reads the current channel configuration (app.notification_settings.
   runtime). Anything but `configured` — not configured, incomplete,
   invalid, or a secret that cannot be decrypted — is the worker's existing
   retryable `channel_adapter_unavailable`, bounded by its attempt limit;
   the safe message names only the configuration state;
2. builds the existing EmailChannelAdapter / TelegramChannelAdapter with
   that configuration and delegates to it unchanged.

So a saved settings change applies to the next attempt without restarting
the worker, and the adapters' own semantics are untouched. The Global Admin
Policy is not checked here: a channel switched OFF pauses its Deliveries
before any attempt starts (app.notifications.delivery, ADR-0048 §2.8), so
an adapter is only called for an enabled channel. A channel switched OFF
while a send is already in progress does not cancel that send.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import SmtpSettings, TelegramSettings
from app.notification_settings.runtime import load_email_runtime, load_telegram_runtime
from app.notification_settings.vocabulary import CONFIG_CONFIGURED
from app.notifications.delivery import (
    CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE,
    ChannelResult,
    DeliveryRequest,
)
from app.notifications.email_adapter import EmailChannelAdapter
from app.notifications.smtp import SmtplibTransport, SmtpTransport
from app.notifications.telegram_adapter import TelegramChannelAdapter
from app.telegram.bot_api import BotApiClient, build_bot_api_client

SmtpTransportFactory = Callable[[SmtpSettings], SmtpTransport]
BotApiClientFactory = Callable[[TelegramSettings], BotApiClient]


def _unavailable(state: str) -> ChannelResult:
    return ChannelResult.retryable(CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE, f"configuration={state}")


class ConfiguredEmailAdapter:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        transport_factory: SmtpTransportFactory = SmtplibTransport,
    ) -> None:
        self._session_factory = session_factory
        self._transport_factory = transport_factory

    def __repr__(self) -> str:
        return "ConfiguredEmailAdapter()"

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        session: Session
        with self._session_factory() as session:
            runtime = load_email_runtime(session)
            session.rollback()
        if runtime.state != CONFIG_CONFIGURED or runtime.settings is None:
            return _unavailable(runtime.state)
        adapter = EmailChannelAdapter(
            settings=runtime.settings,
            transport=self._transport_factory(runtime.settings),
            session_factory=self._session_factory,
        )
        return adapter.deliver(request)


class ConfiguredTelegramAdapter:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        client_factory: BotApiClientFactory = build_bot_api_client,
    ) -> None:
        self._session_factory = session_factory
        self._client_factory = client_factory

    def __repr__(self) -> str:
        return "ConfiguredTelegramAdapter()"

    def deliver(self, request: DeliveryRequest) -> ChannelResult:
        session: Session
        with self._session_factory() as session:
            runtime = load_telegram_runtime(session)
            session.rollback()
        if runtime.state != CONFIG_CONFIGURED or runtime.settings is None:
            return _unavailable(runtime.state)
        adapter = TelegramChannelAdapter(
            client=self._client_factory(runtime.settings), session_factory=self._session_factory
        )
        return adapter.deliver(request)


__all__ = [
    "SmtpTransportFactory",
    "BotApiClientFactory",
    "ConfiguredEmailAdapter",
    "ConfiguredTelegramAdapter",
]
