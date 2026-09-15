"""HTTP-level integration tests for Event Attendance (Issue #94 / TH-0087,
per ADR-0032): persistence/constraints, field validation invariants,
lifecycle gating (Event and EventOccurrence), single upsert idempotency
and order-of-checks, partial/atomic bulk upsert, correction, the full
GET participant projection (including unmarked participants) and its
derived summary, canonical scope authorization (all/own_events/
own_groups/self/children/none), cross-Club IDOR/existence-hiding, audit
codes, and last-write-wins concurrency (no version field).

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_event_conflicts_api.py's own
pattern.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.attendance import Attendance
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import EventOccurrenceParticipant
from app.db.events import Event, EventParticipation
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 20, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {"name": f"Club {uuid.uuid4().hex[:8]}", "status": "active"}
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_person(**overrides: object) -> Person:
    defaults: dict[str, object] = {
        "last_name": f"L-{uuid.uuid4().hex[:8]}",
        "first_name": f"P-{uuid.uuid4().hex[:8]}",
    }
    defaults.update(overrides)
    return Person(**defaults)  # type: ignore[arg-type]


def _make_user(person: Person, **overrides: object) -> User:
    defaults: dict[str, object] = {
        "person": person,
        "login_identifier": f"user-{uuid.uuid4().hex[:8]}@example.com",
        "status": "active",
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def _make_club_membership(club: Club, person: Person, **overrides: object) -> ClubMembership:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "person_id": person.id,
        "membership_type": "student",
        "status": "active",
        "joined_at": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return ClubMembership(**defaults)  # type: ignore[arg-type]


def _make_event(club: Club, **overrides: object) -> Event:
    start_at = overrides.pop("start_at", _START)
    end_at = overrides.pop("end_at", start_at + datetime.timedelta(hours=1))  # type: ignore[operator]
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Test event",
        "start_at": start_at,
        "end_at": end_at,
        "timezone": "UTC",
        "status": "published",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_event_participation(
    event: Event, person: Person, **overrides: object
) -> EventParticipation:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "person_id": person.id,
        "registration_status": "registered",
    }
    defaults.update(overrides)
    return EventParticipation(**defaults)  # type: ignore[arg-type]


def _make_guardian_relationship(
    guardian: Person, child: Person, **overrides: object
) -> GuardianRelationship:
    defaults: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GuardianRelationship(**defaults)  # type: ignore[arg-type]


def _make_series(session, *, club_id: uuid.UUID, **overrides: object) -> EventSeries:
    series_id = uuid.uuid4()
    defaults: dict[str, object] = dict(
        id=series_id,
        root_series_id=series_id,
        supersedes_series_id=None,
        version=1,
        club_id=club_id,
        name="Weekly lesson",
        event_type="lesson",
        series_start_at=_START,
        duration_minutes=60,
        recurrence_rule="FREQ=DAILY",
        timezone="UTC",
        status="active",
    )
    defaults.update(overrides)
    series = EventSeries(**defaults)  # type: ignore[arg-type]
    session.add(series)
    session.commit()
    return series


def _make_occurrence(
    *, series: EventSeries, club_id: uuid.UUID, **overrides: object
) -> EventOccurrence:
    starts_at = overrides.pop("starts_at", _START)
    ends_at = overrides.pop("ends_at", starts_at + datetime.timedelta(hours=1))  # type: ignore[operator]
    defaults: dict[str, object] = dict(
        series_id=series.id,
        club_id=club_id,
        name="Lesson",
        event_type="lesson",
        recurrence_anchor_at=starts_at,
        starts_at=starts_at,
        ends_at=ends_at,
        timezone="UTC",
        status="scheduled",
    )
    defaults.update(overrides)
    return EventOccurrence(**defaults)  # type: ignore[arg-type]


def _make_occurrence_participant(
    occurrence: EventOccurrence, person: Person, **overrides: object
) -> EventOccurrenceParticipant:
    defaults: dict[str, object] = {
        "occurrence_id": occurrence.id,
        "person_id": person.id,
        "registration_status": "registered",
        "valid_from": _utc(2020, 1, 1),
        "is_override": False,
    }
    defaults.update(overrides)
    return EventOccurrenceParticipant(**defaults)  # type: ignore[arg-type]


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
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
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _mark(client: TestClient, object_id: uuid.UUID, person_id: uuid.UUID, **payload: object):
    return client.put(
        f"/api/v1/events/{object_id}/attendance/{person_id}",
        json=payload,
        headers=_csrf_headers(client),
    )


def _bulk_mark(client: TestClient, object_id: uuid.UUID, items: list[dict]):
    return client.put(
        f"/api/v1/events/{object_id}/attendance",
        json={"items": items},
        headers=_csrf_headers(client),
    )


def _correct(client: TestClient, object_id: uuid.UUID, person_id: uuid.UUID, **payload: object):
    return client.post(
        f"/api/v1/events/{object_id}/attendance/{person_id}/corrections",
        json=payload,
        headers=_csrf_headers(client),
    )


def _get_attendance(client: TestClient, object_id: uuid.UUID, **params: object):
    return client.get(f"/api/v1/events/{object_id}/attendance", params=params)


# --- Persistence / DB constraints -------------------------------------------


@requires_postgres
def test_unique_constraint_event_person(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(Attendance(event_id=event.id, person_id=person.id, status="present"))
        session.commit()
        session.add(Attendance(event_id=event.id, person_id=person.id, status="present"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_unique_constraint_occurrence_person(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add(Attendance(occurrence_id=occurrence.id, person_id=person.id, status="present"))
        session.commit()
        session.add(Attendance(occurrence_id=occurrence.id, person_id=person.id, status="present"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_exactly_one_target_check_rejects_both_null(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        session.add(person)
        session.commit()
        session.add(Attendance(person_id=person.id, status="present"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_exactly_one_target_check_rejects_both_set(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        event = _make_event(club)
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add_all([event, occurrence])
        session.commit()
        session.add(
            Attendance(
                event_id=event.id,
                occurrence_id=occurrence.id,
                person_id=person.id,
                status="present",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_status_check_constraint(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(Attendance(event_id=event.id, person_id=person.id, status="late"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_absence_reason_check_constraint(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(
            Attendance(
                event_id=event.id, person_id=person.id, status="absent", absence_reason="bogus"
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_present_with_reason_check_constraint(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(
            Attendance(
                event_id=event.id, person_id=person.id, status="present", absence_reason="sick"
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# --- Setup helper for API-level scenarios -----------------------------------


def _setup_event_with_participant(
    status: str = "published",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, event_id, person_id, user_id) with an active
    EventParticipation for person on event."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, person, actor_person, actor_user])
        session.commit()
        event = _make_event(club, status=status)
        session.add(event)
        session.commit()
        session.add(_make_event_participation(event, person))
        session.commit()
        return club.id, event.id, person.id, actor_user.id


def _setup_occurrence_with_participant(
    status: str = "scheduled",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, person, actor_person, actor_user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id, status=status)
        session.add(occurrence)
        session.commit()
        session.add(_make_occurrence_participant(occurrence, person))
        session.commit()
        return club.id, occurrence.id, person.id, actor_user.id


# --- Field validation (ADR-0032 §2-4) ---------------------------------------


@requires_postgres
def test_present_with_absence_reason_is_rejected(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present", absence_reason="sick")
    assert response.status_code == 422


@requires_postgres
def test_present_with_comment_is_rejected(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present", comment="note")
    assert response.status_code == 422


@requires_postgres
def test_absent_with_invalid_reason_is_rejected(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="absent", absence_reason="bogus")
    assert response.status_code == 422


@requires_postgres
def test_absent_without_reason_is_allowed(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="absent")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "absent"
    assert body["absence_reason"] is None


@requires_postgres
def test_absent_with_comment_but_no_reason_is_allowed(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="absent", comment="forgot to notify")
    assert response.status_code == 200
    assert response.json()["comment"] == "forgot to notify"


@requires_postgres
def test_invalid_status_value_is_rejected(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="late")
    assert response.status_code == 422


# --- Lifecycle (ADR-0032 §5) -------------------------------------------------


@pytest.mark.parametrize("status", ["published", "in_progress"])
@requires_postgres
def test_mark_attendance_allowed_for_eligible_event_status(client: TestClient, status: str) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status=status)
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 200


@pytest.mark.parametrize("status", ["scheduled", "in_progress"])
@requires_postgres
def test_mark_attendance_allowed_for_eligible_occurrence_status(
    client: TestClient, status: str
) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(status=status)
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 200


@requires_postgres
def test_mark_attendance_closed_for_completed_event(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="completed")
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_normal_window_closed"


@pytest.mark.parametrize("status", ["cancelled", "draft", "archived"])
@requires_postgres
def test_mark_attendance_closed_for_non_operational_event_status(
    client: TestClient, status: str
) -> None:
    overrides: dict[str, object] = {"status": status}
    if status == "cancelled":
        overrides["cancellation_reason"] = "weather"
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="published")
    with session_scope() as session:
        event = session.get(Event, event_id)
        for key, value in overrides.items():
            setattr(event, key, value)
        session.commit()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_lifecycle_closed"


@requires_postgres
def test_mark_attendance_closed_for_cancelled_occurrence(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="scheduled"
    )
    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        occurrence.status = "cancelled"
        occurrence.cancellation_reason = "weather"
        session.commit()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_lifecycle_closed"


@requires_postgres
def test_correction_rejected_while_normal_window_is_open(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="published")
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _correct(client, event_id, person_id, status="present", reason="fix")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "use_normal_attendance_endpoint"


@requires_postgres
def test_correction_allowed_after_completed(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="completed")
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _correct(client, event_id, person_id, status="present", reason="forgot to mark")
    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] is None
    assert body["new_status"] == "present"
    assert body["reason"] == "forgot to mark"


@requires_postgres
def test_correction_rejected_for_cancelled_event(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="published")
    with session_scope() as session:
        event = session.get(Event, event_id)
        event.status = "cancelled"
        event.cancellation_reason = "weather"
        session.commit()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _correct(client, event_id, person_id, status="present", reason="fix")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_lifecycle_closed"


@requires_postgres
def test_correction_requires_reason(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="completed")
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _correct(client, event_id, person_id, status="present", reason="")
    assert response.status_code in (409, 422)


# --- Single upsert: participation dependency, idempotency, order of checks -


@requires_postgres
def test_mark_attendance_without_participation_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, person, actor_person, actor_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        club_id, event_id, person_id, user_id = club.id, event.id, person.id, actor_user.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "participation_missing"

    with session_scope() as session:
        count = (
            session.execute(select(Attendance).where(Attendance.event_id == event_id))
            .scalars()
            .all()
        )
        assert count == []


@requires_postgres
def test_mark_attendance_is_idempotent(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    first = _mark(client, event_id, person_id, status="absent", absence_reason="sick")
    assert first.status_code == 200
    second = _mark(client, event_id, person_id, status="absent", absence_reason="sick")
    assert second.status_code == 200
    assert first.json()["person_id"] == second.json()["person_id"]

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.event_id == event_id))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
def test_mark_attendance_update_changes_status(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    first = _mark(client, event_id, person_id, status="present")
    assert first.status_code == 200
    second = _mark(client, event_id, person_id, status="absent", absence_reason="injury")
    assert second.status_code == 200
    assert second.json()["status"] == "absent"
    assert second.json()["absence_reason"] == "injury"


@requires_postgres
def test_mark_attendance_nonexistent_object_is_404(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([person, actor_person, actor_user])
        session.commit()
        person_id, user_id = person.id, actor_user.id
    _grant_permission(user_id, "attendance.update", scope_type="all")
    _authenticate_as(user_id)

    response = _mark(client, uuid.uuid4(), person_id, status="present")
    assert response.status_code == 404


@requires_postgres
def test_mark_attendance_unauthorized_is_404_not_403(client: TestClient) -> None:
    club_id, event_id, person_id, _owner_user_id = _setup_event_with_participant()
    with session_scope() as session:
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([other_person, other_user])
        session.commit()
        other_user_id = other_user.id
    # No permission granted at all.
    _authenticate_as(other_user_id)

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 404


@requires_postgres
def test_mark_attendance_for_recurring_occurrence(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 200
    with session_scope() as session:
        row = session.execute(
            select(Attendance).where(Attendance.occurrence_id == occurrence_id)
        ).scalar_one()
        assert row.person_id == person_id
        assert row.event_id is None


# --- Bulk upsert (ADR-0032 §7) -----------------------------------------------


@requires_postgres
def test_bulk_upsert_partial_creates_and_leaves_others_unchanged(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        p1, p2, p3 = _make_person(), _make_person(), _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, p1, p2, p3, actor_person, actor_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add_all(
            [
                _make_event_participation(event, p1),
                _make_event_participation(event, p2),
                _make_event_participation(event, p3),
            ]
        )
        session.commit()
        club_id, event_id, user_id = club.id, event.id, actor_user.id
        p1_id, p2_id, p3_id = p1.id, p2.id, p3.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    # Mark p3 first via single upsert; bulk should leave it untouched.
    _mark(client, event_id, p3_id, status="present")

    response = _bulk_mark(
        client,
        event_id,
        [
            {"person_id": str(p1_id), "status": "present"},
            {"person_id": str(p2_id), "status": "absent", "absence_reason": "sick"},
        ],
    )
    assert response.status_code == 200
    items = {item["person_id"]: item for item in response.json()["items"]}
    assert items[str(p1_id)]["status"] == "present"
    assert items[str(p2_id)]["status"] == "absent"

    with session_scope() as session:
        row_p3 = session.execute(
            select(Attendance).where(Attendance.event_id == event_id, Attendance.person_id == p3_id)
        ).scalar_one()
        assert row_p3.status == "present"


@requires_postgres
def test_bulk_upsert_duplicate_person_id_rejected_atomically(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _bulk_mark(
        client,
        event_id,
        [
            {"person_id": str(person_id), "status": "present"},
            {"person_id": str(person_id), "status": "absent"},
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "duplicate_person_id"

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.event_id == event_id))
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_bulk_upsert_invalid_item_rejects_whole_operation(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        eligible_person = _make_person()
        ineligible_person = _make_person()  # no EventParticipation
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, eligible_person, ineligible_person, actor_person, actor_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_participation(event, eligible_person))
        session.commit()
        club_id, event_id, user_id = club.id, event.id, actor_user.id
        eligible_id, ineligible_id = eligible_person.id, ineligible_person.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _bulk_mark(
        client,
        event_id,
        [
            {"person_id": str(eligible_id), "status": "present"},
            {"person_id": str(ineligible_id), "status": "present"},
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "participation_missing"

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.event_id == event_id))
            .scalars()
            .all()
        )
        assert rows == [], "no partial persistence on bulk failure"


@requires_postgres
def test_bulk_upsert_produces_exactly_one_audit_event(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        p1, p2 = _make_person(), _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, p1, p2, actor_person, actor_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add_all(
            [_make_event_participation(event, p1), _make_event_participation(event, p2)]
        )
        session.commit()
        club_id, event_id, user_id = club.id, event.id, actor_user.id
        p1_id, p2_id = p1.id, p2.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _bulk_mark(
        client,
        event_id,
        [
            {"person_id": str(p1_id), "status": "present"},
            {"person_id": str(p2_id), "status": "absent"},
        ],
    )
    assert response.status_code == 200

    with session_scope() as session:
        bulk_events = (
            session.execute(select(AuditLog).where(AuditLog.action == "attendance.bulk_changed"))
            .scalars()
            .all()
        )
        assert len(bulk_events) == 1
        assert bulk_events[0].details["count"] == 2
        created_events = (
            session.execute(select(AuditLog).where(AuditLog.action == "attendance.created"))
            .scalars()
            .all()
        )
        assert created_events == [], "bulk must not also emit per-item attendance.created events"


# --- Correction audit --------------------------------------------------------


@requires_postgres
def test_correction_records_previous_and_new_status_with_audit(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant(status="published")
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    _mark(client, event_id, person_id, status="present")

    with session_scope() as session:
        event = session.get(Event, event_id)
        event.status = "completed"
        session.commit()

    response = _correct(
        client,
        event_id,
        person_id,
        status="absent",
        absence_reason="sick",
        reason="was actually sick",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] == "present"
    assert body["new_status"] == "absent"

    with session_scope() as session:
        audit_row = session.execute(
            select(AuditLog).where(AuditLog.action == "attendance.corrected")
        ).scalar_one()
        assert audit_row.details["previous_status"] == "present"
        assert audit_row.details["new_status"] == "absent"
        assert audit_row.details["reason"] == "was actually sick"


# --- GET: full participant projection + summary (ADR-0032 §8/§9) -----------


@requires_postgres
def test_get_attendance_includes_unmarked_participants_and_summary(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        ivanov = _make_person(first_name="Ivan", last_name="Ivanov")
        petrov = _make_person(first_name="Petr", last_name="Petrov")
        sidorov = _make_person(first_name="Sidor", last_name="Sidorov")
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, ivanov, petrov, sidorov, actor_person, actor_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add_all(
            [
                _make_event_participation(event, ivanov),
                _make_event_participation(event, petrov),
                _make_event_participation(event, sidorov),
            ]
        )
        session.commit()
        club_id, event_id, user_id = club.id, event.id, actor_user.id
        ivanov_id, petrov_id, sidorov_id = ivanov.id, petrov.id, sidorov.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    _mark(client, event_id, ivanov_id, status="present")
    _mark(client, event_id, petrov_id, status="absent", absence_reason="sick")
    # sidorov is never marked.

    response = _get_attendance(client, event_id)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total": 3,
        "marked": 2,
        "present": 1,
        "absent": 1,
        "unmarked": 1,
    }
    by_person = {item["person"]["id"]: item for item in body["items"]}
    assert by_person[str(ivanov_id)]["status"] == "present"
    assert by_person[str(petrov_id)]["status"] == "absent"
    assert by_person[str(petrov_id)]["absence_reason"] == "sick"
    assert by_person[str(sidorov_id)]["status"] is None


@requires_postgres
def test_get_attendance_self_scope_sees_only_own_row(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        self_person = _make_person()
        other_person = _make_person()
        self_user = _make_user(self_person)
        session.add_all([club, self_person, other_person, self_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add_all(
            [
                _make_event_participation(event, self_person),
                _make_event_participation(event, other_person),
            ]
        )
        session.commit()
        club_id, event_id, self_user_id = club.id, event.id, self_user.id
        self_person_id, other_person_id = self_person.id, other_person.id
    _grant_permission(self_user_id, "attendance.read", scope_type="self", club_id=club_id)
    _authenticate_as(self_user_id)

    response = _get_attendance(client, event_id)
    assert response.status_code == 200
    body = response.json()
    person_ids = {item["person"]["id"] for item in body["items"]}
    assert person_ids == {str(self_person_id)}
    assert other_person_id not in {uuid.UUID(p) for p in person_ids}
    assert body["summary"]["total"] == 1


@requires_postgres
def test_get_attendance_children_scope_sees_only_child_row(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        child_person = _make_person()
        unrelated_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, guardian_person, child_person, unrelated_person, guardian_user])
        session.commit()
        session.add_all(
            [
                _make_club_membership(club, guardian_person),
                _make_club_membership(club, child_person),
            ]
        )
        session.add(_make_guardian_relationship(guardian_person, child_person))
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add_all(
            [
                _make_event_participation(event, child_person),
                _make_event_participation(event, unrelated_person),
            ]
        )
        session.commit()
        club_id, event_id, guardian_user_id = club.id, event.id, guardian_user.id
        child_person_id = child_person.id
    _grant_permission(guardian_user_id, "attendance.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _get_attendance(client, event_id)
    assert response.status_code == 200
    body = response.json()
    person_ids = {item["person"]["id"] for item in body["items"]}
    assert person_ids == {str(child_person_id)}
    assert body["summary"]["total"] == 1


@requires_postgres
def test_get_attendance_all_scope_sees_full_set(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        p1, p2 = _make_person(), _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, p1, p2, actor_person, actor_user])
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add_all(
            [_make_event_participation(event, p1), _make_event_participation(event, p2)]
        )
        session.commit()
        club_id, event_id, user_id = club.id, event.id, actor_user.id
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _get_attendance(client, event_id)
    assert response.status_code == 200
    assert response.json()["summary"]["total"] == 2


@requires_postgres
def test_get_attendance_none_scope_and_no_permission_is_404(client: TestClient) -> None:
    club_id, event_id, _person_id, _owner_id = _setup_event_with_participant()
    with session_scope() as session:
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([other_person, other_user])
        session.commit()
        other_user_id = other_user.id
    _authenticate_as(other_user_id)

    response = _get_attendance(client, event_id)
    assert response.status_code == 404


# --- Cross-Club IDOR ---------------------------------------------------------


@requires_postgres
def test_cross_club_scope_does_not_grant_access(client: TestClient) -> None:
    club_id, event_id, person_id, _owner_id = _setup_event_with_participant()
    with session_scope() as session:
        other_club = _make_club()
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([other_club, other_person, other_user])
        session.commit()
        other_club_id, other_user_id = other_club.id, other_user.id
    # Grant attendance.read scoped to a DIFFERENT club only.
    _grant_permission(other_user_id, "attendance.read", scope_type="all", club_id=other_club_id)
    _authenticate_as(other_user_id)

    response = _get_attendance(client, event_id)
    assert response.status_code == 404

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 404


# --- Concurrency: last-write-wins, no version field -------------------------


@requires_postgres
def test_no_version_or_concurrency_token_field_exists() -> None:
    columns = {column.name for column in Attendance.__table__.columns}
    assert "version" not in columns
    assert "etag" not in columns


@requires_postgres
def test_last_write_wins_sequential_updates(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    _mark(client, event_id, person_id, status="present")
    _mark(client, event_id, person_id, status="absent", absence_reason="work")
    final = _mark(client, event_id, person_id, status="present")
    assert final.status_code == 200
    assert final.json()["status"] == "present"

    with session_scope() as session:
        row = session.execute(
            select(Attendance).where(
                Attendance.event_id == event_id, Attendance.person_id == person_id
            )
        ).scalar_one()
        assert row.status == "present"
