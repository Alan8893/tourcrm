"""Persistence-level integration tests for the Notification Center
foundation (Issue #318, migration 8c7a78e7178a; ADR-0045 §3, ADR-0046 §5).

Covers: Notification vs channel Delivery (one Notification -> many
Deliveries), idempotency (no duplicate logical Notification / Delivery /
outbox job, without aborting the caller's transaction), the transaction
boundary (business mutation + Notification + outbox commit or roll back
together; a later delivery failure is persisted independently), the
database CHECK/UNIQUE/FK constraints, Telegram routing identity, the
outbox lease/claim structure, the absence of secret columns, and the
migration round trip.

Run against a real PostgreSQL instance, matching the rest of
tests/integration. Self-contained factories, per this codebase's
convention of not importing helpers across test files.
"""

import datetime
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.identity import Club, Person, User
from app.db.notifications import (
    CommunicationPreference,
    Notification,
    NotificationDelivery,
    NotificationRule,
    NotificationTemplate,
    TelegramDestination,
)
from app.db.outbox import OutboxJob
from app.db.session import get_engine, get_session_factory, session_scope
from app.notifications.repository import (
    add_delivery,
    create_notification,
    get_notification_by_idempotency_key,
    list_deliveries,
)
from app.outbox.security import OutboxPayloadError
from app.outbox.service import enqueue_outbox_job

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_MIGRATION_PARENT = "3b1fd730bb0d"
_NEW_TABLES = {
    "notification_templates",
    "notification_rules",
    "notifications",
    "notification_deliveries",
    "communication_preferences",
    "telegram_destinations",
    "outbox_jobs",
}
_JOB_TYPE = "test.job"
_NOW = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=datetime.timezone.utc)


def _club(session) -> Club:  # type: ignore[no-untyped-def]
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _user(session) -> User:  # type: ignore[no-untyped-def]
    person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _notify(session, user: User, key: str | None = None, **overrides: Any):  # type: ignore[no-untyped-def]
    fields: dict[str, Any] = {
        "idempotency_key": key or f"key-{uuid.uuid4().hex}",
        "event_type": "test.event",
        "subject_type": "test_subject",
        "subject_id": uuid.uuid4(),
        "recipient_user_id": user.id,
    }
    fields.update(overrides)
    return create_notification(session, **fields)


def _assert_rejected(session, constraint_name: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError) as exc_info:
        session.flush()
    assert exc_info.value.orig.diag.constraint_name == constraint_name  # type: ignore[union-attr]
    session.rollback()


def _count(model: type) -> int:
    with session_scope() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- Notification / Delivery ---------------------------------------------------


@requires_postgres
def test_create_notification_persists_a_pending_logical_notification() -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        template = NotificationTemplate(
            code="test.template", channel="email", locale="ru", body_template="Body"
        )
        session.add(template)
        session.flush()
        notification, created = _notify(
            session,
            user,
            key="k-1",
            club_id=club.id,
            template_id=template.id,
            priority=5,
            scheduled_at=_NOW,
        )
        session.commit()

    assert created is True
    with session_scope() as session:
        stored = get_notification_by_idempotency_key(session, "k-1")
        assert stored is not None
        assert stored.id == notification.id
        assert stored.status == "pending"
        assert stored.recipient_user_id == user.id
        assert stored.club_id == club.id
        assert stored.template_id == template.id
        assert stored.priority == 5
        assert stored.scheduled_at == _NOW
        assert stored.created_at is not None and stored.updated_at is not None


