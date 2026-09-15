"""HTTP-level integration tests for the Event recurrence API (Issue #79,
ADR-0028): /api/v1/events/series and /api/v1/events/occurrences.

Against the real shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_role_assignments_api.py's
pattern. Uses the centralized session-scoped migration + TRUNCATE-reset
test isolation already established in tests/integration/conftest.py —
no per-file `_migrated_schema` fixture is (re)introduced here.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

_START = "2026-01-05T18:00:00+00:00"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _setup_authorized_caller(*, club_id: uuid.UUID | None, permission_code: str) -> uuid.UUID:
    """Create a User with an `all`-scope assignment for `permission_code`
    (club-specific if `club_id` given, global otherwise). Returns the
    User's id."""
    with session_scope() as s:
        person = Person(last_name="Caller", first_name="X")
        s.add(person)
        s.commit()
        user = User(
            person_id=person.id,
            login_identifier=f"caller-{uuid.uuid4().hex[:8]}@x.example",
            status="active",
        )
        s.add(user)
        s.commit()

        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role", is_system=False)
        permission = s.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one()
        s.add(role)
        s.commit()
        s.add(RolePermission(role_id=role.id, permission_id=permission.id))
        s.add(
            UserRoleAssignment(
                user_id=user.id,
                role_id=role.id,
                club_id=club_id,
                scope_type="all",
                valid_from=datetime.now(dt_timezone.utc),
            )
        )
        s.commit()
        return user.id


def _make_club() -> uuid.UUID:
    with session_scope() as s:
        club = Club(name=f"Club-{uuid.uuid4().hex[:8]}", status="active")
        s.add(club)
        s.commit()
        return club.id


def _series_payload(**overrides) -> dict:
    payload = {
        "club_id": None,
        "name": "Weekly lesson",
        "event_type": "lesson",
        "series_start_at": _START,
        "duration_minutes": 90,
        "recurrence": {"frequency": "WEEKLY"},
        "timezone": "Europe/Moscow",
    }
    payload.update(overrides)
    return payload


# --- Authentication -----------------------------------------------------


@requires_postgres
def test_create_series_unauthenticated_is_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/events/series", json=_series_payload(club_id=str(uuid.uuid4()))
    )
    assert response.status_code == 401


@requires_postgres
def test_get_series_unauthenticated_is_401(client: TestClient) -> None:
    response = client.get(f"/api/v1/events/series/{uuid.uuid4()}")
    assert response.status_code == 401


# --- Happy path: create -> read -> materialize -> lifecycle ---------------


