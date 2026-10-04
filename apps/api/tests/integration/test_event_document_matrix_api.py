"""HTTP-level integration tests for the Event document readiness matrix
(Issue #175 backend foundation):

    GET /api/v1/events/{event_id}/document-requirements/matrix

Covers: matrix correctness through the shared batch evaluator (valid/
missing/expired, revoked -> expired, historical versions never
compensating, several current groups of one type, `required=false`,
participants x requirements, deterministic ordering), registered-only
roster, authorization (`event.read` + `document.read`, existence hiding,
participant-row intersection), parity with the single-participant check,
and a constant SQL statement count (no N+1).

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_event_document_requirements_api.py.
"""

import datetime as dt
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event, EventParticipation, EventStaffAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import get_engine, session_scope
from app.main import app
from app.people.authorization import is_person_visible

from .conftest import requires_postgres

_PAST = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
_FUTURE = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
_MED = "medical_certificate"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories -----------------------------------------------------------------


def _grant(user_id: uuid.UUID, permission_code: str, scope_type: str, club_id: uuid.UUID) -> None:
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
                scopes=[RolePermissionScope(scope_type=scope_type)],
            )
        )
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id, club_id=club_id))
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


class _World:
    """One Club + one Event + an uploader User, with helpers to add
    participants, requirements and documents inside one session."""

    def __init__(self, session) -> None:
        self.session = session
        self.club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        uploader_person = Person(last_name="Uploader", first_name="U")
        self.uploader = User(
            person=uploader_person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([self.club, uploader_person, self.uploader])
        session.flush()
        self.event = Event(
            club_id=self.club.id,
            event_type="competition",
            title="Соревнование",
            start_at=dt.datetime(2026, 10, 1, 10, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc),
            timezone="UTC",
            status="published",
        )
        session.add(self.event)
        session.flush()

    def person(
        self,
        last_name: str = "",
        first_name: str = "",
        *,
        status: str | None = "registered",
        member: bool = True,
        user: bool = False,
    ) -> Person:
        person = Person(
            last_name=last_name or f"L-{uuid.uuid4().hex[:8]}",
            first_name=first_name or f"F-{uuid.uuid4().hex[:8]}",
        )
        self.session.add(person)
        if user:
            self.session.add(
                User(
                    person=person,
                    login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
                    status="active",
                )
            )
        self.session.flush()
        if member:
            self.session.add(
                ClubMembership(
                    club_id=self.club.id,
                    person_id=person.id,
                    membership_type="member",
                    status="active",
                    joined_at=_PAST,
                )
            )
        if status is not None:
            self.session.add(
                EventParticipation(
                    event_id=self.event.id, person_id=person.id, registration_status=status
                )
            )
        self.session.flush()
        return person

    def requirement(self, document_type: str = _MED, *, required: bool = True) -> None:
        self.session.add(
            EventDocumentRequirement(
                event_id=self.event.id, document_type=document_type, required=required
            )
        )
        self.session.flush()

    def document(
        self,
        person: Person,
        document_type: str = _MED,
        *,
        status: str = "active",
        expires_at: dt.datetime | None = _FUTURE,
        group_id: uuid.UUID | None = None,
        version: int = 1,
    ) -> uuid.UUID:
        file_row = File(
            storage_key=f"documents/test/{uuid.uuid4()}",
            original_name="cert.pdf",
            mime_type="application/pdf",
            size_bytes=3,
            checksum=hashlib.sha256(b"abc").hexdigest(),
            storage_backend="local",
            created_by=self.uploader.id,
        )
        self.session.add(file_row)
        self.session.flush()
        group = group_id or uuid.uuid4()
        self.session.add(
            Document(
                person_id=person.id,
                document_group_id=group,
                version_number=version,
                document_type=document_type,
                status=status,
                expires_at=expires_at,
                file_id=file_row.id,
            )
        )
        self.session.flush()
        return group


def _admin(
    club_id: uuid.UUID, codes: tuple[str, ...] = ("event.read", "document.read")
) -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Admin", first_name="A")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    for code in codes:
        _grant(user_id, code, "all", club_id)
    _authenticate_as(user_id)
    return user_id


def _matrix(client: TestClient, event_id: uuid.UUID):
    return client.get(f"/api/v1/events/{event_id}/document-requirements/matrix")


def _results(body: dict) -> dict[str, list[tuple[str, bool, str]]]:
    return {
        p["person_id"]: [
            (r["document_type"], r["required"], r["result"]) for r in p["requirements"]
        ]
        for p in body["participants"]
    }


# --- matrix correctness ----------------------------------------------------------


@requires_postgres
def test_event_without_participants(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        session.commit()
        club_id, event_id = world.club.id, world.event.id
    _admin(club_id)

    response = _matrix(client, event_id)

    assert response.status_code == 200
    assert response.json() == {"event_id": str(event_id), "participants": []}


@requires_postgres
def test_event_without_requirements_lists_participants_with_empty_rows(
    client: TestClient,
) -> None:
    with session_scope() as session:
        world = _World(session)
        person = world.person("Иванова", "Анна")
        session.commit()
        club_id, event_id, person_id = world.club.id, world.event.id, person.id
    _admin(club_id)

    body = _matrix(client, event_id).json()

    assert body["participants"] == [
        {
            "person_id": str(person_id),
            "first_name": "Анна",
            "last_name": "Иванова",
            "middle_name": None,
            "requirements": [],
        }
    ]


@pytest.mark.parametrize(
    ("document_kwargs", "expected"),
    [
        ({"status": "active", "expires_at": _FUTURE}, "valid"),
        ({"status": "active", "expires_at": None}, "valid"),
        (None, "missing"),
        ({"status": "active", "expires_at": _PAST}, "expired"),
        ({"status": "expired", "expires_at": _FUTURE}, "expired"),
        ({"status": "revoked", "expires_at": _FUTURE}, "expired"),
    ],
    ids=["valid", "valid-no-expiry", "missing", "elapsed", "expired-status", "revoked"],
)
@requires_postgres
def test_single_participant_result(
    client: TestClient, document_kwargs: dict | None, expected: str
) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        person = world.person()
        if document_kwargs is not None:
            world.document(person, **document_kwargs)
        session.commit()
        club_id, event_id, person_id = world.club.id, world.event.id, str(person.id)
    _admin(club_id)

    assert _results(_matrix(client, event_id).json()) == {person_id: [(_MED, True, expected)]}


@requires_postgres
def test_historical_valid_version_does_not_compensate_expired_current(
    client: TestClient,
) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        person = world.person()
        group = world.document(person, version=1)
        world.document(person, group_id=group, version=2, expires_at=_PAST)
        session.commit()
        club_id, event_id, person_id = world.club.id, world.event.id, str(person.id)
    _admin(club_id)

    assert _results(_matrix(client, event_id).json()) == {person_id: [(_MED, True, "expired")]}


@requires_postgres
def test_current_valid_version_wins_over_historical_expired(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        person = world.person()
        group = world.document(person, version=1, status="revoked")
        world.document(person, group_id=group, version=2)
        session.commit()
        club_id, event_id, person_id = world.club.id, world.event.id, str(person.id)
    _admin(club_id)

    assert _results(_matrix(client, event_id).json()) == {person_id: [(_MED, True, "valid")]}


@requires_postgres
def test_several_current_groups_of_one_type_any_valid_is_valid(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        person = world.person()
        world.document(person, expires_at=_PAST)
        world.document(person, status="revoked")
        world.document(person)
        session.commit()
        club_id, event_id, person_id = world.club.id, world.event.id, str(person.id)
    _admin(club_id)

    assert _results(_matrix(client, event_id).json()) == {person_id: [(_MED, True, "valid")]}


@requires_postgres
def test_not_required_requirement_keeps_required_false(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement("insurance", required=False)
        person = world.person()
        session.commit()
        club_id, event_id, person_id = world.club.id, world.event.id, str(person.id)
    _admin(club_id)

    assert _results(_matrix(client, event_id).json()) == {
        person_id: [("insurance", False, "missing")]
    }


@requires_postgres
def test_participants_by_requirements_ordering_and_isolation(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement(_MED)
        world.requirement("insurance", required=False)
        world.requirement("passport")
        b = world.person("Борисова", "Вера")
        a = world.person("Алексеев", "Иван")
        c = world.person("Борисова", "Анна")
        world.document(a, _MED)
        world.document(a, "passport", expires_at=_PAST)
        world.document(b, "insurance")
        world.document(c, _MED, status="revoked")
        world.document(c, "passport")
        # A document type nobody requires never leaks into the matrix.
        world.document(c, "unrelated")
        session.commit()
        club_id, event_id = world.club.id, world.event.id
        a_id, b_id, c_id = str(a.id), str(b.id), str(c.id)
    _admin(club_id)

    body = _matrix(client, event_id).json()

    assert [p["person_id"] for p in body["participants"]] == [a_id, c_id, b_id]
    assert _results(body) == {
        a_id: [
            ("insurance", False, "missing"),
            (_MED, True, "valid"),
            ("passport", True, "expired"),
        ],
        c_id: [
            ("insurance", False, "missing"),
            (_MED, True, "expired"),
            ("passport", True, "valid"),
        ],
        b_id: [
            ("insurance", False, "valid"),
            (_MED, True, "missing"),
            ("passport", True, "missing"),
        ],
    }


@requires_postgres
def test_matrix_matches_single_participant_endpoint(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement(_MED)
        world.requirement("passport", required=False)
        people = [world.person() for _ in range(3)]
        world.document(people[0], _MED)
        world.document(people[1], "passport", status="revoked")
        world.document(people[2], _MED, expires_at=_PAST)
        world.document(people[2], "passport")
        session.commit()
        club_id, event_id = world.club.id, world.event.id
        person_ids = [p.id for p in people]
    _admin(club_id)

    matrix = _results(_matrix(client, event_id).json())

    for person_id in person_ids:
        single = client.get(f"/api/v1/events/{event_id}/document-requirements/{person_id}")
        assert single.status_code == 200
        assert matrix[str(person_id)] == [
            (r["document_type"], r["required"], r["result"]) for r in single.json()["requirements"]
        ]


# --- participant semantics ------------------------------------------------------


@requires_postgres
def test_only_registered_participants_are_included(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        registered = world.person()
        for status in ("cancelled", "invited", "waitlisted", "declined", "removed"):
            world.person(status=status)
        # A club member with no EventParticipation at all.
        world.person(status=None)
        session.commit()
        club_id, event_id, registered_id = world.club.id, world.event.id, str(registered.id)
    _admin(club_id)

    body = _matrix(client, event_id).json()

    assert [p["person_id"] for p in body["participants"]] == [registered_id]


# --- authorization ----------------------------------------------------------------


@requires_postgres
def test_nonexistent_event_is_404(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        session.commit()
        club_id = world.club.id
    _admin(club_id)

    response = _matrix(client, uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Event not found"


@pytest.mark.parametrize(
    "codes",
    [("document.read",), ("event.read",), ()],
    ids=["no-event-read", "no-document-read", "none"],
)
@requires_postgres
def test_missing_permission_is_existence_hidden_404(
    client: TestClient, codes: tuple[str, ...]
) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        world.person()
        session.commit()
        club_id, event_id = world.club.id, world.event.id
    _admin(club_id, codes)

    response = _matrix(client, event_id)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Event not found"


@requires_postgres
def test_permissions_in_another_club_do_not_grant_access(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        other = _World(session)
        world.person()
        session.commit()
        event_id, other_club_id = world.event.id, other.club.id
    _admin(other_club_id)

    assert _matrix(client, event_id).status_code == 404


@requires_postgres
def test_participant_without_document_read_visibility_is_not_listed(client: TestClient) -> None:
    """Club-scoped `document.read: all` covers only Persons with a
    ClubMembership in that Club (the single-participant check's own
    rule) — such a registered participant is omitted, never exposed."""
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        visible = world.person()
        world.person(member=False)
        session.commit()
        club_id, event_id, visible_id = world.club.id, world.event.id, str(visible.id)
    _admin(club_id)

    body = _matrix(client, event_id).json()

    assert [p["person_id"] for p in body["participants"]] == [visible_id]


@requires_postgres
def test_instructor_own_events_with_document_read_sees_full_roster(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        p1, p2 = world.person(), world.person()
        instructor_person = world.person(status=None, user=True)
        session.flush()
        instructor = session.execute(
            select(User).where(User.person_id == instructor_person.id)
        ).scalar_one()
        session.add(
            EventStaffAssignment(
                event_id=world.event.id,
                user_id=instructor.id,
                role_in_event="instructor",
                valid_from=_PAST,
            )
        )
        session.commit()
        club_id, event_id, instructor_id = world.club.id, world.event.id, instructor.id
        expected = {str(p1.id), str(p2.id)}
    _grant(instructor_id, "event.read", "own_events", club_id)
    _grant(instructor_id, "document.read", "all", club_id)
    _authenticate_as(instructor_id)

    body = _matrix(client, event_id).json()

    assert {p["person_id"] for p in body["participants"]} == expected


@requires_postgres
def test_instructor_own_events_without_document_read_is_404(client: TestClient) -> None:
    with session_scope() as session:
        world = _World(session)
        world.person()
        instructor_person = world.person(status=None, user=True)
        session.flush()
        instructor = session.execute(
            select(User).where(User.person_id == instructor_person.id)
        ).scalar_one()
        session.add(
            EventStaffAssignment(
                event_id=world.event.id,
                user_id=instructor.id,
                role_in_event="instructor",
                valid_from=_PAST,
            )
        )
        session.commit()
        club_id, event_id, instructor_id = world.club.id, world.event.id, instructor.id
    _grant(instructor_id, "event.read", "own_events", club_id)
    _authenticate_as(instructor_id)

    assert _matrix(client, event_id).status_code == 404


@requires_postgres
def test_event_read_self_row_visibility_limits_rows_to_own(client: TestClient) -> None:
    """Intersection of the existing rules: with `event.read: self` the
    participant roster shows only the requester's own row (as
    `GET .../participants` does), even though `document.read: all` would
    allow every participant's document data."""
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        me = world.person(user=True)
        world.person()
        session.commit()
        me_user = session.execute(select(User).where(User.person_id == me.id)).scalar_one()
        club_id, event_id, me_user_id, me_id = world.club.id, world.event.id, me_user.id, str(me.id)
    _grant(me_user_id, "event.read", "self", club_id)
    _grant(me_user_id, "document.read", "all", club_id)
    _authenticate_as(me_user_id)

    body = _matrix(client, event_id).json()

    assert [p["person_id"] for p in body["participants"]] == [me_id]


@requires_postgres
def test_document_read_self_is_404_like_the_requirement_list(client: TestClient) -> None:
    """The matrix uses the requirement-list endpoint's own club-level
    `document.read` gate (events-api.md §31.2), so a `self`-scoped
    `document.read` gets the same existence-hiding 404 there and here."""
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        me = world.person(user=True)
        session.commit()
        me_user = session.execute(select(User).where(User.person_id == me.id)).scalar_one()
        club_id, event_id, me_user_id = world.club.id, world.event.id, me_user.id
    _grant(me_user_id, "event.read", "self", club_id)
    _grant(me_user_id, "document.read", "self", club_id)
    _authenticate_as(me_user_id)

    assert client.get(f"/api/v1/events/{event_id}/document-requirements").status_code == 404
    assert _matrix(client, event_id).status_code == 404


# --- performance: no N+1 ---------------------------------------------------------


def _build_event(participants: int, requirements: int) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        world = _World(session)
        types = [f"type_{i}" for i in range(requirements)]
        for document_type in types:
            world.requirement(document_type)
        for _ in range(participants):
            person = world.person()
            for document_type in types:
                world.document(person, document_type)
        session.commit()
        return world.club.id, world.event.id


def _statement_count(client: TestClient, event_id: uuid.UUID, path: str = "matrix") -> int:
    statements: list[str] = []

    def _count(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    engine = get_engine()
    sa_event.listen(engine, "before_cursor_execute", _count)
    try:
        response = client.get(f"/api/v1/events/{event_id}/document-requirements/{path}")
    finally:
        sa_event.remove(engine, "before_cursor_execute", _count)
    assert response.status_code == 200
    return len(statements)


@requires_postgres
def test_statement_count_is_independent_of_participants_and_requirements(
    client: TestClient,
) -> None:
    small_club, small_event = _build_event(participants=1, requirements=1)
    large_club, large_event = _build_event(participants=8, requirements=4)
    _admin(small_club)
    small = _statement_count(client, small_event)
    _admin(large_club)
    large = _statement_count(client, large_event)

    assert large == small


@requires_postgres
def test_single_participant_check_statement_count_is_independent_of_requirements(
    client: TestClient,
) -> None:
    """The single-participant endpoint now goes through the same batch
    evaluator: one Document query, not one per requirement."""
    counts = []
    for requirements in (1, 5):
        with session_scope() as session:
            world = _World(session)
            for i in range(requirements):
                world.requirement(f"type_{i}")
            person = world.person()
            session.commit()
            club_id, event_id, person_id = world.club.id, world.event.id, person.id
        _admin(club_id)
        counts.append(_statement_count(client, event_id, str(person_id)))

    assert counts[0] == counts[1]


# --- authorization parity with is_person_visible / the single-participant check -
#
# For every canonical `document.read` scope (ADR-0013 / the DB CHECK on
# `role_permission_scopes.scope_type`: all, own_groups, own_events, self,
# children, none — plus `all` on a global, club_id-less assignment), with
# `event.read: all` held constant so only `document.read` varies:
#
# - `is_person_visible(..., "document.read")` and
#   `GET .../document-requirements/{person_id}` agree for every registered
#   participant (the single-participant endpoint IS that check);
# - the matrix never lists a participant that check would hide;
# - where the matrix's club-level `document.read` gate (the requirement
#   list's own gate, events-api.md §31.2) passes — `all` only — the matrix
#   lists exactly the participants that check shows; for every other scope
#   the gate fails closed with the same existence-hiding 404 as the
#   requirement list.


_PARITY_CASES = {
    # scope label: (scope_type, club-scoped?, expected single-visible labels, matrix status)
    "all-club": ("all", True, {"self", "child", "grouped", "member"}, 200),
    "all-global": ("all", False, {"self", "child", "grouped", "member", "nonmember"}, 200),
    "own_groups": ("own_groups", True, {"grouped"}, 404),
    "own_events": ("own_events", True, set(), 404),
    "self": ("self", True, {"self"}, 404),
    "children": ("children", True, {"child"}, 404),
    "none": ("none", True, set(), 404),
}


def _parity_world() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    """Returns (club_id, event_id, actor_user_id, {label: person_id}).

    The actor is: a registered participant themself ("self"), the active
    guardian of "child" and of "cancelled_child", and the active instructor
    of a Group containing "grouped". "member" is an unrelated club member,
    "nonmember" a registered participant with no ClubMembership.
    "cancelled_child" is never a matrix participant (not `registered`)."""
    with session_scope() as session:
        world = _World(session)
        world.requirement()
        actor_person = world.person("Актор", "А", user=True)
        people = {
            "self": actor_person,
            "child": world.person("Ребёнок", "Б"),
            "cancelled_child": world.person("Ребёнок", "В", status="cancelled"),
            "grouped": world.person("Группа", "Г"),
            "member": world.person("Член", "Д"),
            "nonmember": world.person("Внешний", "Е", member=False),
        }
        actor = session.execute(select(User).where(User.person_id == actor_person.id)).scalar_one()
        for label in ("child", "cancelled_child"):
            session.add(
                GuardianRelationship(
                    guardian_person_id=actor_person.id,
                    child_person_id=people[label].id,
                    relationship_type="parent",
                    status="active",
                    valid_from=_PAST,
                )
            )
        group = Group(club_id=world.club.id, name="Группа", status="active", valid_from=_PAST)
        session.add(group)
        session.flush()
        grouped_membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == people["grouped"].id)
        ).scalar_one()
        session.add_all(
            [
                GroupMembership(
                    group_id=group.id,
                    club_membership_id=grouped_membership.id,
                    membership_status="active",
                    valid_from=_PAST,
                ),
                GroupInstructorAssignment(
                    group_id=group.id,
                    user_id=actor.id,
                    role_in_group="instructor",
                    valid_from=_PAST,
                ),
            ]
        )
        session.commit()
        return (
            world.club.id,
            world.event.id,
            actor.id,
            {label: person.id for label, person in people.items()},
        )


@pytest.mark.parametrize("case", list(_PARITY_CASES), ids=list(_PARITY_CASES))
@requires_postgres
def test_matrix_participant_visibility_parity_with_single_check(
    client: TestClient, case: str
) -> None:
    scope_type, club_scoped, expected_single, expected_matrix_status = _PARITY_CASES[case]
    club_id, event_id, actor_id, people = _parity_world()
    _grant(actor_id, "event.read", "all", club_id)
    _grant(actor_id, "document.read", scope_type, club_id if club_scoped else None)
    _authenticate_as(actor_id)
    registered = {label: pid for label, pid in people.items() if label != "cancelled_child"}

    # 1. is_person_visible and the single-participant endpoint agree.
    single_visible: set[str] = set()
    with session_scope() as session:
        for label, person_id in people.items():
            function_visible = is_person_visible(
                session, person_id=person_id, user_id=actor_id, permission_code="document.read"
            )
            response = client.get(f"/api/v1/events/{event_id}/document-requirements/{person_id}")
            assert response.status_code == (200 if function_visible else 404), label
            if function_visible and label in registered:
                single_visible.add(label)
    assert single_visible == expected_single

    # 2. The matrix never shows more than that check, and exactly as much
    #    wherever its club-level gate admits the requester.
    response = _matrix(client, event_id)
    assert response.status_code == expected_matrix_status
    labels_by_id = {str(pid): label for label, pid in people.items()}
    matrix_visible = (
        {labels_by_id[p["person_id"]] for p in response.json()["participants"]}
        if response.status_code == 200
        else set()
    )
    assert "cancelled_child" not in matrix_visible
    assert matrix_visible <= single_visible
    if expected_matrix_status == 200:
        assert matrix_visible == single_visible
    else:
        # Same fail-closed gate as the requirement list endpoint.
        requirement_list = client.get(f"/api/v1/events/{event_id}/document-requirements")
        assert requirement_list.status_code == 404
