"""Guardian event visibility — `event.read(children)` (TH-0172 / Issue #213).

Canonical sources: docs/03-architecture/adr/ADR-0042-guardian-event-
visibility.md, docs/03-architecture/adr/ADR-0043-guardian-event-read-grant-
and-occurrence-visibility.md, docs/02-requirements/role-permission-scope-
matrix.md §7.1.

Unlike the older `children` tests (which grant `event.read(children)` on an
ad hoc test role), every test here runs through the REAL, migration-seeded
`guardian → event.read → children` grant (migration 127da2741f20) on the
canonical `guardian` role, so a missing seed can never hide behind a test
fixture.

One shared world (`_build_world`) is authorized through four read
surfaces — `GET /events`, `GET /events/calendar`, `GET /events/{id}` and
`GET /events/occurrences/{id}` — and each test asserts what the canonical
policy requires:

- group path: child → active GroupMembership → Group → (occurrence) group
  target → Event/EventOccurrence, with NO participation required;
- direct path: child → EventParticipation / EventOccurrenceParticipant;
- UNION across all of the Guardian's children;
- fail closed without the relationship, without the grant, and for a
  mere `club_id` match;
- query parameters only narrow, never expand, the authorized set.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.authorization.service import applicable_grants
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.event_recurrence_relationships import (
    EventOccurrenceGroupTarget,
    EventOccurrenceParticipant,
)
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.events.materialization import materialize_occurrences
from app.events.series_relationships import create_series_group_target
from app.main import app

from .conftest import requires_postgres

_START = datetime.datetime(2026, 9, 15, 0, 0, tzinfo=datetime.timezone.utc)
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_CALENDAR_RANGE = {
    "from": _START.isoformat(),
    "to": (_START + datetime.timedelta(days=7)).isoformat(),
}


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories ----------------------------------------------------------------


def _person(session) -> Person:  # type: ignore[no-untyped-def]
    person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _club_membership(session, club: Club, person: Person) -> ClubMembership:  # type: ignore[no-untyped-def]
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="student",
        status="active",
        joined_at=_LONG_AGO,
    )
    session.add(membership)
    session.flush()
    return membership


def _guardian_user(session, club: Club) -> tuple[Person, User]:  # type: ignore[no-untyped-def]
    """A real Guardian: an active ClubMembership plus an assignment of the
    canonical, migration-seeded `guardian` role — no ad hoc grant."""
    person = _person(session)
    _club_membership(session, club, person)
    user = User(
        person=person,
        login_identifier=f"guardian-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    guardian_role = session.execute(select(Role).where(Role.code == "guardian")).scalar_one()
    session.add(UserRoleAssignment(user_id=user.id, role_id=guardian_role.id, club_id=club.id))
    session.flush()
    return person, user


def _relationship(session, guardian: Person, child: Person, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "valid_from": _LONG_AGO,
    }
    values.update(overrides)
    session.add(GuardianRelationship(**values))  # type: ignore[arg-type]
    session.flush()


def _group(session, club: Club) -> Group:  # type: ignore[no-untyped-def]
    group = Group(
        club_id=club.id,
        name=f"Group {uuid.uuid4().hex[:8]}",
        status="active",
        valid_from=_LONG_AGO,
    )
    session.add(group)
    session.flush()
    return group


def _group_membership(  # type: ignore[no-untyped-def]
    session, group: Group, club_membership: ClubMembership, **overrides: object
) -> None:
    values: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": club_membership.id,
        "membership_status": "active",
        "valid_from": _LONG_AGO,
    }
    values.update(overrides)
    session.add(GroupMembership(**values))  # type: ignore[arg-type]
    session.flush()


def _event(session, club: Club, title: str, hours: int) -> Event:  # type: ignore[no-untyped-def]
    """ADR-0033: an ordinary Event always has its one linked occurrence."""
    start = _START + datetime.timedelta(hours=hours)
    event = Event(
        id=uuid.uuid4(),
        club_id=club.id,
        event_type="lesson",
        title=title,
        start_at=start,
        end_at=start + datetime.timedelta(hours=1),
        timezone="UTC",
        status="published",
    )
    session.add(event)
    session.flush()
    session.add(
        EventOccurrence(
            event_id=event.id,
            series_id=None,
            club_id=club.id,
            name=title,
            event_type="lesson",
            recurrence_anchor_at=start,
            starts_at=start,
            ends_at=start + datetime.timedelta(hours=1),
            timezone="UTC",
            status="scheduled",
        )
    )
    session.flush()
    return event


def _series(session, club: Club) -> EventSeries:  # type: ignore[no-untyped-def]
    series_id = uuid.uuid4()
    series = EventSeries(
        id=series_id,
        root_series_id=series_id,
        supersedes_series_id=None,
        version=1,
        club_id=club.id,
        name="Weekly lesson",
        event_type="lesson",
        series_start_at=_START,
        duration_minutes=60,
        recurrence_rule="FREQ=DAILY",
        timezone="UTC",
        status="active",
    )
    session.add(series)
    session.flush()
    return series


def _occurrence(session, series: EventSeries, name: str, hours: int) -> EventOccurrence:  # type: ignore[no-untyped-def]
    start = _START + datetime.timedelta(hours=hours)
    occurrence = EventOccurrence(
        series_id=series.id,
        club_id=series.club_id,
        name=name,
        event_type="lesson",
        recurrence_anchor_at=start,
        starts_at=start,
        ends_at=start + datetime.timedelta(hours=1),
        timezone="UTC",
        status="scheduled",
    )
    session.add(occurrence)
    session.flush()
    return occurrence


def _occurrence_group_target(  # type: ignore[no-untyped-def]
    session, occurrence: EventOccurrence, group: Group, **overrides: object
) -> None:
    values: dict[str, object] = {
        "occurrence_id": occurrence.id,
        "group_id": group.id,
        "valid_from": _LONG_AGO,
        "is_override": False,
    }
    values.update(overrides)
    session.add(EventOccurrenceGroupTarget(**values))  # type: ignore[arg-type]
    session.flush()


def _occurrence_participant(session, occurrence: EventOccurrence, person: Person) -> None:  # type: ignore[no-untyped-def]
    session.add(
        EventOccurrenceParticipant(
            occurrence_id=occurrence.id,
            person_id=person.id,
            registration_status="registered",
            valid_from=_LONG_AGO,
            is_override=False,
        )
    )
    session.flush()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


# --- the shared world -----------------------------------------------------------


@dataclass(frozen=True)
class World:
    club_id: uuid.UUID
    guardian_user_id: uuid.UUID
    unrelated_guardian_user_id: uuid.UUID
    child_a_id: uuid.UUID
    group_a_id: uuid.UUID
    group_b_id: uuid.UUID
    group_x_id: uuid.UUID
    staff_user_id: uuid.UUID
    # Ordinary Events.
    ev_group_a: uuid.UUID  # targets child A's group; no participation
    ev_group_b: uuid.UUID  # targets child B's group; no participation
    ev_group_x: uuid.UUID  # targets a group neither child belongs to
    ev_direct_a: uuid.UUID  # no group target; child A participates directly
    ev_unrelated: uuid.UUID  # same club, no relationship at all
    ev_expired_target: uuid.UUID  # child A's group, but the target has ended
    ev_inactive_membership: uuid.UUID  # targets a group child A has left
    # Recurring EventOccurrences.
    occ_group_a: uuid.UUID  # occurrence group target = child A's group
    occ_group_x: uuid.UUID  # occurrence group target = a foreign group
    occ_direct_b: uuid.UUID  # no group target; child B is a participant
    occ_unrelated: uuid.UUID  # same club, same series, no relationship
    occ_expired_target: uuid.UUID  # child A's group, target has ended

    @property
    def visible_events(self) -> set[uuid.UUID]:
        return {self.ev_group_a, self.ev_group_b, self.ev_direct_a}

    @property
    def hidden_events(self) -> set[uuid.UUID]:
        return {
            self.ev_group_x,
            self.ev_unrelated,
            self.ev_expired_target,
            self.ev_inactive_membership,
        }

    @property
    def visible_occurrences(self) -> set[uuid.UUID]:
        return {self.occ_group_a, self.occ_direct_b}

    @property
    def hidden_occurrences(self) -> set[uuid.UUID]:
        return {self.occ_group_x, self.occ_unrelated, self.occ_expired_target}


def _build_world() -> World:
    ended = _START - datetime.timedelta(days=30)
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()

        guardian, guardian_user = _guardian_user(session, club)
        _, unrelated_guardian_user = _guardian_user(session, club)

        child_a = _person(session)
        child_b = _person(session)
        child_a_membership = _club_membership(session, club, child_a)
        child_b_membership = _club_membership(session, club, child_b)
        _relationship(session, guardian, child_a)
        _relationship(session, guardian, child_b)

        group_a = _group(session, club)
        group_b = _group(session, club)
        group_x = _group(session, club)
        group_left = _group(session, club)
        _group_membership(session, group_a, child_a_membership)
        _group_membership(session, group_b, child_b_membership)
        _group_membership(
            session,
            group_left,
            child_a_membership,
            membership_status="ended",
            valid_to=ended,
        )

        staff_person = _person(session)
        staff_user = User(
            person=staff_person,
            login_identifier=f"staff-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add(staff_user)
        session.flush()

        ev_group_a = _event(session, club, "Group A training", 1)
        ev_group_b = _event(session, club, "Group B training", 2)
        ev_group_x = _event(session, club, "Group X training", 3)
        ev_direct_a = _event(session, club, "Child A competition", 4)
        ev_unrelated = _event(session, club, "Unrelated event", 5)
        ev_expired_target = _event(session, club, "Group A, expired target", 6)
        ev_inactive_membership = _event(session, club, "Former group", 7)
        session.add_all(
            [
                EventGroupTarget(event_id=ev_group_a.id, group_id=group_a.id, valid_from=_LONG_AGO),
                EventGroupTarget(event_id=ev_group_b.id, group_id=group_b.id, valid_from=_LONG_AGO),
                EventGroupTarget(event_id=ev_group_x.id, group_id=group_x.id, valid_from=_LONG_AGO),
                EventGroupTarget(
                    event_id=ev_expired_target.id,
                    group_id=group_a.id,
                    valid_from=_LONG_AGO,
                    valid_to=ended,
                ),
                EventGroupTarget(
                    event_id=ev_inactive_membership.id,
                    group_id=group_left.id,
                    valid_from=_LONG_AGO,
                ),
                EventParticipation(
                    event_id=ev_direct_a.id,
                    person_id=child_a.id,
                    registration_status="registered",
                ),
                # A staff assignment on an event the Guardian must not see
                # (used by the "filters only narrow" test).
                EventStaffAssignment(
                    event_id=ev_unrelated.id,
                    user_id=staff_user.id,
                    role_in_event="instructor",
                    valid_from=_LONG_AGO,
                ),
            ]
        )
        session.flush()

        series = _series(session, club)
        occ_group_a = _occurrence(session, series, "Recurring A", 10)
        occ_group_x = _occurrence(session, series, "Recurring X", 11)
        occ_direct_b = _occurrence(session, series, "Recurring B direct", 12)
        occ_unrelated = _occurrence(session, series, "Recurring unrelated", 13)
        occ_expired_target = _occurrence(session, series, "Recurring A expired", 14)
        _occurrence_group_target(session, occ_group_a, group_a)
        _occurrence_group_target(session, occ_group_x, group_x)
        _occurrence_group_target(session, occ_expired_target, group_a, valid_to=ended)
        _occurrence_participant(session, occ_direct_b, child_b)
        session.commit()

        return World(
            club_id=club.id,
            guardian_user_id=guardian_user.id,
            unrelated_guardian_user_id=unrelated_guardian_user.id,
            child_a_id=child_a.id,
            group_a_id=group_a.id,
            group_b_id=group_b.id,
            group_x_id=group_x.id,
            staff_user_id=staff_user.id,
            ev_group_a=ev_group_a.id,
            ev_group_b=ev_group_b.id,
            ev_group_x=ev_group_x.id,
            ev_direct_a=ev_direct_a.id,
            ev_unrelated=ev_unrelated.id,
            ev_expired_target=ev_expired_target.id,
            ev_inactive_membership=ev_inactive_membership.id,
            occ_group_a=occ_group_a.id,
            occ_group_x=occ_group_x.id,
            occ_direct_b=occ_direct_b.id,
            occ_unrelated=occ_unrelated.id,
            occ_expired_target=occ_expired_target.id,
        )


# --- read-surface helpers ---------------------------------------------------------


def _list_ids(client: TestClient, **params: object) -> set[uuid.UUID]:
    response = client.get("/api/v1/events", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return {uuid.UUID(item["id"]) for item in response.json()["items"]}


def _calendar(client: TestClient, **params: object) -> dict[str, set[uuid.UUID]]:
    response = client.get(
        "/api/v1/events/calendar", params={**_CALENDAR_RANGE, "page_size": 100, **params}
    )
    assert response.status_code == 200, response.text
    result: dict[str, set[uuid.UUID]] = {"event": set(), "occurrence": set()}
    for item in response.json()["items"]:
        result[item["kind"]].add(uuid.UUID(item["id"]))
    return result


def _event_detail_ok(client: TestClient, event_id: uuid.UUID) -> bool:
    status = client.get(f"/api/v1/events/{event_id}").status_code
    assert status in (200, 404)
    return status == 200


def _occurrence_detail_ok(client: TestClient, occurrence_id: uuid.UUID) -> bool:
    status = client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code
    assert status in (200, 404)
    return status == 200


def _assert_event_visible_everywhere(client: TestClient, event_id: uuid.UUID) -> None:
    assert event_id in _list_ids(client)
    assert event_id in _calendar(client)["event"]
    assert _event_detail_ok(client, event_id)


def _assert_event_hidden_everywhere(client: TestClient, event_id: uuid.UUID) -> None:
    assert event_id not in _list_ids(client)
    assert event_id not in _calendar(client)["event"]
    assert not _event_detail_ok(client, event_id)


# --- the seeded grant ---------------------------------------------------------------


@requires_postgres
def test_seeded_guardian_role_holds_event_read_with_children_scope_only() -> None:
    world = _build_world()
    with session_scope() as session:
        grants = applicable_grants(session, world.guardian_user_id, "event.read")
    assert [grant.scope_type for grant in grants] == ["children"]


# --- Event: group path / direct path / UNION -------------------------------------------


@requires_postgres
def test_guardian_sees_childs_group_event(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    _assert_event_visible_everywhere(client, world.ev_group_a)


@requires_postgres
def test_guardian_does_not_see_event_of_a_group_the_child_is_not_in(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    _assert_event_hidden_everywhere(client, world.ev_group_x)


@requires_postgres
def test_guardian_sees_event_through_direct_participation(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    _assert_event_visible_everywhere(client, world.ev_direct_a)


@requires_postgres
def test_group_event_is_visible_without_participation_and_reading_creates_none(
    client: TestClient,
) -> None:
    """ADR-0042 §6 / ADR-0043 §3: visibility is not registration."""
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    _assert_event_visible_everywhere(client, world.ev_group_a)

    with session_scope() as session:
        participations = session.execute(
            select(EventParticipation.id).where(EventParticipation.event_id == world.ev_group_a)
        ).all()
    assert participations == []


@requires_postgres
def test_guardian_with_several_children_gets_the_union(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    listed = _list_ids(client)
    # Child A's group, child B's group and child A's direct participation.
    assert listed == world.visible_events
    assert _calendar(client)["event"] == world.visible_events


@requires_postgres
def test_relationship_ended_for_one_child_drops_only_that_childs_events(
    client: TestClient,
) -> None:
    world = _build_world()
    with session_scope() as session:
        relationship = session.execute(
            select(GuardianRelationship).where(
                GuardianRelationship.child_person_id == world.child_a_id
            )
        ).scalar_one()
        relationship.status = "revoked"
        session.commit()
    _authenticate_as(world.guardian_user_id)

    assert _list_ids(client) == {world.ev_group_b}
    _assert_event_hidden_everywhere(client, world.ev_group_a)
    _assert_event_hidden_everywhere(client, world.ev_direct_a)


@requires_postgres
def test_guardian_without_a_relationship_sees_no_child_event(client: TestClient) -> None:
    """Holding the seeded grant and a same-club membership is not enough:
    a `club_id` match alone never satisfies `children` (ADR-0043 §4)."""
    world = _build_world()
    _authenticate_as(world.unrelated_guardian_user_id)

    assert _list_ids(client) == set()
    assert _calendar(client) == {"event": set(), "occurrence": set()}
    for event_id in world.visible_events | world.hidden_events:
        assert not _event_detail_ok(client, event_id)
    for occurrence_id in world.visible_occurrences | world.hidden_occurrences:
        assert not _occurrence_detail_ok(client, occurrence_id)


@requires_postgres
def test_ended_group_target_and_left_group_do_not_expose_events(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    _assert_event_hidden_everywhere(client, world.ev_expired_target)
    _assert_event_hidden_everywhere(client, world.ev_inactive_membership)


@requires_postgres
def test_missing_guardian_event_read_grant_fails_closed(client: TestClient) -> None:
    """With every relationship still in place, removing the seeded
    (guardian, event.read) grant must leave nothing readable."""
    world = _build_world()
    with session_scope() as session:
        grant_id = session.execute(
            select(RolePermission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(Role.code == "guardian", Permission.code == "event.read")
        ).scalar_one()
        session.execute(
            delete(RolePermissionScope).where(RolePermissionScope.role_permission_id == grant_id)
        )
        session.commit()
    _authenticate_as(world.guardian_user_id)

    assert _list_ids(client) == set()
    assert _calendar(client) == {"event": set(), "occurrence": set()}
    for event_id in world.visible_events:
        assert not _event_detail_ok(client, event_id)
    for occurrence_id in world.visible_occurrences:
        assert not _occurrence_detail_ok(client, occurrence_id)


# --- Recurring EventOccurrence ------------------------------------------------------


@requires_postgres
def test_guardian_sees_recurring_occurrence_through_group_membership(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    assert world.occ_group_a in _calendar(client)["occurrence"]
    assert _occurrence_detail_ok(client, world.occ_group_a)


@requires_postgres
def test_recurring_group_occurrence_needs_no_direct_participation(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    assert _occurrence_detail_ok(client, world.occ_group_a)
    with session_scope() as session:
        participants = session.execute(
            select(EventOccurrenceParticipant.id).where(
                EventOccurrenceParticipant.occurrence_id == world.occ_group_a
            )
        ).all()
    assert participants == []


@requires_postgres
def test_guardian_does_not_see_occurrence_of_a_foreign_group(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    calendar_occurrences = _calendar(client)["occurrence"]
    for occurrence_id in world.hidden_occurrences:
        assert occurrence_id not in calendar_occurrences
        assert not _occurrence_detail_ok(client, occurrence_id)


@requires_postgres
def test_occurrence_direct_participation_path_still_works(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    assert world.occ_direct_b in _calendar(client)["occurrence"]
    assert _occurrence_detail_ok(client, world.occ_direct_b)


@requires_postgres
def test_series_group_target_materialized_occurrences_are_visible(client: TestClient) -> None:
    """End to end through the real recurrence path: a SeriesGroupTarget on
    child A's group, materialized into occurrence-level group targets
    (ADR-0030), exposes every generated occurrence — with no participant."""
    world = _build_world()
    with session_scope() as session:
        club = session.get(Club, world.club_id)
        series = _series(session, club)
        session.commit()
        create_series_group_target(
            session,
            event_series_id=series.id,
            group_id=world.group_a_id,
            valid_from=_LONG_AGO,
            actor_user_id=world.staff_user_id,
        )
        created = materialize_occurrences(
            session, series=series, horizon_end=_START + datetime.timedelta(days=3)
        )
        session.commit()
        materialized_ids = {occurrence.id for occurrence in created}
    assert materialized_ids
    _authenticate_as(world.guardian_user_id)

    assert materialized_ids <= _calendar(client)["occurrence"]
    for occurrence_id in materialized_ids:
        assert _occurrence_detail_ok(client, occurrence_id)
    with session_scope() as session:
        participants = session.execute(
            select(EventOccurrenceParticipant.id).where(
                EventOccurrenceParticipant.occurrence_id.in_(materialized_ids)
            )
        ).all()
    assert participants == []


@requires_postgres
def test_calendar_occurrences_are_exactly_the_union(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    assert _calendar(client)["occurrence"] == world.visible_occurrences


# --- Endpoint consistency ---------------------------------------------------------


@requires_postgres
def test_list_calendar_and_detail_agree_for_every_event(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    listed = _list_ids(client)
    in_calendar = _calendar(client)["event"]
    for event_id in world.visible_events | world.hidden_events:
        expected = event_id in world.visible_events
        assert (event_id in listed) is expected, event_id
        assert (event_id in in_calendar) is expected, event_id
        assert _event_detail_ok(client, event_id) is expected, event_id


@requires_postgres
def test_calendar_and_occurrence_detail_agree_for_every_occurrence(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    in_calendar = _calendar(client)["occurrence"]
    for occurrence_id in world.visible_occurrences | world.hidden_occurrences:
        expected = occurrence_id in world.visible_occurrences
        assert (occurrence_id in in_calendar) is expected, occurrence_id
        assert _occurrence_detail_ok(client, occurrence_id) is expected, occurrence_id


@requires_postgres
def test_query_filters_only_narrow_the_authorized_set(client: TestClient) -> None:
    world = _build_world()
    _authenticate_as(world.guardian_user_id)

    # A foreign group, a foreign staff member or a child id passed by the
    # client never widens what the Guardian may read.
    assert _calendar(client, group_id=str(world.group_x_id))["event"] == set()
    assert _calendar(client, user_id=str(world.staff_user_id))["event"] == set()
    assert _list_ids(client, group_id=str(world.group_x_id)) == set()
    assert _list_ids(client, instructor_id=str(world.staff_user_id)) == set()
    # A legitimate filter narrows to the authorized subset only.
    assert _calendar(client, group_id=str(world.group_a_id))["event"] == {world.ev_group_a}
    assert _list_ids(client, group_id=str(world.group_b_id)) == {world.ev_group_b}
    assert _list_ids(client, participant_id=str(world.child_a_id)) == {world.ev_direct_a}
    # Every filtered result stays a subset of the unfiltered authorized set.
    for params in (
        {"event_type": "lesson"},
        {"status": "published"},
        {"search": "training"},
    ):
        assert _list_ids(client, **params) <= world.visible_events
    assert _calendar(client, status="scheduled")["occurrence"] <= world.visible_occurrences
