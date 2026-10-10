"""Integration tests for the business-event notification boundary
(Issue #336 PR-0, ADR-0049) against real PostgreSQL:
app.notifications.business.plan_catalog_notification over the existing
Notification Engine, with the real Global Admin Policy, preference
policies, linked-identity reachability, group/topic routes, the outbox
worker and the Telegram adapter (scripted Bot API transport — never real
Telegram).

Covered: personal master switch + per-event preference (missing = OFF),
mandatory semantics vs Global OFF and a disabled rule, Telegram-link
reachability, group/topic routes independent of personal preferences,
Telegram-only planning, idempotency per fact/recipient/route, atomicity
(rollback leaves nothing, the worker cannot see uncommitted jobs), the
contract-error path (the caller rolls the business change back), and
rendering + escaping + links on the real worker path, including a
permanent render failure and a retry.

No catalog key is wired to a domain service in PR-0 and every key is
`pending` (or `blocked`), which the boundary refuses. The tests therefore
mark the three keys they exercise `implemented` for the duration of each
test only (`_implemented_test_keys`), seed their own rules/templates and
use a stand-in business row written in the same transaction. The status
gate itself is tested against the real catalog at the end of this module.
"""

import dataclasses
import datetime
import uuid
from typing import Any, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.identity import Club, Person, User
from app.db.notifications import (
    CommunicationChannelPreference,
    CommunicationPreference,
    Notification,
    NotificationDelivery,
    NotificationRule,
    NotificationTemplate,
    TelegramDestination,
)
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.db.telegram import TelegramIdentity
from app.notification_settings.policy import GlobalAdminPolicy
from app.notifications.business import CatalogNotificationError, plan_catalog_notification
from app.notifications.catalog import CATALOG, STATUS_IMPLEMENTED
from app.notifications.delivery import NotificationDeliveryHandler
from app.notifications.engine import (
    EXCLUDED_DESTINATION_DISABLED,
    EXCLUDED_GLOBAL_POLICY_DISABLED,
    EXCLUDED_NO_APPLICABLE_RULE,
    EXCLUDED_PREFERENCE_DISABLED,
    EXCLUDED_RECIPIENT_UNREACHABLE,
    EXCLUDED_RULE_DISABLED,
    NotificationPlanOutcome,
)
from app.notifications.telegram_adapter import TelegramChannelAdapter
from app.notifications.vocabulary import NOTIFICATION_DELIVERY_JOB_TYPE
from app.outbox.claiming import claim_jobs
from app.outbox.worker import OutboxWorker, WorkerConfig
from app.telegram.bot_api import TelegramTransportError
from tests.notification_settings_helpers import store_policy
from tests.telegram_fakes import ScriptedTransport, client_for, ok

from .conftest import requires_postgres

OPTIONAL = "registration.created"
MANDATORY = "event.cancelled"
GROUP_ONLY_PERSONAL = "event.updated"  # optional, no group routing
BASE_URL = "https://crm.example.org"
TG_USER = 7_100_000_001
GROUP_CHAT = -1009876543210

TEMPLATES = {
    "registration.created": (
        "registration_created.telegram",
        "Вы зарегистрированы на **{{event_title}}**. Дата: {{event_datetime}}. {{event_url}}",
    ),
    "event.cancelled": (
        "event_cancelled.telegram",
        "Событие **{{event_title}}** отменено. Дата: {{event_datetime}}. "
        "Причина: {{cancellation_reason}}. {{event_url}}",
    ),
    "event.updated": (
        "event_updated.telegram",
        "Обновление события **{{event_title}}**: {{change_summary}}. {{event_url}}",
    ),
}
SCOPES = {
    "registration.created": "registrant",
    "event.cancelled": "registered_participants",
    "event.updated": "registered_participants",
}


class _Allow:
    def allows(self, session: Session, *, user_id: uuid.UUID) -> bool:
        return True


class _Deny:
    def allows(self, session: Session, *, user_id: uuid.UUID) -> bool:
        return False


# --- Setup -----------------------------------------------------------------------


def _user(session: Session, *, linked: bool = True, telegram_id: int = TG_USER) -> uuid.UUID:
    person = Person(last_name="Petrova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    user = User(
        person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test", status="active"
    )
    session.add(user)
    session.flush()
    if linked:
        session.add(
            TelegramIdentity(user_id=user.id, telegram_user_id=telegram_id, status="active")
        )
        session.flush()
    return user.id


