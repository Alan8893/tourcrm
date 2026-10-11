"""Integration tests for the Issue #336 PR-2 slice — `registration.created`
and `registration.cancelled`, planned by app.events.participation through
the Notification Engine inside the self-service registration/withdrawal
transaction (ADR-0049 §2.6) — against real PostgreSQL, the migration-seeded
(disabled) rules/templates, the outbox worker and the Telegram adapter with
a scripted Bot API transport (never real Telegram).

PO decisions of 2026-10-11: notify the registrant only; a withdrawal only
while the Event is `published`/`in_progress`; `event.read` required; only
`active` Users; no date/time in the withdrawal message; never a
group/topic route; automatic lesson registration is not notified.

The registrant rules are enabled the way an administrator would
(`_enable_rules`); the migration seeds them disabled. Registration is
optional: the tests give the registrant the personal master switch and
the event preference ON unless a test says otherwise.
"""

import datetime
import uuid
from collections.abc import Iterator
from typing import Any, Optional

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.notifications import (
    CommunicationChannelPreference,
    CommunicationPreference,
    Notification,
    NotificationDelivery,
    NotificationRule,
    TelegramDestination,
)
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.db.telegram import TelegramIdentity
from app.events import crud, participation
from app.events import notifications as event_notifications
from app.main import app
from app.notification_settings.policy import GlobalAdminPolicy
from app.notifications.delivery import NotificationDeliveryHandler
from app.notifications.telegram_adapter import TelegramChannelAdapter
from app.notifications.vocabulary import NOTIFICATION_DELIVERY_JOB_TYPE
from app.outbox.claiming import claim_jobs
from app.outbox.worker import OutboxWorker, WorkerConfig
from tests.notification_settings_helpers import store_policy
from tests.telegram_fakes import ScriptedTransport, client_for, ok

from .conftest import requires_postgres

CREATED = "registration.created"
CANCELLED = "registration.cancelled"
BASE_URL = "https://crm.example.org"
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
# 2026-12-01 09:00 UTC = 12:00 in Europe/Moscow.
_START = datetime.datetime(2026, 12, 1, 9, 0, tzinfo=datetime.timezone.utc)
_END = _START + datetime.timedelta(hours=8)
_TG_IDS = iter(range(7_400_000_001, 7_400_100_000))
_HEADERS = {"X-CSRF-Token": "t"}


# --- Setup -------------------------------------------------------------------------


def _club(session: Session) -> Club:
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _assign_role(session: Session, user_id: uuid.UUID, role_code: str, club: Club) -> None:
    role = session.execute(sa.select(Role).where(Role.code == role_code)).scalar_one()
    session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, club_id=club.id))
    session.flush()


def _user(
    session: Session,
    club: Club,
    *,
    role: Optional[str] = "member",
    linked: bool = True,
    user_status: str = "active",
    master: Optional[bool] = True,
    preference: Optional[bool] = True,
) -> dict[str, uuid.UUID]:
    """A Person with an active ClubMembership (so self-registration is
    eligible), a User, optionally the `member` role (`event.read` self), an
    active Telegram identity and the personal preferences of both keys."""
    person = Person(last_name="Volkova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    membership = ClubMembership(
        club_id=club.id, person_id=person.id, membership_type="student",
        status="active", joined_at=_LONG_AGO,
    )
    session.add(membership)
    user = User(
        person=person, login_identifier=f"r-{uuid.uuid4().hex[:8]}@club.test", status=user_status
    )
    session.add(user)
    session.flush()
    if role is not None:
        _assign_role(session, user.id, role, club)
    if linked:
        session.add(
            TelegramIdentity(user_id=user.id, telegram_user_id=next(_TG_IDS), status="active")
        )
    if master is not None:
        session.add(
            CommunicationChannelPreference(user_id=user.id, channel="telegram", enabled=master)
        )
    if preference is not None:
        for event_type in (CREATED, CANCELLED):
            session.add(
                CommunicationPreference(
                    user_id=user.id, channel="telegram", notification_type=event_type,
                    enabled=preference,
                )
            )
    session.flush()
    return {"user": user.id, "person": person.id, "club_membership": membership.id}


