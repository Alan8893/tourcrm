"""Integration tests for the Email channel adapter on the real worker path
(Issue #327): Worker -> NotificationDeliveryHandler -> EmailChannelAdapter
-> SMTP transport -> Delivery finalization, against real PostgreSQL.

The SMTP side is either a deterministic recording transport or the local
fake SMTP server (tests/smtp_server.py) — never an external provider.
"""

import email
import logging
import smtplib
import socket
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Optional

import pytest
import sqlalchemy as sa

from app.cli.run_outbox_worker import build_worker
from app.core.config import SmtpSettings
from app.db.identity import Club, Person, User
from app.db.notifications import Notification, NotificationDelivery, NotificationTemplate
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.notifications.delivery import NotificationDeliveryHandler
from app.notifications.email_adapter import (
    DESTINATION_INVALID,
    DESTINATION_UNVERIFIED,
    TEMPLATE_UNAVAILABLE,
    EmailChannelAdapter,
)
from app.notifications.repository import add_delivery, create_notification
from app.notifications.smtp import (
    SMTP_AUTHENTICATION_FAILED,
    SMTP_REJECTED,
    SMTP_TIMEOUT,
    SMTP_TRANSIENT_FAILURE,
)
from app.notifications.vocabulary import (
    NOTIFICATION_DELIVERY_JOB_TYPE,
    notification_delivery_deduplication_key,
)
from app.outbox.service import enqueue_outbox_job
from app.outbox.worker import OutboxWorker, WorkerConfig
from tests.smtp_server import FakeSmtpConfig, FakeSmtpServer

from .conftest import requires_postgres

SECRET = "s3cr3t-smtp-pa55word"
_VERIFIED = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _settings(**overrides: Any) -> SmtpSettings:
    fields: dict[str, Any] = {
        "host": "smtp.test",
        "port": 587,
        "security": "starttls",
        "timeout_seconds": 5.0,
        "sender_email": "noreply@club.test",
        "sender_name": "TourCRM Club",
        "username": "mailer",
        "password": SECRET,
    }
    fields.update(overrides)
    return SmtpSettings(**fields)


class _Transport:
    """Deterministic SmtpTransport double: records messages, optionally
    raises, optionally runs a probe while the "SMTP I/O" is in progress."""

    def __init__(self, raises: Optional[BaseException] = None, probe: Any = None) -> None:
        self.sent: list[tuple[EmailMessage, str, str]] = []
        self.raises = raises
        self.probe = probe

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> None:
        if self.probe is not None:
            self.probe()
        if self.raises is not None:
            raise self.raises
        self.sent.append((message, sender, recipient))


def _worker(transport: Any, *, max_attempts: int = 5) -> OutboxWorker:
    factory = get_session_factory()
    adapter = EmailChannelAdapter(
        settings=_settings(), transport=transport, session_factory=factory
    )
    return OutboxWorker(
        session_factory=factory,
        handlers={NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler({"email": adapter})},
        config=WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}", max_attempts=max_attempts),
    )