@requires_postgres
def test_one_notification_has_multiple_channel_deliveries() -> None:
    with session_scope() as session:
        user = _user(session)
        destination = TelegramDestination(name="Club chat", chat_id=-1001234567890)
        session.add(destination)
        notification, _ = _notify(session, user)
        email, email_created = add_delivery(
            session,
            notification=notification,
            channel="email",
            destination_type="user",
            destination_id=user.id,
        )
        telegram, telegram_created = add_delivery(
            session,
            notification=notification,
            channel="telegram",
            destination_type="telegram_destination",
            destination_id=destination.id,
        )
        session.commit()

    assert email_created and telegram_created
    with session_scope() as session:
        deliveries = list_deliveries(session, notification.id)
        assert {d.id for d in deliveries} == {email.id, telegram.id}
        assert {d.channel for d in deliveries} == {"email", "telegram"}
        assert all(d.notification_id == notification.id for d in deliveries)
        assert all(d.status == "pending" and d.attempts == 0 for d in deliveries)
        # Logical state is not channel state: the Notification row is untouched.
        assert session.get(Notification, notification.id).status == "pending"  # type: ignore[union-attr]


@requires_postgres
def test_delivery_requires_an_existing_notification() -> None:
    with session_scope() as session:
        session.add(
            NotificationDelivery(
                notification_id=uuid.uuid4(),
                channel="email",
                destination_type="user",
                destination_id=uuid.uuid4(),
            )
        )
        _assert_rejected(session, "notification_deliveries_notification_id_fkey")


@requires_postgres
def test_notification_with_a_delivery_cannot_be_deleted() -> None:
    with session_scope() as session:
        user = _user(session)
        notification, _ = _notify(session, user)
        add_delivery(
            session,
            notification=notification,
            channel="email",
            destination_type="user",
            destination_id=user.id,
        )
        session.commit()
        session.delete(notification)
        _assert_rejected(session, "notification_deliveries_notification_id_fkey")


# --- Idempotency ---------------------------------------------------------------


@requires_postgres
def test_same_idempotency_key_returns_the_existing_notification() -> None:
    with session_scope() as session:
        user = _user(session)
        first, first_created = _notify(session, user, key="event:1:user")
        second, second_created = _notify(session, user, key="event:1:user", priority=9)
        session.commit()

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert second.priority == 0  # the duplicate wrote nothing
    assert _count(Notification) == 1


@requires_postgres
def test_duplicate_across_transactions_is_prevented() -> None:
    with session_scope() as session:
        user = _user(session)
        first, _ = _notify(session, user, key="event:2:user")
        session.commit()

    with session_scope() as session:
        again, created = _notify(session, user, key="event:2:user")
        session.commit()

    assert created is False
    assert again.id == first.id
    assert _count(Notification) == 1


@requires_postgres
def test_duplicate_idempotency_does_not_abort_the_business_transaction() -> None:
    with session_scope() as session:
        user = _user(session)
        _notify(session, user, key="event:3:user")
        session.commit()

    with session_scope() as session:
        club = _club(session)  # the business mutation
        _, created = _notify(session, user, key="event:3:user")
        session.commit()
        club_id = club.id

    assert created is False
    with session_scope() as session:
        assert session.get(Club, club_id) is not None


@requires_postgres
def test_raw_duplicate_idempotency_key_is_rejected_by_the_database() -> None:
    with session_scope() as session:
        user = _user(session)
        _notify(session, user, key="event:4:user")
        session.commit()
        session.add(
            Notification(
                idempotency_key="event:4:user",
                event_type="test.event",
                subject_type="test_subject",
                recipient_user_id=user.id,
            )
        )
        _assert_rejected(session, "uq_notifications_idempotency_key")


@requires_postgres
def test_same_delivery_identity_is_created_once() -> None:
    with session_scope() as session:
        user = _user(session)
        notification, _ = _notify(session, user)
        kwargs: dict[str, Any] = {
            "notification": notification,
            "channel": "email",
            "destination_type": "user",
            "destination_id": user.id,
        }
        first, first_created = add_delivery(session, **kwargs)
        second, second_created = add_delivery(session, **kwargs)
        session.commit()

    assert (first_created, second_created) == (True, False)
    assert second.id == first.id
    assert _count(NotificationDelivery) == 1