def _seed(
    event_type: str,
    *,
    personal_rule: Optional[bool] = True,
    group_rule: Optional[bool] = None,
    template_active: bool = True,
    email_rule: bool = False,
) -> None:
    """Rule(s) and template as a slice seeds them; `None` = no rule."""
    code, body = TEMPLATES[event_type]
    with session_scope() as session:
        session.add(
            NotificationTemplate(
                code=code, channel="telegram", locale="ru", body_template=body,
                is_active=template_active,
            )
        )
        if personal_rule is not None:
            session.add(
                NotificationRule(
                    event_type=event_type, channel="telegram",
                    recipient_scope=SCOPES[event_type], is_enabled=personal_rule,
                )
            )
        if group_rule is not None:
            session.add(
                NotificationRule(
                    event_type=event_type, channel="telegram",
                    recipient_scope="telegram_destination", is_enabled=group_rule,
                )
            )
        if email_rule:
            session.add(
                NotificationRule(
                    event_type=event_type, channel="email",
                    recipient_scope=SCOPES[event_type], is_enabled=True,
                )
            )
        session.commit()


def _route(
    session: Session, *, event_types: list[str], enabled: bool = True, thread: Optional[int] = 5
) -> uuid.UUID:
    route = TelegramDestination(
        name="Клуб", chat_id=GROUP_CHAT, message_thread_id=thread, topic_name="Походы",
        enabled=enabled, notification_scope={"event_types": event_types},
    )
    session.add(route)
    session.flush()
    return route.id


def _prefs(
    session: Session, user_id: uuid.UUID, *, master: Optional[bool], event: Optional[bool],
    event_type: str = OPTIONAL,
) -> None:
    if master is not None:
        session.add(
            CommunicationChannelPreference(user_id=user_id, channel="telegram", enabled=master)
        )
    if event is not None:
        session.add(
            CommunicationPreference(
                user_id=user_id, channel="telegram", notification_type=event_type, enabled=event
            )
        )
    session.flush()


def _context(event_type: str) -> dict[str, str]:
    context = {
        "event_title": "Поход <на> Эльбрус & вниз",
        "event_datetime": "12.10.2026 08:00 (Europe/Moscow)",
        "event_id": str(uuid.uuid4()),
    }
    if event_type == MANDATORY:
        context["cancellation_reason"] = ""
    return context


def _plan(
    session: Session,
    event_type: str,
    users: list[uuid.UUID],
    *,
    fact: str = "fact-1",
    routes: bool = False,
    access: Any = None,
    context: Optional[dict[str, str]] = None,
) -> NotificationPlanOutcome:
    return plan_catalog_notification(
        session,
        event_type=event_type,
        fact_id=fact,
        subject_type="event",
        subject_id=None,
        club_id=None,
        recipient_user_ids=users,
        recipient_access=access or _Allow(),
        render_context=context if context is not None else _context(event_type),
        publish_to_routes=routes,
    )


# The real catalog entries, captured before any test changes them.
_REAL_CATALOG = dict(CATALOG)
_TEST_KEYS = (OPTIONAL, MANDATORY, GROUP_ONLY_PERSONAL)


