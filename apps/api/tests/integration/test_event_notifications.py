"""Integration tests for the Issue #336 PR-1 slice — `event.cancelled` and
`event.rescheduled` planned by app.events.crud through the Notification
Engine inside the Event mutation's own transaction (ADR-0049 §2.6) —
against real PostgreSQL, the migration-seeded (disabled) rules/templates,
the outbox worker and the Telegram adapter with a scripted Bot API
transport (never real Telegram).

The rules are enabled the way an administrator would (`_enable_rules`);
the migration seeds them disabled.
"""

import datetime
import threading
import uuid
from typing import Any, Optional

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation
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
from app.events import crud
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

CANCELLED = "event.cancelled"
RESCHEDULED = "event.rescheduled"
BASE_URL = "https://crm.example.org"
GROUP_CHAT = -1007770001
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
# 2026-12-01 09:00 UTC = 12:00 in Europe/Moscow.
_START = datetime.datetime(2026, 12, 1, 9, 0, tzinfo=datetime.timezone.utc)
_END = _START + datetime.timedelta(hours=8)
_TG_IDS = iter(range(7_300_000_001, 7_300_100_000))


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


def _person(session: Session, club: Optional[Club] = None) -> Person:
    person = Person(last_name="Smirnova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    if club is not None:
        session.add(
            ClubMembership(
                club_id=club.id, person_id=person.id, membership_type="student",
                status="active", joined_at=_LONG_AGO,
            )
        )
        session.flush()
    return person


def _member(
    session: Session, club: Club, *, linked: bool = True, user_status: str = "active"
) -> tuple[uuid.UUID, uuid.UUID]:
    """A Member (active ClubMembership + `member` role) with a User and,
    when `linked`, an active Telegram identity. Returns (user_id, person_id)."""
    person = _person(session, club)
    user = User(
        person=person, login_identifier=f"m-{uuid.uuid4().hex[:8]}@club.test", status=user_status
    )
    session.add(user)
    session.flush()
    _assign_role(session, user.id, "member", club)
    if linked:
        session.add(
            TelegramIdentity(user_id=user.id, telegram_user_id=next(_TG_IDS), status="active")
        )
        session.flush()
    return user.id, person.id


def _event(session: Session, club: Club, *, status: str = "published", **kw: Any) -> Event:
    fields: dict[str, Any] = {
        "event_type": "excursion",
        "title": "Поход",
        "start_at": _START,
        "end_at": _END,
        "timezone": "Europe/Moscow",
        "location_name": "Клуб",
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


def _register(session: Session, event: Event, person_id: uuid.UUID, status: str = "registered"):  # type: ignore[no-untyped-def]
    session.add(
        EventParticipation(event_id=event.id, person_id=person_id, registration_status=status)
    )
    session.flush()


def _enable_rules(*event_types: str, personal: bool = True, group: bool = False) -> None:
    """What an administrator does in Settings → Notifications."""
    with session_scope() as session:
        for rule in session.execute(
            sa.select(NotificationRule).where(NotificationRule.event_type.in_(event_types))
        ).scalars():
            if rule.recipient_scope == "telegram_destination":
                rule.is_enabled = group
            else:
                rule.is_enabled = personal
        session.commit()


def _route(session: Session, event_types: list[str], thread: Optional[int] = 3) -> uuid.UUID:
    route = TelegramDestination(
        name="Клуб", chat_id=GROUP_CHAT, message_thread_id=thread,
        notification_scope={"event_types": event_types},
    )
    session.add(route)
    session.flush()
    return route.id


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


def _scenario(*, linked: bool = True) -> dict[str, Any]:
    """A published club-wide Event with two registered Members, one Member
    whose registration was cancelled earlier, one unregistered Member, a
    registered child without a User account whose guardian is a Member,
    and a registered Person whose User is not active."""
    with session_scope() as session:
        club = _club(session)
        event = _event(session, club)
        alice, alice_person = _member(session, club, linked=linked)
        bob, bob_person = _member(session, club, linked=linked)
        withdrawn, withdrawn_person = _member(session, club)
        bystander, _ = _member(session, club)
        guardian, guardian_person = _member(session, club)
        child = _person(session, club)
        locked, locked_person = _member(session, club, user_status="locked")
        for person_id in (alice_person, bob_person, child.id, locked_person):
            _register(session, event, person_id)
        _register(session, event, withdrawn_person, status="cancelled")
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian_person, child_person_id=child.id,
                relationship_type="parent", status="active", valid_from=_LONG_AGO,
            )
        )
        session.commit()
        return {
            "club_id": club.id, "event_id": event.id, "alice": alice, "bob": bob,
            "withdrawn": withdrawn, "bystander": bystander, "guardian": guardian,
            "locked": locked,
        }