@requires_postgres
def test_outbox_deduplication_key_enqueues_once() -> None:
    with session_scope() as session:
        first, first_created = enqueue_outbox_job(
            session, job_type=_JOB_TYPE, payload={"n": 1}, deduplication_key="dedup-1"
        )
        second, second_created = enqueue_outbox_job(
            session, job_type=_JOB_TYPE, payload={"n": 2}, deduplication_key="dedup-1"
        )
        # Jobs without a deduplication key are never deduplicated.
        enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={})
        enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={})
        session.commit()

    assert (first_created, second_created) == (True, False)
    assert second.id == first.id
    assert second.payload == {"n": 1}
    assert _count(OutboxJob) == 3


# --- Transaction boundary --------------------------------------------------------


def _business_notification_and_outbox(session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    user = _user(session)
    club = _club(session)  # the business mutation
    notification, _ = _notify(session, user, key=f"club-created:{club.id}", club_id=club.id)
    add_delivery(
        session,
        notification=notification,
        channel="email",
        destination_type="user",
        destination_id=user.id,
    )
    job, _ = enqueue_outbox_job(
        session,
        job_type=_JOB_TYPE,
        payload={"notification_id": str(notification.id)},
        deduplication_key=f"notification:{notification.id}",
    )
    return club.id, notification.id, job.id


@requires_postgres
def test_business_mutation_notification_and_outbox_commit_together() -> None:
    with session_scope() as session:
        club_id, notification_id, job_id = _business_notification_and_outbox(session)
        session.commit()

    with session_scope() as session:
        assert session.get(Club, club_id) is not None
        assert session.get(Notification, notification_id) is not None
        assert len(list_deliveries(session, notification_id)) == 1
        job = session.get(OutboxJob, job_id)
        assert job is not None
        assert job.status == "pending"
        assert job.payload == {"notification_id": str(notification_id)}


@requires_postgres
def test_business_rollback_discards_notification_and_outbox() -> None:
    with session_scope() as session:
        club_id, notification_id, job_id = _business_notification_and_outbox(session)
        session.rollback()

    with session_scope() as session:
        assert session.get(Club, club_id) is None
        assert session.get(Notification, notification_id) is None
        assert session.get(OutboxJob, job_id) is None
    assert _count(NotificationDelivery) == 0


@requires_postgres
def test_uncommitted_outbox_job_is_invisible_to_other_transactions() -> None:
    factory = get_session_factory()
    writer, reader = factory(), factory()
    try:
        job, _ = enqueue_outbox_job(writer, job_type=_JOB_TYPE, payload={})
        assert reader.get(OutboxJob, job.id) is None
        writer.commit()
        assert reader.get(OutboxJob, job.id) is not None
    finally:
        writer.close()
        reader.close()


@requires_postgres
def test_delivery_failure_persists_without_touching_the_business_transaction() -> None:
    with session_scope() as session:
        club_id, notification_id, _ = _business_notification_and_outbox(session)
        session.commit()

    # A later, separate (worker) transaction records a failed attempt.
    with session_scope() as session:
        delivery = list_deliveries(session, notification_id)[0]
        delivery.status = "failed"
        delivery.attempts = 1
        delivery.first_attempt_at = _NOW
        delivery.last_attempt_at = _NOW
        delivery.next_retry_at = _NOW + datetime.timedelta(minutes=5)
        delivery.last_error_code = "smtp_unavailable"
        delivery.last_error_message = "Connection refused"
        session.commit()

    with session_scope() as session:
        assert session.get(Club, club_id) is not None
        assert session.get(Notification, notification_id).status == "pending"  # type: ignore[union-attr]
        stored = list_deliveries(session, notification_id)[0]
        assert stored.status == "failed"
        assert stored.attempts == 1
        assert stored.next_retry_at == _NOW + datetime.timedelta(minutes=5)
        assert stored.last_error_code == "smtp_unavailable"
        assert stored.delivered_at is None


@requires_postgres
def test_terminal_delivery_states_are_persisted() -> None:
    with session_scope() as session:
        user = _user(session)
        notification, _ = _notify(session, user)
        delivery, _ = add_delivery(
            session,
            notification=notification,
            channel="email",
            destination_type="user",
            destination_id=user.id,
        )
        delivery.status = "delivered"
        delivery.attempts = 2
        delivery.first_attempt_at = _NOW
        delivery.last_attempt_at = _NOW + datetime.timedelta(minutes=1)
        delivery.delivered_at = _NOW + datetime.timedelta(minutes=1)
        delivery.provider_message_id = "<message@example.com>"
        session.commit()

        session.refresh(delivery)
        assert delivery.status == "delivered"
        assert delivery.provider_message_id == "<message@example.com>"


# --- CHECK / FK constraints ------------------------------------------------------


def _delivery(notification_id: uuid.UUID, **overrides: Any) -> NotificationDelivery:
    fields: dict[str, Any] = {
        "notification_id": notification_id,
        "channel": "email",
        "destination_type": "user",
        "destination_id": uuid.uuid4(),
    }
    fields.update(overrides)
    return NotificationDelivery(**fields)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"channel": "max"}, "ck_notification_deliveries_channel_valid"),
        ({"status": "sent"}, "ck_notification_deliveries_status_valid"),
        ({"destination_type": "chat"}, "ck_notification_deliveries_destination_type_valid"),
        (
            {"destination_type": "telegram_destination"},
            "ck_notification_deliveries_telegram_destination_channel",
        ),
        (
            {"attempts": -1, "first_attempt_at": _NOW, "last_attempt_at": _NOW},
            "ck_notification_deliveries_attempts_non_negative",
        ),
        ({"attempts": 1}, "ck_notification_deliveries_first_attempt_iff_attempted"),
        (
            {"attempts": 1, "first_attempt_at": _NOW},
            "ck_notification_deliveries_attempt_timestamps_together",
        ),
        (
            {
                "attempts": 1,
                "first_attempt_at": _NOW,
                "last_attempt_at": _NOW - datetime.timedelta(seconds=1),
            },
            "ck_notification_deliveries_attempt_timestamps_ordered",
        ),
        ({"status": "delivered"}, "ck_notification_deliveries_delivered_at_iff_delivered"),
        ({"delivered_at": _NOW}, "ck_notification_deliveries_delivered_at_iff_delivered"),
    ],
)
@requires_postgres
def test_delivery_check_constraints(overrides: dict[str, Any], constraint: str) -> None:
    with session_scope() as session:
        notification, _ = _notify(session, _user(session))
        session.commit()
        session.add(_delivery(notification.id, **overrides))
        _assert_rejected(session, constraint)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "delivered"}, "ck_notifications_status_valid"),
        ({"idempotency_key": "  "}, "ck_notifications_idempotency_key_not_blank"),
        ({"event_type": ""}, "ck_notifications_event_type_not_blank"),
        ({"subject_type": " "}, "ck_notifications_subject_type_not_blank"),
        ({"recipient_user_id": uuid.uuid4()}, "notifications_recipient_user_id_fkey"),
        ({"club_id": uuid.uuid4()}, "notifications_club_id_fkey"),
        ({"template_id": uuid.uuid4()}, "notifications_template_id_fkey"),
    ],
)
@requires_postgres
def test_notification_constraints(overrides: dict[str, Any], constraint: str) -> None:
    with session_scope() as session:
        user = _user(session)
        session.commit()
        fields: dict[str, Any] = {
            "idempotency_key": f"key-{uuid.uuid4().hex}",
            "event_type": "test.event",
            "subject_type": "test_subject",
            "recipient_user_id": user.id,
        }
        fields.update(overrides)
        session.add(Notification(**fields))
        _assert_rejected(session, constraint)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"channel": "sms"}, "ck_notification_templates_channel_valid"),
        ({"code": " "}, "ck_notification_templates_code_not_blank"),
        ({"locale": ""}, "ck_notification_templates_locale_not_blank"),
        ({"body_template": " "}, "ck_notification_templates_body_not_blank"),
        ({"version": 0}, "ck_notification_templates_version_positive"),
    ],
)
@requires_postgres
def test_template_constraints(overrides: dict[str, Any], constraint: str) -> None:
    fields: dict[str, Any] = {
        "code": "tpl",
        "channel": "email",
        "locale": "ru",
        "body_template": "Body",
    }
    fields.update(overrides)
    with session_scope() as session:
        session.add(NotificationTemplate(**fields))
        _assert_rejected(session, constraint)


