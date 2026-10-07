"""End-to-end self-registration → roster → report verification (Issue #299).

One real flow through the shipped HTTP API and a real PostgreSQL database,
with no direct EventParticipation writes:

1. an eligible Member (active ClubMembership + active GroupMembership in
   the Event's target Group) registers for a published Event
   (`POST /events/{id}/participation`, ADR-0037);
2. the registration is one `EventParticipation` row, `registered`;
3. the Member appears in the Event roster
   (`GET /events/{id}/participants`, events-api.md §18);
4. and in the «Участники мероприятий» report dataset
   (`POST /memberships/exports/preview`, the same dataset as
   `POST /memberships/exports`);
5. the Member withdraws (`DELETE /events/{id}/participation`);
6. the same row becomes `cancelled` (never deleted);
7. the roster keeps its canonical contract — registered rows only;
8. the report with `participation_status=cancelled` shows the row;
9. the report without a status filter shows it with its `cancelled` status.

Authorization boundaries over the same data: the Member and the Guardian
never receive the general roster (only their own / their children's rows
under the canonical `self`/`children` row visibility), the Instructor reads
it only inside the existing `event.read` `own_events` scope, the
Administrator reads it club-wide, and another Club's Event is never exposed
(roster or report). Member/Guardian use the canonical, migration-seeded
roles; the canonical `instructor` role carries no `event.read` grant at
head, so its scope is granted the way the existing Event tests grant it.
"""

import datetime
import uuid
from dataclasses import dataclass

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
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventGroupTarget, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_PREVIEW_URL = "/api/v1/memberships/exports/preview"
_REPORT_FIELDS = [
    "person.last_name",
    "person.first_name",
    "person.middle_name",
    "group.name",
    "event.name",
    "event.starts_at",
    "event_participation.status",
]
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_START = datetime.datetime(2026, 11, 14, 7, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories -----------------------------------------------------------------


def _person(session: Session, last_name: str, first_name: str) -> Person:
    person = Person(last_name=last_name, first_name=first_name)
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


def _club_membership(session: Session, club: Club, person: Person) -> ClubMembership:
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="member",
        status="active",
        joined_at=_LONG_AGO,
    )
    session.add(membership)
    session.flush()
    return membership


def _event(session: Session, club: Club, title: str, target: Group | None = None) -> Event:
    """A published ordinary Event with its backing occurrence (ADR-0033)."""
    event = Event(
        club_id=club.id,
        event_type="trip",
        title=title,
        start_at=_START,
        end_at=_START + datetime.timedelta(hours=8),
        timezone="Europe/Moscow",
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
            event_type=event.event_type,
            recurrence_anchor_at=event.start_at,
            starts_at=event.start_at,
            ends_at=event.end_at,
            timezone=event.timezone,
            status="scheduled",
        )
    )
    if target is not None:
        session.add(EventGroupTarget(event_id=event.id, group_id=target.id, valid_from=_LONG_AGO))
    session.flush()
    return event


def _assign_role(session: Session, user: User, role_code: str, club: Club) -> None:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()


