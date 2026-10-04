"""Integration tests for the Trip Audit Trail (#247; ADR-0024 §4 as
amended by #247; migration 7d2b4e9f1a63).

Covers, against the REAL shipped app and a real PostgreSQL database:

- `trip.created` on successful Trip creation (resource `trip` /
  `Trip.event_id`, the authenticated user as actor);
- `trip_participant.actual_participation_recorded` on the first
  TripParticipant fact and `trip_participant.actual_participation_changed`
  with `details.changes.actual_participation.{from,to}` on a real change
  (resource `trip_participant` / `event_participation_id`);
- idempotent same-value writes record nothing;
- fail-closed transactions: an audit failure leaves no business mutation;
- no audit for rejected (authorization, lifecycle, validation) requests;
- registration lifecycle stays `event_participation.status_changed`, and
  `cancelled` + `actual_participation = true` remains valid without any
  extra cancellation audit;
- the DB CHECK constraint and the Python vocabulary agree, across a
  migration downgrade/upgrade.

Self-contained factories, per this codebase's convention of not importing
helpers across test files.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.audit.vocabulary import CANONICAL_AUDIT_ACTIONS
from app.db.audit import AuditLog
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation, EventStaffAssignment
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.db.trips import Trip, TripParticipant
from app.main import app
from app.trips import service as trips_service

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.timezone.utc)
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_TRIP_AUDIT_MIGRATION_PARENT = "3c1e9a7d5b20"

_TRIP_ACTIONS = (
    "trip.created",
    "trip_participant.actual_participation_recorded",
    "trip_participant.actual_participation_changed",
)

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


# --- factories -------------------------------------------------------------------


def _person(session) -> Person:  # type: ignore[no-untyped-def]
    person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _user_with_role(session, club: Club, role_code: str, person: Person | None = None) -> User:  # type: ignore[no-untyped-def]
    """A real user of one canonical, migration-seeded baseline role."""
    person = person or _person(session)
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="student",
            status="active",
            joined_at=_LONG_AGO,
        )
    )
    user = User(
        person=person,
        login_identifier=f"{role_code}-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()
    return user


def _event(  # type: ignore[no-untyped-def]
    session, club: Club, *, event_type: str = "trip", status: str = "published"
) -> Event:
    """ADR-0033: an ordinary Event always has its one linked occurrence."""
    event = Event(
        id=uuid.uuid4(),
        club_id=club.id,
        event_type=event_type,
        title="Поход",
        start_at=_START,
        end_at=_START + datetime.timedelta(days=2),
        timezone="UTC",
        status=status,
        cancellation_reason="weather" if status == "cancelled" else None,
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
            recurrence_anchor_at=event.start_at,
            starts_at=event.start_at,
            ends_at=event.end_at,
            timezone=event.timezone,
            status=_EVENT_TO_OCCURRENCE_STATUS[status],
            cancellation_reason=event.cancellation_reason,
        )
    )
    session.flush()
    return event


@dataclass(frozen=True)
class World:
    club_id: uuid.UUID
    event_id: uuid.UUID
    lesson_event_id: uuid.UUID
    admin: uuid.UUID
    instructor_events: uuid.UUID
    instructor_unrelated: uuid.UUID
    member: uuid.UUID
    member_person: uuid.UUID
    member_participation_id: uuid.UUID
    other_person: uuid.UUID
    unregistered_person: uuid.UUID


def _world(*, status: str = "published", with_trip: bool = False) -> World:
    """One Club; a trip Event (given status) with a staff-assigned
    instructor; a member registered for it (`self`), another registered
    Person, an unregistered Person; plus a `lesson` Event."""
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()

        event = _event(session, club, status=status)
        lesson_event = _event(session, club, event_type="lesson")
        admin = _user_with_role(session, club, "admin")
        instructor_events = _user_with_role(session, club, "instructor")
        instructor_unrelated = _user_with_role(session, club, "instructor")
        member_person = _person(session)
        member = _user_with_role(session, club, "member", member_person)
        other_person = _person(session)
        unregistered_person = _person(session)

        session.add(
            EventStaffAssignment(
                event_id=event.id,
                user_id=instructor_events.id,
                role_in_event="instructor",
                valid_from=_LONG_AGO,
            )
        )
        member_participation = EventParticipation(
            event_id=event.id, person_id=member_person.id, registration_status="registered"
        )
        session.add_all(
            [
                member_participation,
                EventParticipation(
                    event_id=event.id, person_id=other_person.id, registration_status="registered"
                ),
            ]
        )
        session.flush()
        if with_trip:
            session.add(Trip(event_id=event.id))
        session.commit()

        return World(
            club_id=club.id,
            event_id=event.id,
            lesson_event_id=lesson_event.id,
            admin=admin.id,
            instructor_events=instructor_events.id,
            instructor_unrelated=instructor_unrelated.id,
            member=member.id,
            member_person=member_person.id,
            member_participation_id=member_participation.id,
            other_person=other_person.id,
            unregistered_person=unregistered_person.id,
        )


def _set_event_status(event_id: uuid.UUID, status: str) -> None:
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        event.status = status
        event.cancellation_reason = "weather" if status == "cancelled" else None
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _create_trip(client: TestClient, event_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/trips", json={"event_id": str(event_id)}, headers=_csrf_headers(client)
    )


def _record(  # type: ignore[no-untyped-def]
    client: TestClient, event_id: uuid.UUID, person_id: uuid.UUID, value: bool
):
    return client.put(
        f"/api/v1/trips/{event_id}/participants/{person_id}",
        json={"actual_participation": value},
        headers=_csrf_headers(client),
    )


def _audit_rows(*actions: str) -> list[AuditLog]:
    with session_scope() as session:
        stmt = select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id)
        if actions:
            stmt = stmt.where(AuditLog.action.in_(actions))
        return list(session.execute(stmt).scalars().all())


def _trip_audit_rows() -> list[AuditLog]:
    return _audit_rows(*_TRIP_ACTIONS)


def _stored_value(participation_id: uuid.UUID) -> bool | None:
    with session_scope() as session:
        row = session.get(TripParticipant, participation_id)
        return None if row is None else row.actual_participation


# --- vocabulary ------------------------------------------------------------------------


def test_vocabulary_contains_exactly_the_three_trip_actions() -> None:
    trip_like = {
        action
        for action in CANONICAL_AUDIT_ACTIONS
        if action.startswith(("trip.", "trip_participant."))
    }
    assert trip_like == set(_TRIP_ACTIONS)


@requires_postgres
@pytest.mark.parametrize("action", _TRIP_ACTIONS)
def test_database_check_constraint_accepts_every_trip_action(action: str) -> None:
    with session_scope() as session:
        session.add(AuditLog(actor_type="system", action=action, outcome="success"))
        session.flush()
        session.rollback()


# --- A. trip.created -----------------------------------------------------------------


@requires_postgres
def test_trip_creation_records_trip_created(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)

    response = client.post(
        "/api/v1/trips",
        json={"event_id": str(world.event_id)},
        headers={**_csrf_headers(client), "X-Request-ID": "req-trip-created"},
    )

    assert response.status_code == 201
    (row,) = _trip_audit_rows()
    assert row.action == "trip.created"
    assert row.resource_type == "trip"
    assert row.resource_id == world.event_id
    assert row.actor_type == "user"
    assert row.actor_user_id == world.admin
    assert row.club_id == world.club_id
    assert row.outcome == "success"
    assert row.request_id == "req-trip-created"
    assert row.details is None


@requires_postgres
def test_trip_creation_by_scoped_instructor_records_that_instructor(
    client: TestClient,
) -> None:
    world = _world()
    with session_scope() as session:
        users_before = session.execute(select(func.count()).select_from(User)).scalar_one()
    _authenticate_as(world.instructor_events)

    assert _create_trip(client, world.event_id).status_code == 201

    (row,) = _trip_audit_rows()
    assert row.actor_type == "user"
    assert row.actor_user_id == world.instructor_events
    with session_scope() as session:
        # No synthetic system user is ever created for auditing.
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == users_before


@requires_postgres
def test_rejected_trip_creation_records_nothing(client: TestClient) -> None:
    world = _world(with_trip=True)
    _authenticate_as(world.admin)

    assert _create_trip(client, world.event_id).status_code == 409  # duplicate
    assert _create_trip(client, world.lesson_event_id).status_code == 422  # not a trip
    assert _create_trip(client, uuid.uuid4()).status_code == 404  # missing

    assert _trip_audit_rows() == []


@requires_postgres
@pytest.mark.parametrize("actor", ["member", "instructor_unrelated"])
def test_unauthorized_trip_creation_records_nothing(client: TestClient, actor: str) -> None:
    world = _world()
    _authenticate_as(getattr(world, actor))

    assert _create_trip(client, world.event_id).status_code == 404

    assert _trip_audit_rows() == []
    with session_scope() as session:
        assert session.get(Trip, world.event_id) is None


# --- B. fail-closed ---------------------------------------------------------------------


@requires_postgres
def test_trip_is_not_committed_when_the_audit_insert_fails() -> None:
    """A real database-level audit failure (the actor FK of `audit_logs`
    rejects an unknown user) rolls the Trip back with it."""
    world = _world()
    with session_scope() as session:
        event = session.get(Event, world.event_id)
        assert event is not None
        with pytest.raises(IntegrityError):
            trips_service.create_trip(session, event=event, actor_user_id=uuid.uuid4())

    with session_scope() as session:
        assert session.get(Trip, world.event_id) is None
    assert _trip_audit_rows() == []


@requires_postgres
def test_trip_creation_api_fails_closed_when_audit_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world()
    _authenticate_as(world.admin)

    def _failing_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(trips_service, "record_audit_event", _failing_audit)
    failing_client = TestClient(app, raise_server_exceptions=False)
    response = _create_trip(failing_client, world.event_id)

    assert response.status_code == 500
    with session_scope() as session:
        assert session.get(Trip, world.event_id) is None
    assert _trip_audit_rows() == []


@requires_postgres
def test_actual_participation_is_not_committed_when_the_audit_insert_fails() -> None:
    world = _world(status="in_progress", with_trip=True)
    with session_scope() as session:
        event = session.get(Event, world.event_id)
        assert event is not None
        with pytest.raises(IntegrityError):
            trips_service.record_actual_participation(
                session,
                event=event,
                person_id=world.member_person,
                actual_participation=True,
                actor_user_id=uuid.uuid4(),
            )

    assert _stored_value(world.member_participation_id) is None
    assert _trip_audit_rows() == []


@requires_postgres
def test_actual_participation_change_is_not_committed_when_the_audit_insert_fails(
    client: TestClient,
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, True).status_code == 200

    with session_scope() as session:
        event = session.get(Event, world.event_id)
        assert event is not None
        with pytest.raises(IntegrityError):
            trips_service.record_actual_participation(
                session,
                event=event,
                person_id=world.member_person,
                actual_participation=False,
                actor_user_id=uuid.uuid4(),
            )

    assert _stored_value(world.member_participation_id) is True
    assert [row.action for row in _trip_audit_rows()] == [
        "trip_participant.actual_participation_recorded"
    ]


@requires_postgres
def test_recording_api_fails_closed_when_audit_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, True).status_code == 200

    def _failing_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(trips_service, "record_audit_event", _failing_audit)
    failing_client = TestClient(app, raise_server_exceptions=False)

    assert _record(failing_client, world.event_id, world.member_person, False).status_code == 500
    assert _record(failing_client, world.event_id, world.other_person, True).status_code == 500

    assert _stored_value(world.member_participation_id) is True
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(TripParticipant)).scalar_one() == 1
    assert len(_trip_audit_rows()) == 1


# --- C. first record --------------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("value", [True, False])
def test_first_actual_participation_records_recorded_action(
    client: TestClient, value: bool
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, value).status_code == 200

    (row,) = _trip_audit_rows()
    assert row.action == "trip_participant.actual_participation_recorded"
    assert row.resource_type == "trip_participant"
    assert row.resource_id == world.member_participation_id
    assert row.actor_type == "user"
    assert row.actor_user_id == world.admin
    assert row.club_id == world.club_id
    assert row.outcome == "success"
    assert row.details == {
        "event_id": str(world.event_id),
        "event_participation_id": str(world.member_participation_id),
        "person_id": str(world.member_person),
        "actual_participation": value,
    }


@requires_postgres
def test_first_record_on_completed_event_is_audited(client: TestClient) -> None:
    world = _world(status="completed", with_trip=True)
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, True).status_code == 200

    assert [row.action for row in _trip_audit_rows()] == [
        "trip_participant.actual_participation_recorded"
    ]


# --- D. change ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize(("first", "second"), [(False, True), (True, False)])
def test_change_records_changed_action_with_before_and_after(
    client: TestClient, first: bool, second: bool
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, first).status_code == 200

    _authenticate_as(world.instructor_events)
    assert _record(client, world.event_id, world.member_person, second).status_code == 200

    recorded, changed = _trip_audit_rows()
    assert recorded.action == "trip_participant.actual_participation_recorded"
    assert recorded.actor_user_id == world.admin
    assert changed.action == "trip_participant.actual_participation_changed"
    assert changed.resource_type == "trip_participant"
    assert changed.resource_id == world.member_participation_id
    assert changed.actor_type == "user"
    assert changed.actor_user_id == world.instructor_events
    assert changed.details == {
        "event_id": str(world.event_id),
        "event_participation_id": str(world.member_participation_id),
        "person_id": str(world.member_person),
        "changes": {"actual_participation": {"from": first, "to": second}},
    }
    assert _stored_value(world.member_participation_id) is second


@requires_postgres
def test_every_real_change_is_audited_once(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    for value in (True, False, True):
        assert _record(client, world.event_id, world.member_person, value).status_code == 200

    rows = _trip_audit_rows()
    assert [row.action for row in rows] == [
        "trip_participant.actual_participation_recorded",
        "trip_participant.actual_participation_changed",
        "trip_participant.actual_participation_changed",
    ]
    assert [row.details["changes"]["actual_participation"] for row in rows[1:]] == [  # type: ignore[index]
        {"from": True, "to": False},
        {"from": False, "to": True},
    ]


# --- E. idempotency ----------------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("value", [True, False])
def test_same_value_write_records_nothing_in_progress(client: TestClient, value: bool) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, value).status_code == 200
    with session_scope() as session:
        before = session.get(TripParticipant, world.member_participation_id)
        assert before is not None
        updated_at = before.updated_at

    repeated = _record(client, world.event_id, world.member_person, value)

    assert repeated.status_code == 200
    assert repeated.json()["actual_participation"] is value
    assert [row.action for row in _trip_audit_rows()] == [
        "trip_participant.actual_participation_recorded"
    ]
    with session_scope() as session:
        after = session.get(TripParticipant, world.member_participation_id)
        assert after is not None
        assert after.updated_at == updated_at


@requires_postgres
@pytest.mark.parametrize("value", [True, False])
def test_same_value_write_records_nothing_on_completed_event(
    client: TestClient, value: bool
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, value).status_code == 200
    _set_event_status(world.event_id, "completed")

    assert _record(client, world.event_id, world.member_person, value).status_code == 200
    changed = _record(client, world.event_id, world.member_person, not value)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "trip_participant_historically_closed"

    assert len(_trip_audit_rows()) == 1
    assert _stored_value(world.member_participation_id) is value


# --- F/G. registration lifecycle separation ----------------------------------------------


@requires_postgres
def test_recording_never_emits_registration_audit(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, True).status_code == 200
    assert _record(client, world.event_id, world.member_person, False).status_code == 200

    assert _audit_rows("event_participation.status_changed") == []


@requires_postgres
def test_registration_cancellation_keeps_its_own_audit_and_the_tourism_fact(
    client: TestClient,
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.admin)
    assert _record(client, world.event_id, world.member_person, True).status_code == 200
    audit_before = {row.id for row in _audit_rows()}

    _authenticate_as(world.member)
    withdrawn = client.delete(
        f"/api/v1/events/{world.event_id}/participation", headers=_csrf_headers(client)
    )
    assert withdrawn.status_code == 204

    new_rows = [row for row in _audit_rows() if row.id not in audit_before]
    assert [row.action for row in new_rows] == ["event_participation.status_changed"]
    assert new_rows[0].resource_type == "event_participation"
    assert new_rows[0].resource_id == world.member_participation_id
    assert new_rows[0].actor_user_id == world.member
    assert new_rows[0].details == {
        "changes": {"registration_status": {"from": "registered", "to": "cancelled"}}
    }

    with session_scope() as session:
        participation = session.get(EventParticipation, world.member_participation_id)
        assert participation is not None
        assert participation.registration_status == "cancelled"
    assert _stored_value(world.member_participation_id) is True
    assert len(_trip_audit_rows()) == 1


@requires_postgres
def test_fact_recorded_after_cancellation_is_audited_like_any_first_record(
    client: TestClient,
) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(world.member)
    assert (
        client.delete(
            f"/api/v1/events/{world.event_id}/participation", headers=_csrf_headers(client)
        ).status_code
        == 204
    )
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, True).status_code == 200

    (row,) = _trip_audit_rows()
    assert row.action == "trip_participant.actual_participation_recorded"
    assert row.details["actual_participation"] is True  # type: ignore[index]
    assert "registration_status" not in row.details  # type: ignore[operator]


# --- I. rejected requests ------------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("actor", ["member", "instructor_unrelated"])
def test_unauthorized_recording_records_nothing(client: TestClient, actor: str) -> None:
    world = _world(status="in_progress", with_trip=True)
    _authenticate_as(getattr(world, actor))

    assert _record(client, world.event_id, world.member_person, True).status_code == 404

    assert _trip_audit_rows() == []
    assert _stored_value(world.member_participation_id) is None


@requires_postgres
def test_rejected_recording_records_nothing(client: TestClient) -> None:
    world = _world(status="published", with_trip=True)
    _authenticate_as(world.admin)

    assert _record(client, world.event_id, world.member_person, True).status_code == 409
    _set_event_status(world.event_id, "in_progress")
    assert _record(client, world.event_id, world.unregistered_person, True).status_code == 422

    assert _trip_audit_rows() == []


@requires_postgres
def test_unauthenticated_requests_record_nothing(client: TestClient) -> None:
    world = _world(status="in_progress", with_trip=True)

    assert _create_trip(client, world.lesson_event_id).status_code == 401
    assert _record(client, world.event_id, world.member_person, True).status_code == 401

    assert _trip_audit_rows() == []


# --- J. migration ----------------------------------------------------------------------------


def _db_accepts(action: str) -> bool:
    """Raw insert, bypassing the Python vocabulary check, so only the
    database's own CHECK constraint decides. Always rolled back."""
    with session_scope() as session:
        session.add(AuditLog(actor_type="system", action=action, outcome="success"))
        try:
            session.flush()
        except IntegrityError:
            return False
        finally:
            session.rollback()
        return True


@requires_postgres
def test_migration_downgrade_and_upgrade_switch_the_trip_actions(database_url: str) -> None:
    assert all(_db_accepts(action) for action in _TRIP_ACTIONS)
    try:
        downgrade = run_alembic(
            "downgrade", _TRIP_AUDIT_MIGRATION_PARENT, database_url=database_url
        )
        assert downgrade.returncode == 0, downgrade.stderr
        assert not any(_db_accepts(action) for action in _TRIP_ACTIONS)
        # Every pre-existing action is still accepted.
        assert _db_accepts("event_participation.status_changed")
        assert _db_accepts("membership.import.applied")
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert all(_db_accepts(action) for action in _TRIP_ACTIONS)