def _event(session: Session, club: Club, *, status: str = "published", **kw: Any) -> Event:
    fields: dict[str, Any] = {
        "event_type": "excursion",
        "title": "Поход",
        "start_at": _START,
        "end_at": _END,
        "timezone": "Europe/Moscow",
    }
    fields.update(kw)
    event = Event(id=uuid.uuid4(), club_id=club.id, status=status, **fields)
    session.add(event)
    session.flush()
    session.add(
        EventOccurrence(
            event_id=event.id, series_id=None, club_id=club.id, name=event.title,
            event_type=event.event_type, recurrence_anchor_at=event.start_at,
            starts_at=event.start_at, ends_at=event.end_at, timezone=event.timezone,
            status="scheduled",
        )
    )
    session.flush()
    return event


def _scenario(**user_kw: Any) -> dict[str, Any]:
    with session_scope() as session:
        club = _club(session)
        event = _event(session, club)
        ids = _user(session, club, **user_kw)
        session.commit()
        return {"club_id": club.id, "event_id": event.id, **ids}


def _enable_rules(*event_types: str) -> None:
    """What an administrator does in Settings → Notifications."""
    with session_scope() as session:
        session.execute(
            sa.update(NotificationRule)
            .where(NotificationRule.event_type.in_(event_types))
            .values(is_enabled=True)
        )
        session.commit()


def _set_event_status(event_id: uuid.UUID, status: str) -> None:
    reason = "Шторм" if status == "cancelled" else None
    with session_scope() as session:
        session.execute(
            sa.update(Event)
            .where(Event.id == event_id)
            .values(status=status, cancellation_reason=reason)
        )
        session.commit()


def _register(user_id: uuid.UUID, event_id: uuid.UUID) -> EventParticipation:
    with session_scope() as session:
        row = participation.register_for_event(session, event_id=event_id, user_id=user_id)
        session.expunge(row)
        return row


def _withdraw(user_id: uuid.UUID, event_id: uuid.UUID) -> None:
    with session_scope() as session:
        participation.withdraw_from_event(session, event_id=event_id, user_id=user_id)


def _notifications(event_type: Optional[str] = None) -> list[Notification]:
    with session_scope() as session:
        query = sa.select(Notification).order_by(Notification.created_at, Notification.id)
        if event_type is not None:
            query = query.where(Notification.event_type == event_type)
        rows = list(session.execute(query).scalars())
        session.expunge_all()
        return rows


def _counts() -> tuple[int, int, int]:
    with session_scope() as session:
        return tuple(  # type: ignore[return-value]
            session.scalar(sa.select(sa.func.count()).select_from(model)) or 0
            for model in (Notification, NotificationDelivery, OutboxJob)
        )


def _transition_audits(event_id: uuid.UUID) -> list[AuditLog]:
    with session_scope() as session:
        rows = list(
            session.execute(
                sa.select(AuditLog)
                .join(EventParticipation, EventParticipation.id == AuditLog.resource_id)
                .where(
                    AuditLog.action == "event_participation.status_changed",
                    EventParticipation.event_id == event_id,
                )
                .order_by(AuditLog.occurred_at, AuditLog.id)
            ).scalars()
        )
        session.expunge_all()
        return rows


def _registration_status(event_id: uuid.UUID, person_id: uuid.UUID) -> Optional[str]:
    with session_scope() as session:
        return session.execute(
            sa.select(EventParticipation.registration_status).where(
                EventParticipation.event_id == event_id,
                EventParticipation.person_id == person_id,
            )
        ).scalar_one_or_none()


def _worker(transport: ScriptedTransport, base_url: Optional[str] = BASE_URL) -> OutboxWorker:
    factory = get_session_factory()
    adapter = TelegramChannelAdapter(
        client=client_for(transport), session_factory=factory, public_base_url=base_url
    )
    return OutboxWorker(
        session_factory=factory,
        handlers={
            NOTIFICATION_DELIVERY_JOB_TYPE: NotificationDeliveryHandler(
                {"telegram": adapter}, admin_policy=GlobalAdminPolicy()
            )
        },
        config=WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}", batch_size=50),
    )


def _sent_texts(transport: ScriptedTransport) -> list[str]:
    return [call.params["text"] for call in transport.calls_to("sendMessage")]


@pytest.fixture(autouse=True)
def _telegram_on() -> None:
    store_policy(telegram=True)


# --- registration.created ----------------------------------------------------------