def _cancel(event_id: uuid.UUID, reason: str = "Штормовое предупреждение") -> None:
    with session_scope() as session:
        event = session.execute(
            sa.select(Event).where(Event.id == event_id).with_for_update()
        ).scalar_one()
        crud.transition_event_status(
            session, event=event, new_status="cancelled", cancellation_reason=reason,
            updated_by=None,  # type: ignore[arg-type]
        )


def _update(event_id: uuid.UUID, **fields: Any) -> None:
    with session_scope() as session:
        event = session.execute(
            sa.select(Event).where(Event.id == event_id).with_for_update()
        ).scalar_one()
        crud.update_event_with_targeting(session, event=event, updated_by=None, **fields)  # type: ignore[arg-type]


def _recipients(event_type: str) -> set[Optional[uuid.UUID]]:
    return {n.recipient_user_id for n in _notifications(event_type)}


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


# --- event.cancelled: audience ---------------------------------------------------------


@requires_postgres
def test_cancellation_notifies_exactly_the_currently_registered_active_users() -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    _cancel(s["event_id"])
    # Not: the earlier withdrawn registrant, an unregistered Member, the
    # guardian of a registered child without a User account, a locked User.
    assert _recipients(CANCELLED) == {s["alice"], s["bob"]}
    assert _counts() == (2, 2, 2)
    for notification in _notifications(CANCELLED):
        assert notification.idempotency_key == (
            f"{CANCELLED}:{s['event_id']}:user:{notification.recipient_user_id}"
        )
        assert notification.subject_id == s["event_id"]
        assert notification.render_context["event_datetime"] == (
            "01.12.2026 12:00–20:00 (Europe/Moscow)"
        )
        assert notification.render_context["cancellation_reason"] == "Штормовое предупреждение"


@requires_postgres
def test_rules_seeded_disabled_mean_no_notification_and_the_cancellation_still_commits() -> None:
    s = _scenario()
    _cancel(s["event_id"])
    assert _counts() == (0, 0, 0)
    with session_scope() as session:
        assert session.get(Event, s["event_id"]).status == "cancelled"  # type: ignore[union-attr]


@requires_postgres
def test_global_telegram_off_blocks_cancellation_and_reschedule() -> None:
    s = _scenario()
    _enable_rules(CANCELLED, RESCHEDULED)
    store_policy(telegram=False)
    _update(s["event_id"], start_at=_START + datetime.timedelta(days=1),
            end_at=_END + datetime.timedelta(days=1))
    _cancel(s["event_id"])
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_personal_opt_out_does_not_block_mandatory_notifications() -> None:
    s = _scenario()
    _enable_rules(CANCELLED, RESCHEDULED)
    with session_scope() as session:
        for user_id in (s["alice"], s["bob"]):
            session.add(
                CommunicationChannelPreference(user_id=user_id, channel="telegram", enabled=False)
            )
            for event_type in (CANCELLED, RESCHEDULED):
                session.add(
                    CommunicationPreference(
                        user_id=user_id, channel="telegram", notification_type=event_type,
                        enabled=False,
                    )
                )
        session.commit()
    _update(s["event_id"], timezone="Asia/Yekaterinburg")
    _cancel(s["event_id"])
    assert _recipients(RESCHEDULED) == {s["alice"], s["bob"]}
    assert _recipients(CANCELLED) == {s["alice"], s["bob"]}


@requires_postgres
def test_without_a_linked_telegram_identity_no_personal_delivery_is_created() -> None:
    s = _scenario(linked=False)
    _enable_rules(CANCELLED)
    _cancel(s["event_id"])
    assert _recipients(CANCELLED) == set()
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_disabled_personal_rule_blocks_personal_but_not_the_enabled_route() -> None:
    s = _scenario()
    with session_scope() as session:
        route_id = _route(session, [CANCELLED])
        # Personal preferences ON or OFF never matter for the route.
        session.add(
            CommunicationChannelPreference(user_id=s["alice"], channel="telegram", enabled=False)
        )
        session.commit()
    _enable_rules(CANCELLED, personal=False, group=True)
    _cancel(s["event_id"])
    (published,) = _notifications(CANCELLED)
    assert (published.recipient_user_id, published.recipient_destination_id) == (None, route_id)
    assert published.idempotency_key == f"{CANCELLED}:{s['event_id']}:dest:{route_id}"