@requires_postgres
def test_full_series_lifecycle_happy_path(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    # event.create alone won't cover read/update/cancel/manage/pause/resume
    # -- grant the full canonical set (mirrors a real "club admin" role).
    for perm in ("event.read", "event.update", "event.cancel", "event.manage"):
        _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code=perm)
    _authenticate_as(user_id)

    create_resp = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    )
    assert create_resp.status_code == 201, create_resp.text
    series = create_resp.json()
    assert series["version"] == 1
    assert series["root_series_id"] == series["id"]
    assert series["supersedes_series_id"] is None
    assert series["status"] == "active"
    assert series["duration_minutes"] == 90
    assert "UNTIL" not in series["recurrence_rule"]
    series_id = series["id"]

    get_resp = client.get(f"/api/v1/events/series/{series_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == series_id

    occ_resp = client.get(f"/api/v1/events/series/{series_id}/occurrences")
    assert occ_resp.status_code == 200
    body = occ_resp.json()
    assert body["pagination"]["total"] > 0
    first = body["items"][0]
    assert first["series_id"] == series_id
    assert first["status"] == "scheduled"
    start_dt = datetime.fromisoformat(first["starts_at"])
    end_dt = datetime.fromisoformat(first["ends_at"])
    assert (end_dt - start_dt) == timedelta(minutes=90)

    pause_resp = client.post(
        f"/api/v1/events/series/{series_id}/pause", headers=_csrf_headers(client)
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    resume_resp = client.post(
        f"/api/v1/events/series/{series_id}/resume", headers=_csrf_headers(client)
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "active"

    cancel_resp = client.post(
        f"/api/v1/events/series/{series_id}/cancel", headers=_csrf_headers(client)
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    archive_resp = client.post(
        f"/api/v1/events/series/{series_id}/archive", headers=_csrf_headers(client)
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "archived"


def _setup_authorized_caller_extra(
    user_id: uuid.UUID, *, club_id: uuid.UUID | None, permission_code: str
) -> None:
    with session_scope() as s:
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Extra role", is_system=False)
        permission = s.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one()
        s.add(role)
        s.commit()
        s.add(RolePermission(role_id=role.id, permission_id=permission.id))
        s.add(
            UserRoleAssignment(
                user_id=user_id,
                role_id=role.id,
                club_id=club_id,
                scope_type="all",
                valid_from=datetime.now(dt_timezone.utc),
            )
        )
        s.commit()


# --- Exceptions (reschedule / cancel via the canonical endpoint) ---------


@requires_postgres
def test_reschedule_and_cancel_via_exceptions_endpoint(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    for perm in ("event.read", "event.update", "event.cancel"):
        _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code=perm)
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    series_id = series["id"]
    occurrences = client.get(f"/api/v1/events/series/{series_id}/occurrences").json()["items"]
    occurrence_id = occurrences[0]["id"]
    original_starts_at = occurrences[0]["starts_at"]

    new_start = (datetime.fromisoformat(_START) + timedelta(hours=3)).isoformat()
    new_end = (datetime.fromisoformat(_START) + timedelta(hours=5)).isoformat()
    reschedule_resp = client.post(
        f"/api/v1/events/series/{series_id}/exceptions",
        json={
            "occurrence_id": occurrence_id,
            "exception_type": "rescheduled",
            "effective_start_at": new_start,
            "effective_end_at": new_end,
        },
        headers=_csrf_headers(client),
    )
    assert reschedule_resp.status_code == 200, reschedule_resp.text
    rescheduled = reschedule_resp.json()
    assert rescheduled["id"] == occurrence_id  # id stable
    assert rescheduled["status"] == "scheduled"
    assert rescheduled["starts_at"] != original_starts_at
    assert rescheduled["exception"]["exception_type"] == "rescheduled"

    cancel_no_reason = client.post(
        f"/api/v1/events/series/{series_id}/exceptions",
        json={"occurrence_id": occurrence_id, "exception_type": "cancelled"},
        headers=_csrf_headers(client),
    )
    assert cancel_no_reason.status_code == 422

    cancel_resp = client.post(
        f"/api/v1/events/series/{series_id}/exceptions",
        json={
            "occurrence_id": occurrence_id,
            "exception_type": "cancelled",
            "cancellation_reason": "Instructor unavailable",
        },
        headers=_csrf_headers(client),
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    cancelled = cancel_resp.json()
    assert cancelled["id"] == occurrence_id
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancellation_reason"] == "Instructor unavailable"


@requires_postgres
def test_reschedule_without_effective_end_at_derives_duration_over_http(
    client: TestClient,
) -> None:
    """duration_minutes=90, old start=18:00/end=19:30; reschedule with
    effective_start_at=20:00 and no effective_end_at must yield
    starts_at=20:00, ends_at=21:30 — never the stale old ends_at."""
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    for perm in ("event.read", "event.update"):
        _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code=perm)
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    assert series["duration_minutes"] == 90
    series_id = series["id"]
    occurrence = client.get(f"/api/v1/events/series/{series_id}/occurrences").json()["items"][0]
    assert occurrence["starts_at"] == "2026-01-05T18:00:00Z"
    assert occurrence["ends_at"] == "2026-01-05T19:30:00Z"

    new_start = "2026-01-05T20:00:00+00:00"
    resp = client.post(
        f"/api/v1/events/series/{series_id}/exceptions",
        json={
            "occurrence_id": occurrence["id"],
            "exception_type": "rescheduled",
            "effective_start_at": new_start,
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["starts_at"] == "2026-01-05T20:00:00Z"
    assert body["ends_at"] == "2026-01-05T21:30:00Z"
    assert body["ends_at"] != occurrence["ends_at"]  # old ends_at not carried over


@requires_postgres
def test_reschedule_of_terminal_occurrence_is_422_over_http(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    for perm in ("event.read", "event.update", "event.cancel"):
        _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code=perm)
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    series_id = series["id"]
    occurrence_id = client.get(
        f"/api/v1/events/series/{series_id}/occurrences"
    ).json()["items"][0]["id"]

    cancel_resp = client.post(
        f"/api/v1/events/series/{series_id}/exceptions",
        json={
            "occurrence_id": occurrence_id,
            "exception_type": "cancelled",
            "cancellation_reason": "weather",
        },
        headers=_csrf_headers(client),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    reschedule_resp = client.post(
        f"/api/v1/events/series/{series_id}/exceptions",
        json={
            "occurrence_id": occurrence_id,
            "exception_type": "rescheduled",
            "effective_start_at": "2026-01-05T22:00:00+00:00",
        },
        headers=_csrf_headers(client),
    )
    assert reschedule_resp.status_code == 422, reschedule_resp.text

    # State must be unchanged after the rejected request.
    still_cancelled = client.get(f"/api/v1/events/occurrences/{occurrence_id}")
    assert still_cancelled.json()["status"] == "cancelled"
    assert still_cancelled.json()["exception"]["exception_type"] == "cancelled"


@requires_postgres
def test_dedicated_occurrence_cancel_and_reschedule_endpoints_do_not_exist(
    client: TestClient,
) -> None:
    """Per docs/05-api/event-recurrence-api.md these are explicitly NOT
    canonical — a 404 (route not found) confirms they were never wired."""
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.read")
    _authenticate_as(user_id)
    occurrence_id = uuid.uuid4()
    assert (
        client.post(f"/api/v1/events/occurrences/{occurrence_id}/cancel").status_code == 404
    )
    assert (
        client.post(f"/api/v1/events/occurrences/{occurrence_id}/reschedule").status_code == 404
    )


# --- Direct occurrence lifecycle (PATCH /occurrences/{id}) -----------------


@requires_postgres
def test_occurrence_direct_status_progression(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    for perm in ("event.read", "event.update"):
        _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code=perm)
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    occurrence_id = client.get(
        f"/api/v1/events/series/{series['id']}/occurrences"
    ).json()["items"][0]["id"]

    resp = client.patch(
        f"/api/v1/events/occurrences/{occurrence_id}",
        json={"status": "in_progress"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"

    resp2 = client.patch(
        f"/api/v1/events/occurrences/{occurrence_id}",
        json={"status": "completed"},
        headers=_csrf_headers(client),
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "completed"


@requires_postgres
def test_occurrence_direct_cancel_status_is_rejected_by_schema(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.update")
    _authenticate_as(user_id)
    resp = client.patch(
        f"/api/v1/events/occurrences/{uuid.uuid4()}",
        json={"status": "cancelled"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 422  # not one of the Literal["in_progress","completed"] values


# --- this_and_following via PATCH /series/{id} + stale version 409 --------


@requires_postgres
def test_this_and_following_creates_new_version_and_stale_retry_is_409(
    client: TestClient,
) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    for perm in ("event.read", "event.update"):
        _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code=perm)
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    series_id = series["id"]
    occurrences = client.get(f"/api/v1/events/series/{series_id}/occurrences").json()["items"]
    boundary_id = occurrences[1]["id"]

    patch_payload = {
        "update_scope": "this_and_following",
        "occurrence_id": boundary_id,
        "event_type": "lesson",
        "series_start_at": occurrences[1]["starts_at"],
        "duration_minutes": 60,
        "recurrence": {"frequency": "WEEKLY", "interval": 2},
        "timezone": "Europe/Moscow",
    }
    resp = client.patch(
        f"/api/v1/events/series/{series_id}", json=patch_payload, headers=_csrf_headers(client)
    )
    assert resp.status_code == 200, resp.text
    v2 = resp.json()
    assert v2["version"] == 2
    assert v2["supersedes_series_id"] == series_id
    assert v2["root_series_id"] == series["root_series_id"]
    assert v2["duration_minutes"] == 60

    # Retrying against the now-stale v1 id must fail with 409, no mutation.
    stale_resp = client.patch(
        f"/api/v1/events/series/{series_id}", json=patch_payload, headers=_csrf_headers(client)
    )
    assert stale_resp.status_code == 409, stale_resp.text
    assert stale_resp.json()["error"]["code"] == "stale_series_version"


@requires_postgres
def test_entire_series_updates_name_in_place(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code="event.update")
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    resp = client.patch(
        f"/api/v1/events/series/{series['id']}",
        json={"update_scope": "entire_series", "name": "Renamed lesson"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed lesson"
    assert resp.json()["version"] == 1  # no new version created


# --- Authorization: cross-Club denial / IDOR -------------------------------


@requires_postgres
def test_cross_club_caller_gets_404_not_403(client: TestClient) -> None:
    club_id = _make_club()
    other_club_id = _make_club()
    owner_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    _setup_authorized_caller_extra(owner_id, club_id=club_id, permission_code="event.read")
    outsider_id = _setup_authorized_caller(club_id=other_club_id, permission_code="event.read")

    _authenticate_as(owner_id)
    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()

    _authenticate_as(outsider_id)
    resp = client.get(f"/api/v1/events/series/{series['id']}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "event_series_not_found"


@requires_postgres
def test_unauthenticated_get_nonexistent_series_is_401_not_404(client: TestClient) -> None:
    # Auth is checked before existence — matches every other domain router.
    response = client.get(f"/api/v1/events/series/{uuid.uuid4()}")
    assert response.status_code == 401


# --- Audit ----------------------------------------------------------------


@requires_postgres
def test_series_created_and_status_changed_are_audited(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _setup_authorized_caller(club_id=club_id, permission_code="event.create")
    _setup_authorized_caller_extra(user_id, club_id=club_id, permission_code="event.update")
    _authenticate_as(user_id)

    series = client.post(
        "/api/v1/events/series",
        json=_series_payload(club_id=str(club_id)),
        headers=_csrf_headers(client),
    ).json()
    series_id = uuid.UUID(series["id"])

    with session_scope() as s:
        created_row = s.execute(
            select(AuditLog).where(
                AuditLog.action == "event_series.created", AuditLog.resource_id == series_id
            )
        ).scalar_one()
        assert created_row.actor_user_id == user_id

    client.post(f"/api/v1/events/series/{series['id']}/pause", headers=_csrf_headers(client))

    with session_scope() as s:
        status_row = s.execute(
            select(AuditLog).where(
                AuditLog.action == "event_series.status_changed",
                AuditLog.resource_id == series_id,
            )
        ).scalar_one()
        assert status_row.details["changes"]["status"]["to"] == "paused"
