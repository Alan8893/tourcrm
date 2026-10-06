"""Integration tests for time-based Event lifecycle reconciliation and the
manual status contract it must coexist with (Issue #281, ADR-0018).

Real PostgreSQL: reconciliation is exercised through
app.events.lifecycle_reconciliation / app.cli.reconcile_event_lifecycle
against persisted Event + EventOccurrence rows, and the manual
`POST /events/{id}/status` / `POST /events/{id}/archive` endpoints are
exercised over HTTP with real authorization.
"""

import datetime
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.cli import reconcile_event_lifecycle as reconcile_cli
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event
from app.db.identity import Club, Person, User
from app.db.session import get_session_factory, session_scope
from app.events import lifecycle_reconciliation
from app.events.lifecycle_reconciliation import (
    find_due_event_ids,
    reconcile_event,
    reconcile_event_lifecycle,
)
from app.main import app

from .conftest import requires_postgres

UTC = datetime.timezone.utc

# A fixed 10:00-11:00 UTC schedule; `now` is always passed explicitly so
# the tests never depend on the wall clock.
START = datetime.datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
END = datetime.datetime(2026, 10, 5, 11, 0, tzinfo=UTC)

_EVENT_TO_OCCURRENCE_STATUS = {
    "draft": "scheduled",
    "published": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
    "archived": "completed",
}


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _create_event(
    *,
    status: str,
    start_at: datetime.datetime = START,
    end_at: datetime.datetime = END,
    timezone: str = "Europe/Moscow",
    cancellation_reason: str | None = None,
) -> uuid.UUID:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        event = Event(
            club_id=club.id,
            event_type="lesson",
            title="Lifecycle event",
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            status=status,
            cancellation_reason=cancellation_reason,
        )
        session.add(event)
        session.flush()
        session.add(
            EventOccurrence(
                event_id=event.id,
                series_id=None,
                club_id=club.id,
                name=event.title,
                event_type=event.event_type,
                recurrence_anchor_at=start_at,
                starts_at=start_at,
                ends_at=end_at,
                timezone=timezone,
                status=_EVENT_TO_OCCURRENCE_STATUS[status],
                cancellation_reason=cancellation_reason,
            )
        )
        session.commit()
        return event.id


def _state(event_id: uuid.UUID) -> tuple[str, str, datetime.datetime, uuid.UUID | None]:
    """(event status, occurrence status, event updated_at, event updated_by)."""
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        occurrence = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        return event.status, occurrence.status, event.updated_at, event.updated_by


def _occurrence_count(event_id: uuid.UUID) -> int:
    with session_scope() as session:
        return session.execute(
            select(func.count())
            .select_from(EventOccurrence)
            .where(EventOccurrence.event_id == event_id)
        ).scalar_one()


def _reconcile(now: datetime.datetime) -> lifecycle_reconciliation.LifecycleReconciliationResult:
    with session_scope() as session:
        return reconcile_event_lifecycle(session, now=now)


def _make_user() -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([person, user])
        session.commit()
        return user.id


def _grant_permission(user_id: uuid.UUID, permission_code: str) -> None:
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(code=permission_code)
            session.add(permission)
            session.commit()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.commit()
        session.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                scopes=[RolePermissionScope(scope_type="all")],
            )
        )
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, club_id=None))
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


# --- reconciliation: time-driven transitions --------------------------------


@requires_postgres
def test_published_before_start_is_untouched() -> None:
    event_id = _create_event(status="published")
    result = _reconcile(START - datetime.timedelta(minutes=1))
    assert result.transitioned == 0
    assert _state(event_id)[:2] == ("published", "scheduled")


@requires_postgres
def test_published_becomes_in_progress_once_start_at_is_reached() -> None:
    # Scenario A: start_at < now < end_at.
    event_id = _create_event(status="published")
    result = _reconcile(START + datetime.timedelta(minutes=30))
    assert (result.transitioned, result.started, result.completed) == (1, 1, 0)
    status, occurrence_status, _updated_at, updated_by = _state(event_id)
    assert (status, occurrence_status) == ("in_progress", "in_progress")
    # A system write, not a user's: no actor is recorded.
    assert updated_by is None


@requires_postgres
def test_in_progress_becomes_completed_once_end_at_is_reached() -> None:
    # Scenario C.
    event_id = _create_event(status="in_progress")
    result = _reconcile(END + datetime.timedelta(minutes=1))
    assert (result.transitioned, result.started, result.completed) == (1, 0, 1)
    assert _state(event_id)[:2] == ("completed", "completed")