@requires_postgres
def test_disabled_route_rule_blocks_the_route_but_not_personal_delivery() -> None:
    s = _scenario()
    with session_scope() as session:
        _route(session, [CANCELLED])
        session.commit()
    _enable_rules(CANCELLED, personal=True, group=False)
    _cancel(s["event_id"])
    assert all(n.recipient_destination_id is None for n in _notifications(CANCELLED))
    assert _recipients(CANCELLED) == {s["alice"], s["bob"]}


@requires_postgres
def test_unconfigured_or_unsubscribed_route_gets_nothing() -> None:
    s = _scenario()
    with session_scope() as session:
        _route(session, ["event.created"])  # subscribed to another event only
        _route(session, [CANCELLED], thread=None)
        session.execute(sa.update(TelegramDestination).where(
            TelegramDestination.message_thread_id.is_(None)).values(enabled=False))
        session.commit()
    _enable_rules(CANCELLED, personal=False, group=True)
    _cancel(s["event_id"])
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_whitespace_reason_uses_the_safe_fallback_and_values_are_escaped() -> None:
    s = _scenario()
    with session_scope() as session:
        session.execute(
            sa.update(Event).where(Event.id == s["event_id"])
            .values(title="<b>Поход</b> & {{event_title}}")
        )
        session.commit()
    _enable_rules(CANCELLED)
    _cancel(s["event_id"], reason="   ")
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    _worker(transport).run_once()
    texts = _sent_texts(transport)
    assert len(texts) == 2
    for text in texts:
        assert text == (
            "Событие <b>&lt;b&gt;Поход&lt;/b&gt; &amp; &#123;&#123;event_title&#125;&#125;</b>"
            " отменено. Дата: 01.12.2026 12:00–20:00 (Europe/Moscow). Причина: не указана. "
            f"{BASE_URL}/events?event={s['event_id']}"
        )
        assert "{{" not in text and "}}" not in text


# --- event.rescheduled -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "old", "new"),
    [
        (
            {"start_at": _START + datetime.timedelta(days=1),
             "end_at": _END + datetime.timedelta(days=1)},
            "01.12.2026 12:00–20:00 (Europe/Moscow)",
            "02.12.2026 12:00–20:00 (Europe/Moscow)",
        ),
        (
            {"start_at": _START + datetime.timedelta(hours=2)},
            "01.12.2026 12:00–20:00 (Europe/Moscow)",
            "01.12.2026 14:00–20:00 (Europe/Moscow)",
        ),
        (
            {"end_at": _END + datetime.timedelta(days=1)},
            "01.12.2026 12:00–20:00 (Europe/Moscow)",
            "01.12.2026 12:00 – 02.12.2026 20:00 (Europe/Moscow)",
        ),
        (
            {"timezone": "Asia/Yekaterinburg"},
            "01.12.2026 12:00–20:00 (Europe/Moscow)",
            "01.12.2026 14:00–22:00 (Asia/Yekaterinburg)",
        ),
    ],
)
@requires_postgres
def test_date_time_or_timezone_change_is_a_reschedule_with_old_and_new_values(
    changes: dict[str, Any], old: str, new: str
) -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED)
    _update(s["event_id"], **changes)
    notifications = _notifications(RESCHEDULED)
    assert {n.recipient_user_id for n in notifications} == {s["alice"], s["bob"]}
    (fact,) = {n.idempotency_key.split(":")[1] for n in notifications}
    for notification in notifications:
        assert notification.render_context["old_event_datetime"] == old
        assert notification.render_context["new_event_datetime"] == new
        assert notification.idempotency_key == (
            f"{RESCHEDULED}:{fact}:user:{notification.recipient_user_id}"
        )
    assert _counts() == (2, 2, 2)


@requires_postgres
def test_reschedule_with_location_change_creates_only_event_rescheduled() -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED)
    _update(s["event_id"], start_at=_START + datetime.timedelta(days=2),
            end_at=_END + datetime.timedelta(days=2), location_name="Вокзал")
    assert {n.event_type for n in _notifications()} == {RESCHEDULED}
    assert _counts() == (2, 2, 2)