@pytest.fixture(autouse=True)
def _implemented_test_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate completed slices for the keys under test; monkeypatch
    restores the real catalog after each test."""
    for key in _TEST_KEYS:
        monkeypatch.setitem(
            CATALOG, key, dataclasses.replace(_REAL_CATALOG[key], status=STATUS_IMPLEMENTED)
        )


def _counts() -> tuple[int, int, int]:
    with session_scope() as session:
        return (
            session.scalar(sa.select(sa.func.count()).select_from(Notification)) or 0,
            session.scalar(sa.select(sa.func.count()).select_from(NotificationDelivery)) or 0,
            session.scalar(sa.select(sa.func.count()).select_from(OutboxJob)) or 0,
        )


# --- Personal preferences ------------------------------------------------------------


@pytest.mark.parametrize(
    ("master", "event", "created"),
    [
        (None, None, False),
        (True, None, False),
        (None, True, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
@requires_postgres
def test_optional_event_needs_master_switch_and_event_preference(
    master: Optional[bool], event: Optional[bool], created: bool
) -> None:
    store_policy(telegram=True)
    _seed(OPTIONAL)
    with session_scope() as session:
        user = _user(session)
        _prefs(session, user, master=master, event=event)
        outcome = _plan(session, OPTIONAL, [user])
        session.commit()
    (recipient,) = outcome.recipients
    assert recipient.created is created
    if not created:
        assert recipient.excluded_reason == EXCLUDED_PREFERENCE_DISABLED
        assert _counts() == (0, 0, 0)
    else:
        assert _counts() == (1, 1, 1)


@pytest.mark.parametrize(("master", "event"), [(None, None), (False, False), (False, None)])
@requires_postgres
def test_mandatory_event_ignores_personal_opt_out(
    master: Optional[bool], event: Optional[bool]
) -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        user = _user(session)
        _prefs(session, user, master=master, event=event, event_type=MANDATORY)
        outcome = _plan(session, MANDATORY, [user])
        session.commit()
    assert outcome.recipients[0].created is True
    assert _counts() == (1, 1, 1)


@pytest.mark.parametrize(
    ("policy_on", "rule", "reason"),
    [
        (False, True, EXCLUDED_GLOBAL_POLICY_DISABLED),
        (True, False, EXCLUDED_RULE_DISABLED),
        (True, None, EXCLUDED_NO_APPLICABLE_RULE),
    ],
)
@requires_postgres
def test_global_off_and_admin_rule_block_even_mandatory_events(
    policy_on: bool, rule: Optional[bool], reason: str
) -> None:
    store_policy(telegram=policy_on)
    _seed(MANDATORY, personal_rule=rule)
    with session_scope() as session:
        user = _user(session)
        outcome = _plan(session, MANDATORY, [user])
        session.commit()
    assert outcome.excluded_channels == {"telegram": reason}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_no_saved_global_policy_means_telegram_off() -> None:
    _seed(MANDATORY)
    with session_scope() as session:
        outcome = _plan(session, MANDATORY, [_user(session)])
        session.commit()
    assert outcome.excluded_channels == {"telegram": EXCLUDED_GLOBAL_POLICY_DISABLED}


@pytest.mark.parametrize("event_type", [OPTIONAL, MANDATORY])
@requires_postgres
def test_unlinked_user_gets_no_delivery_even_for_mandatory_events(event_type: str) -> None:
    store_policy(telegram=True)
    _seed(event_type)
    with session_scope() as session:
        user = _user(session, linked=False)
        _prefs(session, user, master=True, event=True, event_type=OPTIONAL)
        outcome = _plan(session, event_type, [user])
        session.commit()
    assert outcome.recipients[0].excluded_reason == EXCLUDED_RECIPIENT_UNREACHABLE
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_recipient_access_is_still_enforced() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        outcome = _plan(session, MANDATORY, [_user(session)], access=_Deny())
        session.commit()
    assert outcome.recipients[0].notification is None
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_email_is_never_a_candidate_for_business_events() -> None:
    store_policy(email=True, telegram=True)
    _seed(MANDATORY, email_rule=True)
    with session_scope() as session:
        outcome = _plan(session, MANDATORY, [_user(session)])
        session.commit()
    (recipient,) = outcome.recipients
    assert [d.channel for d in recipient.deliveries] == ["telegram"]


# --- Group/topic routes --------------------------------------------------------------


@requires_postgres
def test_route_is_independent_of_personal_preferences() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=True)
    with session_scope() as session:
        unlinked = _user(session, linked=False)
        route = _route(session, event_types=[MANDATORY])
        outcome = _plan(session, MANDATORY, [unlinked], routes=True)
        session.commit()
    assert outcome.recipients[0].excluded_reason == EXCLUDED_RECIPIENT_UNREACHABLE
    (published,) = outcome.destinations
    assert published.created is True
    with session_scope() as session:
        notification = session.get(Notification, published.notification.id)  # type: ignore[union-attr]
        assert notification is not None
        assert (notification.recipient_user_id, notification.recipient_destination_id) == (
            None,
            route,
        )
        (delivery,) = session.execute(sa.select(NotificationDelivery)).scalars()
        assert (delivery.destination_type, delivery.destination_id) == (
            "telegram_destination",
            route,
        )


@requires_postgres
def test_personal_rule_and_preferences_never_affect_the_route() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, personal_rule=False, group_rule=True)
    with session_scope() as session:
        user = _user(session)
        _prefs(session, user, master=False, event=False, event_type=MANDATORY)
        route = _route(session, event_types=[MANDATORY])
        outcome = _plan(session, MANDATORY, [user], routes=True)
        session.commit()
    # The personal rule is disabled by the administrator; the route has its
    # own rule and configuration and still publishes.
    assert outcome.excluded_channels == {"telegram": EXCLUDED_RULE_DISABLED}
    assert outcome.destinations[0].destination_id == route
    assert outcome.destinations[0].created is True


@pytest.mark.parametrize(
    ("route_kwargs", "group_rule", "reason"),
    [
        ({"enabled": False}, True, EXCLUDED_DESTINATION_DISABLED),
        ({}, False, EXCLUDED_RULE_DISABLED),
        ({}, None, EXCLUDED_NO_APPLICABLE_RULE),
    ],
)
@requires_postgres
def test_route_needs_its_rule_and_an_enabled_configuration(
    route_kwargs: dict, group_rule: Optional[bool], reason: str
) -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=group_rule)
    with session_scope() as session:
        _route(session, event_types=[MANDATORY], **route_kwargs)
        user = _user(session)
        outcome = _plan(session, MANDATORY, [user], routes=True)
        session.commit()
    if route_kwargs.get("enabled") is False:
        # A disabled route is not even a candidate.
        assert outcome.destinations == ()
    else:
        assert outcome.destinations[0].excluded_reason == reason
    assert outcome.recipients[0].created is True


@requires_postgres
def test_route_not_subscribed_to_the_event_is_not_a_candidate() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=True)
    with session_scope() as session:
        _route(session, event_types=["event.created"])
        _route(session, event_types=[], thread=None)
        outcome = _plan(session, MANDATORY, [], routes=True)
        session.commit()
    assert outcome.destinations == ()
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_global_off_blocks_routes_too() -> None:
    store_policy(telegram=False)
    _seed(MANDATORY, group_rule=True)
    with session_scope() as session:
        _route(session, event_types=[MANDATORY])
        outcome = _plan(session, MANDATORY, [], routes=True)
        session.commit()
    assert outcome.destinations[0].excluded_reason == EXCLUDED_GLOBAL_POLICY_DISABLED
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_routes_are_used_only_when_the_caller_publishes_and_the_key_allows_it() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=True)
    with session_scope() as session:
        _route(session, event_types=[MANDATORY, GROUP_ONLY_PERSONAL])
        outcome = _plan(session, MANDATORY, [], routes=False)
        assert outcome.destinations == ()
        with pytest.raises(CatalogNotificationError):
            _plan(session, GROUP_ONLY_PERSONAL, [], routes=True,
                  context={"event_title": "t", "event_id": str(uuid.uuid4())})
        session.rollback()


# --- Idempotency -----------------------------------------------------------------------


@requires_postgres
def test_same_fact_planned_twice_creates_one_notification_per_recipient_and_route() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=True)
    with session_scope() as session:
        users = [_user(session), _user(session, telegram_id=TG_USER + 1)]
        _route(session, event_types=[MANDATORY])
        context = _context(MANDATORY)
        first = _plan(session, MANDATORY, users, routes=True, context=context)
        session.commit()
    with session_scope() as session:
        second = _plan(session, MANDATORY, users, routes=True, context=context)
        session.commit()
    assert [r.created for r in first.recipients] == [True, True]
    assert [r.created for r in second.recipients] == [False, False]
    assert second.destinations[0].created is False
    assert _counts() == (3, 3, 3)
    with session_scope() as session:
        keys = set(session.execute(sa.select(Notification.idempotency_key)).scalars())
    assert keys == {f"{MANDATORY}:fact-1:user:{u}" for u in users} | {
        f"{MANDATORY}:fact-1:dest:{first.destinations[0].destination_id}"
    }


@requires_postgres
def test_a_new_fact_is_a_new_notification() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        user = _user(session)
        _plan(session, MANDATORY, [user], fact="fact-1")
        _plan(session, MANDATORY, [user], fact="fact-2")
        session.commit()
    assert _counts() == (2, 2, 2)


@pytest.mark.parametrize("fact", ["", "  ", "a:b", "x" * 121])
@requires_postgres
def test_fact_id_must_be_a_plain_id(fact: str) -> None:
    with session_scope() as session:
        with pytest.raises(CatalogNotificationError):
            _plan(session, MANDATORY, [], fact=fact)


# --- Atomicity and contract errors -----------------------------------------------------------


def _business_row(session: Session) -> uuid.UUID:
    club = Club(name=f"Business {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club.id


@requires_postgres
def test_business_rollback_leaves_no_notification_delivery_or_job() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        user = _user(session)
        session.commit()
    with session_scope() as session:
        club_id = _business_row(session)
        outcome = _plan(session, MANDATORY, [user])
        assert outcome.recipients[0].created is True
        session.rollback()
    assert _counts() == (0, 0, 0)
    with session_scope() as session:
        assert session.get(Club, club_id) is None


@requires_postgres
def test_worker_cannot_claim_a_job_before_the_business_commit() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        user = _user(session)
        session.commit()
    factory = get_session_factory()
    business = factory()
    try:
        _business_row(business)
        _plan(business, MANDATORY, [user])
        business.flush()
        with factory() as worker_session:
            claimed = claim_jobs(
                worker_session, worker_id="w", batch_size=10, lease=datetime.timedelta(seconds=30)
            )
            worker_session.rollback()
        assert claimed == []
        business.commit()
    finally:
        business.close()
    with factory() as worker_session:
        claimed = claim_jobs(
            worker_session, worker_id="w", batch_size=10, lease=datetime.timedelta(seconds=30)
        )
        worker_session.rollback()
    assert [job.job_type for job in claimed] == [NOTIFICATION_DELIVERY_JOB_TYPE]


@pytest.mark.parametrize(
    ("event_type", "context"),
    [
        (MANDATORY, {"event_title": "t", "event_id": str(uuid.uuid4())}),  # missing datetime
        (MANDATORY, {**{"event_title": "t", "event_datetime": "d", "event_id": "x"}, "x": "1"}),
        ("membership.approved", {}),
        ("not.a.catalog.key", {}),
    ],
)
@requires_postgres
def test_contract_error_is_raised_before_any_write_and_the_caller_rolls_back(
    event_type: str, context: dict[str, str]
) -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        user = _user(session)
        session.commit()
    with session_scope() as session:
        club_id = _business_row(session)
        with pytest.raises(CatalogNotificationError):
            _plan(session, event_type, [user], context=context)
        # PO decision 14 / ADR-0049 §2.6: no savepoint — the business
        # change is rolled back with it.
        session.rollback()
    assert _counts() == (0, 0, 0)
    with session_scope() as session:
        assert session.get(Club, club_id) is None


@requires_postgres
def test_inactive_template_creates_no_delivery_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, template_active=False)
    with session_scope() as session:
        outcome = _plan(session, MANDATORY, [_user(session)])
        session.commit()
    assert outcome.excluded_channels == {"telegram": "template_unavailable"}
    assert "notifications.template_unavailable event_type=event.cancelled" in caplog.text
    assert _counts() == (0, 0, 0)


# --- Worker: rendering, escaping, links, failures --------------------------------------------


def _worker(transport: ScriptedTransport, *, base_url: Optional[str] = BASE_URL) -> OutboxWorker:
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
        config=WorkerConfig(worker_id=f"w-{uuid.uuid4().hex[:6]}"),
    )


@requires_postgres
def test_worker_sends_rendered_escaped_html_with_the_link() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=True)
    context = _context(MANDATORY)
    with session_scope() as session:
        user = _user(session)
        _route(session, event_types=[MANDATORY], thread=9)
        _plan(session, MANDATORY, [user], routes=True, context=context)
        session.commit()
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    worker = _worker(transport)
    reports = [*worker.run_once(), *worker.run_once()]
    assert [r.result for r in reports] == ["completed", "completed"]
    expected = (
        "Событие <b>Поход &lt;на&gt; Эльбрус &amp; вниз</b> отменено. "
        "Дата: 12.10.2026 08:00 (Europe/Moscow). Причина: не указана. "
        f"{BASE_URL}/events?event={context['event_id']}"
    )
    calls = sorted(
        (call.params for call in transport.calls_to("sendMessage")), key=lambda p: p["chat_id"]
    )
    assert calls == [
        {"chat_id": GROUP_CHAT, "text": expected, "message_thread_id": 9, "parse_mode": "HTML"},
        {"chat_id": TG_USER, "text": expected, "parse_mode": "HTML"},
    ]


@requires_postgres
def test_link_is_omitted_when_no_public_base_url_is_configured() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        _plan(session, MANDATORY, [_user(session)])
        session.commit()
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    _worker(transport, base_url=None).run_once()
    (call,) = transport.calls_to("sendMessage")
    assert call.params["text"].endswith("Причина: не указана.")
    assert "http" not in call.params["text"]


@requires_postgres
def test_render_failure_is_permanent_and_nothing_is_sent() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        _plan(session, MANDATORY, [_user(session)])
        session.commit()
    # The stored template changed after planning to need an undeclared value.
    with session_scope() as session:
        session.execute(
            sa.update(NotificationTemplate).values(body_template="{{event_title}} {{secret}}")
        )
        session.commit()
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    (report,) = _worker(transport).run_once()
    assert (report.result, report.error_code) == ("dead", "template_render_failed")
    assert transport.calls_to("sendMessage") == []
    with session_scope() as session:
        (delivery,) = session.execute(sa.select(NotificationDelivery)).scalars()
    assert (delivery.status, delivery.next_retry_at, delivery.last_error_code) == (
        "failed",
        None,
        "template_render_failed",
    )
    assert delivery.last_error_message == "variable secret is not declared"


@requires_postgres
def test_retryable_provider_failure_is_retried_by_the_existing_policy() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        _plan(session, MANDATORY, [_user(session)])
        session.commit()
    transport = ScriptedTransport().script("sendMessage", TelegramTransportError("timeout"))
    (report,) = _worker(transport).run_once()
    assert report.result == "pending"
    with session_scope() as session:
        (delivery,) = session.execute(sa.select(NotificationDelivery)).scalars()
        (job,) = session.execute(sa.select(OutboxJob)).scalars()
        assert (delivery.status, delivery.attempts) == ("failed", 1)
        assert delivery.next_retry_at is not None
        assert (job.status, job.attempts) == ("pending", 1)
        session.execute(sa.update(OutboxJob).values(next_attempt_at=sa.func.now()))
        session.commit()
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 2})})
    (report,) = _worker(transport).run_once()
    assert report.result == "completed"
    with session_scope() as session:
        (delivery,) = session.execute(sa.select(NotificationDelivery)).scalars()
    assert (delivery.status, delivery.attempts) == ("delivered", 2)


@requires_postgres
def test_global_off_after_planning_pauses_delivery() -> None:
    store_policy(telegram=True)
    _seed(MANDATORY)
    with session_scope() as session:
        _plan(session, MANDATORY, [_user(session)])
        session.commit()
    store_policy(telegram=False)
    transport = ScriptedTransport(defaults={"sendMessage": ok({"message_id": 1})})
    (report,) = _worker(transport).run_once()
    assert report.result == "deferred"
    assert transport.calls_to("sendMessage") == []


# --- Catalog status gate against the real catalog ---------------------------------------------


@pytest.mark.parametrize("event_type", ["event.cancelled", "membership.approved"])
@requires_postgres
def test_pending_and_blocked_keys_write_nothing_and_roll_the_business_back(
    event_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every rule, template, preference and route in place, a key that
    is not `implemented` (event.cancelled is `pending` in PR-0,
    membership.approved is `blocked`) is still refused: no Notification,
    Delivery or outbox job is written and the caller rolls back."""
    for key in _TEST_KEYS:
        monkeypatch.setitem(CATALOG, key, _REAL_CATALOG[key])
    assert CATALOG[event_type].status != STATUS_IMPLEMENTED
    store_policy(telegram=True)
    _seed(MANDATORY, group_rule=True)
    with session_scope() as session:
        user = _user(session)
        _route(session, event_types=[MANDATORY, "membership.approved"])
        session.commit()
    with session_scope() as session:
        club_id = _business_row(session)
        with pytest.raises(CatalogNotificationError, match="cannot be planned"):
            _plan(session, event_type, [user], routes=True, context=_context(MANDATORY))
        session.rollback()
    assert _counts() == (0, 0, 0)
    with session_scope() as session:
        assert session.get(Club, club_id) is None