@requires_postgres
def test_missed_published_converges_to_completed_in_one_run() -> None:
    # Scenario B: first run at 11:30 for a 10:00-11:00 Event.
    event_id = _create_event(status="published")
    result = _reconcile(END + datetime.timedelta(minutes=30))
    assert (result.transitioned, result.started, result.completed) == (1, 1, 1)
    assert _state(event_id)[:2] == ("completed", "completed")


@requires_postgres
@pytest.mark.parametrize(
    ("status", "reason"),
    [("draft", None), ("cancelled", "Bad weather"), ("archived", None), ("completed", None)],
)
def test_non_time_driven_statuses_are_never_changed(status: str, reason: str | None) -> None:
    # Scenarios D/E/F: end_at long past.
    event_id = _create_event(status=status, cancellation_reason=reason)
    before = _state(event_id)
    result = _reconcile(END + datetime.timedelta(days=30))
    assert result.examined == 0
    assert _state(event_id) == before


@requires_postgres
def test_repeated_reconciliation_is_idempotent_and_side_effect_free() -> None:
    event_id = _create_event(status="published")
    now = END + datetime.timedelta(minutes=30)
    first = _reconcile(now)
    assert first.transitioned == 1
    after_first = _state(event_id)

    second = _reconcile(now)
    third = _reconcile(now + datetime.timedelta(days=1))
    assert (second.examined, second.transitioned) == (0, 0)
    assert (third.examined, third.transitioned) == (0, 0)
    # No status change, no updated_at bump, no duplicate occurrence.
    assert _state(event_id) == after_first
    assert _occurrence_count(event_id) == 1


@requires_postgres
def test_reconciliation_respects_the_event_timezone() -> None:
    # 10:00-11:00 Asia/Tokyo == 01:00-02:00 UTC.
    tokyo = datetime.timezone(datetime.timedelta(hours=9))
    start_at = datetime.datetime(2026, 10, 5, 10, 0, tzinfo=tokyo)
    end_at = datetime.datetime(2026, 10, 5, 11, 0, tzinfo=tokyo)
    event_id = _create_event(
        status="published", start_at=start_at, end_at=end_at, timezone="Asia/Tokyo"
    )
    # 09:30 UTC: the UTC wall-clock is "before 10:00", but the Tokyo Event
    # ended 7.5 hours ago.
    _reconcile(datetime.datetime(2026, 10, 5, 9, 30, tzinfo=UTC))
    assert _state(event_id)[0] == "completed"

    event_id = _create_event(
        status="published", start_at=start_at, end_at=end_at, timezone="Asia/Tokyo"
    )
    _reconcile(datetime.datetime(2026, 10, 5, 0, 59, tzinfo=UTC))
    assert _state(event_id)[0] == "published"
    _reconcile(datetime.datetime(2026, 10, 5, 1, 30, tzinfo=UTC))
    assert _state(event_id)[0] == "in_progress"


@requires_postgres
def test_find_due_event_ids_selects_only_time_driven_candidates() -> None:
    now = START + datetime.timedelta(minutes=30)
    due_published = _create_event(status="published")
    due_in_progress = _create_event(
        status="in_progress",
        start_at=START - datetime.timedelta(hours=2),
        end_at=START - datetime.timedelta(hours=1),
    )
    not_due_in_progress = _create_event(status="in_progress")
    _create_event(status="draft")
    _create_event(status="cancelled", cancellation_reason="x")
    _create_event(status="published", start_at=END, end_at=END + datetime.timedelta(hours=1))

    with session_scope() as session:
        ids = set(find_due_event_ids(session, now=now))
    assert ids == {due_published, due_in_progress}
    assert not_due_in_progress not in ids


@requires_postgres
def test_reconcile_event_recomputes_after_a_concurrent_manual_cancellation() -> None:
    # The Event was selected as due, then cancelled manually before the
    # reconciler locked it: time must not override the cancellation.
    event_id = _create_event(status="published")
    now = END + datetime.timedelta(minutes=30)
    with session_scope() as session:
        assert find_due_event_ids(session, now=now) == [event_id]
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        event.status = "cancelled"
        event.cancellation_reason = "Closed trail"
        session.commit()
    with session_scope() as session:
        assert reconcile_event(session, event_id=event_id, now=now) == ()
    assert _state(event_id)[0] == "cancelled"


