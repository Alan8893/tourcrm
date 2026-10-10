"""Integration tests for the Notification Engine (Issue #319, ADR-0045
§2.4/§2.10) against a real PostgreSQL instance.

Covers the policy hierarchy (Global -> Club Admin Policy -> installation-
wide -> club-specific Rule -> User Preference, restrict-only, fail closed
without an Admin Policy source, no Rule -> no Delivery), audience
authorization through the existing RBAC engine (including cross-club),
template resolution, "zero eligible Deliveries -> no Notification",
idempotency, the `notification.delivery` outbox contract and the
caller-owned transaction boundary.

The AdminPolicy/PreferencePolicy/RecipientAccess fakes stand in for what
the Settings slice and each event's specification gate will supply; they
encode no business rule of their own. Self-contained factories, per this
codebase's convention of not importing helpers across test files.
"""

import datetime
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import Club, Person, User
from app.db.notifications import (
    CommunicationPreference,
    Notification,
    NotificationDelivery,
    NotificationRule,
    NotificationTemplate,
)
from app.db.outbox import OutboxJob
from app.db.session import get_session_factory, session_scope
from app.notifications.engine import (
    EXCLUDED_ADMIN_POLICY_UNAVAILABLE,
    EXCLUDED_CLUB_POLICY_DISABLED,
    EXCLUDED_GLOBAL_POLICY_DISABLED,
    EXCLUDED_NO_APPLICABLE_RULE,
    EXCLUDED_NO_ELIGIBLE_CHANNEL,
    EXCLUDED_PREFERENCE_DISABLED,
    EXCLUDED_RECIPIENT_NOT_AUTHORIZED,
    EXCLUDED_RULE_DISABLED,
    EXCLUDED_TEMPLATE_UNAVAILABLE,
    IdempotencyKeyConflictError,
    InvalidNotificationRequestError,
    MultiChannelTemplateNotRepresentableError,
    NotificationPlanOutcome,
    NotificationRequest,
    Recipient,
    plan_notifications,
)
from app.notifications.ports import PermissionRecipientAccess
from app.notifications.repository import InvalidNotificationChannelError

from .conftest import requires_postgres

_EVENT = "test.event"
_SCOPE = "test_scope"
_EMAIL_TEMPLATE = "test.event.email"
_TELEGRAM_TEMPLATE = "test.event.telegram"
_BOTH_CHANNELS = {"email": _EMAIL_TEMPLATE, "telegram": _TELEGRAM_TEMPLATE}


# --- Port fakes ----------------------------------------------------------------


@dataclass
class _AdminPolicy:
    global_enabled: dict[str, bool] = field(
        default_factory=lambda: {"email": True, "telegram": True}
    )
    club_enabled: dict[tuple[uuid.UUID, str], bool] = field(default_factory=dict)

    def global_channel_enabled(self, session: Session, *, channel: str) -> bool:
        return self.global_enabled[channel]

    def club_channel_enabled(self, session: Session, *, club_id: uuid.UUID, channel: str) -> bool:
        return self.club_enabled.get((club_id, channel), True)


@dataclass(frozen=True)
class _Allow:
    allowed: frozenset[uuid.UUID]

    def allows(self, session: Session, *, user_id: uuid.UUID) -> bool:
        return user_id in self.allowed


class _OptOut:
    """Spec-gate stand-in: delivered unless the user stored OFF."""

    def allows(
        self, *, channel: str, stored_enabled: bool | None, master_enabled: bool | None
    ) -> bool:
        return stored_enabled is not False


class _OptIn:
    """Spec-gate stand-in: delivered only when the user stored ON."""

    def allows(
        self, *, channel: str, stored_enabled: bool | None, master_enabled: bool | None
    ) -> bool:
        return stored_enabled is True


class _Reachable:
    """Every recipient has a verified destination on every channel."""

    def reachable(self, session: Session, *, user_id: uuid.UUID, channel: str) -> bool:
        return True


# --- Factories -----------------------------------------------------------------


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


def _template(session, code: str = _EMAIL_TEMPLATE, channel: str = "email", **kw: Any):  # type: ignore[no-untyped-def]
    template = NotificationTemplate(
        code=code, channel=channel, locale="ru", body_template="Body", **kw
    )
    session.add(template)
    session.flush()
    return template


def _rule(session, club_id: uuid.UUID | None = None, channel: str = "email", enabled: bool = True):  # type: ignore[no-untyped-def]
    session.add(
        NotificationRule(
            club_id=club_id,
            event_type=_EVENT,
            channel=channel,
            recipient_scope=_SCOPE,
            is_enabled=enabled,
        )
    )
    session.flush()


def _preference(session, user: User, enabled: bool, channel: str = "email") -> None:  # type: ignore[no-untyped-def]
    session.add(
        CommunicationPreference(
            user_id=user.id, channel=channel, notification_type=_EVENT, enabled=enabled
        )
    )
    session.flush()