@pytest.mark.parametrize(
    "changes",
    [
        {"location_name": "Вокзал", "location_address": "пл. Комсомольская, 1"},
        {"title": "Новый поход"},
        {"description": "Взять фонарик"},
        {"event_type": "competition"},
        # Same instants written with another UTC offset: no change.
        {"start_at": _START.astimezone(datetime.timezone(datetime.timedelta(hours=3))),
         "end_at": _END.astimezone(datetime.timezone(datetime.timedelta(hours=3)))},
        {"start_at": _START, "end_at": _END, "timezone": "Europe/Moscow"},
    ],
)
@requires_postgres
def test_changes_that_are_not_a_reschedule_create_nothing(changes: dict[str, Any]) -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED, CANCELLED)
    _update(s["event_id"], **changes)
    assert _counts() == (0, 0, 0)


@pytest.mark.parametrize("status", ["draft", "in_progress"])
@requires_postgres
def test_only_a_published_event_is_rescheduled(status: str) -> None:
    s = _scenario()
    with session_scope() as session:
        session.execute(sa.update(Event).where(Event.id == s["event_id"]).values(status=status))
        session.commit()
    _enable_rules(RESCHEDULED)
    _update(s["event_id"], start_at=_START + datetime.timedelta(days=1),
            end_at=_END + datetime.timedelta(days=1))
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_plain_update_event_plans_the_reschedule_too() -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED)
    with session_scope() as session:
        event = session.get(Event, s["event_id"])
        assert event is not None
        crud.update_event(session, event=event, updated_by=None,  # type: ignore[arg-type]
                          timezone="Asia/Yekaterinburg")
    assert _recipients(RESCHEDULED) == {s["alice"], s["bob"]}


@requires_postgres
def test_each_reschedule_is_its_own_fact_even_back_to_earlier_values() -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED)
    later = {"start_at": _START + datetime.timedelta(days=1),
             "end_at": _END + datetime.timedelta(days=1)}
    original = {"start_at": _START, "end_at": _END}
    _update(s["event_id"], **later)
    _update(s["event_id"], **original)
    _update(s["event_id"], **later)
    facts = {n.idempotency_key.split(":")[1] for n in _notifications(RESCHEDULED)}
    assert len(facts) == 3
    assert _counts() == (6, 6, 6)


# --- Idempotency -----------------------------------------------------------------------


@requires_postgres
def test_planning_the_same_fact_again_creates_no_duplicates() -> None:
    s = _scenario()
    with session_scope() as session:
        _route(session, [CANCELLED, RESCHEDULED])
        session.commit()
    _enable_rules(CANCELLED, RESCHEDULED, group=True)
    with session_scope() as session:
        event = session.execute(
            sa.select(Event).where(Event.id == s["event_id"]).with_for_update()
        ).scalar_one()
        before = event_notifications.schedule_of(event)
        event.start_at = _START + datetime.timedelta(days=1)
        event.end_at = _END + datetime.timedelta(days=1)
        first = event_notifications.plan_event_rescheduled_if_changed(
            session, event=event, before=before
        )
        second = event_notifications.plan_event_rescheduled_if_changed(
            session, event=event, before=before
        )
        assert first is not None and first == second
        session.commit()
    _cancel(s["event_id"])
    with session_scope() as session:
        event = session.get(Event, s["event_id"])
        assert event is not None
        event_notifications.plan_event_cancelled(session, event=event)
        session.commit()
    # 2 users + 1 route, per event type; replays added nothing.
    assert _counts() == (6, 6, 6)


@requires_postgres
def test_concurrent_planning_of_one_cancellation_creates_one_notification_per_user() -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    with session_scope() as session:
        session.execute(
            sa.update(Event).where(Event.id == s["event_id"])
            .values(status="cancelled", cancellation_reason="Дождь")
        )
        session.commit()
    factory = get_session_factory()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def plan() -> None:
        try:
            with factory() as session:
                event = session.get(Event, s["event_id"])
                assert event is not None
                barrier.wait()
                event_notifications.plan_event_cancelled(session, event=event)
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=plan) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert _counts() == (2, 2, 2)


# --- Atomicity ---------------------------------------------------------------------------


def _fail_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planning failed")

    monkeypatch.setattr(event_notifications, "plan_catalog_notification", boom)