@requires_postgres
def test_reconciler_waits_for_a_locked_row_and_then_sees_its_final_state() -> None:
    # A manual cancellation holds the Event row lock (as the status
    # endpoint does); the reconciler blocks on it, then re-reads the
    # committed `cancelled` row and changes nothing.
    event_id = _create_event(status="in_progress")
    now = END + datetime.timedelta(minutes=30)
    factory = get_session_factory()
    holder = factory()
    holder.execute(select(Event).where(Event.id == event_id).with_for_update()).scalar_one()

    outcome: dict[str, object] = {}

    def run() -> None:
        with session_scope() as session:
            outcome["steps"] = reconcile_event(session, event_id=event_id, now=now)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=1.0)
    assert worker.is_alive(), "reconciler must wait for the manual transition's lock"

    event = holder.get(Event, event_id)
    assert event is not None
    event.status = "cancelled"
    event.cancellation_reason = "Storm"
    occurrence = holder.execute(
        select(EventOccurrence).where(EventOccurrence.event_id == event_id)
    ).scalar_one()
    occurrence.status = "cancelled"
    occurrence.cancellation_reason = "Storm"
    holder.commit()
    holder.close()

    worker.join(timeout=10.0)
    assert not worker.is_alive()
    assert outcome["steps"] == ()
    assert _state(event_id)[:2] == ("cancelled", "cancelled")


@requires_postgres
def test_concurrent_reconcilers_apply_each_transition_exactly_once() -> None:
    event_ids = [_create_event(status="published") for _ in range(5)]
    now = END + datetime.timedelta(minutes=30)
    results: list[lifecycle_reconciliation.LifecycleReconciliationResult] = []
    lock = threading.Lock()

    def run() -> None:
        result = _reconcile(now)
        with lock:
            results.append(result)

    workers = [threading.Thread(target=run) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30.0)

    assert len(results) == 3
    assert sum(r.transitioned for r in results) == 5
    assert sum(r.completed for r in results) == 5
    assert sum(r.failed for r in results) == 0
    for event_id in event_ids:
        assert _state(event_id)[:2] == ("completed", "completed")
        assert _occurrence_count(event_id) == 1


@requires_postgres
def test_one_failing_event_does_not_block_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = _create_event(status="published")
    healthy = _create_event(status="published", start_at=START + datetime.timedelta(minutes=1))
    original = lifecycle_reconciliation.reconcile_event

    def flaky(session, *, event_id, now):  # type: ignore[no-untyped-def]
        if event_id == failing:
            raise RuntimeError("boom")
        return original(session, event_id=event_id, now=now)

    monkeypatch.setattr(lifecycle_reconciliation, "reconcile_event", flaky)
    result = _reconcile(END + datetime.timedelta(minutes=30))
    assert (result.examined, result.transitioned, result.failed) == (2, 1, 1)
    assert _state(failing)[0] == "published"
    assert _state(healthy)[0] == "completed"

    # The next run retries and converges the previously failed Event.
    monkeypatch.setattr(lifecycle_reconciliation, "reconcile_event", original)
    assert _reconcile(END + datetime.timedelta(minutes=30)).transitioned == 1
    assert _state(failing)[0] == "completed"


@requires_postgres
def test_cli_entrypoint_persists_reconciled_status(capsys: pytest.CaptureFixture[str]) -> None:
    # The deployment's invocation path (`python -m app.cli.reconcile_event_lifecycle`)
    # runs against the real clock: this Event ended a day ago.
    now = datetime.datetime.now(UTC)
    event_id = _create_event(
        status="published",
        start_at=now - datetime.timedelta(days=1, hours=2),
        end_at=now - datetime.timedelta(days=1),
    )
    assert reconcile_cli.main() == 0
    assert "1 transitioned" in capsys.readouterr().out
    assert _state(event_id)[:2] == ("completed", "completed")
    assert reconcile_cli.main() == 0
    assert "0 transitioned" in capsys.readouterr().out


# --- manual status contract (the UI's backend) -------------------------------


@requires_postgres
def test_manual_early_completion_before_end_at_succeeds(client: TestClient) -> None:
    # Scenario G: the Event's end_at is still in the future.
    now = datetime.datetime.now(UTC)
    event_id = _create_event(
        status="in_progress",
        start_at=now - datetime.timedelta(hours=1),
        end_at=now + datetime.timedelta(hours=1),
    )
    user_id = _make_user()
    _grant_permission(user_id, "event.update")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": "completed"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    # Reconciliation afterwards leaves the manual result alone.
    assert _reconcile(now + datetime.timedelta(hours=2)).examined == 0
    assert _state(event_id)[:2] == ("completed", "completed")