@requires_postgres
def test_template_code_is_unique() -> None:
    with session_scope() as session:
        session.add(
            NotificationTemplate(code="tpl", channel="email", locale="ru", body_template="A")
        )
        session.commit()
        session.add(
            NotificationTemplate(code="tpl", channel="telegram", locale="en", body_template="B")
        )
        _assert_rejected(session, "uq_notification_templates_code")


def _rule(club_id: uuid.UUID | None = None, **overrides: Any) -> NotificationRule:
    fields: dict[str, Any] = {
        "club_id": club_id,
        "event_type": "test.event",
        "channel": "email",
        "recipient_scope": "test_scope",
    }
    fields.update(overrides)
    return NotificationRule(**fields)


@requires_postgres
def test_rule_is_unique_per_club_event_channel_and_scope() -> None:
    with session_scope() as session:
        club = _club(session)
        session.add_all(
            [
                _rule(club.id, scheduling={"any": {"extensible": ["shape"]}}),
                _rule(club.id, channel="telegram"),
                _rule(club.id, recipient_scope="other_scope"),
                _rule(None),  # installation-wide rule
            ]
        )
        session.commit()
        assert session.execute(select(func.count()).select_from(NotificationRule)).scalar() == 4

        session.add(_rule(club.id, is_enabled=False))
        _assert_rejected(session, "uq_notification_rules_club_event_channel_scope")