@requires_postgres
def test_registration_plans_one_notification_keyed_by_its_audit_row() -> None:
    s = _scenario()
    _enable_rules(CREATED)
    _register(s["user"], s["event_id"])
    (notification,) = _notifications(CREATED)
    (audit,) = _transition_audits(s["event_id"])
    assert notification.recipient_user_id == s["user"]
    assert notification.recipient_destination_id is None
    assert notification.idempotency_key == f"{CREATED}:{audit.id}:user:{s['user']}"
    assert notification.subject_id == s["event_id"]
    assert notification.render_context == {
        "event_title": "Поход",
        "event_datetime": "01.12.2026 12:00–20:00 (Europe/Moscow)",
        "event_id": str(s["event_id"]),
    }
    assert _counts() == (1, 1, 1)
    with session_scope() as session:
        (delivery,) = session.execute(sa.select(NotificationDelivery)).scalars()
        assert (delivery.channel, delivery.destination_type, delivery.destination_id) == (
            "telegram",
            "user",
            s["user"],
        )
        (job,) = session.execute(sa.select(OutboxJob)).scalars()
        assert job.payload == {"delivery_id": str(delivery.id)}


@requires_postgres
def test_repeated_registration_creates_no_duplicate() -> None:
    s = _scenario()
    _enable_rules(CREATED)
    _register(s["user"], s["event_id"])
    _register(s["user"], s["event_id"])
    assert len(_transition_audits(s["event_id"])) == 1
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_restoring_a_cancelled_registration_is_a_new_fact() -> None:
    s = _scenario()
    _enable_rules(CREATED, CANCELLED)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    audits = _transition_audits(s["event_id"])
    assert len(audits) == 4
    keys = [n.idempotency_key for n in _notifications()]
    assert keys == [
        f"{CREATED}:{audits[0].id}:user:{s['user']}",
        f"{CANCELLED}:{audits[1].id}:user:{s['user']}",
        f"{CREATED}:{audits[2].id}:user:{s['user']}",
        f"{CANCELLED}:{audits[3].id}:user:{s['user']}",
    ]
    assert _counts() == (4, 4, 4)


@requires_postgres
def test_replanning_the_same_transition_creates_no_duplicate() -> None:
    s = _scenario()
    _enable_rules(CREATED)
    _register(s["user"], s["event_id"])
    (audit,) = _transition_audits(s["event_id"])
    with session_scope() as session:
        event = session.get(Event, s["event_id"])
        assert event is not None
        event_notifications.plan_registration_created(
            session, event=event, user_id=s["user"], fact_id=audit.id
        )
        session.commit()
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_automatic_lesson_registration_is_not_notified() -> None:
    with session_scope() as session:
        club = _club(session)
        ids = _user(session, club)
        group = Group(club_id=club.id, name=f"G-{uuid.uuid4().hex[:6]}", status="active",
                      valid_from=_LONG_AGO)
        session.add(group)
        session.flush()
        session.add(
            GroupMembership(group_id=group.id, club_membership_id=ids["club_membership"],
                            valid_from=_LONG_AGO, membership_status="active")
        )
        admin = _user(session, club, role="admin", linked=False)
        session.commit()
        club_id, group_id = club.id, group.id
    _enable_rules(CREATED, CANCELLED)
    with session_scope() as session:
        event = crud.create_event_with_targeting(
            session, club_id=club_id, event_type="lesson", title="Занятие", description=None,
            start_at=_START, end_at=_END, timezone="Europe/Moscow", location_type=None,
            location_name=None, location_address=None, location_latitude=None,
            location_longitude=None, created_by=admin["user"], group_ids=[group_id],
        )
        event_id = event.id
    assert _registration_status(event_id, ids["person"]) == "registered"
    assert _counts() == (0, 0, 0)

    # A later self-service withdrawal of that registration is notified.
    _set_event_status(event_id, "published")
    _withdraw(ids["user"], event_id)
    (notification,) = _notifications()
    assert (notification.event_type, notification.recipient_user_id) == (CANCELLED, ids["user"])