def _grant_event_read(session: Session, user: User, scope_type: str, club: Club) -> None:
    permission = session.execute(
        select(Permission).where(Permission.code == "event.read")
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


@dataclass(frozen=True)
class Flow:
    club_id: uuid.UUID
    group_id: uuid.UUID
    event_id: uuid.UUID
    unassigned_event_id: uuid.UUID
    member_user_id: uuid.UUID
    member_person_id: uuid.UUID
    other_person_id: uuid.UUID
    guardian_user_id: uuid.UUID
    child_person_id: uuid.UUID
    instructor_user_id: uuid.UUID
    admin_user_id: uuid.UUID


def _flow() -> tuple[Flow, uuid.UUID, uuid.UUID]:
    """One Club, one Group «Юные туристы» and the published Event «Поход
    выходного дня» targeted to it, plus:

    - Member Иванова Мария — eligible (active CM + active GM), canonical
      `member` role; registers/withdraws herself through the API;
    - Петров Павел — another eligible Member, registered through the API;
    - Guardian Сидорова Светлана (canonical `guardian` role) of the child
      Сидоров Семён, who is registered through his own self-registration;
    - Instructor — `event.read` `own_events`, staff of the Event only;
    - Administrator — canonical `admin` role in the Club.
    """
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        group = Group(club_id=club.id, name="Юные туристы", status="active", valid_from=_LONG_AGO)
        session.add(group)
        session.flush()
        event = _event(session, club, "Поход выходного дня", target=group)
        unassigned = _event(session, club, "Сплав", target=group)

        def eligible(last_name: str, first_name: str) -> tuple[Person, User]:
            person = _person(session, last_name, first_name)
            membership = _club_membership(session, club, person)
            session.add(
                GroupMembership(
                    group_id=group.id,
                    club_membership_id=membership.id,
                    membership_status="active",
                    valid_from=_LONG_AGO,
                )
            )
            user = _user(session, person)
            _assign_role(session, user, "member", club)
            return person, user

        member_person, member_user = eligible("Иванова", "Мария")
        other_person, other_user = eligible("Петров", "Павел")
        child_person, child_user = eligible("Сидоров", "Семён")

        guardian_person = _person(session, "Сидорова", "Светлана")
        _club_membership(session, club, guardian_person)
        guardian_user = _user(session, guardian_person)
        _assign_role(session, guardian_user, "guardian", club)
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian_person.id,
                child_person_id=child_person.id,
                relationship_type="parent",
                status="active",
                valid_from=_LONG_AGO,
            )
        )

        instructor_user = _user(session, _person(session, "Орлов", "Олег"))
        _grant_event_read(session, instructor_user, "own_events", club)
        session.add(
            EventStaffAssignment(
                event_id=event.id,
                user_id=instructor_user.id,
                role_in_event="instructor",
                valid_from=_LONG_AGO,
            )
        )

        admin_user = _user(session, _person(session, "Админова", "Алла"))
        _assign_role(session, admin_user, "admin", club)
        session.commit()
        flow = Flow(
            club_id=club.id,
            group_id=group.id,
            event_id=event.id,
            unassigned_event_id=unassigned.id,
            member_user_id=member_user.id,
            member_person_id=member_person.id,
            other_person_id=other_person.id,
            guardian_user_id=guardian_user.id,
            child_person_id=child_person.id,
            instructor_user_id=instructor_user.id,
            admin_user_id=admin_user.id,
        )
        other_user_id, child_user_id = other_user.id, child_user.id
    return flow, other_user_id, child_user_id


@pytest.fixture
def flow(client: TestClient) -> Flow:
    data, other_user_id, child_user_id = _flow()
    # The other participants register themselves too: every row in this
    # module comes from the real self-registration endpoint.
    for user_id in (other_user_id, child_user_id):
        _authenticate_as(user_id)
        assert _register(client, data.event_id).status_code == 200
    return data


# --- HTTP helpers --------------------------------------------------------------


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _register(client: TestClient, event_id: uuid.UUID):
    return client.post(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )


def _withdraw(client: TestClient, event_id: uuid.UUID):
    return client.delete(
        f"/api/v1/events/{event_id}/participation", headers=_csrf_headers(client)
    )


def _roster(client: TestClient, event_id: uuid.UUID, **params: object):
    return client.get(f"/api/v1/events/{event_id}/participants", params=params)


def _roster_ids(client: TestClient, event_id: uuid.UUID) -> list[str]:
    response = _roster(client, event_id)
    assert response.status_code == 200, response.text
    return [item["person_id"] for item in response.json()["items"]]


def _report(client: TestClient, flow: Flow, context: str = "event", **filters: object):
    body: dict[str, object] = {"context": context, "fields": _REPORT_FIELDS, **filters}
    if context in ("event", "group_event"):
        body["event_id"] = str(flow.event_id)
    if context in ("group", "group_event"):
        body["group_id"] = str(flow.group_id)
    else:
        body["fields"] = [code for code in _REPORT_FIELDS if code != "group.name"]
    return client.post(_PREVIEW_URL, json=body, headers=_csrf_headers(client))