@requires_postgres
def test_installation_wide_rule_is_unique_too() -> None:
    with session_scope() as session:
        session.add(_rule(None))
        session.commit()
        session.add(_rule(None))
        _assert_rejected(session, "uq_notification_rules_club_event_channel_scope")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"channel": "push"}, "ck_notification_rules_channel_valid"),
        ({"event_type": " "}, "ck_notification_rules_event_type_not_blank"),
        ({"recipient_scope": ""}, "ck_notification_rules_recipient_scope_not_blank"),
        ({"scheduling": [1, 2]}, "ck_notification_rules_scheduling_is_object"),
        ({"club_id": uuid.uuid4()}, "notification_rules_club_id_fkey"),
    ],
)
@requires_postgres
def test_rule_constraints(overrides: dict[str, Any], constraint: str) -> None:
    with session_scope() as session:
        session.add(_rule(**overrides))
        _assert_rejected(session, constraint)


# --- Communication preferences ---------------------------------------------------


@requires_postgres
def test_communication_preference_is_unique_per_user_channel_and_type() -> None:
    with session_scope() as session:
        user = _user(session)
        session.add_all(
            [
                CommunicationPreference(
                    user_id=user.id,
                    channel="email",
                    notification_type="test.event",
                    enabled=False,
                ),
                CommunicationPreference(
                    user_id=user.id,
                    channel="telegram",
                    notification_type="test.event",
                    enabled=True,
                    quiet_hours_start=datetime.time(22, 0),
                    quiet_hours_end=datetime.time(7, 0),
                    quiet_hours_timezone="Europe/Moscow",
                ),
            ]
        )
        session.commit()

        session.add(
            CommunicationPreference(
                user_id=user.id, channel="email", notification_type="test.event", enabled=True
            )
        )
        _assert_rejected(session, "uq_communication_preferences_user_channel_destination_type")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"channel": "max"}, "ck_communication_preferences_channel_valid"),
        ({"notification_type": " "}, "ck_communication_preferences_notification_type_not_blank"),
        (
            {"quiet_hours_start": datetime.time(22, 0)},
            "ck_communication_preferences_quiet_hours_complete",
        ),
        (
            {
                "quiet_hours_start": datetime.time(22, 0),
                "quiet_hours_end": datetime.time(7, 0),
            },
            "ck_communication_preferences_quiet_hours_complete",
        ),
        (
            {
                "quiet_hours_start": datetime.time(22, 0),
                "quiet_hours_end": datetime.time(22, 0),
                "quiet_hours_timezone": "UTC",
            },
            "ck_communication_preferences_quiet_hours_non_empty",
        ),
        ({"user_id": uuid.uuid4()}, "communication_preferences_user_id_fkey"),
    ],
)
@requires_postgres
def test_communication_preference_constraints(overrides: dict[str, Any], constraint: str) -> None:
    with session_scope() as session:
        user = _user(session)
        session.commit()
        fields: dict[str, Any] = {
            "user_id": user.id,
            "channel": "email",
            "notification_type": "test.event",
            "enabled": True,
        }
        fields.update(overrides)
        session.add(CommunicationPreference(**fields))
        _assert_rejected(session, constraint)