@requires_postgres
def test_only_the_registrant_is_notified() -> None:
    with session_scope() as session:
        club = _club(session)
        event = _event(session, club)
        registrant = _user(session, club)
        other_participant = _user(session, club)
        instructor = _user(session, club, role="instructor")
        guardian = _user(session, club)
        session.add(
            EventParticipation(event_id=event.id, person_id=other_participant["person"],
                               registration_status="registered")
        )
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian["person"], child_person_id=registrant["person"],
                relationship_type="parent", status="active", valid_from=_LONG_AGO,
            )
        )
        session.commit()
        event_id = event.id
    del instructor
    _enable_rules(CREATED, CANCELLED)
    _register(registrant["user"], event_id)
    _withdraw(registrant["user"], event_id)
    assert {n.recipient_user_id for n in _notifications()} == {registrant["user"]}
    assert _counts() == (2, 2, 2)


@requires_postgres
def test_registrant_without_event_read_is_not_notified() -> None:
    # Eligible to self-register (active ClubMembership, club-wide Event) but
    # holds no role granting event.read: the registration succeeds, no
    # notification is planned.
    s = _scenario(role=None)
    _enable_rules(CREATED, CANCELLED)
    _register(s["user"], s["event_id"])
    assert _registration_status(s["event_id"], s["person"]) == "registered"
    _withdraw(s["user"], s["event_id"])
    assert _registration_status(s["event_id"], s["person"]) == "cancelled"
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_inactive_user_is_not_notified() -> None:
    s = _scenario(user_status="locked")
    _enable_rules(CREATED, CANCELLED)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    assert len(_transition_audits(s["event_id"])) == 2
    assert _counts() == (0, 0, 0)


@pytest.mark.parametrize(
    "user_kw",
    [
        {"master": None, "preference": None},  # nothing stored -> OFF
        {"master": False},  # personal master switch OFF
        {"preference": False},  # per-event preference OFF
        {"preference": None},  # per-event preference missing -> OFF
        {"master": None},  # master switch missing -> OFF
        {"linked": False},  # no verified Telegram identity
    ],
)
@requires_postgres
def test_personal_settings_and_linking_block_optional_notifications(
    user_kw: dict[str, Any],
) -> None:
    s = _scenario(**user_kw)
    _enable_rules(CREATED, CANCELLED)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_seeded_rules_are_disabled_and_global_off_blocks() -> None:
    s = _scenario()
    _register(s["user"], s["event_id"])  # rules as seeded: disabled
    _withdraw(s["user"], s["event_id"])
    assert _counts() == (0, 0, 0)
    _enable_rules(CREATED, CANCELLED)
    store_policy(telegram=False)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_registration_notifications_are_never_published_to_routes() -> None:
    s = _scenario()
    with session_scope() as session:
        session.add(
            TelegramDestination(name="Клуб", chat_id=-1007770009, message_thread_id=4,
                                notification_scope={"event_types": [CREATED, CANCELLED]})
        )
        session.commit()
    _enable_rules(CREATED, CANCELLED)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    assert all(n.recipient_destination_id is None for n in _notifications())
    assert _counts() == (2, 2, 2)
    with session_scope() as session:
        assert not session.execute(
            sa.select(NotificationRule).where(
                NotificationRule.event_type.in_([CREATED, CANCELLED]),
                NotificationRule.recipient_scope == "telegram_destination",
            )
        ).first()


# --- registration.cancelled --------------------------------------------------------


@pytest.mark.parametrize("status", ["published", "in_progress"])
@requires_postgres
def test_withdrawal_while_the_event_runs_is_notified(status: str) -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    _register(s["user"], s["event_id"])
    _set_event_status(s["event_id"], status)
    _withdraw(s["user"], s["event_id"])
    (notification,) = _notifications(CANCELLED)
    audits = _transition_audits(s["event_id"])
    assert notification.idempotency_key == f"{CANCELLED}:{audits[-1].id}:user:{s['user']}"
    assert notification.render_context == {"event_title": "Поход", "event_id": str(s["event_id"])}


@pytest.mark.parametrize("status", ["draft", "completed", "cancelled", "archived"])
@requires_postgres
def test_withdrawal_in_any_other_status_happens_without_notification(status: str) -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    with session_scope() as session:
        session.add(
            EventParticipation(event_id=s["event_id"], person_id=s["person"],
                               registration_status="registered")
        )
        session.commit()
    _set_event_status(s["event_id"], status)
    _withdraw(s["user"], s["event_id"])
    assert _registration_status(s["event_id"], s["person"]) == "cancelled"
    assert len(_transition_audits(s["event_id"])) == 1
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_repeated_or_empty_withdrawal_creates_nothing() -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    _withdraw(s["user"], s["event_id"])  # nothing to cancel
    assert _counts() == (0, 0, 0)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    assert _counts() == (1, 1, 1)
    with session_scope() as session:
        assert participation.withdraw_from_event(
            session, event_id=uuid.uuid4(), user_id=s["user"]
        ) is None