def _request(
    users: list[User],
    club: Club | None,
    channels: dict[str, str] | None = None,
    **overrides: Any,
) -> NotificationRequest:
    fields: dict[str, Any] = {
        "event_type": _EVENT,
        "subject_type": "test_subject",
        "subject_id": uuid.uuid4(),
        "recipient_scope": _SCOPE,
        "club_id": club.id if club else None,
        "recipients": [Recipient(user.id, f"{_EVENT}:{user.id}") for user in users],
        "channel_templates": channels or {"email": _EMAIL_TEMPLATE},
    }
    fields.update(overrides)
    return NotificationRequest(**fields)


def _plan(  # type: ignore[no-untyped-def]
    session,
    request: NotificationRequest,
    *,
    admin_policy: Any = None,
    access: Any = None,
    preference: Any = None,
) -> NotificationPlanOutcome:
    allowed = frozenset(r.user_id for r in request.recipients)
    return plan_notifications(
        session,
        request,
        admin_policy=_AdminPolicy() if admin_policy is None else admin_policy,
        recipient_access=access or _Allow(allowed),
        preference_policy=preference or _OptOut(),
        reachability=_Reachable(),
    )


def _counts() -> tuple[int, int, int]:
    with session_scope() as session:
        return tuple(  # type: ignore[return-value]
            session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (Notification, NotificationDelivery, OutboxJob)
        )


def _setup(session, *, rule_club: bool = False):  # type: ignore[no-untyped-def]
    club, user = _club(session), _user(session)
    _template(session)
    _rule(session, None)
    if rule_club:
        _rule(session, club.id)
    return club, user


# --- One eligible Delivery -------------------------------------------------------


@requires_postgres
def test_one_eligible_delivery_creates_notification_delivery_and_outbox_job() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        template_id = session.execute(select(NotificationTemplate.id)).scalar_one()
        request = _request([user], club)
        outcome = _plan(session, request)
        session.commit()

    (result,) = outcome.recipients
    assert result.created is True and result.excluded_reason is None
    assert result.notification is not None
    assert result.notification.template_id == template_id
    assert result.notification.status == "pending"
    (delivery,) = result.deliveries
    assert (delivery.channel, delivery.destination_type, delivery.destination_id) == (
        "email",
        "user",
        user.id,
    )
    with session_scope() as session:
        (job,) = session.execute(select(OutboxJob)).scalars().all()
        assert job.job_type == "notification.delivery"
        assert job.payload == {"delivery_id": str(delivery.id)}
        assert job.deduplication_key == f"notification_delivery:{delivery.id}"
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_scheduled_notification_schedules_its_outbox_job() -> None:
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    with session_scope() as session:
        club, user = _setup(session)
        _plan(session, _request([user], club, scheduled_at=later))
        session.commit()
        job = session.execute(select(OutboxJob)).scalar_one()
        notification = session.execute(select(Notification)).scalar_one()
    assert job.next_attempt_at == later
    assert notification.scheduled_at == later


# --- Admin Policy ------------------------------------------------------------------


@requires_postgres
def test_missing_admin_policy_source_fails_closed() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        outcome = plan_notifications(
            session,
            _request([user], club),
            admin_policy=None,
            recipient_access=_Allow(frozenset({user.id})),
            preference_policy=_OptOut(),
            reachability=_Reachable(),
        )
        session.commit()

    assert outcome.recipients[0].excluded_reason == EXCLUDED_ADMIN_POLICY_UNAVAILABLE
    assert outcome.excluded_channels == {"email": EXCLUDED_ADMIN_POLICY_UNAVAILABLE}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_global_admin_off_means_no_delivery() -> None:
    with session_scope() as session:
        club, user = _setup(session, rule_club=True)
        policy = _AdminPolicy(global_enabled={"email": False, "telegram": True})
        # Club ON cannot override Global OFF.
        policy.club_enabled[(club.id, "email")] = True
        outcome = _plan(session, _request([user], club), admin_policy=policy)
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_GLOBAL_POLICY_DISABLED}
    assert outcome.recipients[0].excluded_reason == EXCLUDED_NO_ELIGIBLE_CHANNEL
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_club_admin_off_means_no_delivery_even_with_rules_and_preference_on() -> None:
    with session_scope() as session:
        club, user = _setup(session, rule_club=True)
        _preference(session, user, enabled=True)
        policy = _AdminPolicy(club_enabled={(club.id, "email"): False})
        outcome = _plan(session, _request([user], club), admin_policy=policy)
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_CLUB_POLICY_DISABLED}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_admin_off_overrides_user_on() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        _preference(session, user, enabled=True)
        policy = _AdminPolicy(global_enabled={"email": False, "telegram": False})
        outcome = _plan(session, _request([user], club), admin_policy=policy, preference=_OptIn())
        session.commit()

    assert outcome.recipients[0].notification is None
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_club_admin_policy_is_not_consulted_for_installation_level_events() -> None:
    with session_scope() as session:
        user = _user(session)
        _template(session)
        _rule(session, None)
        outcome = _plan(session, _request([user], None))
        session.commit()

    assert outcome.recipients[0].created is True