# --- Telegram destinations ---------------------------------------------------------


@requires_postgres
def test_telegram_destination_preserves_routing_identity() -> None:
    supergroup_chat_id = -1009876543210123  # beyond 32 bits, negative for groups
    with session_scope() as session:
        club = _club(session)
        chat = TelegramDestination(club_id=club.id, name="Club chat", chat_id=supergroup_chat_id)
        topic = TelegramDestination(
            club_id=club.id,
            name="Club chat / Trips",
            chat_id=supergroup_chat_id,
            message_thread_id=42,
            topic_name="Походы",
            notification_scope={"event_types": ["test.event"]},
        )
        session.add_all([chat, topic])
        session.commit()
        chat_id, topic_id = chat.id, topic.id

    with session_scope() as session:
        stored_chat = session.get(TelegramDestination, chat_id)
        stored_topic = session.get(TelegramDestination, topic_id)
        assert stored_chat is not None and stored_topic is not None
        assert stored_chat.chat_id == supergroup_chat_id
        assert stored_chat.message_thread_id is None
        assert stored_chat.enabled is True
        assert stored_chat.notification_scope == {}
        assert stored_topic.chat_id == supergroup_chat_id
        assert stored_topic.message_thread_id == 42
        assert stored_topic.notification_scope == {"event_types": ["test.event"]}

        # Renaming the Topic is presentation-only: routing identity is unchanged.
        stored_topic.topic_name = "Походы и сборы"
        session.commit()
        session.refresh(stored_topic)
        assert (stored_topic.chat_id, stored_topic.message_thread_id) == (supergroup_chat_id, 42)


@requires_postgres
def test_topic_name_is_not_a_routing_identity() -> None:
    with session_scope() as session:
        session.add(
            TelegramDestination(name="A", chat_id=-100555, message_thread_id=7, topic_name="News")
        )
        session.commit()
        # Same thread id under another display name is the same routing target.
        session.add(
            TelegramDestination(name="B", chat_id=-100555, message_thread_id=7, topic_name="Other")
        )
        _assert_rejected(session, "uq_telegram_destinations_chat_id_message_thread_id")

        # Same display name for a different thread is a different target.
        session.add(
            TelegramDestination(name="C", chat_id=-100555, message_thread_id=8, topic_name="News")
        )
        session.commit()


@requires_postgres
def test_chat_without_topic_is_unique() -> None:
    with session_scope() as session:
        session.add(TelegramDestination(name="A", chat_id=-100777))
        session.commit()
        session.add(TelegramDestination(name="B", chat_id=-100777))
        _assert_rejected(session, "uq_telegram_destinations_chat_id_message_thread_id")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"name": " "}, "ck_telegram_destinations_name_not_blank"),
        ({"chat_id": 0}, "ck_telegram_destinations_chat_id_non_zero"),
        ({"message_thread_id": 0}, "ck_telegram_destinations_message_thread_id_positive"),
        ({"notification_scope": ["x"]}, "ck_telegram_destinations_notification_scope_is_object"),
        ({"club_id": uuid.uuid4()}, "telegram_destinations_club_id_fkey"),
    ],
)
@requires_postgres
def test_telegram_destination_constraints(overrides: dict[str, Any], constraint: str) -> None:
    fields: dict[str, Any] = {"name": "Chat", "chat_id": -100123}
    fields.update(overrides)
    with session_scope() as session:
        session.add(TelegramDestination(**fields))
        _assert_rejected(session, constraint)