@requires_postgres
def test_planning_failure_rolls_the_cancellation_back(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    _fail_planning(monkeypatch)
    with pytest.raises(RuntimeError, match="planning failed"):
        _cancel(s["event_id"])
    with session_scope() as session:
        event = session.get(Event, s["event_id"])
        assert event is not None and event.status == "published"
        assert event.cancellation_reason is None
        occurrence = session.execute(
            sa.select(EventOccurrence).where(EventOccurrence.event_id == s["event_id"])
        ).scalar_one()
        assert occurrence.status == "scheduled"
    assert _counts() == (0, 0, 0)
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    assert _worker(transport).run_once() == []
    assert transport.calls_to("sendMessage") == []


@requires_postgres
def test_planning_failure_rolls_the_reschedule_back(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED)
    _fail_planning(monkeypatch)
    with pytest.raises(RuntimeError, match="planning failed"):
        _update(s["event_id"], start_at=_START + datetime.timedelta(days=1),
                end_at=_END + datetime.timedelta(days=1), location_name="Вокзал")
    with session_scope() as session:
        event = session.get(Event, s["event_id"])
        assert event is not None
        assert (event.start_at, event.location_name) == (_START, "Клуб")
        occurrence = session.execute(
            sa.select(EventOccurrence).where(EventOccurrence.event_id == s["event_id"])
        ).scalar_one()
        assert occurrence.starts_at == _START
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_worker_sees_the_jobs_only_after_the_business_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _scenario()
    _enable_rules(RESCHEDULED)
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
        event = session.execute(
            sa.select(Event).where(Event.id == s["event_id"]).with_for_update()
        ).scalar_one()
        crud.update_event_with_targeting(
            session, event=event, updated_by=None,  # type: ignore[arg-type]
            start_at=_START + datetime.timedelta(days=1), end_at=_END + datetime.timedelta(days=1),
        )
    assert probed == [[]]
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    reports = _worker(transport).run_once()
    assert [r.result for r in reports] == ["completed", "completed"]
    for text in _sent_texts(transport):
        assert text == (
            "Событие <b>Поход</b> перенесено: 01.12.2026 12:00–20:00 (Europe/Moscow) → "
            f"02.12.2026 12:00–20:00 (Europe/Moscow). {BASE_URL}/events?event={s['event_id']}"
        )


@requires_postgres
def test_link_is_omitted_without_public_base_url() -> None:
    s = _scenario()
    _enable_rules(CANCELLED)
    _cancel(s["event_id"], reason="Дождь")
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    _worker(transport, base_url=None).run_once()
    for text in _sent_texts(transport):
        assert text.endswith("Причина: Дождь.")
        assert "http" not in text


# --- Through the HTTP API ---------------------------------------------------------------


@pytest.fixture
def client() -> Any:
    test_client = TestClient(app, raise_server_exceptions=True)
    test_client.cookies.set("csrf_token", "t")
    yield test_client
    app.dependency_overrides.pop(get_current_principal, None)


def _as_admin(club_id: uuid.UUID) -> None:
    with session_scope() as session:
        person = _person(session)
        user = User(person=person, login_identifier=f"a-{uuid.uuid4().hex[:8]}@club.test",
                    status="active")
        session.add(user)
        session.flush()
        club = session.get(Club, club_id)
        assert club is not None
        _assign_role(session, user.id, "admin", club)
        session.commit()
        user_id = user.id
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


@requires_postgres
def test_api_cancel_and_reschedule_plan_notifications(client: TestClient) -> None:
    s = _scenario()
    _enable_rules(CANCELLED, RESCHEDULED)
    _as_admin(s["club_id"])
    headers = {"X-CSRF-Token": "t"}
    new_start = (_START + datetime.timedelta(days=3)).isoformat()
    new_end = (_END + datetime.timedelta(days=3)).isoformat()
    response = client.patch(
        f"/api/v1/events/{s['event_id']}",
        json={"start_at": new_start, "end_at": new_end, "location_name": "Вокзал"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert _recipients(RESCHEDULED) == {s["alice"], s["bob"]}
    # The same PATCH again: no new fact.
    assert client.patch(
        f"/api/v1/events/{s['event_id']}", json={"start_at": new_start, "end_at": new_end},
        headers=headers,
    ).status_code == 200
    response = client.post(
        f"/api/v1/events/{s['event_id']}/status",
        json={"status": "cancelled", "cancellation_reason": "Шторм"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert _recipients(CANCELLED) == {s["alice"], s["bob"]}
    assert {n.event_type for n in _notifications()} == {CANCELLED, RESCHEDULED}
    assert _counts() == (4, 4, 4)