# --- Notification Rules ------------------------------------------------------------


@requires_postgres
def test_no_applicable_rule_means_no_delivery() -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        _template(session)
        # Rules for another scope / another club do not apply.
        session.add(
            NotificationRule(
                event_type=_EVENT, channel="email", recipient_scope="other_scope", is_enabled=True
            )
        )
        _rule(session, _club(session).id)
        outcome = _plan(session, _request([user], club))
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_NO_APPLICABLE_RULE}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_installation_rule_off_cannot_be_re_enabled_by_club_rule() -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        _template(session)
        _rule(session, None, enabled=False)
        _rule(session, club.id, enabled=True)
        outcome = _plan(session, _request([user], club))
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_RULE_DISABLED}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_club_rule_off_restricts_installation_rule_on() -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        _template(session)
        _rule(session, None, enabled=True)
        _rule(session, club.id, enabled=False)
        outcome = _plan(session, _request([user], club))
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_RULE_DISABLED}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_club_rule_alone_is_applicable() -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        _template(session)
        _rule(session, club.id)
        outcome = _plan(session, _request([user], club))
        session.commit()

    assert outcome.recipients[0].created is True


@requires_postgres
def test_another_clubs_rule_never_applies() -> None:
    with session_scope() as session:
        club, other, user = _club(session), _club(session), _user(session)
        _template(session)
        _rule(session, None, enabled=True)
        _rule(session, other.id, enabled=False)
        outcome = _plan(session, _request([user], club))
        session.commit()

    assert outcome.recipients[0].created is True


# --- User Preference ---------------------------------------------------------------


@requires_postgres
def test_user_preference_off_means_no_delivery_when_opt_out_is_allowed() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        _preference(session, user, enabled=False)
        outcome = _plan(session, _request([user], club), preference=_OptOut())
        session.commit()

    assert outcome.recipients[0].excluded_reason == EXCLUDED_PREFERENCE_DISABLED
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_user_preference_on_with_admin_and_rule_on_delivers() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        _preference(session, user, enabled=True)
        outcome = _plan(session, _request([user], club), preference=_OptIn())
        session.commit()

    assert outcome.recipients[0].created is True


@requires_postgres
def test_missing_preference_is_decided_by_the_events_policy_not_the_engine() -> None:
    with session_scope() as session:
        club, opt_in_user, opt_out_user = _club(session), _user(session), _user(session)
        _template(session)
        _rule(session, None)
        opt_in = _plan(session, _request([opt_in_user], club), preference=_OptIn())
        opt_out = _plan(session, _request([opt_out_user], club), preference=_OptOut())
        session.commit()

    assert opt_in.recipients[0].excluded_reason == EXCLUDED_PREFERENCE_DISABLED
    assert opt_out.recipients[0].created is True


@requires_postgres
def test_preference_of_another_event_type_is_ignored() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        session.add(
            CommunicationPreference(
                user_id=user.id, channel="email", notification_type="other.event", enabled=False
            )
        )
        outcome = _plan(session, _request([user], club), preference=_OptOut())
        session.commit()

    assert outcome.recipients[0].created is True


# --- Audience / authorization --------------------------------------------------------


@requires_postgres
def test_unauthorized_recipient_gets_no_notification() -> None:
    with session_scope() as session:
        club, allowed, denied = _club(session), _user(session), _user(session)
        _template(session)
        _rule(session, None)
        outcome = _plan(
            session, _request([allowed, denied], club), access=_Allow(frozenset({allowed.id}))
        )
        session.commit()

    by_user = {r.user_id: r for r in outcome.recipients}
    assert by_user[allowed.id].created is True
    assert by_user[denied.id].notification is None
    assert by_user[denied.id].excluded_reason == EXCLUDED_RECIPIENT_NOT_AUTHORIZED
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_no_eligible_audience_creates_nothing() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        outcome = _plan(session, _request([user], club), access=_Allow(frozenset()))
        session.commit()

    assert outcome.recipients[0].excluded_reason == EXCLUDED_RECIPIENT_NOT_AUTHORIZED
    assert _counts() == (0, 0, 0)


def _grant_club_role(session, user: User, club: Club, permission: Permission) -> None:  # type: ignore[no-untyped-def]
    role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test Role")
    session.add(role)
    session.flush()
    session.add(
        RolePermission(
            role_id=role.id,
            permission_id=permission.id,
            scopes=[RolePermissionScope(scope_type="all")],
        )
    )
    session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()