def _email_delivery(
    *,
    login: Optional[str] = "anna@club.test",
    verified: bool = True,
    person_email: Optional[str] = None,
    template_channel: Optional[str] = "email",
    subject: Optional[str] = "Поход в субботу",
    body: str = "Сбор в 8:00 у клуба.",
) -> dict[str, uuid.UUID]:
    """Business mutation + Notification + email Delivery + outbox job,
    committed together the way the Notification Engine leaves them."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        person = Person(
            last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}", email=person_email
        )
        user = User(
            person=person,
            login_identifier=login,
            status="active" if login else "pending",
            email_verified_at=_VERIFIED if verified else None,
        )
        session.add_all([club, user])
        session.flush()
        template_id = None
        if template_channel is not None:
            template = NotificationTemplate(
                code=f"tpl-{uuid.uuid4().hex[:8]}",
                channel=template_channel,
                locale="ru",
                subject_template=subject,
                body_template=body,
            )
            session.add(template)
            session.flush()
            template_id = template.id
        notification, _ = create_notification(
            session,
            idempotency_key=f"test:{uuid.uuid4()}",
            event_type="test.event",
            subject_type="test_subject",
            recipient_user_id=user.id,
            club_id=club.id,
            template_id=template_id,
        )
        delivery, _ = add_delivery(
            session,
            notification=notification,
            channel="email",
            destination_type="user",
            destination_id=user.id,
        )
        job, _ = enqueue_outbox_job(
            session,
            job_type=NOTIFICATION_DELIVERY_JOB_TYPE,
            payload={"delivery_id": str(delivery.id)},
            deduplication_key=notification_delivery_deduplication_key(delivery.id),
        )
        session.commit()
        return {
            "club": club.id,
            "notification": notification.id,
            "delivery": delivery.id,
            "job": job.id,
        }


def _delivery(delivery_id: uuid.UUID) -> NotificationDelivery:
    with session_scope() as session:
        row = session.get(NotificationDelivery, delivery_id)
        assert row is not None
        return row


def _job(job_id: uuid.UUID) -> OutboxJob:
    with session_scope() as session:
        row = session.get(OutboxJob, job_id)
        assert row is not None
        return row


# --- Success ----------------------------------------------------------------------------


@requires_postgres
def test_verified_user_is_emailed_and_the_delivery_is_delivered() -> None:
    ids = _email_delivery()
    transport = _Transport()
    (report,) = _worker(transport).run_once()

    assert report.result == "completed"
    ((message, sender, recipient),) = transport.sent
    assert (sender, recipient) == ("noreply@club.test", "anna@club.test")
    parsed = email.message_from_bytes(message.as_bytes(), policy=email.policy.default)
    assert parsed["From"] == "TourCRM Club <noreply@club.test>"
    assert parsed["To"] == "anna@club.test"
    assert parsed["Subject"] == "Поход в субботу"
    assert parsed.get_content_type() == "text/plain"
    assert parsed.get_content().strip() == "Сбор в 8:00 у клуба."
    delivery = _delivery(ids["delivery"])
    assert delivery.status == "delivered"
    assert delivery.provider_message_id == str(message["Message-ID"])
    assert _job(ids["job"]).status == "completed"


@requires_postgres
def test_template_without_subject_sends_without_a_subject_header() -> None:
    _email_delivery(subject=None)
    transport = _Transport()
    _worker(transport).run_once()
    ((message, _, _),) = transport.sent
    assert message["Subject"] is None


@requires_postgres
def test_full_path_through_the_production_worker_and_a_real_smtp_conversation() -> None:
    ids = _email_delivery()
    config = FakeSmtpConfig(username="mailer", password=SECRET)
    with FakeSmtpServer(config) as server:
        settings = _settings(host="127.0.0.1", port=server.port, security="none")
        worker = build_worker(
            WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"), smtp_settings=settings
        )
        (report,) = worker.run_once()

    assert report.result == "completed"
    (received,) = config.received
    assert received.rcpt_to == ["anna@club.test"]
    assert b"Subject: =?utf-8?" in received.data
    assert _delivery(ids["delivery"]).status == "delivered"


# --- Destination (PO decision G1) ----------------------------------------------------------


@requires_postgres
def test_unverified_user_is_a_terminal_failure_without_retry() -> None:
    ids = _email_delivery(verified=False)
    transport = _Transport()
    (report,) = _worker(transport, max_attempts=5).run_once()

    assert (report.result, report.error_code) == ("dead", DESTINATION_UNVERIFIED)
    assert transport.sent == []
    delivery = _delivery(ids["delivery"])
    assert (delivery.status, delivery.next_retry_at, delivery.last_error_code) == (
        "failed",
        None,
        DESTINATION_UNVERIFIED,
    )
    assert _job(ids["job"]).attempts == 1


@requires_postgres
def test_person_email_is_never_used_as_a_fallback() -> None:
    _email_delivery(verified=False, person_email="anna.person@club.test")
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert report.error_code == DESTINATION_UNVERIFIED
    assert transport.sent == []


@requires_postgres
def test_login_identifier_wins_over_a_different_person_email() -> None:
    _email_delivery(login="anna.login@club.test", person_email="anna.person@club.test")
    transport = _Transport()
    _worker(transport).run_once()
    ((_, _, recipient),) = transport.sent
    assert recipient == "anna.login@club.test"


@requires_postgres
def test_malformed_login_identifier_is_a_terminal_failure() -> None:
    ids = _email_delivery(login="anna-without-domain")
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", DESTINATION_INVALID)
    assert transport.sent == []
    assert _delivery(ids["delivery"]).next_retry_at is None


# --- Template (PO decision G2) ----------------------------------------------------------


@pytest.mark.parametrize("template_channel", ["telegram", None])
@requires_postgres
def test_missing_or_non_email_template_is_a_terminal_failure(
    template_channel: Optional[str],
) -> None:
    ids = _email_delivery(template_channel=template_channel)
    transport = _Transport()
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", TEMPLATE_UNAVAILABLE)
    assert transport.sent == []
    assert _delivery(ids["delivery"]).status == "failed"


# --- SMTP failures -----------------------------------------------------------------------


@requires_postgres
def test_transient_smtp_failure_schedules_a_retry() -> None:
    ids = _email_delivery()
    transport = _Transport(raises=smtplib.SMTPDataError(451, b"4.3.0 try again later"))
    (report,) = _worker(transport).run_once()

    assert (report.result, report.error_code) == ("pending", SMTP_TRANSIENT_FAILURE)
    job, delivery = _job(ids["job"]), _delivery(ids["delivery"])
    assert job.status == "pending" and job.next_attempt_at is not None
    assert (delivery.status, delivery.last_error_code, delivery.last_error_message) == (
        "failed",
        SMTP_TRANSIENT_FAILURE,
        "SMTP 451",
    )
    assert delivery.next_retry_at == job.next_attempt_at


@requires_postgres
def test_timeout_schedules_a_retry() -> None:
    ids = _email_delivery()
    (report,) = _worker(_Transport(raises=socket.timeout("timed out"))).run_once()
    assert (report.result, report.error_code) == ("pending", SMTP_TIMEOUT)
    assert _delivery(ids["delivery"]).next_retry_at is not None


@requires_postgres
def test_permanent_smtp_rejection_is_terminal() -> None:
    ids = _email_delivery()
    transport = _Transport(
        raises=smtplib.SMTPRecipientsRefused({"anna@club.test": (550, b"5.1.1 unknown user")})
    )
    (report,) = _worker(transport, max_attempts=5).run_once()
    assert (report.result, report.error_code) == ("dead", SMTP_REJECTED)
    delivery = _delivery(ids["delivery"])
    assert (delivery.status, delivery.next_retry_at, delivery.last_error_message) == (
        "failed",
        None,
        "SMTP 550",
    )


@requires_postgres
def test_smtp_failure_never_rolls_back_business_data() -> None:
    ids = _email_delivery()
    _worker(_Transport(raises=ConnectionResetError(104, "reset"))).run_once()
    with session_scope() as session:
        assert session.get(Club, ids["club"]) is not None
        notification = session.get(Notification, ids["notification"])
        assert notification is not None and notification.status == "pending"


# --- Transaction boundary ------------------------------------------------------------------


@requires_postgres
def test_no_database_transaction_is_open_during_smtp_io() -> None:
    ids = _email_delivery()
    observed: dict[str, Any] = {}

    def probe() -> None:
        with session_scope() as session:
            observed["open_transactions"] = session.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                    "AND state IN ('idle in transaction', 'idle in transaction (aborted)')"
                )
            ).scalar_one()
            for table, row_id in (
                ("outbox_jobs", ids["job"]),
                ("notification_deliveries", ids["delivery"]),
            ):
                session.execute(
                    sa.text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": row_id},
                )
            session.rollback()

    (report,) = _worker(_Transport(probe=probe)).run_once()
    assert report.result == "completed"
    assert observed["open_transactions"] == 0


# --- Secrets --------------------------------------------------------------------------------


@requires_postgres
def test_smtp_password_never_reaches_state_logs_or_payloads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = _email_delivery()
    caplog.set_level(logging.DEBUG)
    # A server that echoes the credentials in its rejection text.
    config = FakeSmtpConfig(username="mailer", password="expected-other")
    with FakeSmtpServer(config) as server:
        settings = _settings(host="127.0.0.1", port=server.port, security="none")
        worker = build_worker(
            WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"), smtp_settings=settings
        )
        (report,) = worker.run_once()

    assert (report.result, report.error_code) == ("dead", SMTP_AUTHENTICATION_FAILED)
    job, delivery = _job(ids["job"]), _delivery(ids["delivery"])
    persisted = " ".join(
        str(value)
        for value in (
            job.payload,
            job.last_error_code,
            job.last_error_message,
            delivery.last_error_code,
            delivery.last_error_message,
            delivery.provider_message_id,
        )
    )
    assert SECRET not in persisted and "mailer" not in persisted
    assert delivery.last_error_message == "SMTP 535"
    assert SECRET not in caplog.text
    assert SECRET not in repr(settings)
