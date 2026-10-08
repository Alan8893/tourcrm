"""Pure-Python unit tests for the Notification/outbox write-boundary input
validation (Issue #318) — no database, no HTTP.

Every case here is rejected before the session is touched, so a
placeholder object stands in for the Session. Database-level behavior
(idempotency, transaction boundary, constraints) is covered by
tests/integration/test_notification_persistence.py.
"""

import uuid
from typing import Any

import pytest

from app.db.notifications import CommunicationPreference, Notification
from app.db.outbox import OutboxJob
from app.events.lifecycle import InvalidTimezoneError
from app.notifications.repository import (
    BlankNotificationFieldError,
    InvalidDeliveryDestinationError,
    InvalidNotificationChannelError,
    add_delivery,
    create_notification,
)
from app.notifications.vocabulary import (
    CANONICAL_DELIVERY_STATUSES,
    CANONICAL_NOTIFICATION_CHANNELS,
)
from app.outbox.security import OutboxPayloadError, assert_safe_outbox_payload
from app.outbox.service import InvalidOutboxJobError, enqueue_outbox_job

_NO_SESSION: Any = object()


def test_mvp_channels_are_email_and_telegram_only() -> None:
    assert CANONICAL_NOTIFICATION_CHANNELS == {"email", "telegram"}


def test_delivery_statuses_match_the_module_contract() -> None:
    assert CANONICAL_DELIVERY_STATUSES == {
        "pending",
        "processing",
        "delivered",
        "failed",
        "cancelled",
        "skipped",
    }


@pytest.mark.parametrize("field", ["idempotency_key", "event_type", "subject_type"])
def test_create_notification_rejects_blank_fields(field: str) -> None:
    fields: dict[str, Any] = {
        "idempotency_key": "key",
        "event_type": "test.event",
        "subject_type": "test_subject",
        "recipient_user_id": uuid.uuid4(),
    }
    fields[field] = "  "
    with pytest.raises(BlankNotificationFieldError) as exc_info:
        create_notification(_NO_SESSION, **fields)
    assert exc_info.value.field == field


def test_add_delivery_rejects_an_unknown_channel() -> None:
    with pytest.raises(InvalidNotificationChannelError):
        add_delivery(
            _NO_SESSION,
            notification=Notification(id=uuid.uuid4()),
            channel="max",
            destination_type="user",
            destination_id=uuid.uuid4(),
        )


def test_add_delivery_rejects_an_unknown_destination_type() -> None:
    with pytest.raises(InvalidDeliveryDestinationError):
        add_delivery(
            _NO_SESSION,
            notification=Notification(id=uuid.uuid4()),
            channel="email",
            destination_type="email_address",
            destination_id=uuid.uuid4(),
        )


def test_add_delivery_rejects_a_telegram_destination_on_email() -> None:
    with pytest.raises(InvalidDeliveryDestinationError):
        add_delivery(
            _NO_SESSION,
            notification=Notification(id=uuid.uuid4()),
            channel="email",
            destination_type="telegram_destination",
            destination_id=uuid.uuid4(),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"job_type": " ", "payload": {}},
        {"job_type": "test.job", "payload": {}, "deduplication_key": ""},
    ],
)
def test_enqueue_rejects_blank_identifiers(kwargs: dict[str, Any]) -> None:
    with pytest.raises(InvalidOutboxJobError):
        enqueue_outbox_job(_NO_SESSION, **kwargs)


@pytest.mark.parametrize(
    "payload",
    [
        {"smtp_password": "x"},
        {"bot_token": "x"},
        {"context": {"reset_token": "x"}},
        {"items": [{"secret": "x"}]},
        {"credentials": {}},
        {"when": object()},
    ],
)
def test_outbox_payload_rejects_secrets_and_unsafe_values(payload: dict[str, Any]) -> None:
    with pytest.raises(OutboxPayloadError):
        assert_safe_outbox_payload(payload)
    with pytest.raises(OutboxPayloadError):
        enqueue_outbox_job(_NO_SESSION, job_type="test.job", payload=payload)
    with pytest.raises(OutboxPayloadError):
        OutboxJob(job_type="test.job", payload=payload)


def test_outbox_payload_must_be_a_dict() -> None:
    with pytest.raises(OutboxPayloadError):
        assert_safe_outbox_payload(["notification_id"])  # type: ignore[arg-type]


def test_outbox_payload_accepts_identifiers_and_context() -> None:
    assert_safe_outbox_payload(
        {"notification_id": str(uuid.uuid4()), "delivery_ids": ["a", "b"], "attempt": 1}
    )


def test_communication_preference_rejects_an_invalid_quiet_hours_timezone() -> None:
    with pytest.raises(InvalidTimezoneError):
        CommunicationPreference(quiet_hours_timezone="Mars/Olympus")


def test_communication_preference_accepts_an_iana_timezone() -> None:
    preference = CommunicationPreference(quiet_hours_timezone="Europe/Moscow")
    assert preference.quiet_hours_timezone == "Europe/Moscow"