@requires_postgres
def test_permission_access_uses_existing_authorization_and_blocks_cross_club_leak() -> None:
    with session_scope() as session:
        club, other_club = _club(session), _club(session)
        member, outsider, no_role = _user(session), _user(session), _user(session)
        permission = Permission(code=f"resource-{uuid.uuid4().hex[:8]}.read")
        session.add(permission)
        session.flush()
        _grant_club_role(session, member, club, permission)
        _grant_club_role(session, outsider, other_club, permission)
        _template(session)
        _rule(session, None)
        access = PermissionRecipientAccess(
            permission_code=permission.code,
            context_for=lambda _session, _user_id: ResourceContext(club_id=club.id),
        )
        outcome = _plan(session, _request([member, outsider, no_role], club), access=access)
        session.commit()

    by_user = {r.user_id: r for r in outcome.recipients}
    assert by_user[member.id].created is True
    assert by_user[outsider.id].excluded_reason == EXCLUDED_RECIPIENT_NOT_AUTHORIZED
    assert by_user[no_role.id].excluded_reason == EXCLUDED_RECIPIENT_NOT_AUTHORIZED


@requires_postgres
def test_notification_settings_never_widen_authorization() -> None:
    with session_scope() as session:
        club, user = _setup(session, rule_club=True)
        _preference(session, user, enabled=True)
        outcome = _plan(
            session, _request([user], club), access=_Allow(frozenset()), preference=_OptIn()
        )
        session.commit()

    assert outcome.recipients[0].excluded_reason == EXCLUDED_RECIPIENT_NOT_AUTHORIZED
    assert _counts() == (0, 0, 0)


# --- Templates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "template_kwargs",
    [
        None,  # missing
        {"is_active": False},
        {"channel": "telegram"},  # channel mismatch
    ],
)
@requires_postgres
def test_unresolvable_template_means_no_delivery(template_kwargs: dict[str, Any] | None) -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        if template_kwargs is not None:
            _template(session, **template_kwargs)
        _rule(session, None)
        outcome = _plan(session, _request([user], club))
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_TEMPLATE_UNAVAILABLE}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_template_is_resolved_per_channel_before_any_delivery() -> None:
    with session_scope() as session:
        club, user = _club(session), _user(session)
        _template(session, code=_TELEGRAM_TEMPLATE, channel="telegram")
        _rule(session, None, channel="email")
        _rule(session, None, channel="telegram")
        # Email has a rule but no template: only Telegram is eligible.
        outcome = _plan(
            session,
            _request([user], club, channels=_BOTH_CHANNELS),
        )
        session.commit()

    assert outcome.excluded_channels == {"email": EXCLUDED_TEMPLATE_UNAVAILABLE}
    (delivery,) = outcome.recipients[0].deliveries
    assert delivery.channel == "telegram"


@requires_postgres
def test_multiple_eligible_channels_are_rejected_before_any_write() -> None:
    """GAP of #319: Notification has one template_id, templates are
    channel-specific — a multi-channel plan is not representable."""
    with session_scope() as session:
        club, user = _club(session), _user(session)
        _template(session)
        _template(session, code=_TELEGRAM_TEMPLATE, channel="telegram")
        _rule(session, None, channel="email")
        _rule(session, None, channel="telegram")
        with pytest.raises(MultiChannelTemplateNotRepresentableError):
            _plan(
                session,
                _request([user], club, channels=_BOTH_CHANNELS),
            )
        session.commit()

    assert _counts() == (0, 0, 0)


# --- Idempotency -------------------------------------------------------------------


@requires_postgres
def test_repeated_invocation_is_idempotent() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        first = _plan(session, _request([user], club))
        session.commit()

    with session_scope() as session:
        second = _plan(session, _request([user], club))
        third = _plan(session, _request([user], club))
        session.commit()

    assert first.recipients[0].created is True
    for repeat in (second, third):
        (result,) = repeat.recipients
        assert result.created is False
        assert result.notification is not None
        assert result.notification.id == first.recipients[0].notification.id  # type: ignore[union-attr]
        assert [d.id for d in result.deliveries] == [first.recipients[0].deliveries[0].id]
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_repeated_invocation_returns_existing_state_even_if_policy_changed() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        _plan(session, _request([user], club))
        session.commit()

    with session_scope() as session:
        outcome = plan_notifications(
            session,
            _request([user], club),
            admin_policy=None,
            recipient_access=_Allow(frozenset()),
            preference_policy=_OptIn(),
            reachability=_Reachable(),
        )
        session.commit()

    assert outcome.recipients[0].created is False
    assert len(outcome.recipients[0].deliveries) == 1
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_duplicate_invocation_does_not_abort_the_caller_transaction() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        _plan(session, _request([user], club))
        session.commit()

    with session_scope() as session:
        business = _club(session)  # the caller's business mutation
        _plan(session, _request([user], club))
        session.commit()
        business_id = business.id

    with session_scope() as session:
        assert session.get(Club, business_id) is not None


