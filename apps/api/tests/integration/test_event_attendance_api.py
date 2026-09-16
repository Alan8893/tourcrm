"""HTTP-level integration tests for Event Attendance (Issue #94 / TH-0087,
per ADR-0032 and ADR-0033): persistence/constraints (occurrence-only
identity — `Attendance.occurrence_id` is a real `NOT NULL` FK, no
`Attendance.event_id`), field validation invariants, lifecycle gating,
single upsert idempotency and order-of-checks, partial/atomic bulk
upsert, correction (never creates a first record), the full GET
participant projection (including unmarked participants) and its
derived summary, canonical scope authorization (all/own_events/
own_groups/self/children/none), cross-Club IDOR/existence-hiding, audit
codes, last-write-wins concurrency (no version field), and — per
ADR-0033 — an ordinary, non-recurring `Event` resolving deterministically
to its own one concrete `EventOccurrence` (never ambiguous UUID probing,
never a second addressable identity for the same real-world event).

Also covers the historical-roster fix from PR #97 review: a recurring
occurrence's participant eligibility (for GET's roster/summary and for
correction) is keyed off the occurrence's own `[starts_at, ends_at)`
window, not "is the person an active participant right now" — so ending
a participation after the occurrence happened neither hides an already-
marked Attendance row nor blocks correcting it, and a participation that
only starts after the occurrence's window has closed never enters that
occurrence's historical denominator.

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


_EVENT_TO_OCCURRENCE_STATUS = {
    "draft": "scheduled",
    "published": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
}


def _make_event(session, club: Club, **overrides: object) -> Event:
    """ADR-0033: every Event has exactly one linked EventOccurrence — so
    this helper creates both, adding them to `session` itself (the
    caller's own subsequent `session.add(event)`/`add_all([...])` is
    then a harmless no-op) rather than returning a bare, unlinked Event.
    """
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
    event = Event(id=uuid.uuid4(), **defaults)  # type: ignore[arg-type]
    session.add(event)
    session.flush()
    session.add(
        EventOccurrence(
            event_id=event.id,
            series_id=None,
            club_id=event.club_id,
            name=event.title,
            description=event.description,
            event_type=event.event_type,
            recurrence_anchor_at=event.start_at,
            starts_at=event.start_at,
            ends_at=event.end_at,
            timezone=event.timezone,
            status=_EVENT_TO_OCCURRENCE_STATUS.get(event.status, "scheduled"),
            cancellation_reason=event.cancellation_reason,
        )
    )
    return event


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


def _mark(client: TestClient, occurrence_id: uuid.UUID, person_id: uuid.UUID, **payload: object):
    return client.put(
        f"/api/v1/events/{occurrence_id}/attendance/{person_id}",
        json=payload,
        headers=_csrf_headers(client),
    )


def _bulk_mark(client: TestClient, occurrence_id: uuid.UUID, items: list[dict]):
    return client.put(
        f"/api/v1/events/{occurrence_id}/attendance",
        json={"items": items},
        headers=_csrf_headers(client),
    )


def _correct(client: TestClient, occurrence_id: uuid.UUID, person_id: uuid.UUID, **payload: object):
    return client.post(
        f"/api/v1/events/{occurrence_id}/attendance/{person_id}/corrections",
        json=payload,
        headers=_csrf_headers(client),
    )


def _get_attendance(client: TestClient, occurrence_id: uuid.UUID, **params: object):
    return client.get(f"/api/v1/events/{occurrence_id}/attendance", params=params)


def _setup_occurrence_with_participant(
    status: str = "scheduled",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, occurrence_id, person_id, actor_user_id) with an
    active EventOccurrenceParticipant for person on occurrence."""
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


# --- Persistence / DB constraints (occurrence-only identity) ---------------


@requires_postgres
def test_no_event_id_column_exists() -> None:
    columns = {column.name for column in Attendance.__table__.columns}
    assert "event_id" not in columns
    assert "occurrence_id" in columns


@requires_postgres
def test_occurrence_id_is_not_nullable() -> None:
    column = Attendance.__table__.columns["occurrence_id"]
    assert column.nullable is False