# --- Outbox ------------------------------------------------------------------------


@requires_postgres
def test_outbox_job_defaults_to_pending_and_immediately_eligible() -> None:
    with session_scope() as session:
        job, created = enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={"id": "1"})
        session.commit()
        session.refresh(job)

    assert created is True
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.locked_by is None and job.locked_until is None
    assert job.finished_at is None
    assert job.next_attempt_at <= job.created_at


@requires_postgres
def test_outbox_job_can_be_scheduled_for_later() -> None:
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    with session_scope() as session:
        job, _ = enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={}, available_at=later)
        session.commit()
        session.refresh(job)
    assert job.next_attempt_at == later


@requires_postgres
def test_outbox_payload_secret_is_rejected_and_nothing_is_written() -> None:
    with session_scope() as session:
        with pytest.raises(OutboxPayloadError):
            enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={"bot_token": "x"})
        with pytest.raises(OutboxPayloadError):
            OutboxJob(job_type=_JOB_TYPE, payload={"smtp": {"password": "x"}})
        session.commit()
    assert _count(OutboxJob) == 0


@requires_postgres
def test_outbox_lease_retry_and_terminal_states_persist() -> None:
    lease_until = _NOW + datetime.timedelta(minutes=5)
    with session_scope() as session:
        job, _ = enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={})
        session.commit()

        job.status, job.locked_by, job.locked_until = "processing", "worker-1", lease_until
        job.attempts = 1
        session.commit()

        # Retryable failure: lease released, back to pending in the future.
        job.status, job.locked_by, job.locked_until = "pending", None, None
        job.next_attempt_at = _NOW + datetime.timedelta(minutes=10)
        job.last_error_code = "timeout"
        session.commit()

        # Permanent failure: terminal, observable.
        job.status, job.finished_at = "dead", _NOW
        job.attempts = 2
        job.last_error_code = "rejected"
        session.commit()
        session.refresh(job)

    assert (job.status, job.attempts, job.last_error_code) == ("dead", 2, "rejected")
    assert job.finished_at == _NOW


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "failed"}, "ck_outbox_jobs_status_valid"),
        ({"job_type": " "}, "ck_outbox_jobs_job_type_not_blank"),
        ({"deduplication_key": ""}, "ck_outbox_jobs_deduplication_key_not_blank"),
        ({"attempts": -1}, "ck_outbox_jobs_attempts_non_negative"),
        ({"status": "processing"}, "ck_outbox_jobs_lease_iff_processing"),
        (
            {"locked_until": _NOW, "locked_by": "worker-1"},
            "ck_outbox_jobs_lease_iff_processing",
        ),
        (
            {"status": "processing", "locked_until": _NOW},
            "ck_outbox_jobs_lease_owner_and_expiry_together",
        ),
        ({"status": "completed"}, "ck_outbox_jobs_finished_at_iff_terminal"),
        ({"finished_at": _NOW}, "ck_outbox_jobs_finished_at_iff_terminal"),
    ],
)
@requires_postgres
def test_outbox_constraints(overrides: dict[str, Any], constraint: str) -> None:
    fields: dict[str, Any] = {"job_type": _JOB_TYPE, "payload": {}}
    fields.update(overrides)
    with session_scope() as session:
        session.add(OutboxJob(**fields))
        _assert_rejected(session, constraint)


