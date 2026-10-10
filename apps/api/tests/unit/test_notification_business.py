"""Unit tests for the catalog status gate of
app.notifications.business.plan_catalog_notification (Issue #336 PR-0,
ADR-0049 §2.8): only a key whose vertical slice is implemented can be
planned; `pending` and `blocked` keys are refused before any database
access, so no Notification, Delivery or outbox job can be written. Pure —
the session is a sentinel that fails on any use."""

import dataclasses
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.notifications import business
from app.notifications.business import CatalogNotificationError, plan_catalog_notification
from app.notifications.catalog import (
    CATALOG,
    STATUS_BLOCKED,
    STATUS_IMPLEMENTED,
    STATUS_PENDING,
    CatalogStatus,
)
from app.notifications.engine import NotificationPlanOutcome
from app.notifications.preferences import (
    MandatoryPreferencePolicy,
    PersonalOptInPreferencePolicy,
    TelegramIdentityReachability,
)

EVENT_ID = str(uuid.uuid4())


class _UntouchableSession:
    """Any attribute access means the gate let the call reach the database."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"session.{name} used: the database was touched")


class _Allow:
    def allows(self, session: Session, *, user_id: uuid.UUID) -> bool:
        return True


def _with_status(monkeypatch: pytest.MonkeyPatch, event_type: str, status: CatalogStatus) -> None:
    monkeypatch.setitem(
        CATALOG, event_type, dataclasses.replace(CATALOG[event_type], status=status)
    )


def _context(event_type: str) -> dict[str, str]:
    variables = CATALOG[event_type].variables
    return {name: "value" for name in variables.required} | (
        {"event_id": EVENT_ID} if "event_id" in variables.required_context_keys else {}
    )


def _call(event_type: str, session: Any, **overrides: Any) -> NotificationPlanOutcome:
    kwargs: dict[str, Any] = {
        "event_type": event_type,
        "fact_id": "fact-1",
        "subject_type": "event",
        "subject_id": None,
        "club_id": None,
        "recipient_user_ids": [uuid.uuid4()],
        "recipient_access": _Allow(),
        "render_context": _context(event_type),
    }
    kwargs.update(overrides)
    return plan_catalog_notification(session, **kwargs)


def _forbid_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the Notification Engine was called")

    monkeypatch.setattr(business, "plan_notifications", fail)
    monkeypatch.setattr(business, "subscribed_destination_ids", fail)


@pytest.mark.parametrize("status", [STATUS_PENDING, STATUS_BLOCKED])
@pytest.mark.parametrize("event_type", ["event.cancelled", "registration.created"])
def test_pending_and_blocked_keys_are_refused_before_any_write(
    monkeypatch: pytest.MonkeyPatch, event_type: str, status: CatalogStatus
) -> None:
    _with_status(monkeypatch, event_type, status)
    _forbid_engine(monkeypatch)
    with pytest.raises(CatalogNotificationError, match=f"is {status} and cannot be planned"):
        _call(event_type, _UntouchableSession())


@pytest.mark.parametrize("event_type", sorted(CATALOG))
def test_every_key_is_refused_in_pr0_because_no_slice_is_implemented(
    monkeypatch: pytest.MonkeyPatch, event_type: str
) -> None:
    _forbid_engine(monkeypatch)
    assert CATALOG[event_type].status in (STATUS_PENDING, STATUS_BLOCKED)
    with pytest.raises(CatalogNotificationError):
        _call(event_type, _UntouchableSession())


def test_refusal_wins_over_every_later_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending key is refused even with routes requested and an empty
    context — the status gate runs first."""
    _forbid_engine(monkeypatch)
    with pytest.raises(CatalogNotificationError, match="is pending"):
        _call("event.cancelled", _UntouchableSession(), render_context={},
              publish_to_routes=True)


@pytest.mark.parametrize(
    ("event_type", "policy"),
    [
        ("event.cancelled", MandatoryPreferencePolicy),
        ("registration.created", PersonalOptInPreferencePolicy),
    ],
)
def test_implemented_key_is_planned_through_the_engine(
    monkeypatch: pytest.MonkeyPatch, event_type: str, policy: type
) -> None:
    _with_status(monkeypatch, event_type, STATUS_IMPLEMENTED)
    calls: list[dict[str, Any]] = []
    sentinel = NotificationPlanOutcome(excluded_channels={}, recipients=())

    def fake_plan(session: Any, request: Any, **ports: Any) -> NotificationPlanOutcome:
        calls.append({"session": session, "request": request, **ports})
        return sentinel

    monkeypatch.setattr(business, "plan_notifications", fake_plan)
    session = object()
    user_id = uuid.uuid4()
    outcome = _call(event_type, session, recipient_user_ids=[user_id, user_id])

    assert outcome is sentinel
    (call,) = calls
    request = call["request"]
    assert call["session"] is session
    assert request.event_type == event_type
    assert request.channel_templates == {"telegram": CATALOG[event_type].template_code}
    assert request.recipient_scope == CATALOG[event_type].recipient_scope
    assert [(r.user_id, r.idempotency_key) for r in request.recipients] == [
        (user_id, f"{event_type}:fact-1:user:{user_id}")
    ]
    assert list(request.destinations) == []
    assert isinstance(call["preference_policy"], policy)
    assert isinstance(call["reachability"], TelegramIdentityReachability)