@requires_postgres
def test_concurrent_invocation_creates_one_notification_and_one_outbox_job() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        session.commit()

    factory = get_session_factory()
    first, second = factory(), factory()
    results: dict[str, NotificationPlanOutcome] = {}

    def _second() -> None:
        # Its idempotency lookup misses the uncommitted row; its INSERT ...
        # ON CONFLICT then waits for `first` and resolves to the existing row.
        results["second"] = _plan(second, _request([user], club))
        second.commit()

    try:
        results["first"] = _plan(first, _request([user], club))
        worker = threading.Thread(target=_second)
        worker.start()
        worker.join(timeout=1)
        assert worker.is_alive(), "the second invocation must wait on the first"
        first.commit()
        worker.join(timeout=30)
        assert not worker.is_alive()
    finally:
        first.close()
        second.close()
    outcome_first, outcome_second = results["first"], results["second"]

    assert outcome_first.recipients[0].created is True
    assert outcome_second.recipients[0].created is False
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_idempotency_key_of_another_recipient_is_a_conflict() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        other = _user(session)
        _plan(session, _request([user], club))
        session.commit()
        hijack = _request([other], club, recipients=[Recipient(other.id, f"{_EVENT}:{user.id}")])
        with pytest.raises(IdempotencyKeyConflictError):
            _plan(session, hijack)


# --- Transaction boundary ----------------------------------------------------------


@requires_postgres
def test_business_rollback_leaves_no_notification_delivery_or_outbox() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        session.commit()

    with session_scope() as session:
        business = _club(session)
        outcome = _plan(session, _request([user], club))
        assert outcome.recipients[0].created is True
        session.rollback()
        assert session.get(Club, business.id) is None

    assert _counts() == (0, 0, 0)


@requires_postgres
def test_business_mutation_and_notification_commit_together() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        session.commit()

    with session_scope() as session:
        business = _club(session)
        _plan(session, _request([user], club))
        session.commit()
        business_id = business.id

    with session_scope() as session:
        assert session.get(Club, business_id) is not None
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_engine_never_commits_or_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    with session_scope() as session:
        club, user = _setup(session)
        session.commit()

        def _forbidden(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("the Engine must not end the caller's transaction")

        monkeypatch.setattr(session, "commit", _forbidden)
        monkeypatch.setattr(session, "rollback", _forbidden)
        outcome = _plan(session, _request([user], club))
        monkeypatch.undo()
        assert outcome.recipients[0].created is True
        session.rollback()

    assert _counts() == (0, 0, 0)


# --- Request validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_type": " "},
        {"subject_type": ""},
        {"recipient_scope": " "},
        {"channel_templates": {}},
        {"channel_templates": {"email": " "}},
    ],
)
@requires_postgres
def test_malformed_request_is_rejected(overrides: dict[str, Any]) -> None:
    with session_scope() as session:
        club, user = _setup(session)
        with pytest.raises(InvalidNotificationRequestError):
            _plan(session, _request([user], club, **overrides))


@requires_postgres
def test_unknown_channel_is_rejected() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        with pytest.raises(InvalidNotificationChannelError):
            _plan(session, _request([user], club, channels={"max": "tpl"}))


@requires_postgres
def test_duplicate_recipients_or_keys_are_rejected() -> None:
    with session_scope() as session:
        club, user = _setup(session)
        other = _user(session)
        with pytest.raises(InvalidNotificationRequestError):
            _plan(session, _request([user, user], club))
        with pytest.raises(InvalidNotificationRequestError):
            _plan(
                session,
                _request(
                    [user, other],
                    club,
                    recipients=[Recipient(user.id, "k"), Recipient(other.id, "k")],
                ),
            )


# --- The persisted Global Admin Policy as the Engine's source (#333, ADR-0048) -------


@requires_postgres
def test_persisted_global_policy_unsaved_means_every_channel_off() -> None:
    from app.notification_settings.policy import GlobalAdminPolicy

    with session_scope() as session:
        club, user = _setup(session)
        outcome = _plan(session, _request([user], club), admin_policy=GlobalAdminPolicy())
        session.commit()
    assert outcome.excluded_channels == {"email": EXCLUDED_GLOBAL_POLICY_DISABLED}
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_persisted_global_policy_on_allows_and_off_blocks() -> None:
    from app.db.notification_settings import NotificationGlobalPolicy
    from app.notification_settings.policy import GlobalAdminPolicy

    with session_scope() as session:
        club, user = _setup(session)
        session.add(NotificationGlobalPolicy(id=1, email_enabled=True, telegram_enabled=False))
        session.flush()
        outcome = _plan(session, _request([user], club), admin_policy=GlobalAdminPolicy())
        session.commit()
    assert outcome.recipients[0].created is True
    assert _counts() == (1, 1, 1)

    with session_scope() as session:
        session.execute(
            update(NotificationGlobalPolicy).values(email_enabled=False)
        )
        other = _user(session)
        outcome = _plan(session, _request([other], club), admin_policy=GlobalAdminPolicy())
        session.commit()
    assert outcome.excluded_channels == {"email": EXCLUDED_GLOBAL_POLICY_DISABLED}
    assert _counts() == (1, 1, 1)


