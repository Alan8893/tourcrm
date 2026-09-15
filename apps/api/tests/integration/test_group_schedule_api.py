"""HTTP-level integration tests for `GET /api/v1/groups/{group_id}/schedule`
(Issue #88 / TH-0083, resolved by ODR-0002): canonical scope authorization
(`all`/`own_groups`/`own_events`/`self`/`children`/`none`), GroupMembership-
vs-EventGroupTarget qualification, future-only `self`/`children`, archived
Group behavior, recurring EventOccurrence GroupTarget, cross-Club IDOR,
pagination/counting after authorization, `[from,to)` boundaries and
deterministic ordering.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_events_calendar_api.py's own
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

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import EventOccurrenceGroupTarget
from app.db.events import Event, EventGroupTarget
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 15, 0, 0, tzinfo=datetime.timezone.utc)


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
        "last_name": "Ivanova",
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


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Group {uuid.uuid4().hex[:8]}",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


def _make_group_membership(
    group: Group, club_membership: ClubMembership, **overrides: object
) -> GroupMembership:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": club_membership.id,
        "membership_status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GroupMembership(**defaults)  # type: ignore[arg-type]


def _make_group_instructor_assignment(
    group: Group, user: User, **overrides: object
) -> GroupInstructorAssignment:
    defaults: dict[str, object] = {
        "group_id": group.id,
        "user_id": user.id,
        "role_in_group": "instructor",
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return GroupInstructorAssignment(**defaults)  # type: ignore[arg-type]


def _make_event(club: Club, **overrides: object) -> Event:
    start_at = overrides.pop("start_at", _START + datetime.timedelta(hours=1))
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Test event",
        "start_at": start_at,
        "end_at": start_at + datetime.timedelta(hours=1),  # type: ignore[operator]
        "timezone": "UTC",
        "status": "published",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_event_group_target(event: Event, group: Group, **overrides: object) -> EventGroupTarget:
    defaults: dict[str, object] = {
        "event_id": event.id,
        "group_id": group.id,
        "valid_from": _utc(2020, 1, 1),
    }
    defaults.update(overrides)
    return EventGroupTarget(**defaults)  # type: ignore[arg-type]


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
    starts_at = overrides.pop("starts_at", _START + datetime.timedelta(hours=1))
    defaults: dict[str, object] = dict(
        series_id=series.id,
        club_id=club_id,
        name="Lesson",
        event_type="lesson",
        recurrence_anchor_at=starts_at,
        starts_at=starts_at,
        ends_at=starts_at + datetime.timedelta(hours=1),  # type: ignore[operator]
        timezone="UTC",
        status="scheduled",
    )
    defaults.update(overrides)
    return EventOccurrence(**defaults)  # type: ignore[arg-type]


def _make_occurrence_group_target(
    occurrence: EventOccurrence, group: Group, **overrides: object
) -> EventOccurrenceGroupTarget:
    defaults: dict[str, object] = {
        "occurrence_id": occurrence.id,
        "group_id": group.id,
        "valid_from": _utc(2020, 1, 1),
        "is_override": False,
    }
    defaults.update(overrides)
    return EventOccurrenceGroupTarget(**defaults)  # type: ignore[arg-type]


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


def _range(from_dt: datetime.datetime, to_dt: datetime.datetime) -> dict:
    return {"from": from_dt.isoformat(), "to": to_dt.isoformat()}


# A wide default range spanning both past and future relative to `_START`,
# used by tests that create both historical and future items.
_WIDE_RANGE = _range(
    _START - datetime.timedelta(days=365), _START + datetime.timedelta(days=365)
)


def _schedule(client: TestClient, group_id: uuid.UUID, params: dict | None = None):
    return client.get(
        f"/api/v1/groups/{group_id}/schedule", params={**_WIDE_RANGE, **(params or {})}
    )


# --- `all` -------------------------------------------------------------


@requires_postgres
def test_all_scope_sees_targeted_event_within_permitted_club(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        club_id, user_id, group_id, event_id = club.id, user.id, group.id, event.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) in ids


@requires_postgres
def test_all_scope_cross_club_group_is_hidden(client: TestClient) -> None:
    with session_scope() as session:
        allowed_club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([allowed_club, other_club, person, user])
        session.commit()
        other_group = _make_group(other_club)
        session.add(other_group)
        session.commit()
        allowed_club_id, user_id, other_group_id = allowed_club.id, user.id, other_group.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=allowed_club_id)
    _authenticate_as(user_id)

    response = _schedule(client, other_group_id)
    assert response.status_code == 404


# --- `own_groups` --------------------------------------------------------


@requires_postgres
def test_own_groups_scope_allows_active_instructor(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        session.commit()
        club_id, user_id, group_id, event_id = club.id, user.id, group.id, event.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(event_id) in ids


@requires_postgres
def test_own_groups_scope_denies_without_instructor_assignment(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        # No GroupInstructorAssignment for `user`.
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 404


# --- `own_events` --------------------------------------------------------


@requires_postgres
def test_own_events_scope_does_not_grant_group_schedule_access(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        event = _make_event(club)
        session.add(event)
        session.commit()
        session.add(_make_event_group_target(event, group))
        # Even a real EventStaffAssignment relationship elsewhere would not
        # matter here — own_events is never checked by Group Schedule at all.
        session.commit()
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.read", scope_type="own_events", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 404


# --- `self` --------------------------------------------------------------


@requires_postgres
def test_self_scope_allows_active_member_with_future_group_target(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()
        future_event = _make_event(club, start_at=_START + datetime.timedelta(days=10))
        session.add(future_event)
        session.commit()
        session.add(_make_event_group_target(future_event, group))
        session.commit()
        club_id, user_id, group_id, future_event_id = (
            club.id,
            user.id,
            group.id,
            future_event.id,
        )
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id, {"from": (_START).isoformat()})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(future_event_id) in ids


@requires_postgres
def test_self_scope_denies_non_member(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        # No GroupMembership for `person` at all.
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 404


@requires_postgres
def test_self_scope_excludes_historical_events(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()
        past_event = _make_event(club, start_at=_START - datetime.timedelta(days=10))
        session.add(past_event)
        session.commit()
        session.add(_make_event_group_target(past_event, group))
        session.commit()
        club_id, user_id, group_id, past_event_id = club.id, user.id, group.id, past_event.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(past_event_id) not in ids


@requires_postgres
def test_self_scope_excludes_events_after_membership_ended(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        # Membership has already ended (currently inactive), even though
        # the group-targeted event is still in the future.
        session.add(
            _make_group_membership(
                group,
                club_membership,
                membership_status="ended",
                valid_from=_utc(2020, 1, 1),
                valid_to=_START - datetime.timedelta(days=1),
            )
        )
        session.commit()
        future_event = _make_event(club, start_at=_START + datetime.timedelta(days=10))
        session.add(future_event)
        session.commit()
        session.add(_make_event_group_target(future_event, group))
        session.commit()
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 404


# --- `children` -----------------------------------------------------------


@requires_postgres
def test_children_scope_allows_eligible_child_member_with_future_group_target(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        session.add_all([club, guardian_person, guardian_user, child_person])
        session.commit()
        guardian_membership = _make_club_membership(club, guardian_person)
        child_membership = _make_club_membership(club, child_person)
        session.add_all([guardian_membership, child_membership])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, child_membership))
        session.commit()
        future_event = _make_event(club, start_at=_START + datetime.timedelta(days=10))
        session.add(future_event)
        session.commit()
        session.add(_make_event_group_target(future_event, group))
        session.commit()
        club_id, guardian_user_id, group_id, future_event_id = (
            club.id,
            guardian_user.id,
            group.id,
            future_event.id,
        )
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _schedule(client, group_id, {"from": (_START).isoformat()})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(future_event_id) in ids


@requires_postgres
def test_children_scope_denies_unrelated_child_group(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        unrelated_child = _make_person()
        session.add_all([club, guardian_person, guardian_user, unrelated_child])
        session.commit()
        guardian_membership = _make_club_membership(club, guardian_person)
        unrelated_membership = _make_club_membership(club, unrelated_child)
        session.add_all([guardian_membership, unrelated_membership])
        session.commit()
        # No GuardianRelationship at all between guardian and this child.
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, unrelated_membership))
        session.commit()
        club_id, guardian_user_id, group_id = club.id, guardian_user.id, group.id
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 404


@requires_postgres
def test_children_scope_excludes_historical_events(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        session.add_all([club, guardian_person, guardian_user, child_person])
        session.commit()
        guardian_membership = _make_club_membership(club, guardian_person)
        child_membership = _make_club_membership(club, child_person)
        session.add_all([guardian_membership, child_membership])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, child_membership))
        session.commit()
        past_event = _make_event(club, start_at=_START - datetime.timedelta(days=10))
        session.add(past_event)
        session.commit()
        session.add(_make_event_group_target(past_event, group))
        session.commit()
        club_id, guardian_user_id, group_id, past_event_id = (
            club.id,
            guardian_user.id,
            group.id,
            past_event.id,
        )
    _grant_permission(guardian_user_id, "event.read", scope_type="children", club_id=club_id)
    _authenticate_as(guardian_user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(past_event_id) not in ids


# --- `none` ---------------------------------------------------------------


@requires_postgres
def test_none_scope_denies_everything(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.read", scope_type="none", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 404


# --- GroupMembership is never an EventGroupTarget -------------------------


@requires_postgres
def test_active_membership_never_exposes_an_untargeted_event(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()
        # A club event that exists but has NO EventGroupTarget for `group`.
        untargeted_event = _make_event(club, start_at=_START + datetime.timedelta(days=10))
        session.add(untargeted_event)
        session.commit()
        club_id, user_id, group_id, untargeted_event_id = (
            club.id,
            user.id,
            group.id,
            untargeted_event.id,
        )
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id, {"from": (_START).isoformat()})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(untargeted_event_id) not in ids


# --- Archived Group --------------------------------------------------------


@requires_postgres
def test_archived_group_remains_readable_for_own_groups_historical_query(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club, status="archived")
        session.add(group)
        session.commit()
        session.add(_make_group_instructor_assignment(group, user))
        past_event = _make_event(club, start_at=_START - datetime.timedelta(days=10))
        session.add(past_event)
        session.commit()
        session.add(_make_event_group_target(past_event, group))
        session.commit()
        club_id, user_id, group_id, past_event_id = club.id, user.id, group.id, past_event.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(past_event_id) in ids


@requires_postgres
def test_archived_group_still_future_only_for_self_scope(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club, status="archived")
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.commit()
        past_event = _make_event(club, start_at=_START - datetime.timedelta(days=10))
        session.add(past_event)
        session.commit()
        session.add(_make_event_group_target(past_event, group))
        session.commit()
        club_id, user_id, group_id, past_event_id = club.id, user.id, group.id, past_event.id
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(past_event_id) not in ids


# --- Recurring EventOccurrence GroupTarget ----------------------------------


@requires_postgres
def test_all_scope_sees_recurring_occurrence_with_group_target(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        series = _make_series(session, club_id=club.id)
        occ = _make_occurrence(series=series, club_id=club.id)
        session.add(occ)
        session.commit()
        session.add(_make_occurrence_group_target(occ, group))
        session.commit()
        club_id, user_id, group_id, occ_id = club.id, user.id, group.id, occ.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(occ_id) in ids


@requires_postgres
def test_self_scope_sees_only_occurrence_with_group_target_not_untargeted_one(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_membership = _make_club_membership(club, person)
        session.add(club_membership)
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        series = _make_series(session, club_id=club.id)
        targeted = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(days=10)
        )
        untargeted = _make_occurrence(
            series=series, club_id=club.id, starts_at=_START + datetime.timedelta(days=10, hours=3)
        )
        session.add_all([targeted, untargeted])
        session.commit()
        session.add(_make_occurrence_group_target(targeted, group))
        session.commit()
        club_id, user_id, group_id, targeted_id, untargeted_id = (
            club.id,
            user.id,
            group.id,
            targeted.id,
            untargeted.id,
        )
    _grant_permission(user_id, "event.read", scope_type="self", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(targeted_id) in ids
    assert str(untargeted_id) not in ids


# --- Cross-Club IDOR ---------------------------------------------------------


@requires_postgres
def test_own_groups_instructor_of_a_different_club_group_is_hidden(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, other_club, person, user])
        session.commit()
        other_group = _make_group(other_club)
        session.add(other_group)
        session.commit()
        # Instructor assignment for a Group in a Club the requester has no
        # event.read grant for at all.
        session.add(_make_group_instructor_assignment(other_group, user))
        session.commit()
        club_id, user_id, other_group_id = club.id, user.id, other_group.id
    _grant_permission(user_id, "event.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, other_group_id)
    assert response.status_code == 404


@requires_postgres
def test_nonexistent_group_returns_the_same_404_as_unauthorized(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        club_id, user_id = club.id, user.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, uuid.uuid4())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "group_not_found"


# --- Pagination / counting after authorization + [from,to) + ordering -----


@requires_postgres
def test_pagination_and_counting_happen_after_group_target_filtering(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        other_group = _make_group(club)
        session.add_all([group, other_group])
        session.commit()
        targeted_ids = []
        for i in range(3):
            event = _make_event(club, start_at=_START + datetime.timedelta(hours=i + 1))
            session.add(event)
            session.commit()
            session.add(_make_event_group_target(event, group))
            session.commit()
            targeted_ids.append(str(event.id))
        # Two more events targeted to a DIFFERENT group — must never count.
        for i in range(2):
            other_event = _make_event(club, start_at=_START + datetime.timedelta(hours=i + 1))
            session.add(other_event)
            session.commit()
            session.add(_make_event_group_target(other_event, other_group))
            session.commit()
        club_id, user_id, group_id = club.id, user.id, group.id
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id, {"page": 1, "page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["pages"] == 2
    assert len(body["items"]) == 2
    seen_ids = {item["id"] for item in body["items"]}
    assert seen_ids.issubset(set(targeted_ids))


@requires_postgres
def test_from_to_boundary_is_half_open(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        at_from = _make_event(club, start_at=_START)
        at_to = _make_event(club, start_at=_START + datetime.timedelta(days=1))
        session.add_all([at_from, at_to])
        session.commit()
        session.add(_make_event_group_target(at_from, group))
        session.add(_make_event_group_target(at_to, group))
        session.commit()
        club_id, user_id, group_id, at_from_id, at_to_id = (
            club.id,
            user.id,
            group.id,
            at_from.id,
            at_to.id,
        )
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(
        client,
        group_id,
        {"from": _START.isoformat(), "to": (_START + datetime.timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(at_from_id) in ids
    assert str(at_to_id) not in ids


@requires_postgres
def test_results_are_sorted_by_start_at_then_id(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        group = _make_group(club)
        session.add(group)
        session.commit()
        later = _make_event(club, start_at=_START + datetime.timedelta(hours=5), title="later")
        earlier = _make_event(club, start_at=_START + datetime.timedelta(hours=1), title="earlier")
        session.add_all([later, earlier])
        session.commit()
        session.add(_make_event_group_target(later, group))
        session.add(_make_event_group_target(earlier, group))
        session.commit()
        club_id, user_id, group_id, later_id, earlier_id = (
            club.id,
            user.id,
            group.id,
            later.id,
            earlier.id,
        )
    _grant_permission(user_id, "event.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _schedule(client, group_id)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert ids.index(str(earlier_id)) < ids.index(str(later_id))