def _report_rows(client: TestClient, flow: Flow, context: str = "event", **filters: object):
    response = _report(client, flow, context, **filters)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _participation(flow: Flow) -> list[tuple[str, uuid.UUID]]:
    with session_scope() as session:
        rows = session.execute(
            select(EventParticipation.registration_status, EventParticipation.id).where(
                EventParticipation.event_id == flow.event_id,
                EventParticipation.person_id == flow.member_person_id,
            )
        ).all()
        return [(row.registration_status, row.id) for row in rows]


# --- the full flow -------------------------------------------------------------


def test_registration_roster_report_withdrawal_report(client: TestClient, flow: Flow) -> None:
    # 1-2. The eligible Member registers: one EventParticipation, registered.
    _authenticate_as(flow.member_user_id)
    registered = _register(client, flow.event_id)
    assert registered.status_code == 200, registered.text
    assert registered.json()["registration_status"] == "registered"
    assert registered.json()["person_id"] == str(flow.member_person_id)
    [(status_after_registration, participation_id)] = _participation(flow)
    assert status_after_registration == "registered"

    # 3. The Administrator sees the Member in the Event roster.
    _authenticate_as(flow.admin_user_id)
    assert _roster_ids(client, flow.event_id) == [
        str(flow.member_person_id),
        str(flow.other_person_id),
        str(flow.child_person_id),
    ]

    # 4. … and in the report dataset, in both Event contexts.
    expected_row = [
        "Иванова",
        "Мария",
        "",
        "Юные туристы",
        "Поход выходного дня",
        "2026-11-14 10:00",
        "registered",
    ]
    assert expected_row in _report_rows(client, flow, "group_event")
    assert ["Иванова", "Мария", "", "Поход выходного дня", "2026-11-14 10:00", "registered"] in (
        _report_rows(client, flow, "event", participation_status="registered")
    )

    # 5-6. The Member withdraws: the same row, now cancelled — not deleted.
    _authenticate_as(flow.member_user_id)
    assert _withdraw(client, flow.event_id).status_code == 204
    assert _participation(flow) == [("cancelled", participation_id)]

    # 7. The roster keeps its canonical contract: registered rows only.
    _authenticate_as(flow.admin_user_id)
    roster = _roster(client, flow.event_id).json()
    assert [item["person_id"] for item in roster["items"]] == [
        str(flow.other_person_id),
        str(flow.child_person_id),
    ]
    assert roster["pagination"]["total"] == 2

    # 8. The report filtered on `cancelled` shows exactly that row …
    cancelled_row = [*expected_row[:-1], "cancelled"]
    assert _report_rows(client, flow, "group_event", participation_status="cancelled") == [
        cancelled_row
    ]
    # … the `registered` report no longer does …
    registered_names = [
        row[0]
        for row in _report_rows(client, flow, "group_event", participation_status="registered")
    ]
    assert registered_names == ["Петров", "Сидоров"]
    # 9. … and the unfiltered report (every status) keeps it, as cancelled.
    every_status = _report_rows(client, flow, "group_event")
    assert [row[0] for row in every_status] == ["Иванова", "Петров", "Сидоров"]
    assert every_status[0] == cancelled_row


def test_registering_again_after_withdrawal_restores_roster_and_report(
    client: TestClient, flow: Flow
) -> None:
    _authenticate_as(flow.member_user_id)
    assert _register(client, flow.event_id).status_code == 200
    assert _withdraw(client, flow.event_id).status_code == 204
    assert _register(client, flow.event_id).status_code == 200
    assert [status for status, _id in _participation(flow)] == ["registered"]

    _authenticate_as(flow.admin_user_id)
    assert str(flow.member_person_id) in _roster_ids(client, flow.event_id)
    assert _report_rows(client, flow, "group_event", participation_status="cancelled") == []