# --- Personal levels, reachability, routes, render context (#336, ADR-0049) --------


class _Unreachable:
    def reachable(self, session: Session, *, user_id: uuid.UUID, channel: str) -> bool:
        return False


class _MasterAndEvent:
    def allows(
        self, *, channel: str, stored_enabled: bool | None, master_enabled: bool | None
    ) -> bool:
        return master_enabled is True and stored_enabled is True


def _telegram_route(session, *, event_types: list[str], enabled: bool = True):  # type: ignore[no-untyped-def]
    from app.db.notifications import TelegramDestination

    route = TelegramDestination(
        name="Club", chat_id=-100_000_000 - uuid.uuid4().int % 1_000_000, enabled=enabled,
        notification_scope={"event_types": event_types},
    )
    session.add(route)
    session.flush()
    return route


def _telegram_setup(session, *, group_rule: bool = True):  # type: ignore[no-untyped-def]
    club, user = _club(session), _user(session)
    _template(session, code=_TELEGRAM_TEMPLATE, channel="telegram")
    _rule(session, None, channel="telegram")
    if group_rule:
        session.add(
            NotificationRule(
                event_type=_EVENT, channel="telegram", recipient_scope="telegram_destination",
                is_enabled=True,
            )
        )
        session.flush()
    return club, user


@requires_postgres
def test_engine_hands_both_personal_levels_to_the_preference_policy() -> None:
    from app.db.notifications import CommunicationChannelPreference

    with session_scope() as session:
        club, user = _setup(session)
        _preference(session, user, True)
        first = _plan(session, _request([user], club), preference=_MasterAndEvent())
        session.add(CommunicationChannelPreference(user_id=user.id, channel="email", enabled=True))
        session.flush()
        second = _plan(session, _request([user], club), preference=_MasterAndEvent())
        session.commit()
    assert first.recipients[0].excluded_reason == EXCLUDED_PREFERENCE_DISABLED
    assert second.recipients[0].created is True


@requires_postgres
def test_unreachable_recipient_gets_no_notification() -> None:
    from app.notifications.engine import EXCLUDED_RECIPIENT_UNREACHABLE

    with session_scope() as session:
        club, user = _setup(session)
        outcome = plan_notifications(
            session,
            _request([user], club),
            admin_policy=_AdminPolicy(),
            recipient_access=_Allow(frozenset({user.id})),
            preference_policy=_OptOut(),
            reachability=_Unreachable(),
        )
        session.commit()
    assert outcome.recipients[0].excluded_reason == EXCLUDED_RECIPIENT_UNREACHABLE
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_route_notification_is_addressed_to_the_route_with_the_render_context() -> None:
    from app.notifications.engine import DestinationRecipient

    with session_scope() as session:
        club, user = _telegram_setup(session)
        route = _telegram_route(session, event_types=[_EVENT])
        outcome = _plan(
            session,
            _request(
                [], club, channels={"telegram": _TELEGRAM_TEMPLATE},
                destinations=[DestinationRecipient(route.id, f"{_EVENT}:dest:{route.id}")],
                render_context={"title": "T"},
            ),
        )
        session.commit()
        (published,) = outcome.destinations
        notification = session.get(Notification, published.notification.id)  # type: ignore[union-attr]
        assert notification is not None
        assert notification.recipient_user_id is None
        assert notification.recipient_destination_id == route.id
        assert notification.render_context == {"title": "T"}
        (delivery,) = published.deliveries
        assert (delivery.channel, delivery.destination_type, delivery.destination_id) == (
            "telegram",
            "telegram_destination",
            route.id,
        )
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_route_exclusions_and_idempotency() -> None:
    from app.notifications.engine import (
        EXCLUDED_DESTINATION_DISABLED,
        EXCLUDED_DESTINATION_NOT_FOUND,
        EXCLUDED_DESTINATION_NOT_SUBSCRIBED,
        DestinationRecipient,
    )

    with session_scope() as session:
        club, user = _telegram_setup(session)
        disabled = _telegram_route(session, event_types=[_EVENT], enabled=False)
        other_event = _telegram_route(session, event_types=["other.event"])
        missing = uuid.uuid4()
        ok_route = _telegram_route(session, event_types=[_EVENT])
        request = _request(
            [], club, channels={"telegram": _TELEGRAM_TEMPLATE},
            destinations=[
                DestinationRecipient(route_id, f"k:{route_id}")
                for route_id in (disabled.id, other_event.id, missing, ok_route.id)
            ],
        )
        first = _plan(session, request)
        second = _plan(session, request)
        session.commit()
    assert [d.excluded_reason for d in first.destinations] == [
        EXCLUDED_DESTINATION_DISABLED,
        EXCLUDED_DESTINATION_NOT_SUBSCRIBED,
        EXCLUDED_DESTINATION_NOT_FOUND,
        None,
    ]
    assert [d.created for d in second.destinations] == [False, False, False, False]
    assert second.destinations[3].notification is not None
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_route_key_reused_for_a_user_is_a_conflict() -> None:
    from app.notifications.engine import DestinationRecipient

    with session_scope() as session:
        club, user = _telegram_setup(session)
        route = _telegram_route(session, event_types=[_EVENT])
        _plan(
            session,
            _request([], club, channels={"telegram": _TELEGRAM_TEMPLATE},
                     destinations=[DestinationRecipient(route.id, "shared")]),
        )
        with pytest.raises(IdempotencyKeyConflictError):
            _plan(
                session,
                _request([user], club, channels={"telegram": _TELEGRAM_TEMPLATE},
                         recipients=[Recipient(user.id, "shared")]),
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"render_context": {"title": 1}},
        {"render_context": {" ": "x"}},
        {"channel_templates": {"email": _EMAIL_TEMPLATE}},
    ],
)
@requires_postgres
def test_invalid_route_or_context_request_is_rejected_before_any_write(overrides: dict) -> None:
    from app.notifications.engine import DestinationRecipient

    with session_scope() as session:
        club, user = _telegram_setup(session)
        route = _telegram_route(session, event_types=[_EVENT])
        fields = {
            "channels": {"telegram": _TELEGRAM_TEMPLATE},
            "destinations": [DestinationRecipient(route.id, "k")],
        }
        if "channel_templates" in overrides:
            fields["channels"] = overrides.pop("channel_templates")
        with pytest.raises(InvalidNotificationRequestError):
            _plan(session, _request([], club, **fields, **overrides))
    assert _counts() == (0, 0, 0)


