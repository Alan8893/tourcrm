"""The `notification.delivery` outbox job handler and the channel adapter
boundary (Issue #325, ADR-0045 §2.2/§2.5/§2.9/§2.10, ADR-0046 §5).

The handler runs in the worker after the business transaction committed.
It never creates a Notification, never re-runs the Notification Engine and
never touches business or Notification state — only the one Delivery its
job names (payload `{"delivery_id": "<uuid>"}`).

Per attempt:

1. fenced transaction — load the Delivery. Already terminal
   (`delivered`, `cancelled`, `skipped`, or `failed` with no retry
   scheduled) -> the job completes with no channel work (idempotent
   re-processing). If the Global Admin Policy (the AdminPolicy port,
   ADR-0048 §2.8) has the Delivery's channel OFF, the Delivery is
   **paused**: nothing is sent and nothing is counted — the Delivery keeps
   its status and attempts, and the job is deferred (`HandlerResult.
   deferred`) for `pause_recheck` with the claim's attempt not counted, so
   it resumes by the ordinary path once the channel is switched back on.
   Otherwise mark it `processing` and count the attempt (`attempts`,
   `first_attempt_at`, `last_attempt_at`).
2. no transaction held — hand an immutable `DeliveryRequest` to the
   channel's `ChannelAdapter`. Email/Telegram adapters are separate
   Issues; a channel without a registered adapter is a retryable
   `channel_adapter_unavailable` failure, bounded like any other.
3. in the worker's finalize transaction, atomically with the job result:
   success -> `delivered` (+ `delivered_at`, `provider_message_id`);
   retry -> `failed` with `next_retry_at` = the job's next attempt;
   terminal failure -> `failed` with no `next_retry_at`. The last safe
   error code/message is kept (ADR-0045 §2.9).

Only these existing Delivery statuses are used
(docs/04-modules/notifications-and-communications.md §4); no new status
is introduced.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Optional, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.notifications import NotificationDelivery
from app.notifications.ports import AdminPolicy
from app.notifications.vocabulary import (
    DELIVERY_CANCELLED,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_PROCESSING,
    DELIVERY_SKIPPED,
    NOTIFICATION_DELIVERY_JOB_TYPE,
)
from app.outbox.vocabulary import OUTBOX_COMPLETED, OUTBOX_PENDING
from app.outbox.worker import (
    LEASE_EXPIRED_ERROR_CODE,
    LEASE_LOST_RESULT,
    Disposition,
    HandlerResult,
    JobLease,
    RecordFn,
)

INVALID_PAYLOAD_ERROR_CODE = "invalid_payload"
DELIVERY_NOT_FOUND_ERROR_CODE = "delivery_not_found"
CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE = "channel_adapter_unavailable"
# Not an error: the job's reason code while it is paused by Global OFF.
CHANNEL_DISABLED_BY_POLICY = "channel_disabled_by_policy"
ADAPTER_ERROR_CODE = "channel_adapter_error"

_TERMINAL_STATUSES = frozenset({DELIVERY_DELIVERED, DELIVERY_CANCELLED, DELIVERY_SKIPPED})


@dataclass(frozen=True)
class DeliveryRequest:
    """What a channel adapter receives: internal references only. The
    adapter resolves the concrete address/rendering itself."""

    delivery_id: uuid.UUID
    notification_id: uuid.UUID
    channel: str
    destination_type: str
    destination_id: uuid.UUID
    # 1-based attempt number of this Delivery.
    attempt: int


@dataclass(frozen=True)
class ChannelResult:
    """An adapter's outcome. `error_code`/`error_message` must be safe to
    persist and display (no credentials, tokens or message bodies)."""

    kind: Literal["delivered", "retryable_failure", "permanent_failure"]
    provider_message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def delivered(cls, provider_message_id: Optional[str] = None) -> "ChannelResult":
        return cls("delivered", provider_message_id=provider_message_id)

    @classmethod
    def retryable(cls, code: str, message: Optional[str] = None) -> "ChannelResult":
        return cls("retryable_failure", error_code=code, error_message=message)

    @classmethod
    def permanent(cls, code: str, message: Optional[str] = None) -> "ChannelResult":
        return cls("permanent_failure", error_code=code, error_message=message)


class ChannelAdapter(Protocol):
    """One channel's provider integration (Email, Telegram — separate
    Issues). Called with no database transaction held; must return within
    the worker lease. At-least-once: after an uncertain provider failure it
    may be called again for the same Delivery (ADR-0046 §5.5)."""

    def deliver(self, request: DeliveryRequest) -> ChannelResult: ...


def _start_attempt(
    session: Session, delivery_id: uuid.UUID, admin_policy: Optional[AdminPolicy]
) -> DeliveryRequest | str:
    """Fenced step 1. Returns the request to send, `"terminal"` when the
    Delivery needs no further work, `"paused"` when its channel is disabled
    by the Global Admin Policy (nothing written), or `"missing"`."""
    delivery = session.execute(
        sa.select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update()
    ).scalar_one_or_none()
    if delivery is None:
        return "missing"
    if delivery.status in _TERMINAL_STATUSES or (
        delivery.status == DELIVERY_FAILED and delivery.next_retry_at is None
    ):
        return "terminal"
    if admin_policy is not None and not admin_policy.global_channel_enabled(
        session, channel=delivery.channel
    ):
        return "paused"
    session.execute(
        sa.update(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .values(
            status=DELIVERY_PROCESSING,
            attempts=NotificationDelivery.attempts + 1,
            first_attempt_at=sa.func.coalesce(
                NotificationDelivery.first_attempt_at, sa.func.now()
            ),
            last_attempt_at=sa.func.now(),
            next_retry_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    return DeliveryRequest(
        delivery_id=delivery.id,
        notification_id=delivery.notification_id,
        channel=delivery.channel,
        destination_type=delivery.destination_type,
        destination_id=delivery.destination_id,
        attempt=delivery.attempts + 1,
    )


def _recorder(delivery_id: uuid.UUID, result: ChannelResult) -> RecordFn:
    def record(session: Session, disposition: Disposition) -> None:
        if disposition.status == OUTBOX_COMPLETED:
            values: dict[str, object] = {
                "status": DELIVERY_DELIVERED,
                "delivered_at": sa.func.now(),
                "provider_message_id": result.provider_message_id,
                "next_retry_at": None,
            }
        else:
            values = {
                "status": DELIVERY_FAILED,
                "next_retry_at": (
                    disposition.next_attempt_at
                    if disposition.status == OUTBOX_PENDING
                    else None
                ),
                "last_error_code": result.error_code,
                "last_error_message": (
                    None if result.error_message is None else result.error_message[:1024]
                ),
            }
        session.execute(
            sa.update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    return record


def _pause_recorder(delivery_id: uuid.UUID) -> RecordFn:
    """A paused Delivery keeps its status, attempts and last error. Only a
    Delivery already waiting for a retry (`failed` with `next_retry_at`)
    has that time moved to the job's new eligibility, so the two agree."""

    def record(session: Session, disposition: Disposition) -> None:
        session.execute(
            sa.update(NotificationDelivery)
            .where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status == DELIVERY_FAILED,
                NotificationDelivery.next_retry_at.is_not(None),
            )
            .values(next_retry_at=disposition.next_attempt_at)
            .execution_options(synchronize_session=False)
        )

    return record