@requires_postgres
def test_outbox_payload_must_be_a_json_object_at_the_database_level() -> None:
    with session_scope() as session:
        with pytest.raises(IntegrityError) as exc_info:
            session.execute(
                sa.text(
                    "INSERT INTO outbox_jobs (id, job_type, payload) "
                    "VALUES (gen_random_uuid(), 'test.job', '[]'::jsonb)"
                )
            )
        assert exc_info.value.orig.diag.constraint_name == "ck_outbox_jobs_payload_is_object"  # type: ignore[union-attr]


@requires_postgres
def test_outbox_structure_supports_skip_locked_claiming() -> None:
    """The ADR-0046 §5.2 claim shape works against this table: two
    concurrent claimers never receive the same eligible row, and neither
    a future-scheduled job nor a live lease is eligible, while an expired
    lease is. (The worker itself is a separate Issue.)"""
    now = datetime.datetime.now(datetime.timezone.utc)
    with session_scope() as session:
        due = [
            enqueue_outbox_job(session, job_type=_JOB_TYPE, payload={"n": n})[0].id
            for n in range(4)
        ]
        enqueue_outbox_job(
            session,
            job_type=_JOB_TYPE,
            payload={},
            available_at=now + datetime.timedelta(hours=1),
        )
        session.add(
            OutboxJob(
                job_type=_JOB_TYPE,
                payload={},
                status="processing",
                locked_by="live-worker",
                locked_until=now + datetime.timedelta(hours=1),
            )
        )
        expired = OutboxJob(
            job_type=_JOB_TYPE,
            payload={},
            status="processing",
            locked_by="dead-worker",
            locked_until=now - datetime.timedelta(minutes=1),
        )
        session.add(expired)
        session.commit()
        eligible = set(due) | {expired.id}

    claim = (
        select(OutboxJob.id)
        .where(
            sa.or_(
                sa.and_(OutboxJob.status == "pending", OutboxJob.next_attempt_at <= func.now()),
                sa.and_(OutboxJob.status == "processing", OutboxJob.locked_until < func.now()),
            )
        )
        .order_by(OutboxJob.next_attempt_at, OutboxJob.id)
        .limit(3)
        .with_for_update(skip_locked=True)
    )
    factory = get_session_factory()
    first, second = factory(), factory()
    try:
        first_batch = set(first.execute(claim).scalars())
        second_batch = set(second.execute(claim).scalars())
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()

    assert len(first_batch) == 3
    assert len(second_batch) == 2
    assert first_batch.isdisjoint(second_batch)
    assert first_batch | second_batch == eligible


# --- Schema shape ----------------------------------------------------------------


@requires_postgres
def test_worker_claim_indexes_are_partial() -> None:
    indexes = {i["name"]: i for i in sa.inspect(get_engine()).get_indexes("outbox_jobs")}
    pending = indexes["ix_outbox_jobs_pending_next_attempt_at"]
    assert pending["column_names"] == ["next_attempt_at", "id"]
    assert "pending" in pending["dialect_options"]["postgresql_where"]
    expired = indexes["ix_outbox_jobs_processing_locked_until"]
    assert expired["column_names"] == ["locked_until"]
    assert "processing" in expired["dialect_options"]["postgresql_where"]


@requires_postgres
def test_no_notification_table_has_a_secret_column() -> None:
    inspector = sa.inspect(get_engine())
    prohibited = ("password", "token", "secret", "credential", "api_key")
    for table in _NEW_TABLES:
        for column in inspector.get_columns(table):
            assert not any(word in column["name"] for word in prohibited), (table, column["name"])


@requires_postgres
def test_user_profile_has_no_telegram_id_column() -> None:
    columns = {c["name"] for c in sa.inspect(get_engine()).get_columns("users")}
    assert not any("telegram" in name for name in columns)


@requires_postgres
def test_notification_migration_downgrades_and_upgrades_cleanly(database_url: str) -> None:
    try:
        downgrade = run_alembic("downgrade", _MIGRATION_PARENT, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        assert not _NEW_TABLES & set(sa.inspect(get_engine()).get_table_names())
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert _NEW_TABLES <= set(sa.inspect(get_engine()).get_table_names())