@requires_postgres
def test_duplicate_routes_are_rejected() -> None:
    from app.notifications.engine import DestinationRecipient

    with session_scope() as session:
        club, user = _telegram_setup(session)
        route = _telegram_route(session, event_types=[_EVENT])
        with pytest.raises(InvalidNotificationRequestError):
            _plan(
                session,
                _request([], club, channels={"telegram": _TELEGRAM_TEMPLATE},
                         destinations=[DestinationRecipient(route.id, "a"),
                                       DestinationRecipient(route.id, "b")]),
            )


# --- The created=False branch: a key committed concurrently (#336 PR-0) ----------
#
# The Engine looks a key up before writing, then inserts with
# INSERT ... ON CONFLICT DO NOTHING (app.notifications.repository). A
# transaction that commits the same key between the two makes the insert a
# no-op and `create_notification` return the other transaction's row with
# `created=False`. `_simulate_race` reproduces that interleaving
# deterministically: the Engine's pre-write lookup is made blind, so the
# existing row is found only by `create_notification` itself; the spy
# records each `created` flag to prove that branch ran.


def _simulate_race(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    from app.notifications import engine, repository

    monkeypatch.setattr(engine, "get_notification_by_idempotency_key", lambda session, key: None)
    created_flags: list[bool] = []
    real_create = repository.create_notification

    def spy(*args: Any, **kwargs: Any) -> Any:
        notification, created = real_create(*args, **kwargs)
        created_flags.append(created)
        return notification, created

    monkeypatch.setattr(engine, "create_notification", spy)
    return created_flags


def _session_counts(session: Session) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        session.execute(select(func.count()).select_from(model)).scalar_one()
        for model in (Notification, NotificationDelivery, OutboxJob)
    )


def _committed_notification(  # type: ignore[no-untyped-def]
    session,
    key: str,
    *,
    event_type: str = _EVENT,
    user_id: uuid.UUID | None = None,
    destination_id: uuid.UUID | None = None,
) -> Notification:
    """The row the 'concurrent' transaction committed under `key`."""
    from app.notifications.repository import create_notification

    notification, created = create_notification(
        session,
        idempotency_key=key,
        event_type=event_type,
        subject_type="test_subject",
        recipient_user_id=user_id,
        recipient_destination_id=destination_id,
    )
    assert created
    return notification