class NotificationDeliveryHandler:
    """Handler for `notification.delivery` jobs. `adapters` maps a channel
    to its adapter; no adapter is built into the worker."""

    job_type = NOTIFICATION_DELIVERY_JOB_TYPE

    def __init__(
        self,
        adapters: Mapping[str, ChannelAdapter],
        *,
        admin_policy: Optional[AdminPolicy] = None,
        pause_recheck: timedelta = timedelta(seconds=60),
    ) -> None:
        """`admin_policy`, when given, pauses Deliveries of a channel the
        Global Admin Policy disables (re-checked every `pause_recheck`)."""
        self._adapters = dict(adapters)
        self._admin_policy = admin_policy
        self._pause_recheck = pause_recheck

    def __call__(self, lease: JobLease) -> HandlerResult:
        try:
            delivery_id = uuid.UUID(str(lease.job.payload["delivery_id"]))
        except (KeyError, ValueError, TypeError):
            return HandlerResult.permanent(INVALID_PAYLOAD_ERROR_CODE)

        started = lease.run_fenced(
            lambda session: _start_attempt(session, delivery_id, self._admin_policy)
        )
        if started is None:
            # Lease lost before any work; the finalize step will see it too.
            return HandlerResult.retryable(LEASE_LOST_RESULT)
        if started == "missing":
            return HandlerResult.permanent(DELIVERY_NOT_FOUND_ERROR_CODE)
        if started == "terminal":
            return HandlerResult.success()
        if started == "paused":
            return HandlerResult.deferred(
                CHANNEL_DISABLED_BY_POLICY, self._pause_recheck, _pause_recorder(delivery_id)
            )
        assert isinstance(started, DeliveryRequest)

        adapter = self._adapters.get(started.channel)
        if adapter is None:
            result = ChannelResult.retryable(CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE)
        else:
            try:
                result = adapter.deliver(started)
            except Exception as exc:  # noqa: BLE001 - recorded as a retryable attempt
                # Only the exception type is kept: its text may carry secrets.
                result = ChannelResult.retryable(ADAPTER_ERROR_CODE, type(exc).__name__)

        record = _recorder(delivery_id, result)
        if result.kind == "delivered":
            return HandlerResult.success(record)
        if result.kind == "permanent_failure":
            return HandlerResult.permanent(
                result.error_code or ADAPTER_ERROR_CODE, result.error_message, record
            )
        return HandlerResult.retryable(
            result.error_code or ADAPTER_ERROR_CODE, result.error_message, record
        )

    def abandon(self, lease: JobLease) -> HandlerResult:
        """The job's lease expired with no attempts left (its worker kept
        dying): close the not yet terminal Delivery as a terminal failure."""

        def record(session: Session, disposition: Disposition) -> None:
            try:
                delivery_id = uuid.UUID(str(lease.job.payload["delivery_id"]))
            except (KeyError, ValueError, TypeError):
                return
            session.execute(
                sa.update(NotificationDelivery)
                .where(
                    NotificationDelivery.id == delivery_id,
                    NotificationDelivery.status.not_in(_TERMINAL_STATUSES),
                )
                .values(
                    status=DELIVERY_FAILED,
                    next_retry_at=None,
                    last_error_code=LEASE_EXPIRED_ERROR_CODE,
                    last_error_message=None,
                )
                .execution_options(synchronize_session=False)
            )

        return HandlerResult.permanent(LEASE_EXPIRED_ERROR_CODE, record=record)


__all__ = [
    "INVALID_PAYLOAD_ERROR_CODE",
    "DELIVERY_NOT_FOUND_ERROR_CODE",
    "CHANNEL_ADAPTER_UNAVAILABLE_ERROR_CODE",
    "CHANNEL_DISABLED_BY_POLICY",
    "ADAPTER_ERROR_CODE",
    "DeliveryRequest",
    "ChannelResult",
    "ChannelAdapter",
    "NotificationDeliveryHandler",
]
