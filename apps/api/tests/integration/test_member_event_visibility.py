"""Member Event visibility (Issue #285, PO decision 2026-10-06).

`event.read` + `self` for the canonical `member` role is the Member Event
object policy (ADR-0020 §2/§3, ADR-0029, role-permission-scope-matrix
§7.2): with an active ClubMembership in the Event's Club, a Member reads

- club-wide Events (no active EventGroupTarget), and
- Events targeted to a Group in which the Member has an active
  GroupMembership;

EventParticipation is neither required nor by itself sufficient; ended
membership and other Clubs never authorize; `draft` is not available
before publication (ADR-0018); the same policy applies to recurring
EventOccurrences. Every Member test runs through the REAL,
migration-seeded `member -> event.read -> self` grant (migration
9b4d6e2f8a10) on the canonical `member` role — no ad hoc grant.

Also covers: authorization applied before count/pagination, detail and
list agreeing, no mutation capability, participant list limited to the
Member's own row, the ODR-0002 Group Schedule `self` path enabled by the
same grant (future-only, explicit GroupTarget), the occurrence backing an
ordinary Event never treated as "untargeted", and Admin (`all`) /
Instructor (`own_groups`) regression.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration/test_member_event_visibility.py -v
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal
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
from app.db.events import Event, EventGroupTarget, EventParticipation
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.events.materialization import materialize_occurrences
from app.events.series_relationships import create_series_group_target
from app.main import app

from .conftest import requires_postgres

_NOW = datetime.datetime.now(datetime.timezone.utc)
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_YESTERDAY = _NOW - datetime.timedelta(days=1)
_TOMORROW = _NOW + datetime.timedelta(days=1)
# Future items: ODR-0002 `self` Group Schedule access is future-only.
_START = (_NOW + datetime.timedelta(days=2)).replace(microsecond=0)
_SCHEDULE_RANGE = {
    "from": (_NOW - datetime.timedelta(days=5)).isoformat(),
    "to": (_NOW + datetime.timedelta(days=30)).isoformat(),
}
_CALENDAR_RANGE = {
    "from": (_NOW - datetime.timedelta(days=1)).isoformat(),
    "to": (_NOW + datetime.timedelta(days=30)).isoformat(),
}

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


# --- factories ----------------------------------------------------------------


def _club(session: Session) -> Club:
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _person(session: Session) -> Person:
    person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _user(session: Session, person: Person) -> User:
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _club_membership(
    session: Session, club: Club, person: Person, *, status: str = "active"
) -> ClubMembership:
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="student",
        status=status,
        joined_at=_LONG_AGO,
    )
    session.add(membership)
    session.flush()
    return membership


def _group(session: Session, club: Club) -> Group:
    group = Group(
        club_id=club.id,
        name=f"Group {uuid.uuid4().hex[:8]}",
        status="active",
        valid_from=_LONG_AGO,
    )
    session.add(group)
    session.flush()
    return group


def _group_membership(
    session: Session,
    group: Group,
    club_membership: ClubMembership,
    *,
    membership_status: str = "active",
    valid_to: datetime.datetime | None = None,
) -> GroupMembership:
    membership = GroupMembership(
        group_id=group.id,
        club_membership_id=club_membership.id,
        valid_from=_LONG_AGO,
        valid_to=valid_to,
        membership_status=membership_status,
    )
    session.add(membership)
    session.flush()
    return membership


def _event(
    session: Session,
    club: Club,
    *,
    title: str = "Event",
    status: str = "published",
    start_at: datetime.datetime = _START,
    targets: tuple[Group, ...] = (),
) -> Event:
    """An ordinary Event with its ADR-0033 linked occurrence and the
    given active EventGroupTargets."""
    event = Event(
        id=uuid.uuid4(),
        club_id=club.id,
        event_type="lesson",
        title=title,
        start_at=start_at,
        end_at=start_at + datetime.timedelta(hours=1),
        timezone="Europe/Moscow",
        status=status,
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
            recurrence_anchor_at=start_at,
            starts_at=start_at,
            ends_at=start_at + datetime.timedelta(hours=1),
            timezone="Europe/Moscow",
            status=_EVENT_TO_OCCURRENCE_STATUS[status],
        )
    )
    for group in targets:
        session.add(EventGroupTarget(event_id=event.id, group_id=group.id, valid_from=_LONG_AGO))
    session.flush()
    return event


def _series(session: Session, club: Club) -> EventSeries:
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


def _occurrence(
    session: Session,
    series: EventSeries,
    *,
    hours_after_start: int = 0,
    targets: tuple[Group, ...] = (),
) -> EventOccurrence:
    starts_at = _START + datetime.timedelta(hours=hours_after_start)
    occurrence = EventOccurrence(
        series_id=series.id,
        club_id=series.club_id,
        name="Recurring",
        event_type="lesson",
        recurrence_anchor_at=starts_at,
        starts_at=starts_at,
        ends_at=starts_at + datetime.timedelta(hours=1),
        timezone="UTC",
        status="scheduled",
    )
    session.add(occurrence)
    session.flush()
    for group in targets:
        session.add(
            EventOccurrenceGroupTarget(
                occurrence_id=occurrence.id,
                group_id=group.id,
                valid_from=_LONG_AGO,
                is_override=False,
            )
        )
    session.flush()
    return occurrence


def _assign_role(session: Session, user: User, role_code: str, club: Club | None) -> None:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=club.id if club is not None else None
        )
    )
    session.flush()


def _member(
    session: Session, club: Club, *, global_role: bool = False
) -> tuple[User, Person, ClubMembership]:
    """A real Member: an active ClubMembership in `club` plus an
    assignment of the canonical, migration-seeded `member` role (scoped to
    `club`, or global with `global_role=True`)."""
    person = _person(session)
    club_membership = _club_membership(session, club, person)
    user = _user(session, person)
    _assign_role(session, user, "member", None if global_role else club)
    return user, person, club_membership


def _grant_ad_hoc(
    session: Session, user: User, permission_code: str, scope_type: str, club: Club
) -> None:
    """Ad hoc role for the Instructor regression: the canonical
    `instructor` role carries no `event.read` grant at head, so
    `own_groups` is exercised the way the existing Event tests do."""
    permission = session.execute(
        select(Permission).where(Permission.code == permission_code)
    ).scalar_one()
    role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
    session.add(role)
    session.flush()
    session.add(
        RolePermission(
            role_id=role.id,
            permission_id=permission.id,
            scopes=[RolePermissionScope(scope_type=scope_type)],
        )
    )
    session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _list_ids(client: TestClient, **params: object) -> set[str]:
    response = client.get("/api/v1/events", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _calendar_ids(client: TestClient) -> set[str]:
    response = client.get("/api/v1/events/calendar", params=_CALENDAR_RANGE)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _detail_status(client: TestClient, event_id: uuid.UUID) -> int:
    return client.get(f"/api/v1/events/{event_id}").status_code


# --- Member Event visibility --------------------------------------------------


@requires_postgres
def test_member_sees_event_targeted_to_active_group_without_participation(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        group = _group(session, club)
        _group_membership(session, group, membership)
        event = _event(session, club, targets=(group,))
        session.commit()
        user_id, event_id = user.id, event.id
        participation_count = session.execute(
            select(EventParticipation).where(EventParticipation.event_id == event_id)
        ).all()
    assert participation_count == []
    _authenticate_as(user_id)

    assert str(event_id) in _list_ids(client)
    assert str(event_id) in _calendar_ids(client)
    assert _detail_status(client, event_id) == 200


@requires_postgres
@pytest.mark.parametrize(
    "membership_status", ["ended", "active"], ids=["ended-status", "expired-interval"]
)
def test_ended_group_membership_does_not_authorize_targeted_event(
    client: TestClient, membership_status: str
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        group = _group(session, club)
        _group_membership(
            session, group, membership, membership_status=membership_status, valid_to=_YESTERDAY
        )
        event = _event(session, club, targets=(group,))
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    assert str(event_id) not in _list_ids(client)
    assert str(event_id) not in _calendar_ids(client)
    assert _detail_status(client, event_id) == 404


@requires_postgres
def test_event_targeted_only_to_unrelated_group_is_invisible(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        my_group = _group(session, club)
        unrelated = _group(session, club)
        _group_membership(session, my_group, membership)
        event = _event(session, club, targets=(unrelated,))
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    assert str(event_id) not in _list_ids(client)
    assert _detail_status(client, event_id) == 404


@requires_postgres
def test_event_targeted_to_my_group_and_an_unrelated_group_is_visible(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        my_group = _group(session, club)
        unrelated = _group(session, club)
        _group_membership(session, my_group, membership)
        event = _event(session, club, targets=(unrelated, my_group))
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    assert str(event_id) in _list_ids(client)


@requires_postgres
def test_member_sees_club_wide_event_of_own_club(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, _membership = _member(session, club)
        event = _event(session, club)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    assert str(event_id) in _list_ids(client)
    assert str(event_id) in _calendar_ids(client)
    assert _detail_status(client, event_id) == 200


@requires_postgres
def test_event_whose_only_target_ended_is_club_wide(client: TestClient) -> None:
    # ADR-0020 §2: "an Event with no active GroupTarget (club-wide Event)".
    with session_scope() as session:
        club = _club(session)
        user, _person_row, _membership = _member(session, club)
        group = _group(session, club)
        event = _event(session, club)
        session.add(
            EventGroupTarget(
                event_id=event.id, group_id=group.id, valid_from=_LONG_AGO, valid_to=_YESTERDAY
            )
        )
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    assert str(event_id) in _list_ids(client)


@requires_postgres
@pytest.mark.parametrize("club_membership_status", ["suspended", "inactive"])
def test_no_active_club_membership_authorizes_nothing(
    client: TestClient, club_membership_status: str
) -> None:
    with session_scope() as session:
        club = _club(session)
        person = _person(session)
        membership = _club_membership(session, club, person, status=club_membership_status)
        user = _user(session, person)
        _assign_role(session, user, "member", club)
        group = _group(session, club)
        _group_membership(session, group, membership)
        club_wide = _event(session, club, title="club-wide")
        targeted = _event(session, club, title="targeted", targets=(group,))
        session.commit()
        user_id, ids = user.id, {str(club_wide.id), str(targeted.id)}
    _authenticate_as(user_id)

    assert not (ids & _list_ids(client))
    assert not (ids & _calendar_ids(client))


@requires_postgres
def test_event_participation_alone_does_not_authorize(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, person, _membership = _member(session, club)
        unrelated = _group(session, club)
        event = _event(session, club, targets=(unrelated,))
        session.add(
            EventParticipation(
                event_id=event.id, person_id=person.id, registration_status="registered"
            )
        )
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)

    assert str(event_id) not in _list_ids(client)
    assert str(event_id) not in _calendar_ids(client)
    assert _detail_status(client, event_id) == 404


@requires_postgres
@pytest.mark.parametrize("global_role", [False, True], ids=["club-scoped-role", "global-role"])
def test_cross_club_event_is_never_visible(client: TestClient, global_role: bool) -> None:
    with session_scope() as session:
        club = _club(session)
        other_club = _club(session)
        user, person, _membership = _member(session, club, global_role=global_role)
        foreign_group = _group(session, other_club)
        club_wide_foreign = _event(session, other_club, title="foreign club-wide")
        targeted_foreign = _event(session, other_club, title="foreign", targets=(foreign_group,))
        session.add(
            EventParticipation(
                event_id=club_wide_foreign.id, person_id=person.id, registration_status="registered"
            )
        )
        session.commit()
        user_id = user.id
        ids = {str(club_wide_foreign.id), str(targeted_foreign.id)}
    _authenticate_as(user_id)

    assert not (ids & _list_ids(client))
    assert not (ids & _calendar_ids(client))
    for event_id in ids:
        assert _detail_status(client, uuid.UUID(event_id)) == 404


@requires_postgres
def test_draft_event_is_not_visible_before_publication(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        group = _group(session, club)
        _group_membership(session, group, membership)
        draft_club_wide = _event(session, club, status="draft")
        draft_targeted = _event(session, club, status="draft", targets=(group,))
        session.commit()
        user_id = user.id
        ids = {str(draft_club_wide.id), str(draft_targeted.id)}
    _authenticate_as(user_id)

    assert not (ids & _list_ids(client))
    for event_id in ids:
        assert _detail_status(client, uuid.UUID(event_id)) == 404


@requires_postgres
def test_count_and_pagination_cover_only_authorized_events(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        my_group = _group(session, club)
        unrelated = _group(session, club)
        _group_membership(session, my_group, membership)
        visible = set()
        for index in range(3):
            visible.add(
                str(
                    _event(
                        session,
                        club,
                        title=f"mine {index}",
                        start_at=_START + datetime.timedelta(hours=index),
                        targets=(my_group,),
                    ).id
                )
            )
        visible.add(str(_event(session, club, title="club-wide").id))
        for index in range(4):
            _event(session, club, title=f"hidden {index}", targets=(unrelated,))
        _event(session, _club(session), title="foreign")
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    seen: set[str] = set()
    for page in (1, 2, 3):
        response = client.get("/api/v1/events", params={"page": page, "page_size": 2})
        assert response.status_code == 200
        body = response.json()
        assert body["pagination"]["total"] == len(visible)
        assert body["pagination"]["pages"] == 2
        seen |= {item["id"] for item in body["items"]}
    assert seen == visible

    calendar = client.get("/api/v1/events/calendar", params={**_CALENDAR_RANGE, "page_size": 2})
    assert calendar.json()["pagination"]["total"] == len(visible)


@requires_postgres
def test_member_cannot_mutate_a_visible_event(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, _membership = _member(session, club)
        event = _event(session, club)
        session.commit()
        user_id, event_id = user.id, event.id
    _authenticate_as(user_id)
    client.cookies.set("csrf_token", "test-csrf-token")
    headers = {"X-CSRF-Token": "test-csrf-token"}

    assert _detail_status(client, event_id) == 200
    responses = [
        client.patch(f"/api/v1/events/{event_id}", json={"title": "Hacked"}, headers=headers),
        client.post(
            f"/api/v1/events/{event_id}/status",
            json={"status": "cancelled", "cancellation_reason": "x"},
            headers=headers,
        ),
        client.post(f"/api/v1/events/{event_id}/archive", headers=headers),
    ]
    for response in responses:
        assert response.status_code in (403, 404), response.text

    with session_scope() as session:
        stored = session.get(Event, event_id)
        assert stored is not None
        assert (stored.title, stored.status) == ("Event", "published")


@requires_postgres
def test_participant_list_shows_only_the_members_own_row(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, person, _membership = _member(session, club)
        other = _person(session)
        _club_membership(session, club, other)
        event = _event(session, club)
        session.add_all(
            [
                EventParticipation(
                    event_id=event.id, person_id=person.id, registration_status="registered"
                ),
                EventParticipation(
                    event_id=event.id, person_id=other.id, registration_status="registered"
                ),
            ]
        )
        session.commit()
        user_id, event_id, person_id = user.id, event.id, person.id
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/events/{event_id}/participants")
    assert response.status_code == 200
    body = response.json()
    assert [item["person_id"] for item in body["items"]] == [str(person_id)]
    assert body["pagination"]["total"] == 1


# --- Recurring EventOccurrences (ADR-0029) -------------------------------------


@requires_postgres
def test_occurrences_follow_the_same_member_policy(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, person, membership = _member(session, club)
        my_group = _group(session, club)
        unrelated = _group(session, club)
        _group_membership(session, my_group, membership)
        series = _series(session, club)
        club_wide = _occurrence(session, series)
        mine = _occurrence(session, series, hours_after_start=3, targets=(my_group,))
        other = _occurrence(session, series, hours_after_start=6, targets=(unrelated,))
        session.add(
            EventOccurrenceParticipant(
                occurrence_id=other.id,
                person_id=person.id,
                registration_status="registered",
                valid_from=_LONG_AGO,
                is_override=False,
            )
        )
        session.commit()
        user_id = user.id
        visible = {str(club_wide.id), str(mine.id)}
        hidden_id = other.id
    _authenticate_as(user_id)

    ids = _calendar_ids(client)
    assert visible <= ids
    assert str(hidden_id) not in ids
    for occurrence_id in visible:
        assert client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code == 200
    assert client.get(f"/api/v1/events/occurrences/{hidden_id}").status_code == 404


@requires_postgres
def test_occurrence_with_ended_group_membership_is_invisible(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        group = _group(session, club)
        _group_membership(
            session, group, membership, membership_status="ended", valid_to=_YESTERDAY
        )
        occurrence = _occurrence(session, _series(session, club), targets=(group,))
        session.commit()
        user_id, occurrence_id = user.id, occurrence.id
    _authenticate_as(user_id)

    assert str(occurrence_id) not in _calendar_ids(client)
    assert client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code == 404


@requires_postgres
def test_occurrence_backing_an_ordinary_event_is_not_treated_as_club_wide(
    client: TestClient,
) -> None:
    # ADR-0033: the occurrence linked to an ordinary Event has no
    # occurrence-level GroupTarget rows; it must not become "club-wide"
    # and expose an Event targeted only to an unrelated Group.
    with session_scope() as session:
        club = _club(session)
        user, _person_row, _membership = _member(session, club)
        unrelated = _group(session, club)
        event = _event(session, club, targets=(unrelated,))
        linked_occurrence_id = session.execute(
            select(EventOccurrence.id).where(EventOccurrence.event_id == event.id)
        ).scalar_one()
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/events/occurrences/{linked_occurrence_id}").status_code == 404


def _materialized_series_occurrence(
    session: Session, club: Club, *, target: Group | None, target_valid_from: datetime.datetime
) -> EventOccurrence:
    """The real recurring path: an EventSeries (optionally targeted to
    `target` through a SeriesGroupTarget) materialized by
    app.events.materialization, which copies the target into an
    occurrence-level EventOccurrenceGroupTarget (ADR-0030)."""
    actor = _user(session, _person(session))
    series = _series(session, club)
    if target is not None:
        create_series_group_target(
            session,
            event_series_id=series.id,
            group_id=target.id,
            valid_from=target_valid_from,
            actor_user_id=actor.id,
        )
    created = materialize_occurrences(
        session, series=series, horizon_end=_START + datetime.timedelta(hours=1)
    )
    assert len(created) == 1
    return created[0]


@requires_postgres
@pytest.mark.parametrize(
    "target_valid_from", [_LONG_AGO, _TOMORROW], ids=["target-in-force", "target-starts-later"]
)
def test_series_targeted_to_group_a_is_invisible_to_a_member_of_group_b(
    client: TestClient, target_valid_from: datetime.datetime
) -> None:
    # Review scenario: recurring series targeted to Group A; a Member whose
    # only active GroupMembership is in Group B must not see its occurrence
    # — also when the target's validity starts after "now" (it is still in
    # force at the occurrence's own start, so the occurrence is targeted and
    # never falls back to club-wide).
    with session_scope() as session:
        club = _club(session)
        group_a = _group(session, club)
        group_b = _group(session, club)
        member_b, _person_b, membership_b = _member(session, club)
        _group_membership(session, group_b, membership_b)
        member_a, _person_a, membership_a = _member(session, club)
        _group_membership(session, group_a, membership_a)
        occurrence = _materialized_series_occurrence(
            session, club, target=group_a, target_valid_from=target_valid_from
        )
        target_groups = (
            session.execute(
                select(EventOccurrenceGroupTarget.group_id).where(
                    EventOccurrenceGroupTarget.occurrence_id == occurrence.id
                )
            )
            .scalars()
            .all()
        )
        session.commit()
        occurrence_id, member_b_id, member_a_id = occurrence.id, member_b.id, member_a.id
    assert target_groups == [group_a.id]  # the occurrence-level target exists

    _authenticate_as(member_b_id)
    assert str(occurrence_id) not in _calendar_ids(client)
    assert client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code == 404

    _authenticate_as(member_a_id)
    assert str(occurrence_id) in _calendar_ids(client)
    assert client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code == 200


@requires_postgres
def test_event_targeted_to_group_a_with_later_starting_target_is_invisible_to_group_b(
    client: TestClient,
) -> None:
    # Ordinary-Event counterpart: an EventGroupTarget whose validity starts
    # after "now" (but before the Event) still makes the Event targeted. Its
    # ADR-0033 linked occurrence (no occurrence-level targets) is not
    # readable as a recurring occurrence either.
    with session_scope() as session:
        club = _club(session)
        group_a = _group(session, club)
        group_b = _group(session, club)
        member_b, _person_b, membership_b = _member(session, club)
        _group_membership(session, group_b, membership_b)
        member_a, _person_a, membership_a = _member(session, club)
        _group_membership(session, group_a, membership_a)
        event = _event(session, club)
        session.add(EventGroupTarget(event_id=event.id, group_id=group_a.id, valid_from=_TOMORROW))
        linked_occurrence_id = session.execute(
            select(EventOccurrence.id).where(EventOccurrence.event_id == event.id)
        ).scalar_one()
        session.commit()
        event_id, member_b_id, member_a_id = event.id, member_b.id, member_a.id

    _authenticate_as(member_b_id)
    assert str(event_id) not in _list_ids(client)
    assert str(event_id) not in _calendar_ids(client)
    assert _detail_status(client, event_id) == 404
    assert client.get(f"/api/v1/events/occurrences/{linked_occurrence_id}").status_code == 404

    _authenticate_as(member_a_id)
    assert str(event_id) in _list_ids(client)
    assert _detail_status(client, event_id) == 200


@requires_postgres
def test_untargeted_materialized_series_occurrence_is_club_wide(client: TestClient) -> None:
    # Review scenario: a recurring occurrence with no GroupTarget is
    # club-wide — readable with an active ClubMembership in its Club, never
    # from another Club.
    with session_scope() as session:
        club = _club(session)
        member, _person_row, _membership = _member(session, club)
        outsider, _outsider_person, _outsider_membership = _member(session, _club(session))
        occurrence = _materialized_series_occurrence(
            session, club, target=None, target_valid_from=_LONG_AGO
        )
        session.commit()
        occurrence_id, member_id, outsider_id = occurrence.id, member.id, outsider.id

    _authenticate_as(member_id)
    assert str(occurrence_id) in _calendar_ids(client)
    assert client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code == 200

    _authenticate_as(outsider_id)
    assert str(occurrence_id) not in _calendar_ids(client)
    assert client.get(f"/api/v1/events/occurrences/{occurrence_id}").status_code == 404


# --- Group Schedule (ODR-0002 `self`, unchanged) ------------------------------


@requires_postgres
def test_member_reads_own_group_schedule_only_for_targeted_future_items(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        group = _group(session, club)
        unrelated = _group(session, club)
        _group_membership(session, group, membership)
        targeted = _event(session, club, title="targeted", targets=(group,))
        club_wide = _event(session, club, title="club-wide")
        past = _event(
            session,
            club,
            title="past",
            status="completed",
            start_at=_NOW - datetime.timedelta(days=2),
            targets=(group,),
        )
        session.commit()
        user_id, group_id, unrelated_id = user.id, group.id, unrelated.id
        targeted_id, club_wide_id, past_id = targeted.id, club_wide.id, past.id
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/groups/{group_id}/schedule", params=_SCHEDULE_RANGE)
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert ids == {str(targeted_id)}
    assert str(club_wide_id) not in ids and str(past_id) not in ids

    assert (
        client.get(f"/api/v1/groups/{unrelated_id}/schedule", params=_SCHEDULE_RANGE).status_code
        == 404
    )


@requires_postgres
def test_ended_group_membership_does_not_authorize_group_schedule(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        user, _person_row, membership = _member(session, club)
        group = _group(session, club)
        _group_membership(
            session, group, membership, membership_status="ended", valid_to=_YESTERDAY
        )
        _event(session, club, targets=(group,))
        session.commit()
        user_id, group_id = user.id, group.id
    _authenticate_as(user_id)

    assert (
        client.get(f"/api/v1/groups/{group_id}/schedule", params=_SCHEDULE_RANGE).status_code == 404
    )


# --- Admin / Instructor regression --------------------------------------------


@requires_postgres
def test_admin_still_sees_every_event_including_drafts(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        person = _person(session)
        user = _user(session, person)
        _assign_role(session, user, "admin", club)
        group = _group(session, club)
        ids = {
            str(_event(session, club, title="club-wide").id),
            str(_event(session, club, title="targeted", targets=(group,)).id),
            str(_event(session, club, title="draft", status="draft").id),
        }
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    assert ids <= _list_ids(client)


@requires_postgres
def test_instructor_own_groups_unchanged_and_not_widened_to_club_wide(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _club(session)
        person = _person(session)
        _club_membership(session, club, person)
        user = _user(session, person)
        _grant_ad_hoc(session, user, "event.read", "own_groups", club)
        instructed = _group(session, club)
        other = _group(session, club)
        session.add(
            GroupInstructorAssignment(
                group_id=instructed.id,
                user_id=user.id,
                role_in_group="instructor",
                valid_from=_LONG_AGO,
            )
        )
        mine = _event(session, club, title="instructed", targets=(instructed,))
        not_mine = _event(session, club, title="other", targets=(other,))
        club_wide = _event(session, club, title="club-wide")
        session.commit()
        user_id = user.id
        mine_id, hidden = mine.id, {str(not_mine.id), str(club_wide.id)}
    _authenticate_as(user_id)

    ids = _list_ids(client)
    assert str(mine_id) in ids
    assert not (hidden & ids)