@requires_postgres
def test_race_same_user_and_event_returns_the_concurrent_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_scope() as session:
        club, user = _telegram_setup(session)
        request = _request([user], club, channels={"telegram": _TELEGRAM_TEMPLATE})
        first = _plan(session, request)
        session.commit()
        first_id = first.recipients[0].notification.id  # type: ignore[union-attr]
        first_deliveries = [d.id for d in first.recipients[0].deliveries]

    created_flags = _simulate_race(monkeypatch)
    with session_scope() as session:
        second = _plan(session, request)
        session.commit()

    assert created_flags == [False]
    (recipient,) = second.recipients
    assert recipient.created is False
    assert recipient.notification is not None and recipient.notification.id == first_id
    assert [d.id for d in recipient.deliveries] == first_deliveries
    assert _counts() == (1, 1, 1)


@requires_postgres
def test_race_same_route_and_event_returns_the_concurrent_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.notifications.engine import DestinationRecipient

    with session_scope() as session:
        club, _user_row = _telegram_setup(session)
        route = _telegram_route(session, event_types=[_EVENT])
        request = _request(
            [], club, channels={"telegram": _TELEGRAM_TEMPLATE},
            destinations=[DestinationRecipient(route.id, "route-key")],
        )
        first = _plan(session, request)
        session.commit()
        first_id = first.destinations[0].notification.id  # type: ignore[union-attr]

    created_flags = _simulate_race(monkeypatch)
    with session_scope() as session:
        second = _plan(session, request)
        session.commit()

    assert created_flags == [False]
    assert second.destinations[0].created is False
    assert second.destinations[0].notification.id == first_id  # type: ignore[union-attr]
    assert _counts() == (1, 1, 1)


def _personal_conflict(club, user, key: str):  # type: ignore[no-untyped-def]
    return _request(
        [user], club, channels={"telegram": _TELEGRAM_TEMPLATE},
        recipients=[Recipient(user.id, key)],
    )


def _route_conflict(club, route_id: uuid.UUID, key: str):  # type: ignore[no-untyped-def]
    from app.notifications.engine import DestinationRecipient

    return _request(
        [], club, channels={"telegram": _TELEGRAM_TEMPLATE},
        destinations=[DestinationRecipient(route_id, key)],
    )


@pytest.mark.parametrize(
    ("requester", "existing_owner", "existing_event"),
    [
        ("user", "other_user", _EVENT),  # personal vs another user's notification
        ("user", "route", _EVENT),  # personal vs a route-addressed notification
        ("user", "same_user", "other.event"),  # personal, same user, other event
        ("route", "other_route", _EVENT),  # route vs another route
        ("route", "user", _EVENT),  # route vs a personal notification
        ("route", "same_route", "other.event"),  # route, same route, other event
    ],
)
@requires_postgres
def test_race_with_a_mismatching_concurrent_notification_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch, requester: str, existing_owner: str, existing_event: str
) -> None:
    key = f"race-key-{uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        club, user = _telegram_setup(session)
        other_user = _user(session)
        route = _telegram_route(session, event_types=[_EVENT])
        other_route = _telegram_route(session, event_types=[_EVENT])
        owner = {
            "other_user": {"user_id": other_user.id},
            "same_user": {"user_id": user.id},
            "user": {"user_id": user.id},
            "route": {"destination_id": route.id},
            "other_route": {"destination_id": other_route.id},
            "same_route": {"destination_id": route.id},
        }[existing_owner]
        existing = _committed_notification(session, key, event_type=existing_event, **owner)
        session.commit()
        existing_id = existing.id
        request = (
            _personal_conflict(club, user, key)
            if requester == "user"
            else _route_conflict(club, route.id, key)
        )
    before = _counts()
    assert before == (1, 0, 0)

    created_flags = _simulate_race(monkeypatch)
    with session_scope() as session:
        with pytest.raises(IdempotencyKeyConflictError):
            _plan(session, request)
        # Raised before any Delivery or outbox job of the conflicting
        # recipient could be written, even inside the open transaction.
        assert _session_counts(session) == before
        session.rollback()

    assert created_flags == [False]
    assert _counts() == before
    with session_scope() as session:
        unchanged = session.get(Notification, existing_id)
        assert unchanged is not None and unchanged.event_type == existing_event


@requires_postgres
def test_race_conflict_after_earlier_writes_rolls_the_whole_call_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conflict detected after a concurrent insert can follow writes for
    earlier recipients of the same call; the caller rolls everything back
    (ADR-0049 §2.6), leaving only the concurrent transaction's row."""
    with session_scope() as session:
        club, user = _telegram_setup(session)
        first_user = _user(session)
        other_user = _user(session)
        _committed_notification(session, "taken", user_id=other_user.id)
        session.commit()
        request = _request(
            [first_user, user], club, channels={"telegram": _TELEGRAM_TEMPLATE},
            recipients=[Recipient(first_user.id, "fresh"), Recipient(user.id, "taken")],
        )

    created_flags = _simulate_race(monkeypatch)
    with session_scope() as session:
        with pytest.raises(IdempotencyKeyConflictError):
            _plan(session, request)
        session.rollback()

    assert created_flags == [True, False]
    assert _counts() == (1, 0, 0)