@requires_postgres
def test_rendered_messages_and_no_date_in_the_withdrawal() -> None:
    s = _scenario()
    with session_scope() as session:
        session.execute(
            sa.update(Event).where(Event.id == s["event_id"]).values(title="<b>Поход</b> & {{x}}")
        )
        session.commit()
    _enable_rules(CREATED, CANCELLED)
    _register(s["user"], s["event_id"])
    _withdraw(s["user"], s["event_id"])
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    _worker(transport).run_once()
    title = "<b>&lt;b&gt;Поход&lt;/b&gt; &amp; &#123;&#123;x&#125;&#125;</b>"
    link = f"{BASE_URL}/events?event={s['event_id']}"
    assert sorted(_sent_texts(transport)) == sorted([
        f"Вы зарегистрированы на {title}. Дата: 01.12.2026 12:00–20:00 (Europe/Moscow). {link}",
        f"Ваша регистрация на {title} отменена. {link}",
    ])
    withdrawal = next(t for t in _sent_texts(transport) if "отменена" in t)
    assert "2026" not in withdrawal and "12:00" not in withdrawal and "Дата" not in withdrawal
    assert "{{" not in withdrawal


# --- Atomicity ---------------------------------------------------------------------------


def _fail_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planning failed")

    monkeypatch.setattr(event_notifications, "plan_catalog_notification", boom)


@requires_postgres
def test_planning_failure_rolls_back_the_registration_and_its_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _scenario()
    _enable_rules(CREATED)
    _fail_planning(monkeypatch)
    with pytest.raises(RuntimeError, match="planning failed"):
        _register(s["user"], s["event_id"])
    assert _registration_status(s["event_id"], s["person"]) is None
    assert _transition_audits(s["event_id"]) == []
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_planning_failure_rolls_back_the_withdrawal_and_its_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    _register(s["user"], s["event_id"])
    _fail_planning(monkeypatch)
    with pytest.raises(RuntimeError, match="planning failed"):
        _withdraw(s["user"], s["event_id"])
    assert _registration_status(s["event_id"], s["person"]) == "registered"
    assert len(_transition_audits(s["event_id"])) == 1
    assert _counts() == (0, 0, 0)
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    assert _worker(transport).run_once() == []


@requires_postgres
def test_worker_sees_the_job_only_after_the_registration_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _scenario()
    _enable_rules(CREATED)
    factory = get_session_factory()
    probed: list[list[Any]] = []
    with session_scope() as session:
        real_commit = session.commit

        def commit_after_probe() -> None:
            with factory() as probe:
                probed.append(
                    claim_jobs(probe, worker_id="probe", batch_size=10,
                               lease=datetime.timedelta(seconds=30))
                )
                probe.rollback()
            real_commit()

        monkeypatch.setattr(session, "commit", commit_after_probe)
        participation.register_for_event(session, event_id=s["event_id"], user_id=s["user"])
    assert probed == [[]]
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    assert [r.result for r in _worker(transport).run_once()] == ["completed"]


# --- Through the HTTP API ---------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    test_client = TestClient(app, raise_server_exceptions=True)
    test_client.cookies.set("csrf_token", "t")
    yield test_client
    app.dependency_overrides.pop(get_current_principal, None)


@requires_postgres
def test_api_register_and_withdraw_keep_their_responses_and_plan_notifications(
    client: TestClient,
) -> None:
    s = _scenario()
    _enable_rules(CREATED, CANCELLED)
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=s["user"], session_id=uuid.uuid4()
    )
    url = f"/api/v1/events/{s['event_id']}/participation"
    response = client.post(url, headers=_HEADERS)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "id", "event_id", "person_id", "registration_status", "created_at", "updated_at"
    }
    assert response.json()["registration_status"] == "registered"
    assert client.post(url, headers=_HEADERS).status_code == 200
    response = client.delete(url, headers=_HEADERS)
    assert response.status_code == 204 and response.content == b""
    assert client.delete(url, headers=_HEADERS).status_code == 204
    assert [n.event_type for n in _notifications()] == [CREATED, CANCELLED]
    assert _counts() == (2, 2, 2)