@requires_postgres
@pytest.mark.parametrize(
    ("from_status", "to_status", "permission", "body_extra"),
    [
        ("draft", "published", "event.update", {}),
        ("published", "in_progress", "event.update", {}),
        ("published", "cancelled", "event.cancel", {"cancellation_reason": "Rain"}),
        ("in_progress", "completed", "event.update", {}),
        ("in_progress", "cancelled", "event.cancel", {"cancellation_reason": "Injury"}),
    ],
)
def test_valid_manual_transitions_succeed_and_persist(
    client: TestClient,
    from_status: str,
    to_status: str,
    permission: str,
    body_extra: dict,
) -> None:
    event_id = _create_event(status=from_status)
    user_id = _make_user()
    _grant_permission(user_id, permission)
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": to_status, **body_extra},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    status, occurrence_status, _updated_at, updated_by = _state(event_id)
    assert status == to_status
    assert occurrence_status == _EVENT_TO_OCCURRENCE_STATUS[to_status]
    assert updated_by == user_id


@requires_postgres
@pytest.mark.parametrize("from_status", ["completed", "cancelled"])
def test_manual_archive_succeeds_with_event_manage(client: TestClient, from_status: str) -> None:
    event_id = _create_event(
        status=from_status, cancellation_reason="x" if from_status == "cancelled" else None
    )
    user_id = _make_user()
    _grant_permission(user_id, "event.manage")
    _authenticate_as(user_id)

    response = client.post(f"/api/v1/events/{event_id}/archive", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    assert _state(event_id)[0] == "archived"


@requires_postgres
@pytest.mark.parametrize("reason", [None, ""])
def test_manual_cancellation_requires_a_reason(client: TestClient, reason: str | None) -> None:
    event_id = _create_event(status="in_progress")
    user_id = _make_user()
    _grant_permission(user_id, "event.cancel")
    _authenticate_as(user_id)

    body: dict = {"status": "cancelled"}
    if reason is not None:
        body["cancellation_reason"] = reason
    response = client.post(
        f"/api/v1/events/{event_id}/status", json=body, headers=_csrf_headers(client)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cancellation_reason_required"
    assert _state(event_id)[0] == "in_progress"


@requires_postgres
@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("draft", "in_progress"),
        ("draft", "completed"),
        ("published", "completed"),
        ("completed", "in_progress"),
        ("cancelled", "completed"),
        ("archived", "published"),
        ("in_progress", "published"),
    ],
)
def test_invalid_manual_transitions_are_rejected(
    client: TestClient, from_status: str, to_status: str
) -> None:
    event_id = _create_event(
        status=from_status, cancellation_reason="x" if from_status == "cancelled" else None
    )
    user_id = _make_user()
    _grant_permission(user_id, "event.update")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status",
        json={"status": to_status},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_status_transition"
    assert _state(event_id)[0] == from_status


@requires_postgres
@pytest.mark.parametrize(
    ("from_status", "body"),
    [
        ("published", {"status": "in_progress"}),
        ("in_progress", {"status": "completed"}),
        ("published", {"status": "cancelled", "cancellation_reason": "x"}),
    ],
)
def test_unauthorized_manual_transition_remains_rejected(
    client: TestClient, from_status: str, body: dict
) -> None:
    event_id = _create_event(status=from_status)
    user_id = _make_user()
    # Read access only — no event.update / event.cancel.
    _grant_permission(user_id, "event.read")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/events/{event_id}/status", json=body, headers=_csrf_headers(client)
    )
    assert response.status_code == 404
    assert _state(event_id)[0] == from_status


@requires_postgres
def test_unauthorized_manual_archive_remains_rejected(client: TestClient) -> None:
    event_id = _create_event(status="completed")
    user_id = _make_user()
    _grant_permission(user_id, "event.update")
    _authenticate_as(user_id)

    response = client.post(f"/api/v1/events/{event_id}/archive", headers=_csrf_headers(client))
    assert response.status_code == 404
    assert _state(event_id)[0] == "completed"


@requires_postgres
def test_no_event_http_endpoint_exposes_reconciliation() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("/events" in path and "reconcil" in path for path in paths)