@requires_postgres
def test_occurrence_id_is_a_real_fk_to_event_occurrences(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        session.add(person)
        session.commit()
        session.add(Attendance(occurrence_id=uuid.uuid4(), person_id=person.id, status="present"))
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
def test_status_check_constraint(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add(Attendance(occurrence_id=occurrence.id, person_id=person.id, status="late"))
        with pytest.raises(IntegrityError):
            session.commit()


@requires_postgres
def test_absence_reason_check_constraint(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add(
            Attendance(
                occurrence_id=occurrence.id,
                person_id=person.id,
                status="absent",
                absence_reason="bogus",
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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add(
            Attendance(
                occurrence_id=occurrence.id,
                person_id=person.id,
                status="present",
                absence_reason="sick",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# --- Field validation (ADR-0032 §2-4) ---------------------------------------


@requires_postgres
def test_present_with_absence_reason_is_rejected(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present", absence_reason="sick")
    assert response.status_code == 422


@requires_postgres
def test_present_with_comment_is_rejected(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present", comment="note")
    assert response.status_code == 422


@requires_postgres
def test_absent_with_invalid_reason_is_rejected(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="absent", absence_reason="bogus")
    assert response.status_code == 422


@requires_postgres
def test_absent_without_reason_is_allowed(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="absent")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "absent"
    assert body["absence_reason"] is None


@requires_postgres
def test_absent_with_comment_but_no_reason_is_allowed(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="absent", comment="forgot to notify")
    assert response.status_code == 200
    assert response.json()["comment"] == "forgot to notify"


@requires_postgres
def test_invalid_status_value_is_rejected(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="late")
    assert response.status_code == 422


# --- Lifecycle (ADR-0032 §5) -------------------------------------------------


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
def test_mark_attendance_closed_for_completed_occurrence(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="completed"
    )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_normal_window_closed"


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
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="scheduled"
    )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _correct(client, occurrence_id, person_id, status="present", reason="fix")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "use_normal_attendance_endpoint"


@requires_postgres
def test_correction_rejected_for_cancelled_occurrence(client: TestClient) -> None:
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

    response = _correct(client, occurrence_id, person_id, status="present", reason="fix")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attendance_lifecycle_closed"


# --- Correction never creates a first record (ADR-0032 §5) ------------------


@requires_postgres
def test_correction_cannot_create_missing_attendance(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="scheduled"
    )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    # No attendance ever marked. Close the normal window.
    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        occurrence.status = "completed"
        session.commit()

    response = _correct(client, occurrence_id, person_id, status="present", reason="forgot to mark")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "attendance_not_found"

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.occurrence_id == occurrence_id))
            .scalars()
            .all()
        )
        assert rows == [], "correction must never create a first Attendance record"


@requires_postgres
def test_correction_of_existing_attendance_succeeds(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="scheduled"
    )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    _mark(client, occurrence_id, person_id, status="present")

    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        occurrence.status = "completed"
        session.commit()

    response = _correct(
        client,
        occurrence_id,
        person_id,
        status="absent",
        absence_reason="sick",
        reason="was actually sick",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["previous_status"] == "present"
    assert body["new_status"] == "absent"


@requires_postgres
def test_correction_requires_reason(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="scheduled"
    )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    _mark(client, occurrence_id, person_id, status="present")
    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        occurrence.status = "completed"
        session.commit()

    response = _correct(client, occurrence_id, person_id, status="present", reason="")
    assert response.status_code in (409, 422)


@requires_postgres
def test_correction_records_previous_and_new_status_with_audit(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant(
        status="scheduled"
    )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    _mark(client, occurrence_id, person_id, status="present")

    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        occurrence.status = "completed"
        session.commit()

    response = _correct(
        client,
        occurrence_id,
        person_id,
        status="absent",
        absence_reason="sick",
        reason="was actually sick",
    )
    assert response.status_code == 200

    with session_scope() as session:
        audit_row = session.execute(
            select(AuditLog).where(AuditLog.action == "attendance.corrected")
        ).scalar_one()
        assert audit_row.details["previous_status"] == "present"
        assert audit_row.details["new_status"] == "absent"
        assert audit_row.details["reason"] == "was actually sick"


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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        club_id, occurrence_id, person_id, user_id = (
            club.id,
            occurrence.id,
            person.id,
            actor_user.id,
        )
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "participation_missing"

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.occurrence_id == occurrence_id))
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_mark_attendance_is_idempotent(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    first = _mark(client, occurrence_id, person_id, status="absent", absence_reason="sick")
    assert first.status_code == 200
    second = _mark(client, occurrence_id, person_id, status="absent", absence_reason="sick")
    assert second.status_code == 200
    assert first.json()["person_id"] == second.json()["person_id"]

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.occurrence_id == occurrence_id))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
def test_mark_attendance_update_changes_status(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    first = _mark(client, occurrence_id, person_id, status="present")
    assert first.status_code == 200
    second = _mark(client, occurrence_id, person_id, status="absent", absence_reason="injury")
    assert second.status_code == 200
    assert second.json()["status"] == "absent"
    assert second.json()["absence_reason"] == "injury"


@requires_postgres
def test_mark_attendance_nonexistent_occurrence_is_404(client: TestClient) -> None:
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
    club_id, occurrence_id, person_id, _owner_user_id = _setup_occurrence_with_participant()
    with session_scope() as session:
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([other_person, other_user])
        session.commit()
        other_user_id = other_user.id
    # No permission granted at all.
    _authenticate_as(other_user_id)

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 404


# --- Ordinary Event resolves to its one concrete occurrence (ADR-0033) -----


def _setup_event_with_participant(
    status: str = "published",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (club_id, event_id, person_id, actor_user_id) with an
    EventParticipation for person on an ordinary Event."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, person, actor_person, actor_user])
        session.commit()
        event = _make_event(session, club, status=status)
        session.add(_make_event_participation(event, person))
        session.commit()
        return club.id, event.id, person.id, actor_user.id


@requires_postgres
def test_ordinary_event_resolves_to_its_concrete_occurrence(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    mark_response = _mark(client, event_id, person_id, status="present")
    assert mark_response.status_code == 200, mark_response.text
    assert mark_response.json()["person_id"] == str(person_id)

    get_response = _get_attendance(client, event_id)
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["summary"] == {"total": 1, "marked": 1, "present": 1, "absent": 0, "unmarked": 0}

    with session_scope() as session:
        occurrence = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        row = session.execute(
            select(Attendance).where(
                Attendance.occurrence_id == occurrence.id, Attendance.person_id == person_id
            )
        ).scalar_one()
        assert row.status == "present"


@requires_postgres
def test_ordinary_event_occurrence_id_remains_stable_after_event_update(
    client: TestClient,
) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "event.update", scope_type="all", club_id=club_id)
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    with session_scope() as session:
        occurrence_before = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        occurrence_id_before = occurrence_before.id

    response = client.patch(
        f"/api/v1/events/{event_id}",
        json={"title": "Updated title"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text

    with session_scope() as session:
        occurrence_after = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        assert occurrence_after.id == occurrence_id_before
        assert occurrence_after.name == "Updated title"

    mark_response = _mark(client, event_id, person_id, status="present")
    assert mark_response.status_code == 200, mark_response.text


@requires_postgres
def test_ordinary_event_bulk_and_correction_work_through_event_id(client: TestClient) -> None:
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    bulk_response = _bulk_mark(
        client, event_id, [{"person_id": str(person_id), "status": "present"}]
    )
    assert bulk_response.status_code == 200, bulk_response.text

    with session_scope() as session:
        event = session.get(Event, event_id)
        event.status = "completed"
        occurrence = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        occurrence.status = "completed"
        session.commit()

    correction_response = _correct(
        client, event_id, person_id, status="absent", absence_reason="sick", reason="was sick"
    )
    assert correction_response.status_code == 200, correction_response.text
    assert correction_response.json()["previous_status"] == "present"
    assert correction_response.json()["new_status"] == "absent"


@requires_postgres
def test_ordinary_event_without_participation_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, person, actor_person, actor_user])
        session.commit()
        event = _make_event(session, club)
        session.commit()
        club_id, event_id, person_id, user_id = club.id, event.id, person.id, actor_user.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _mark(client, event_id, person_id, status="present")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "participation_missing"


@requires_postgres
def test_event_backed_occurrence_own_id_is_not_independently_addressable(
    client: TestClient,
) -> None:
    """ADR-0033 §5: the occurrence backing an ordinary Event is not a
    second public identity for the same real-world event — only the
    Event's own id resolves it."""
    club_id, event_id, person_id, user_id = _setup_event_with_participant()
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    with session_scope() as session:
        occurrence = session.execute(
            select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        occurrence_id = occurrence.id

    assert _get_attendance(client, event_id).status_code == 200
    assert _get_attendance(client, occurrence_id).status_code == 404


# --- Historical preservation (ADR-0032 §1/§6) -------------------------------


@requires_postgres
def test_attendance_survives_participation_ending(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    _mark(client, occurrence_id, person_id, status="present")

    with session_scope() as session:
        participant = session.execute(
            select(EventOccurrenceParticipant).where(
                EventOccurrenceParticipant.occurrence_id == occurrence_id,
                EventOccurrenceParticipant.person_id == person_id,
            )
        ).scalar_one()
        participant.valid_to = datetime.datetime.now(datetime.timezone.utc)
        session.commit()

        row = session.execute(
            select(Attendance).where(
                Attendance.occurrence_id == occurrence_id, Attendance.person_id == person_id
            )
        ).scalar_one_or_none()
        assert row is not None
        assert row.status == "present"


@requires_postgres
def test_completed_attendance_remains_visible_and_correctable_after_participation_ends(
    client: TestClient,
) -> None:
    """Persisting the Attendance row alone (as the previous test proves)
    is not the same guarantee as it remaining *visible and countable* on
    GET, or *correctable*, once the participant's validity has since
    ended (PR #97 review). Both the GET roster/summary and the
    correction endpoint must key participation eligibility off the
    occurrence's own time window, not "is the person an active
    participant right now"."""
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)
    _mark(client, occurrence_id, person_id, status="present")

    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        occurrence.status = "completed"
        participant = session.execute(
            select(EventOccurrenceParticipant).where(
                EventOccurrenceParticipant.occurrence_id == occurrence_id,
                EventOccurrenceParticipant.person_id == person_id,
            )
        ).scalar_one()
        # Ends after the occurrence itself took place, not "now" — the
        # participation was valid throughout the occurrence and only
        # later ended.
        participant.valid_to = occurrence.ends_at + datetime.timedelta(hours=1)
        session.commit()

    get_response = _get_attendance(client, occurrence_id)
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["summary"] == {"total": 1, "marked": 1, "present": 1, "absent": 0, "unmarked": 0}
    assert body["items"][0]["person"]["id"] == str(person_id)
    assert body["items"][0]["status"] == "present"

    correction_response = _correct(
        client,
        occurrence_id,
        person_id,
        status="absent",
        absence_reason="sick",
        reason="was actually sick",
    )
    assert correction_response.status_code == 200
    correction_body = correction_response.json()
    assert correction_body["previous_status"] == "present"
    assert correction_body["new_status"] == "absent"

    get_after_correction = _get_attendance(client, occurrence_id)
    assert get_after_correction.json()["summary"] == {
        "total": 1,
        "marked": 1,
        "present": 0,
        "absent": 1,
        "unmarked": 0,
    }


@requires_postgres
def test_unmarked_participant_remains_in_historical_roster_after_participation_ends(
    client: TestClient,
) -> None:
    """The historical roster/denominator must not shrink just because
    nobody marked attendance before the participation validity ended —
    "current roster" and "historical occurrence roster" are different
    things (PR #97 review)."""
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    with session_scope() as session:
        occurrence = session.get(EventOccurrence, occurrence_id)
        participant = session.execute(
            select(EventOccurrenceParticipant).where(
                EventOccurrenceParticipant.occurrence_id == occurrence_id,
                EventOccurrenceParticipant.person_id == person_id,
            )
        ).scalar_one()
        participant.valid_to = occurrence.ends_at + datetime.timedelta(hours=1)
        occurrence.status = "completed"
        session.commit()

    # No attendance was ever marked for this person, and their
    # participation has since ended — they must still surface as
    # "unmarked", not disappear from the denominator entirely.
    response = _get_attendance(client, occurrence_id)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"total": 1, "marked": 0, "present": 0, "absent": 0, "unmarked": 1}
    assert body["items"][0]["person"]["id"] == str(person_id)
    assert body["items"][0]["status"] is None


@requires_postgres
def test_participant_joining_after_occurrence_window_is_not_in_historical_roster(
    client: TestClient,
) -> None:
    """The mirror image of the two tests above: a participation that
    only starts after the occurrence's own window has already closed
    must not silently enter that occurrence's historical denominator as
    "unmarked" (PR #97 review)."""
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, person, actor_person, actor_user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id, status="completed")
        session.add(occurrence)
        session.commit()
        session.add(
            _make_occurrence_participant(
                occurrence,
                person,
                valid_from=occurrence.ends_at + datetime.timedelta(days=1),
            )
        )
        session.commit()
        club_id, occurrence_id, user_id = club.id, occurrence.id, actor_user.id
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _get_attendance(client, occurrence_id)
    assert response.status_code == 200
    assert response.json()["summary"] == {
        "total": 0,
        "marked": 0,
        "present": 0,
        "absent": 0,
        "unmarked": 0,
    }


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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add_all(
            [
                _make_occurrence_participant(occurrence, p1),
                _make_occurrence_participant(occurrence, p2),
                _make_occurrence_participant(occurrence, p3),
            ]
        )
        session.commit()
        club_id, occurrence_id, user_id = club.id, occurrence.id, actor_user.id
        p1_id, p2_id, p3_id = p1.id, p2.id, p3.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    # Mark p3 first via single upsert; bulk should leave it untouched.
    _mark(client, occurrence_id, p3_id, status="present")

    response = _bulk_mark(
        client,
        occurrence_id,
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
            select(Attendance).where(
                Attendance.occurrence_id == occurrence_id, Attendance.person_id == p3_id
            )
        ).scalar_one()
        assert row_p3.status == "present"


@requires_postgres
def test_bulk_upsert_duplicate_person_id_rejected_atomically(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _bulk_mark(
        client,
        occurrence_id,
        [
            {"person_id": str(person_id), "status": "present"},
            {"person_id": str(person_id), "status": "absent"},
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "duplicate_person_id"

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.occurrence_id == occurrence_id))
            .scalars()
            .all()
        )
        assert rows == []


@requires_postgres
def test_bulk_upsert_invalid_item_rejects_whole_operation(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        eligible_person = _make_person()
        ineligible_person = _make_person()  # no EventOccurrenceParticipant
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, eligible_person, ineligible_person, actor_person, actor_user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add(_make_occurrence_participant(occurrence, eligible_person))
        session.commit()
        club_id, occurrence_id, user_id = club.id, occurrence.id, actor_user.id
        eligible_id, ineligible_id = eligible_person.id, ineligible_person.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _bulk_mark(
        client,
        occurrence_id,
        [
            {"person_id": str(eligible_id), "status": "present"},
            {"person_id": str(ineligible_id), "status": "present"},
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "participation_missing"

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.occurrence_id == occurrence_id))
            .scalars()
            .all()
        )
        assert rows == [], "no partial persistence on bulk failure"


@requires_postgres
def test_bulk_upsert_is_idempotent(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    payload = [{"person_id": str(person_id), "status": "present"}]
    first = _bulk_mark(client, occurrence_id, payload)
    second = _bulk_mark(client, occurrence_id, payload)
    assert first.status_code == 200
    assert second.status_code == 200

    with session_scope() as session:
        rows = (
            session.execute(select(Attendance).where(Attendance.occurrence_id == occurrence_id))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@requires_postgres
def test_bulk_upsert_produces_exactly_one_audit_event(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        p1, p2 = _make_person(), _make_person()
        actor_person = _make_person()
        actor_user = _make_user(actor_person)
        session.add_all([club, p1, p2, actor_person, actor_user])
        session.commit()
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add_all(
            [
                _make_occurrence_participant(occurrence, p1),
                _make_occurrence_participant(occurrence, p2),
            ]
        )
        session.commit()
        club_id, occurrence_id, user_id = club.id, occurrence.id, actor_user.id
        p1_id, p2_id = p1.id, p2.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _bulk_mark(
        client,
        occurrence_id,
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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add_all(
            [
                _make_occurrence_participant(occurrence, ivanov),
                _make_occurrence_participant(occurrence, petrov),
                _make_occurrence_participant(occurrence, sidorov),
            ]
        )
        session.commit()
        club_id, occurrence_id, user_id = club.id, occurrence.id, actor_user.id
        ivanov_id, petrov_id, sidorov_id = ivanov.id, petrov.id, sidorov.id
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    _mark(client, occurrence_id, ivanov_id, status="present")
    _mark(client, occurrence_id, petrov_id, status="absent", absence_reason="sick")
    # sidorov is never marked.

    response = _get_attendance(client, occurrence_id)
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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add_all(
            [
                _make_occurrence_participant(occurrence, self_person),
                _make_occurrence_participant(occurrence, other_person),
            ]
        )
        session.commit()
        club_id, occurrence_id, self_user_id = club.id, occurrence.id, self_user.id
        self_person_id, other_person_id = self_person.id, other_person.id
    _grant_permission(self_user_id, "attendance.read", scope_type="self", club_id=club_id)
    _authenticate_as(self_user_id)

    response = _get_attendance(client, occurrence_id)
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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add_all(
            [
                _make_occurrence_participant(occurrence, child_person),
                _make_occurrence_participant(occurrence, unrelated_person),
            ]
        )
        session.commit()
        club_id, occurrence_id, guardian_user_id = club.id, occurrence.id, guardian_user.id
        child_person_id = child_person.id
    _grant_permission(guardian_user_id, "attendance.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _get_attendance(client, occurrence_id)
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
        series = _make_series(session, club_id=club.id)
        occurrence = _make_occurrence(series=series, club_id=club.id)
        session.add(occurrence)
        session.commit()
        session.add_all(
            [
                _make_occurrence_participant(occurrence, p1),
                _make_occurrence_participant(occurrence, p2),
            ]
        )
        session.commit()
        club_id, occurrence_id, user_id = club.id, occurrence.id, actor_user.id
    _grant_permission(user_id, "attendance.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _get_attendance(client, occurrence_id)
    assert response.status_code == 200
    assert response.json()["summary"]["total"] == 2


@requires_postgres
def test_get_attendance_no_permission_is_404(client: TestClient) -> None:
    club_id, occurrence_id, _person_id, _owner_id = _setup_occurrence_with_participant()
    with session_scope() as session:
        other_person = _make_person()
        other_user = _make_user(other_person)
        session.add_all([other_person, other_user])
        session.commit()
        other_user_id = other_user.id
    _authenticate_as(other_user_id)

    response = _get_attendance(client, occurrence_id)
    assert response.status_code == 404


# --- Cross-Club IDOR ---------------------------------------------------------


@requires_postgres
def test_cross_club_scope_does_not_grant_access(client: TestClient) -> None:
    club_id, occurrence_id, person_id, _owner_id = _setup_occurrence_with_participant()
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

    response = _get_attendance(client, occurrence_id)
    assert response.status_code == 404

    response = _mark(client, occurrence_id, person_id, status="present")
    assert response.status_code == 404


# --- Concurrency: last-write-wins, no version field -------------------------


@requires_postgres
def test_no_version_or_concurrency_token_field_exists() -> None:
    columns = {column.name for column in Attendance.__table__.columns}
    assert "version" not in columns
    assert "etag" not in columns


@requires_postgres
def test_last_write_wins_sequential_updates(client: TestClient) -> None:
    club_id, occurrence_id, person_id, user_id = _setup_occurrence_with_participant()
    _grant_permission(user_id, "attendance.update", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    _mark(client, occurrence_id, person_id, status="present")
    _mark(client, occurrence_id, person_id, status="absent", absence_reason="work")
    final = _mark(client, occurrence_id, person_id, status="present")
    assert final.status_code == 200
    assert final.json()["status"] == "present"

    with session_scope() as session:
        row = session.execute(
            select(Attendance).where(
                Attendance.occurrence_id == occurrence_id, Attendance.person_id == person_id
            )
        ).scalar_one()
        assert row.status == "present"