def test_report_and_roster_paginate_the_same_registered_set(
    client: TestClient, flow: Flow
) -> None:
    _authenticate_as(flow.member_user_id)
    assert _register(client, flow.event_id).status_code == 200
    _authenticate_as(flow.admin_user_id)

    roster_pages = [_roster(client, flow.event_id, page=p, page_size=2).json() for p in (1, 2)]
    assert [len(page["items"]) for page in roster_pages] == [2, 1]
    assert {page["pagination"]["total"] for page in roster_pages} == {3}
    assert {page["pagination"]["pages"] for page in roster_pages} == {2}

    report_pages = [
        _report(
            client, flow, "event", participation_status="registered", page=p, page_size=2
        ).json()
        for p in (1, 2)
    ]
    assert [[row[0] for row in page["items"]] for page in report_pages] == [
        ["Иванова", "Петров"],
        ["Сидоров"],
    ]
    assert {page["pagination"]["total"] for page in report_pages} == {3}


# --- authorization boundaries --------------------------------------------------


def test_member_never_receives_the_general_roster_or_report(
    client: TestClient, flow: Flow
) -> None:
    _authenticate_as(flow.member_user_id)
    # Not registered: the canonical `self` row visibility shows nothing of
    # the other participants.
    assert _roster_ids(client, flow.event_id) == []
    assert _register(client, flow.event_id).status_code == 200
    assert _roster_ids(client, flow.event_id) == [str(flow.member_person_id)]
    assert _withdraw(client, flow.event_id).status_code == 204
    assert _roster_ids(client, flow.event_id) == []

    report = _report(client, flow, "group_event")
    assert report.status_code == 403
    assert report.json()["error"]["code"] == "forbidden"


def test_guardian_never_receives_the_general_roster_or_report(
    client: TestClient, flow: Flow
) -> None:
    _authenticate_as(flow.guardian_user_id)
    # Only the guardian's own child (`children` row visibility), never the
    # other registered participants.
    assert _roster_ids(client, flow.event_id) == [str(flow.child_person_id)]
    assert _report(client, flow, "event").status_code == 403


def test_instructor_reads_the_roster_only_inside_event_read_scope(
    client: TestClient, flow: Flow
) -> None:
    _authenticate_as(flow.member_user_id)
    assert _register(client, flow.event_id).status_code == 200
    _authenticate_as(flow.instructor_user_id)

    assert _roster_ids(client, flow.event_id) == [
        str(flow.member_person_id),
        str(flow.other_person_id),
        str(flow.child_person_id),
    ]
    # An Event of the same Club the Instructor is not staff of: hidden.
    unassigned = _roster(client, flow.unassigned_event_id)
    assert unassigned.status_code == 404
    # The report stays Administrator-only.
    assert _report(client, flow, "event").status_code == 403


def test_another_clubs_event_is_never_exposed(
    client: TestClient, flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope() as session:
        other_club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(other_club)
        session.flush()
        foreign_event = _event(session, other_club, "Чужой поход")
        stranger = _person(session, "Чужой", "Участник")
        _club_membership(session, other_club, stranger)
        stranger_user = _user(session, stranger)
        session.commit()
        foreign_event_id, stranger_user_id = foreign_event.id, stranger_user.id
    _authenticate_as(stranger_user_id)
    assert _register(client, foreign_event_id).status_code == 200

    # Two Clubs violate the single-Club product invariant: the current Club
    # is pinned the way resolve_sole_club_id resolves it.
    monkeypatch.setattr("app.exports.service.resolve_sole_club_id", lambda _s: flow.club_id)
    for user_id in (flow.admin_user_id, flow.instructor_user_id, flow.member_user_id):
        _authenticate_as(user_id)
        response = _roster(client, foreign_event_id)
        assert response.status_code == 404, user_id
        assert response.json()["error"]["code"] == "not_found"

    _authenticate_as(flow.admin_user_id)
    foreign_report = client.post(
        _PREVIEW_URL,
        json={
            "context": "event",
            "event_id": str(foreign_event_id),
            "fields": ["person.last_name"],
        },
        headers=_csrf_headers(client),
    )
    assert foreign_report.status_code == 404
    assert "Чужой" not in [row[0] for row in _report_rows(client, flow, "event")]
