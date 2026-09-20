"""HTTP-level integration tests for /api/v1/persons and
/api/v1/memberships (Issue #62, extended by TH-0102): deterministic
backend authorization, canonical scope resolution
(`all`/`own_groups`/`self`/`children`/`none`), IDOR regression coverage,
sensitive-field withholding, audit recording, and transaction/fail-closed
behavior.

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, matching tests/integration/test_events_api.py's pattern.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.audit.service import record_audit_event
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.events import EventParticipation
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ----------------------------------------------------


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
        "membership_type": "member",
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


def _grant_via_system_admin_role(user_id: uuid.UUID, *, club_id: uuid.UUID | None = None) -> None:
    """Grant `user_id` an `all`-scope assignment through the actual
    canonical system `admin` role (seeded by migration e5ae1ad9e1e1 with
    `is_system = true`) — distinct from `_grant_permission`, which always
    creates a brand-new, non-system ad hoc role. Required for exercising
    ADR-0035 §5's `birth_date` rule, which checks the granting role's own
    identity (`Role.code == "admin"` and `Role.is_system`), not merely
    the assignment's scope.
    """
    with session_scope() as session:
        admin_role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=admin_role.id, scope_type="all", club_id=club_id
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


def _latest_audit_row(*, action: str, resource_id: uuid.UUID) -> AuditLog | None:
    with session_scope() as session:
        return session.execute(
            select(AuditLog)
            .where(AuditLog.action == action, AuditLog.resource_id == resource_id)
            .order_by(AuditLog.occurred_at.desc())
        ).scalars().first()


# --- Person: create -----------------------------------------------------


@requires_postgres
def test_create_person_with_global_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={
            "first_name": "Anna",
            "last_name": "Petrova",
            "birth_date": "2010-05-01",
            "phone": "+70000000000",
            "email": "anna@example.com",
            "address": "1 Main St",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["first_name"] == "Anna"
    assert body["last_name"] == "Petrova"
    assert body["birth_date"] == "2010-05-01"
    # ADR-0035 §3/§4 supersedes ADR-0025 §8: contact fields are now
    # returned to a requester already authorized for the record (the
    # creator, here, has an `all`-scope grant).
    assert body["phone"] == "+70000000000"
    assert body["email"] == "anna@example.com"
    assert body["address"] == "1 Main St"
    assert "status" not in body

    audit_row = _latest_audit_row(action="person.created", resource_id=uuid.UUID(body["id"]))
    assert audit_row is not None
    assert audit_row.actor_type == "user"
    assert audit_row.actor_user_id == user_id
    assert audit_row.resource_type == "person"
    assert audit_row.outcome == "success"

    # TH-0111 / Issue #140: since no other qualifying assignment is
    # club-scoped, `resolve_current_club_id_for_person_create` falls back
    # to the sole Club row — the ClubMembership auto-created alongside
    # this Person must land in that Club.
    with session_scope() as session:
        membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == uuid.UUID(body["id"]))
        ).scalar_one()
        assert membership.club_id == club_id


@requires_postgres
def test_create_person_without_permission_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_create_person_with_club_scoped_all_assignment_succeeds(client: TestClient) -> None:
    """TH-0106 / Issue #131: a club-scoped `all`-scope `person.create`
    assignment is exactly as sufficient as a global one. Person is
    Club-neutral (ADR-0017) and a not-yet-created Person has no target
    Club to check `assignment.club_id` against at all — this used to be
    (incorrectly) rejected here, which is precisely the bug that made the
    bootstrap-created primary administrator (whose own `UserRoleAssignment`
    is club-scoped, never global — see `app.authentication.bootstrap`)
    unable to create a Person.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text


@requires_postgres
def test_create_person_as_bootstrap_primary_administrator_succeeds(client: TestClient) -> None:
    """End-to-end regression test for the actual reported bug (TH-0106 /
    Issue #131): the primary administrator created by
    `bootstrap_initial_administrator` (TH-0091 / PR #104, unmodified by
    this fix) holds a club-scoped, never global, `all`-scope assignment —
    `POST /persons` must succeed for that exact real-world caller, not
    just for a hand-built test assignment.
    """
    from app.authentication.bootstrap import bootstrap_initial_administrator

    with session_scope() as session:
        result = bootstrap_initial_administrator(
            session,
            club_name=f"Bootstrap Club {uuid.uuid4().hex[:8]}",
            email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
            password="a-strong-bootstrap-password",
        )
        admin_user_id = result.user_id
    _authenticate_as(admin_user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text


@requires_postgres
def test_create_person_with_non_all_scope_is_forbidden(client: TestClient) -> None:
    """TH-0106 / Issue #131 narrows only the club-boundary condition, not
    the scope requirement: a `person.create` assignment with any
    `scope_type` other than `all` (e.g. `own_groups`) must still be
    rejected — this is not a new, broader grant.
    """
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.create", scope_type="own_groups")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_create_person_also_creates_active_club_membership(client: TestClient) -> None:
    """TH-0111 / Issue #140 (test A, happy path): `POST /persons` is an
    atomic "add Person to the current Club" operation — creating a Person
    without a `ClubMembership` left the club-scoped admin unable to see
    the very Person they just created (`person_visibility_filter`'s
    club-scoped `all` predicate requires an actual membership row). This
    supersedes the old (pre-TH-0111) `does_not_create_club_membership`
    test, whose premise — that Person and ClubMembership creation must
    stay decoupled — was the exact fragile design this issue fixes.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    before = datetime.datetime.now(datetime.timezone.utc)
    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    new_person_id = uuid.UUID(response.json()["id"])

    with session_scope() as session:
        membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == new_person_id)
        ).scalar_one()
        assert membership.club_id == club_id
        assert membership.status == "active"
        assert membership.membership_type == "member"
        assert membership.joined_at is not None
        assert membership.joined_at >= before
        assert membership.left_at is None

    audit_row = _latest_audit_row(action="membership.created", resource_id=membership.id)
    assert audit_row is not None
    assert audit_row.actor_type == "user"
    assert audit_row.actor_user_id == user_id
    assert audit_row.club_id == club_id
    assert audit_row.resource_type == "club_membership"
    assert audit_row.outcome == "success"


@requires_postgres
def test_create_person_new_person_immediately_visible_to_club_scoped_admin(
    client: TestClient,
) -> None:
    """TH-0111 / Issue #140 (test B, visibility): the exact reported bug
    — a club-scoped admin creates a Person, then cannot find it via
    either the list or the detail endpoint, because
    `person_visibility_filter`'s club-scoped `all` predicate requires an
    actual `ClubMembership` row in that Club. Must now succeed via both.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all", club_id=club_id)
    _grant_permission(user_id, "person.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    new_person_id = response.json()["id"]

    detail_response = client.get(f"/api/v1/persons/{new_person_id}")
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["id"] == new_person_id

    list_response = client.get("/api/v1/persons")
    assert list_response.status_code == 200, list_response.text
    listed_ids = {item["id"] for item in list_response.json()["items"]}
    assert new_person_id in listed_ids


@requires_postgres
def test_create_person_rolls_back_entirely_on_membership_failure(client: TestClient) -> None:
    """TH-0111 / Issue #140 (test C, atomicity): if ClubMembership
    creation fails, the Person must not remain in the database either —
    no partially-created Person, no orphaned audit rows. Forces the
    failure with a real DB constraint violation (an invalid `club_id`
    that cannot satisfy `ClubMembership`'s FK), exercised directly against
    the service function so the failure happens inside the same
    transaction boundary `POST /persons` uses.
    """
    from app.people.service import create_person_with_membership

    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id

    nonexistent_club_id = uuid.uuid4()
    with session_scope() as session, pytest.raises(IntegrityError):
        create_person_with_membership(
            session,
            first_name="Anna",
            last_name="Petrova",
            middle_name=None,
            birth_date=None,
            phone=None,
            email=None,
            address=None,
            club_id=nonexistent_club_id,
            actor_user_id=user_id,
        )

    with session_scope() as session:
        orphaned_persons = session.execute(
            select(Person).where(Person.first_name == "Anna", Person.last_name == "Petrova")
        ).scalars().all()
        assert orphaned_persons == []
        orphaned_memberships = session.execute(
            select(ClubMembership).where(ClubMembership.club_id == nonexistent_club_id)
        ).scalars().all()
        assert orphaned_memberships == []
        audit_rows = session.execute(
            select(AuditLog).where(AuditLog.action.in_(["person.created", "membership.created"]))
        ).scalars().all()
        assert all(row.actor_user_id != user_id for row in audit_rows)


@requires_postgres
def test_create_person_does_not_create_user_or_role_assignment(client: TestClient) -> None:
    """TH-0111 / Issue #140 (test D): `POST /persons` never provisions a
    system account or grants any permission for the new Person — Role
    assignment stays a separate, later operation (ADR-0005/ADR-0035).
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    new_person_id = uuid.UUID(response.json()["id"])

    with session_scope() as session:
        linked_users = session.execute(
            select(User).where(User.person_id == new_person_id)
        ).scalars().all()
        assert linked_users == []


@requires_postgres
def test_create_person_does_not_create_other_domain_relationships(client: TestClient) -> None:
    """TH-0111 / Issue #140 (test E): no GuardianRelationship,
    GroupMembership, or EventParticipation is auto-created alongside the
    new Person and its initial ClubMembership — every such relationship
    remains a distinct, later operation triggered from Person detail.
    (GroupInstructorAssignment and RegistrationRequest are not checked
    directly: the former links a User, not a Person, to a Group — see
    test D, which confirms no User is ever created here — and the latter
    has no corresponding table in this codebase yet.)
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    new_person_id = uuid.UUID(response.json()["id"])

    with session_scope() as session:
        guardian_rows = session.execute(
            select(GuardianRelationship).where(
                (GuardianRelationship.guardian_person_id == new_person_id)
                | (GuardianRelationship.child_person_id == new_person_id)
            )
        ).scalars().all()
        assert guardian_rows == []

        membership = session.execute(
            select(ClubMembership).where(ClubMembership.person_id == new_person_id)
        ).scalar_one()
        group_memberships = session.execute(
            select(GroupMembership).where(GroupMembership.club_membership_id == membership.id)
        ).scalars().all()
        assert group_memberships == []

        # GroupInstructorAssignment links a User (not a Person) to a
        # Group; test D already confirms no User is created for this
        # Person at all, so no such assignment can exist for them either.
        event_participations = session.execute(
            select(EventParticipation).where(EventParticipation.person_id == new_person_id)
        ).scalars().all()
        assert event_participations == []


@requires_postgres
def test_create_person_denies_cross_club_scoped_assignment_boundary_still_enforced(
    client: TestClient,
) -> None:
    """TH-0111 / Issue #140 (test F): the compound operation's own Club
    resolution (`resolve_current_club_id_for_person_create`) must not
    weaken the existing permission contract — a caller with no qualifying
    `person.create` assignment at all is still denied, exactly as before
    TH-0111. (Cross-club *visibility* boundary coverage for the resulting
    membership already exists in the `Person: read` tests below, e.g.
    `test_get_person_club_scoped_all_denies_person_without_membership_in_that_club`.)
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


# --- Person: create — single-Club invariant (TH-0111 PO follow-up) ------
#
# TourCRM is not, and is not becoming, a multi-club product in the current
# MVP: `club_id` stays a required technical column everywhere (models,
# authorization, API) — nothing here removes it or adds Club selection.
# The only change is that `resolve_current_club_id_for_person_create` no
# longer silently resolves `.first()`/an unverified assignment `club_id`:
# it fails closed whenever "exactly one Club" doesn't actually hold.


@requires_postgres
def test_create_person_with_zero_clubs_fails_closed() -> None:
    """No Club exists at all (should be unreachable via bootstrap in
    practice) — must not silently proceed; the club-count `SELECT`
    resolving to nothing surfaces as the canonical, detail-free 500
    `internal_error` envelope (`app.people.authorization.
    NoClubConfiguredError`, uncaught by design — see its docstring) rather
    than a Person being created without any Club context at all.

    Uses a local `raise_server_exceptions=False` client (matching
    `tests/api/conftest.py`'s `real_client` pattern) instead of this
    file's shared `client` fixture: with `raise_server_exceptions=True`,
    an uncaught exception propagates out of Starlette's
    `BaseHTTPMiddleware` layer before the registered `Exception` handler
    converts it to a response, which would make this test fail on the
    raw exception instead of asserting the client-facing contract.
    """
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with session_scope() as session:
            person = _make_person()
            user = _make_user(person)
            session.add_all([person, user])
            session.commit()
            user_id = user.id
        _grant_permission(user_id, "person.create", scope_type="all")
        _authenticate_as(user_id)

        response = client.post(
            "/api/v1/persons",
            json={"first_name": "Anna", "last_name": "Petrova"},
            headers=_csrf_headers(client),
        )
        assert response.status_code == 500, response.text
        assert response.json()["error"]["code"] == "internal_error"

        with session_scope() as session:
            orphaned = session.execute(
                select(Person).where(Person.first_name == "Anna", Person.last_name == "Petrova")
            ).scalars().all()
            assert orphaned == []
    finally:
        app.dependency_overrides.clear()


@requires_postgres
def test_create_person_with_multiple_clubs_fails_closed() -> None:
    """More than one Club exists — a state the current product/MVP does
    not support and should never reach in normal usage (there is still no
    `POST /clubs` endpoint and bootstrap refuses to run twice). A global
    `person.create` assignment gives no way to disambiguate which Club is
    "current," so this must fail closed (`MultipleClubsConfiguredError`,
    surfaced as the generic 500 `internal_error`) rather than silently
    picking one via `.first()`. Uses a local
    `raise_server_exceptions=False` client — see
    `test_create_person_with_zero_clubs_fails_closed`'s docstring.
    """
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with session_scope() as session:
            club_a = _make_club()
            club_b = _make_club()
            person = _make_person()
            user = _make_user(person)
            session.add_all([club_a, club_b, person, user])
            session.commit()
            user_id = user.id
        _grant_permission(user_id, "person.create", scope_type="all")
        _authenticate_as(user_id)

        response = client.post(
            "/api/v1/persons",
            json={"first_name": "Anna", "last_name": "Petrova"},
            headers=_csrf_headers(client),
        )
        assert response.status_code == 500, response.text
        assert response.json()["error"]["code"] == "internal_error"

        with session_scope() as session:
            orphaned = session.execute(
                select(Person).where(Person.first_name == "Anna", Person.last_name == "Petrova")
            ).scalars().all()
            assert orphaned == []
    finally:
        app.dependency_overrides.clear()


@requires_postgres
def test_resolve_current_club_id_uses_sole_club_for_club_scoped_assignment() -> None:
    """Test A precondition, isolated at the resolver level: exactly one
    Club exists, and the caller's `person.create` assignment is scoped to
    that same Club — `resolve_current_club_id_for_person_create` must
    return exactly that Club's id (already exercised end-to-end by
    `test_create_person_with_club_scoped_all_assignment_succeeds`; this
    adds direct unit-level coverage of the resolver itself).
    """
    from app.people.authorization import resolve_current_club_id_for_person_create

    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id, club_id = user.id, club.id
    _grant_permission(user_id, "person.create", scope_type="all", club_id=club_id)

    with session_scope() as session:
        resolved = resolve_current_club_id_for_person_create(session, user_id)
        assert resolved == club_id


@requires_postgres
def test_resolve_current_club_id_denies_club_scoped_assignment_pointing_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A club-scoped `person.create` assignment whose `club_id` does not
    match the system's sole Club must fail closed rather than being used
    anyway or silently swapped for the sole Club.

    `UserRoleAssignment.club_id` has a real `FOREIGN KEY ... ON DELETE
    RESTRICT` to `clubs.id`, so a *persisted* assignment can only ever
    reference a Club that actually exists — meaning this exact mismatch
    (one real Club overall, but the assignment points elsewhere) cannot
    be produced by inserting a second real Club: that would instead make
    `test_create_person_with_multiple_clubs_fails_closed`'s scenario
    apply. This test exercises the resolver's own defensive equality
    check directly by stubbing `applicable_assignments` to return a
    club-scoped assignment referencing an arbitrary, unpersisted club id
    — the shape a corrupted/legacy row would have if the FK were ever
    relaxed — while a single real Club exists in the database.
    """
    from app.authorization.service import AuthorizationDenied
    from app.people import authorization as people_authorization

    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()

    class _ElsewhereAssignment:
        scope_type = "all"
        club_id = uuid.uuid4()

    monkeypatch.setattr(
        people_authorization,
        "applicable_assignments",
        lambda *args, **kwargs: [_ElsewhereAssignment()],
    )

    with session_scope() as session, pytest.raises(AuthorizationDenied):
        people_authorization.resolve_current_club_id_for_person_create(session, uuid.uuid4())


@requires_postgres
def test_create_person_rejects_missing_required_field(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.create", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons", json={"last_name": "Petrova"}, headers=_csrf_headers(client)
    )
    assert response.status_code == 422, response.text


@requires_postgres
def test_create_person_with_only_person_update_permission_is_forbidden(client: TestClient) -> None:
    """ADR-0035 §2: `person.create` is its own canonical permission,
    distinct from `person.update` — holding only `person.update`, however
    broad its scope, must never authorize creating a new Person.
    """
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


@requires_postgres
def test_create_person_accepts_photo_file_id(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        user = _make_user(person)
        session.add_all([club, person, user])
        session.commit()
        user_id = user.id
    _grant_permission(user_id, "person.create", scope_type="all")
    _authenticate_as(user_id)
    photo_file_id = uuid.uuid4()

    response = client.post(
        "/api/v1/persons",
        json={"first_name": "Anna", "last_name": "Petrova", "photo_file_id": str(photo_file_id)},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    assert response.json()["photo_file_id"] == str(photo_file_id)


# --- Person: read -------------------------------------------------------


@requires_postgres
def test_get_person_all_scope_succeeds(client: TestClient) -> None:
    """ADR-0035 §3/§4 supersedes ADR-0025 §8: an `all`-scope requester
    authorized to read the Person also sees its contact fields — they are
    ordinary Person fields with no separate permission.
    """
    with session_scope() as session:
        target = _make_person(
            first_name="Target", phone="+71234567890", email="target@example.com", address="Home"
        )
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([target, requester_person, requester_user])
        session.commit()
        target_id, user_id = target.id, requester_user.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{target_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(target_id)
    assert body["phone"] == "+71234567890"
    assert body["email"] == "target@example.com"
    assert body["address"] == "Home"


@requires_postgres
def test_get_person_club_scoped_all_sees_person_with_membership_in_that_club(
    client: TestClient,
) -> None:
    """Issue #62 accepted decision: a club-scoped `all` assignment may
    access a Person who has a ClubMembership in that specific Club — the
    one case where a club-scoped `all` assignment does grant Person
    access (creation still requires global `all`; see
    test_create_person_with_club_scoped_all_assignment_is_forbidden).
    """
    with session_scope() as session:
        club = _make_club()
        target = _make_person(first_name="Target")
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, target, requester_person, requester_user])
        session.commit()
        session.add(_make_club_membership(club, target))
        session.commit()
        target_id, user_id, club_id = target.id, requester_user.id, club.id
    _grant_permission(user_id, "person.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{target_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(target_id)


@requires_postgres
def test_get_person_club_scoped_all_denies_person_without_membership_in_that_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        target = _make_person(first_name="Target")
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, target, requester_person, requester_user])
        session.commit()
        # target has no ClubMembership in `club` at all.
        target_id, user_id, club_id = target.id, requester_user.id, club.id
    _grant_permission(user_id, "person.read", scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{target_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_get_person_none_scope_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        target = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([target, requester_person, requester_user])
        session.commit()
        target_id, user_id = target.id, requester_user.id
    _grant_permission(user_id, "person.read", scope_type="none")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{target_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_get_person_nonexistent_id_returns_identical_404(client: TestClient) -> None:
    # Two separate users: permission grants are additive, so reusing one
    # user for both the "all" and "none" cases would leave the earlier
    # "all" grant still in effect for the second request.
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        denied_person = _make_person()
        denied_user = _make_user(denied_person)
        other_person = _make_person()
        session.add_all(
            [requester_person, requester_user, denied_person, denied_user, other_person]
        )
        session.commit()
        user_id, denied_user_id, other_id = requester_user.id, denied_user.id, other_person.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _grant_permission(denied_user_id, "person.read", scope_type="none")

    _authenticate_as(user_id)
    missing_response = client.get(f"/api/v1/persons/{uuid.uuid4()}")

    _authenticate_as(denied_user_id)
    denied_response = client.get(f"/api/v1/persons/{other_id}")

    assert missing_response.status_code == denied_response.status_code == 404
    assert missing_response.json()["error"]["code"] == denied_response.json()["error"]["code"]


@requires_postgres
def test_self_scope_sees_own_person_but_not_others(client: TestClient) -> None:
    with session_scope() as session:
        own_person = _make_person()
        own_user = _make_user(own_person)
        other_person = _make_person()
        session.add_all([own_person, own_user, other_person])
        session.commit()
        own_person_id, user_id, other_person_id = own_person.id, own_user.id, other_person.id
    _grant_permission(user_id, "person.read", scope_type="self")
    _authenticate_as(user_id)

    own_response = client.get(f"/api/v1/persons/{own_person_id}")
    assert own_response.status_code == 200, own_response.text

    other_response = client.get(f"/api/v1/persons/{other_person_id}")
    assert other_response.status_code == 404, other_response.text


@requires_postgres
def test_own_groups_scope_sees_person_in_responsible_group_but_not_unrelated_person(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        unrelated_person = _make_person()
        session.add_all(
            [
                club,
                instructor_person,
                instructor_user,
                member_person,
                unrelated_person,
            ]
        )
        session.commit()
        group = _make_group(club)
        member_club_membership = _make_club_membership(club, member_person)
        session.add_all([group, member_club_membership])
        session.commit()
        session.add(_make_group_membership(group, member_club_membership))
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id = instructor_user.id
        member_person_id = member_person.id
        unrelated_person_id = unrelated_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    member_response = client.get(f"/api/v1/persons/{member_person_id}")
    assert member_response.status_code == 200, member_response.text

    unrelated_response = client.get(f"/api/v1/persons/{unrelated_person_id}")
    assert unrelated_response.status_code == 404, unrelated_response.text


def _setup_group_membership(
    session,
    *,
    club: Club,
    person: Person,
    club_membership_status: str = "active",
    group_membership_status: str = "active",
):
    """Commit an active-by-default Club/Group membership chain for
    `person` in `club`: ClubMembership -> GroupMembership -> Group.
    Returns (club_membership, group).
    """
    club_membership = _make_club_membership(club, person, status=club_membership_status)
    session.add(club_membership)
    session.commit()
    group = _make_group(club)
    session.add(group)
    session.commit()
    session.add(
        _make_group_membership(
            group, club_membership, membership_status=group_membership_status
        )
    )
    session.commit()
    return club_membership, group


@requires_postgres
def test_own_groups_club_scoped_grant_sees_person_in_same_club(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        _, group = _setup_group_membership(session, club=club, person=member_person)
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        club_id = club.id
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups", club_id=club_id)
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 200, response.text


@requires_postgres
def test_own_groups_club_scoped_grant_denies_person_in_other_club(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club_a, club_b, instructor_person, instructor_user, member_person])
        session.commit()
        # The instructor's own_groups grant is scoped to club_a, but the
        # responsible-group relationship (and the member's membership)
        # exist entirely in club_b.
        _, group = _setup_group_membership(session, club=club_b, person=member_person)
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        club_a_id = club_a.id
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups", club_id=club_a_id)
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_person_with_no_group_membership(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        # member_person has an active ClubMembership but was never added
        # to any Group.
        club_membership = _make_club_membership(club, member_person)
        group = _make_group(club)
        session.add_all([club_membership, group])
        session.commit()
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_inactive_group_membership(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        _, group = _setup_group_membership(
            session, club=club, person=member_person, group_membership_status="ended"
        )
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_inactive_club_membership(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        _, group = _setup_group_membership(
            session, club=club, person=member_person, club_membership_status="archived"
        )
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_inactive_group_instructor_assignment(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        _, group = _setup_group_membership(session, club=club, person=member_person)
        session.add(
            _make_group_instructor_assignment(group, instructor_user, valid_to=_utc(2020, 6, 1))
        )
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_different_instructor(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        other_instructor_person = _make_person()
        other_instructor_user = _make_user(other_instructor_person)
        member_person = _make_person()
        session.add_all(
            [
                club,
                instructor_person,
                instructor_user,
                other_instructor_person,
                other_instructor_user,
                member_person,
            ]
        )
        session.commit()
        _, group = _setup_group_membership(session, club=club, person=member_person)
        # Only other_instructor_user is assigned as instructor for this group.
        session.add(_make_group_instructor_assignment(group, other_instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_co_membership_without_instructor_assignment(client: TestClient) -> None:
    """A requester who is merely another member of the same Group (no
    GroupInstructorAssignment at all) must not gain access via
    own_groups — co-membership alone is never sufficient (Issue #62
    accepted decisions: "Never infer access from co-membership").
    """
    with session_scope() as session:
        club = _make_club()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        member_person = _make_person()
        session.add_all([club, requester_person, requester_user, member_person])
        session.commit()
        _, group = _setup_group_membership(session, club=club, person=requester_person)
        member_club_membership = _make_club_membership(club, member_person)
        session.add(member_club_membership)
        session.commit()
        session.add(_make_group_membership(group, member_club_membership))
        session.commit()
        requester_user_id, member_person_id = requester_user.id, member_person.id
    _grant_permission(requester_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(requester_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_own_groups_denies_cross_club_group_for_person_even_with_inconsistent_data(
    client: TestClient,
) -> None:
    """Same requirement as the Membership-side equivalent
    (test_membership_own_groups_denies_cross_club_group_even_with_inconsistent_data):
    the Person authorization query itself must reject a GroupMembership
    that inconsistently points to a Group in a different Club than the
    Person's own ClubMembership, not rely on ADR-0022/service-layer
    integrity to prevent this state from ever existing.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club_a, club_b, instructor_person, instructor_user, member_person])
        session.commit()
        # member_person's ClubMembership is in club_a, but the Group (and
        # its GroupMembership row) are in club_b.
        club_membership = _make_club_membership(club_a, member_person)
        group = _make_group(club_b)
        session.add_all([club_membership, group])
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/persons/{member_person_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_list_persons_all_scope_returns_all_persons(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        p1 = _make_person()
        p2 = _make_person()
        session.add_all([requester_person, requester_user, p1, p2])
        session.commit()
        user_id = requester_user.id
    _grant_permission(user_id, "person.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get("/api/v1/persons")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] >= 3  # requester + p1 + p2
    # ADR-0035 §3/§4: contact fields are ordinary Person fields, present
    # for every item the requester is authorized to see (value may be
    # null when unset, but the key itself is not withheld).
    for item in body["items"]:
        assert "phone" in item


@requires_postgres
def test_list_persons_none_scope_returns_empty(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([requester_person, requester_user])
        session.commit()
        user_id = requester_user.id
    _grant_permission(user_id, "person.read", scope_type="none")
    _authenticate_as(user_id)

    response = client.get("/api/v1/persons")
    assert response.status_code == 200, response.text
    assert response.json()["pagination"]["total"] == 0


@requires_postgres
def test_list_persons_pagination_reflects_authorization_filtering_not_all_rows(
    client: TestClient,
) -> None:
    """Authorization filtering must happen inside the SQL query (before
    COUNT/LIMIT/OFFSET), never as a fetch-then-filter-in-Python pass over
    a page: with a `self`-scoped requester and several other unrelated
    Persons in the database, the reported `total`/`pages` must reflect
    only the requester's own, visible Person — not the full unfiltered
    row count truncated to a page.
    """
    with session_scope() as session:
        own_person = _make_person()
        own_user = _make_user(own_person)
        other_persons = [_make_person() for _ in range(5)]
        session.add_all([own_person, own_user, *other_persons])
        session.commit()
        user_id, own_person_id = own_user.id, own_person.id
    _grant_permission(user_id, "person.read", scope_type="self")
    _authenticate_as(user_id)

    response = client.get("/api/v1/persons", params={"page": 1, "page_size": 10})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["pagination"]["pages"] == 1
    assert [item["id"] for item in body["items"]] == [str(own_person_id)]


# --- Person: update -------------------------------------------------------


@requires_postgres
def test_update_person_with_self_scope_succeeds_and_audits(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person(first_name="Old")
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"first_name": "New", "phone": "+79999999999"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["first_name"] == "New"

    audit_row = _latest_audit_row(action="person.updated", resource_id=person_id)
    assert audit_row is not None
    assert audit_row.details is not None
    changes = audit_row.details["changes"]
    assert changes["first_name"] == {"from": "Old", "to": "New"}
    # Sensitive field: recorded as changed, but the value itself is never stored.
    assert changes["phone"] == {"changed": True}
    for value in changes.values():
        assert "+79999999999" not in str(value)


@requires_postgres
def test_update_person_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([person, requester_person, requester_user])
        session.commit()
        person_id, user_id = person.id, requester_user.id
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"first_name": "Hacked"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


@requires_postgres
def test_update_person_idor_member_cannot_update_another_person(client: TestClient) -> None:
    with session_scope() as session:
        own_person = _make_person()
        own_user = _make_user(own_person)
        other_person = _make_person(first_name="Victim")
        session.add_all([own_person, own_user, other_person])
        session.commit()
        user_id, other_id = own_user.id, other_person.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{other_id}",
        json={"first_name": "Pwned"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text
    with session_scope() as session:
        victim = session.get(Person, other_id)
        assert victim.first_name == "Victim"


# --- Person: contact fields (ADR-0035 §4) --------------------------------


@requires_postgres
def test_own_groups_instructor_can_read_and_update_target_contact_fields(
    client: TestClient,
) -> None:
    """ADR-0035 §4: instructor may read/update contact fields of Persons
    reachable through own_groups, exactly like any other Person field.
    """
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person(phone="+70000000001", email="m@example.com", address="Addr")
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        _, group = _setup_group_membership(session, club=club, person=member_person)
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.read", scope_type="own_groups")
    _grant_permission(instructor_user_id, "person.update", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    read_response = client.get(f"/api/v1/persons/{member_person_id}")
    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["phone"] == "+70000000001"

    update_response = client.patch(
        f"/api/v1/persons/{member_person_id}",
        json={"phone": "+70000000002"},
        headers=_csrf_headers(client),
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["phone"] == "+70000000002"


@requires_postgres
def test_guardian_relationship_does_not_grant_child_contact_access(client: TestClient) -> None:
    """ADR-0035 §4/§8.4: an active GuardianRelationship never substitutes
    for `person.read` + a matching scope on the child's Person record —
    Person authorization has no `children` branch (it fails closed), and
    `guardian_relationship.read` is a wholly separate permission that
    never grants Person access by itself.
    """
    with session_scope() as session:
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person(
            first_name="Child", phone="+79990001122", email="child@example.com"
        )
        session.add_all([guardian_person, guardian_user, child_person])
        session.commit()
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian_person.id,
                child_person_id=child_person.id,
                relationship_type="parent",
                status="active",
                valid_from=_utc(2020, 1, 1),
            )
        )
        session.commit()
        guardian_user_id, child_id = guardian_user.id, child_person.id
    # The guardian only holds person.read/self and guardian_relationship.read —
    # never a `children`/`all`/`own_groups` grant on Person itself.
    _grant_permission(guardian_user_id, "person.read", scope_type="self")
    _grant_permission(guardian_user_id, "guardian_relationship.read", scope_type="self")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/persons/{child_id}")
    assert response.status_code == 404, response.text


# --- Person: birth_date admin-only update (ADR-0035 §5) -------------------


@requires_postgres
def test_self_scope_cannot_update_own_birth_date(client: TestClient) -> None:
    """ADR-0035 §3.1/§5: self-scope alone never authorizes a birth_date
    change, even on the requester's own Person — this is the same
    "member and guardian have the same self-Person edit model, minus
    birth_date" rule.
    """
    with session_scope() as session:
        person = _make_person(birth_date=None)
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"birth_date": "2000-01-01"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    with session_scope() as session:
        unchanged = session.get(Person, person_id)
        assert unchanged.birth_date is None


@requires_postgres
def test_own_groups_instructor_cannot_update_target_birth_date(client: TestClient) -> None:
    """ADR-0035 §3.1: instructor's own_groups access to a Person never
    extends to birth_date, even though other fields are updatable.
    """
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person(birth_date=None)
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        _, group = _setup_group_membership(session, club=club, person=member_person)
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, member_person_id = instructor_user.id, member_person.id
    _grant_permission(instructor_user_id, "person.update", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.patch(
        f"/api/v1/persons/{member_person_id}",
        json={"birth_date": "2000-01-01"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_birth_date_and_other_fields_together_are_rejected_atomically_without_scope(
    client: TestClient,
) -> None:
    """A PATCH bundling an unauthorized birth_date change with an
    otherwise-permitted field change must reject the whole request and
    apply neither change — never a silent partial write.
    """
    with session_scope() as session:
        person = _make_person(first_name="Old", birth_date=None)
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"first_name": "New", "birth_date": "2000-01-01"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    with session_scope() as session:
        unchanged = session.get(Person, person_id)
        assert unchanged.first_name == "Old"
        assert unchanged.birth_date is None


@requires_postgres
def test_non_admin_role_with_all_scope_person_update_cannot_update_birth_date(
    client: TestClient,
) -> None:
    """ADR-0035 §5 requires the canonical `admin` role specifically, not
    merely `person.update` with `all` scope: a custom, non-system role
    seeded with exactly that grant (same shape `_grant_permission` always
    creates) must still be denied — this is the scenario a bare
    scope-only check would incorrectly allow.
    """
    with session_scope() as session:
        person = _make_person(birth_date=None)
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"birth_date": "2000-01-01"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    with session_scope() as session:
        unchanged = session.get(Person, person_id)
        assert unchanged.birth_date is None


@requires_postgres
def test_system_admin_role_can_update_own_birth_date(client: TestClient) -> None:
    """ADR-0035 §5: the canonical system admin role may update
    birth_date, explicitly including their own Person."""
    with session_scope() as session:
        person = _make_person(birth_date=None)
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_via_system_admin_role(user_id)
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"birth_date": "2000-01-01"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["birth_date"] == "2000-01-01"

    audit_row = _latest_audit_row(action="person.updated", resource_id=person_id)
    assert audit_row is not None
    assert audit_row.details["changes"]["birth_date"] == {"from": None, "to": "2000-01-01"}


@requires_postgres
def test_system_admin_role_can_update_another_persons_birth_date(client: TestClient) -> None:
    with session_scope() as session:
        target = _make_person(birth_date=None)
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([target, requester_person, requester_user])
        session.commit()
        target_id, user_id = target.id, requester_user.id
    _grant_via_system_admin_role(user_id)
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/persons/{target_id}",
        json={"birth_date": "1999-12-31"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["birth_date"] == "1999-12-31"


# --- Person: id immutability -----------------------------------------------


@requires_postgres
def test_patch_person_ignores_client_supplied_id(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person(first_name="Old")
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="self")
    _authenticate_as(user_id)

    spoofed_id = str(uuid.uuid4())
    response = client.patch(
        f"/api/v1/persons/{person_id}",
        json={"id": spoofed_id, "first_name": "New"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(person_id)
    assert body["id"] != spoofed_id
    assert body["first_name"] == "New"


# --- Person: no archive endpoint (ADR-0034) --------------------------------


@requires_postgres
def test_person_archive_endpoint_does_not_exist(client: TestClient) -> None:
    with session_scope() as session:
        person = _make_person()
        user = _make_user(person)
        session.add_all([person, user])
        session.commit()
        person_id, user_id = person.id, user.id
    _grant_permission(user_id, "person.update", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/persons/{person_id}/archive",
        json={},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


# --- Person: update audit atomicity -----------------------------------------


@requires_postgres
def test_person_update_rolls_back_when_audit_insert_fails() -> None:
    """Same fail-closed contract as test_person_create_rolls_back_when_
    audit_insert_fails, applied to update_person: if the audit insert
    fails, the field mutation must not survive either.
    """
    from app.people.service import update_person

    with session_scope() as session:
        person = _make_person(first_name="Old")
        session.add(person)
        session.commit()
        person_id = person.id

    with session_scope() as session:
        person = session.get(Person, person_id)
        bogus_actor_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            update_person(
                session,
                person=person,
                actor_user_id=bogus_actor_id,
                first_name="New",
            )

    with session_scope() as verify_session:
        unchanged = verify_session.get(Person, person_id)
        assert unchanged.first_name == "Old"


# --- Membership: create -----------------------------------------------------


@requires_postgres
def test_create_membership_with_all_scope_succeeds_and_audits(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        club_id, person_id, user_id = club.id, person.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "pending",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["left_at"] is None

    audit_row = _latest_audit_row(action="membership.created", resource_id=uuid.UUID(body["id"]))
    assert audit_row is not None
    assert audit_row.club_id == club_id
    assert audit_row.outcome == "success"


@requires_postgres
def test_create_membership_cross_club_assignment_is_forbidden(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club_a, club_b, person, requester_person, requester_user])
        session.commit()
        club_a_id, club_b_id, person_id, user_id = (
            club_a.id,
            club_b.id,
            person.id,
            requester_user.id,
        )
    _grant_permission(user_id, "membership.manage", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_b_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403, response.text


@requires_postgres
def test_create_membership_nonexistent_person_returns_422(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, requester_person, requester_user])
        session.commit()
        club_id, user_id = club.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(uuid.uuid4()),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_person_id"


@requires_postgres
def test_create_membership_overlapping_active_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        existing = _make_club_membership(club, person, membership_type="member", status="active")
        session.add(existing)
        session.commit()
        club_id, person_id, user_id = club.id, person.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


# --- Membership: read ---------------------------------------------------


@requires_postgres
def test_membership_self_scope_grants_access_to_own_membership(client: TestClient) -> None:
    """ADR-0035 §7.3: `member` reads their own membership data via
    `self` scope."""
    with session_scope() as session:
        club = _make_club()
        member_person = _make_person()
        member_user = _make_user(member_person)
        session.add_all([club, member_person, member_user])
        session.commit()
        membership = _make_club_membership(club, member_person)
        session.add(membership)
        session.commit()
        membership_id, member_user_id = membership.id, member_user.id
    _grant_permission(member_user_id, "membership.read", scope_type="self")
    _authenticate_as(member_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(membership_id)


@requires_postgres
def test_membership_self_scope_denies_other_persons_membership(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        other_person = _make_person()
        member_person = _make_person()
        member_user = _make_user(member_person)
        session.add_all([club, other_person, member_person, member_user])
        session.commit()
        membership = _make_club_membership(club, other_person)
        session.add(membership)
        session.commit()
        membership_id, member_user_id = membership.id, member_user.id
    _grant_permission(member_user_id, "membership.read", scope_type="self")
    _authenticate_as(member_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_get_membership_all_scope_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person)
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(membership_id)


@requires_postgres
def test_membership_own_groups_active_chain_grants_access(client: TestClient) -> None:
    """own_groups on the ClubMembership resource itself, via a fully
    active Person -> ClubMembership -> GroupMembership -> Group ->
    GroupInstructorAssignment -> requesting User chain.
    """
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        club_membership, group = _setup_group_membership(session, club=club, person=member_person)
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, membership_id = instructor_user.id, club_membership.id
    _grant_permission(instructor_user_id, "membership.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(membership_id)


@requires_postgres
def test_membership_own_groups_denies_inactive_club_membership(client: TestClient) -> None:
    """The ClubMembership resource being accessed must itself be active
    — an active GroupMembership/GroupInstructorAssignment chain is not
    enough on its own (this is the specific gap fixed in
    `_own_group_condition_for_membership`).
    """
    with session_scope() as session:
        club = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club, instructor_person, instructor_user, member_person])
        session.commit()
        club_membership, group = _setup_group_membership(
            session, club=club, person=member_person, club_membership_status="archived"
        )
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, membership_id = instructor_user.id, club_membership.id
    _grant_permission(instructor_user_id, "membership.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_membership_own_groups_denies_cross_club_group_even_with_inconsistent_data(
    client: TestClient,
) -> None:
    """The authorization boundary itself must reject a GroupMembership
    that (inconsistently, bypassing app.groups.service/ADR-0022) points
    to a Group in a different Club than the ClubMembership it is
    supposedly for — the query must not rely solely on write-time
    service-layer integrity to prevent cross-Club access.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_person = _make_person()
        session.add_all([club_a, club_b, instructor_person, instructor_user, member_person])
        session.commit()
        # member_person's ClubMembership is in club_a, but the Group (and
        # its GroupMembership row) are in club_b — an inconsistent state
        # that should never occur via the real API, constructed directly
        # here to prove the authorization query itself guards against it.
        club_membership = _make_club_membership(club_a, member_person)
        group = _make_group(club_b)
        session.add_all([club_membership, group])
        session.commit()
        session.add(_make_group_membership(group, club_membership))
        session.add(_make_group_instructor_assignment(group, instructor_user))
        session.commit()
        instructor_user_id, membership_id = instructor_user.id, club_membership.id
    _grant_permission(instructor_user_id, "membership.read", scope_type="own_groups")
    _authenticate_as(instructor_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_list_memberships_cross_club_assignment_does_not_leak_other_club(
    client: TestClient,
) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person_a = _make_person()
        person_b = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all(
            [
                club_a,
                club_b,
                person_a,
                person_b,
                requester_person,
                requester_user,
            ]
        )
        session.commit()
        membership_a = _make_club_membership(club_a, person_a)
        membership_b = _make_club_membership(club_b, person_b)
        session.add_all([membership_a, membership_b])
        session.commit()
        club_a_id, user_id = club_a.id, requester_user.id
        membership_a_id, membership_b_id = membership_a.id, membership_b.id
    _grant_permission(user_id, "membership.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(membership_a_id) in ids
    assert str(membership_b_id) not in ids


@requires_postgres
def test_list_memberships_pagination_reflects_authorization_filtering_not_all_rows(
    client: TestClient,
) -> None:
    """Same requirement as the Person-list equivalent: `total`/`pages`
    must reflect only the Club-scoped requester's visible memberships,
    not every ClubMembership row in the database.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person_a = _make_person()
        other_persons = [_make_person() for _ in range(4)]
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all(
            [club_a, club_b, person_a, *other_persons, requester_person, requester_user]
        )
        session.commit()
        own_membership = _make_club_membership(club_a, person_a)
        other_memberships = [_make_club_membership(club_b, p) for p in other_persons]
        session.add_all([own_membership, *other_memberships])
        session.commit()
        club_a_id, user_id, own_membership_id = club_a.id, requester_user.id, own_membership.id
    _grant_permission(user_id, "membership.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.get("/api/v1/memberships", params={"page": 1, "page_size": 10})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["pagination"]["pages"] == 1
    assert [item["id"] for item in body["items"]] == [str(own_membership_id)]


@requires_postgres
def test_get_membership_of_other_club_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all(
            [club_a, club_b, person, requester_person, requester_user]
        )
        session.commit()
        membership = _make_club_membership(club_b, person)
        session.add(membership)
        session.commit()
        club_a_id, membership_id, user_id = club_a.id, membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all", club_id=club_a_id)
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


# --- Membership: guardian `children` scope (ADR-0035 §7.3, TH-0102) -------


@requires_postgres
def test_membership_children_scope_grants_access_to_own_child(client: TestClient) -> None:
    """A Guardian with an active GuardianRelationship to the membership's
    Person may read that membership through the `children` scope."""
    with session_scope() as session:
        club = _make_club()
        child_person = _make_person()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, child_person, guardian_person, guardian_user])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        membership = _make_club_membership(club, child_person)
        session.add(membership)
        session.commit()
        membership_id, guardian_user_id = membership.id, guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(membership_id)


@requires_postgres
def test_membership_children_scope_denies_unrelated_person(client: TestClient) -> None:
    """`children` scope must not grant access to a Person the requester
    has no active GuardianRelationship to — a bare `membership.read`
    grant with this scope must not become a global read."""
    with session_scope() as session:
        club = _make_club()
        unrelated_person = _make_person()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, unrelated_person, guardian_person, guardian_user])
        session.commit()
        membership = _make_club_membership(club, unrelated_person)
        session.add(membership)
        session.commit()
        membership_id, guardian_user_id = membership.id, guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_membership_children_scope_denies_inactive_relationship_status(
    client: TestClient,
) -> None:
    """A stored `status != active` GuardianRelationship (e.g. `revoked`)
    must not satisfy `children` scope even though the row still exists."""
    with session_scope() as session:
        club = _make_club()
        child_person = _make_person()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, child_person, guardian_person, guardian_user])
        session.commit()
        session.add(
            _make_guardian_relationship(guardian_person, child_person, status="revoked")
        )
        membership = _make_club_membership(club, child_person)
        session.add(membership)
        session.commit()
        membership_id, guardian_user_id = membership.id, guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_membership_children_scope_denies_expired_relationship_interval(
    client: TestClient,
) -> None:
    """A GuardianRelationship whose `valid_to` has already passed is
    read-time `inactive` (people-api.md §18) and must not satisfy
    `children` scope even though the stored `status` column is still
    `active`."""
    with session_scope() as session:
        club = _make_club()
        child_person = _make_person()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, child_person, guardian_person, guardian_user])
        session.commit()
        session.add(
            _make_guardian_relationship(
                guardian_person,
                child_person,
                valid_from=_utc(2020, 1, 1),
                valid_to=_utc(2021, 1, 1),
            )
        )
        membership = _make_club_membership(club, child_person)
        session.add(membership)
        session.commit()
        membership_id, guardian_user_id = membership.id, guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}")
    assert response.status_code == 404, response.text


@requires_postgres
def test_list_memberships_children_scope_returns_only_own_children(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        own_child = _make_person()
        other_child = _make_person()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, own_child, other_child, guardian_person, guardian_user])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, own_child))
        own_membership = _make_club_membership(club, own_child)
        other_membership = _make_club_membership(club, other_child)
        session.add_all([own_membership, other_membership])
        session.commit()
        own_membership_id = own_membership.id
        other_membership_id = other_membership.id
        guardian_user_id = guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(own_membership_id) in ids
    assert str(other_membership_id) not in ids


@requires_postgres
def test_person_memberships_children_scope_grants_history_access(client: TestClient) -> None:
    """`GET /persons/{person_id}/memberships` for a Guardian's own child
    is governed by the same `children` scope as the top-level list/detail
    endpoints (people-api.md §7.3/§13)."""
    with session_scope() as session:
        club = _make_club()
        child_person = _make_person()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        session.add_all([club, child_person, guardian_person, guardian_user])
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        membership = _make_club_membership(club, child_person)
        session.add(membership)
        session.commit()
        child_id, membership_id, guardian_user_id = (
            child_person.id,
            membership.id,
            guardian_user.id,
        )
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/persons/{child_id}/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(membership_id) in ids


# --- Membership: multiple simultaneous assignments are independent branches
# (ADR-0035 §7.3 regression — `children` combined with every other scope) --


@requires_postgres
def test_membership_self_and_children_scopes_are_independent_branches(
    client: TestClient,
) -> None:
    """A user holding both `self` and `children` grants for
    `membership.read` sees the union of what each grants alone — `self`
    does not extend to the child's membership and `children` does not
    extend to an unrelated third person's membership."""
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        unrelated_person = _make_person()
        session.add_all(
            [club, guardian_person, guardian_user, child_person, unrelated_person]
        )
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        own_membership = _make_club_membership(club, guardian_person)
        child_membership = _make_club_membership(club, child_person)
        unrelated_membership = _make_club_membership(club, unrelated_person)
        session.add_all([own_membership, child_membership, unrelated_membership])
        session.commit()
        own_id, child_id, unrelated_id = (
            own_membership.id,
            child_membership.id,
            unrelated_membership.id,
        )
        guardian_user_id = guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="self")
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    list_response = client.get("/api/v1/memberships")
    assert list_response.status_code == 200, list_response.text
    ids = {item["id"] for item in list_response.json()["items"]}
    assert str(own_id) in ids
    assert str(child_id) in ids
    assert str(unrelated_id) not in ids

    assert client.get(f"/api/v1/memberships/{own_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{child_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{unrelated_id}").status_code == 404


@requires_postgres
def test_membership_own_groups_and_children_scopes_are_independent_branches(
    client: TestClient,
) -> None:
    """An Instructor who is also a Guardian (both grants held at once)
    sees their own_groups-reachable memberships AND their own child's
    membership, but not a third person who is neither."""
    with session_scope() as session:
        club = _make_club()
        instructor_guardian_person = _make_person()
        instructor_guardian_user = _make_user(instructor_guardian_person)
        group_member_person = _make_person()
        child_person = _make_person()
        unrelated_person = _make_person()
        session.add_all(
            [
                club,
                instructor_guardian_person,
                instructor_guardian_user,
                group_member_person,
                child_person,
                unrelated_person,
            ]
        )
        session.commit()
        group_membership, group = _setup_group_membership(
            session, club=club, person=group_member_person
        )
        session.add(_make_group_instructor_assignment(group, instructor_guardian_user))
        session.add(_make_guardian_relationship(instructor_guardian_person, child_person))
        child_membership = _make_club_membership(club, child_person)
        unrelated_membership = _make_club_membership(club, unrelated_person)
        session.add_all([child_membership, unrelated_membership])
        session.commit()
        group_member_membership_id = group_membership.id
        child_membership_id = child_membership.id
        unrelated_membership_id = unrelated_membership.id
        user_id = instructor_guardian_user.id
    _grant_permission(user_id, "membership.read", scope_type="own_groups")
    _grant_permission(user_id, "membership.read", scope_type="children")
    _authenticate_as(user_id)

    list_response = client.get("/api/v1/memberships")
    assert list_response.status_code == 200, list_response.text
    ids = {item["id"] for item in list_response.json()["items"]}
    assert str(group_member_membership_id) in ids
    assert str(child_membership_id) in ids
    assert str(unrelated_membership_id) not in ids

    assert client.get(f"/api/v1/memberships/{group_member_membership_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{child_membership_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{unrelated_membership_id}").status_code == 404


@requires_postgres
def test_membership_all_scoped_club_and_children_scope_are_independent_branches(
    client: TestClient,
) -> None:
    """A club-scoped `all` grant and an (unscoped) `children` grant must
    not bleed into each other: `all` stays bounded to its own Club, and
    `children` reaches the child's membership even in a *different* Club
    the `all` grant does not cover — but never an unrelated person there.
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        club_a_person = _make_person()
        child_person = _make_person()
        unrelated_person_in_club_b = _make_person()
        session.add_all(
            [
                club_a,
                club_b,
                guardian_person,
                guardian_user,
                club_a_person,
                child_person,
                unrelated_person_in_club_b,
            ]
        )
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        club_a_membership = _make_club_membership(club_a, club_a_person)
        # The child's own membership lives in club_b, outside the
        # club_a-scoped `all` grant — only `children` can reach it.
        child_membership = _make_club_membership(club_b, child_person)
        unrelated_membership = _make_club_membership(club_b, unrelated_person_in_club_b)
        session.add_all([club_a_membership, child_membership, unrelated_membership])
        session.commit()
        club_a_id = club_a.id
        club_a_membership_id = club_a_membership.id
        child_membership_id = child_membership.id
        unrelated_membership_id = unrelated_membership.id
        guardian_user_id = guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="all", club_id=club_a_id)
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    list_response = client.get("/api/v1/memberships")
    assert list_response.status_code == 200, list_response.text
    ids = {item["id"] for item in list_response.json()["items"]}
    assert str(club_a_membership_id) in ids
    assert str(child_membership_id) in ids
    assert str(unrelated_membership_id) not in ids

    assert client.get(f"/api/v1/memberships/{club_a_membership_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{child_membership_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{unrelated_membership_id}").status_code == 404


@requires_postgres
def test_membership_none_scope_does_not_block_or_extend_children_scope(
    client: TestClient,
) -> None:
    """A `none` grant contributes no access of its own but must not
    suppress a separate `children` grant held by the same user either —
    there is no explicit-deny model (roles-and-permissions.md §13):
    permissions/scopes are purely additive."""
    with session_scope() as session:
        club = _make_club()
        guardian_person = _make_person()
        guardian_user = _make_user(guardian_person)
        child_person = _make_person()
        unrelated_person = _make_person()
        session.add_all(
            [club, guardian_person, guardian_user, child_person, unrelated_person]
        )
        session.commit()
        session.add(_make_guardian_relationship(guardian_person, child_person))
        child_membership = _make_club_membership(club, child_person)
        unrelated_membership = _make_club_membership(club, unrelated_person)
        session.add_all([child_membership, unrelated_membership])
        session.commit()
        child_membership_id = child_membership.id
        unrelated_membership_id = unrelated_membership.id
        guardian_user_id = guardian_user.id
    _grant_permission(guardian_user_id, "membership.read", scope_type="none")
    _grant_permission(guardian_user_id, "membership.read", scope_type="children")
    _authenticate_as(guardian_user_id)

    list_response = client.get("/api/v1/memberships")
    assert list_response.status_code == 200, list_response.text
    ids = {item["id"] for item in list_response.json()["items"]}
    assert str(child_membership_id) in ids
    assert str(unrelated_membership_id) not in ids

    assert client.get(f"/api/v1/memberships/{child_membership_id}").status_code == 200
    assert client.get(f"/api/v1/memberships/{unrelated_membership_id}").status_code == 404


@requires_postgres
def test_list_memberships_multiple_own_groups_assignments_across_clubs_are_additive(
    client: TestClient,
) -> None:
    """Several assignments of the *same* permission+scope_type but
    different club_id boundaries must each independently contribute their
    own Club's memberships — not just the first/last one evaluated."""
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        instructor_person = _make_person()
        instructor_user = _make_user(instructor_person)
        member_a = _make_person()
        member_b = _make_person()
        session.add_all(
            [club_a, club_b, instructor_person, instructor_user, member_a, member_b]
        )
        session.commit()
        membership_a, group_a = _setup_group_membership(session, club=club_a, person=member_a)
        membership_b, group_b = _setup_group_membership(session, club=club_b, person=member_b)
        session.add(_make_group_instructor_assignment(group_a, instructor_user))
        session.add(_make_group_instructor_assignment(group_b, instructor_user))
        session.commit()
        club_a_id, club_b_id = club_a.id, club_b.id
        membership_a_id, membership_b_id = membership_a.id, membership_b.id
        instructor_user_id = instructor_user.id
    _grant_permission(
        instructor_user_id, "membership.read", scope_type="own_groups", club_id=club_a_id
    )
    _grant_permission(
        instructor_user_id, "membership.read", scope_type="own_groups", club_id=club_b_id
    )
    _authenticate_as(instructor_user_id)

    response = client.get("/api/v1/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(membership_a_id) in ids
    assert str(membership_b_id) in ids


@requires_postgres
def test_person_memberships_endpoint_returns_person_history(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person)
        session.add(membership)
        session.commit()
        person_id, membership_id, user_id = person.id, membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{person_id}/memberships")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(membership_id) in ids


@requires_postgres
def test_person_memberships_endpoint_nonexistent_person_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([requester_person, requester_user])
        session.commit()
        user_id = requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{uuid.uuid4()}/memberships")
    assert response.status_code == 404, response.text


# --- Membership: update (membership_type) --------------------------------


@requires_postgres
def test_update_membership_type_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, membership_type="member")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/memberships/{membership_id}",
        json={"membership_type": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["membership_type"] == "instructor"

    audit_row = _latest_audit_row(action="membership.updated", resource_id=membership_id)
    assert audit_row is not None
    assert audit_row.details["changes"]["membership_type"] == {
        "from": "member",
        "to": "instructor",
    }


@requires_postgres
def test_update_membership_type_noop_does_not_emit_audit(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, membership_type="member")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/memberships/{membership_id}",
        json={"membership_type": "member"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text

    assert _latest_audit_row(action="membership.updated", resource_id=membership_id) is None


@requires_postgres
def test_update_membership_type_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, membership_type="member")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _authenticate_as(user_id)

    response = client.patch(
        f"/api/v1/memberships/{membership_id}",
        json={"membership_type": "instructor"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


# --- Membership: status transitions --------------------------------------


@requires_postgres
def test_transition_active_to_suspended_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"
    assert response.json()["left_at"] is None

    audit_row = _latest_audit_row(action="membership.status_changed", resource_id=membership_id)
    assert audit_row is not None
    assert audit_row.details["changes"]["status"] == {"from": "active", "to": "suspended"}
    # active -> suspended does not end the membership period: no
    # membership.ended row is emitted for it.
    assert _latest_audit_row(action="membership.ended", resource_id=membership_id) is None


@requires_postgres
def test_transition_pending_to_active_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="pending")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "active"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert response.json()["left_at"] is None


@requires_postgres
def test_transition_pending_to_archived_leaves_left_at_null(client: TestClient) -> None:
    """Issue #62 accepted decisions / TH-0102: `pending -> archived` is
    the one ENDING_STATUSES transition that must NOT set `left_at` —
    the membership period never actually started, so there is no
    "leave" moment to record.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="pending")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "archived"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"
    assert response.json()["left_at"] is None


@requires_postgres
def test_transition_active_to_archived_sets_left_at(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "archived"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"
    assert response.json()["left_at"] is not None


@requires_postgres
def test_transition_suspended_to_active_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="suspended")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "active"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert response.json()["left_at"] is None


@requires_postgres
def test_transition_suspended_to_inactive_sets_left_at(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="suspended")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "inactive"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "inactive"
    assert response.json()["left_at"] is not None
    assert _latest_audit_row(action="membership.ended", resource_id=membership_id) is not None


@requires_postgres
def test_transition_suspended_to_archived_sets_left_at(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="suspended")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "archived"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"
    assert response.json()["left_at"] is not None


@requires_postgres
def test_transition_inactive_to_archived_succeeds(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(
            club, person, status="inactive", left_at=_utc(2025, 1, 1)
        )
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "archived"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"
    # left_at was already set (inactive is itself an ENDING_STATUSES value)
    # and must not be rewritten by this further ending transition.
    assert response.json()["left_at"] == "2025-01-01T00:00:00Z"


@requires_postgres
def test_transition_pending_to_suspended_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="pending")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


@requires_postgres
def test_transition_pending_to_inactive_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="pending")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "inactive"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


@requires_postgres
def test_transition_active_to_inactive_sets_left_at_and_emits_ended(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "inactive", "reason": "left the club"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "inactive"
    assert body["left_at"] is not None

    ended_row = _latest_audit_row(action="membership.ended", resource_id=membership_id)
    assert ended_row is not None
    assert ended_row.details["reason"] == "left the club"

    # One transition that both changes status and ends the period must
    # produce both audit actions (Issue #62 accepted decisions), not just
    # one or the other.
    status_changed_row = _latest_audit_row(
        action="membership.status_changed", resource_id=membership_id
    )
    assert status_changed_row is not None
    assert status_changed_row.details["changes"]["status"] == {"from": "active", "to": "inactive"}


@requires_postgres
def test_transition_ending_twice_does_not_re_emit_membership_ended(client: TestClient) -> None:
    """`left_at` is set once; a later transition into another
    ENDING_STATUSES value (`inactive -> archived`) must not emit a second
    `membership.ended` row, even though `membership.status_changed` is
    still recorded for it.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    first = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "inactive"},
        headers=_csrf_headers(client),
    )
    assert first.status_code == 200, first.text
    first_left_at = first.json()["left_at"]
    assert first_left_at is not None

    second = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "archived"},
        headers=_csrf_headers(client),
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] == "archived"
    # left_at is not rewritten by the second ending transition.
    assert body["left_at"] == first_left_at

    with session_scope() as session:
        ended_rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "membership.ended", AuditLog.resource_id == membership_id
                )
            )
            .scalars()
            .all()
        )
        assert len(ended_rows) == 1

        status_changed_rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "membership.status_changed",
                    AuditLog.resource_id == membership_id,
                )
            )
            .scalars()
            .all()
        )
        assert len(status_changed_rows) == 2


@requires_postgres
def test_transition_disallowed_edge_returns_409(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="pending")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


@requires_postgres
def test_transition_from_archived_is_rejected(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="archived")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "active"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text


@requires_postgres
def test_transition_inactive_to_active_is_rejected(client: TestClient) -> None:
    """`inactive -> active` is explicitly prohibited (Issue #62 accepted
    decisions): one ClubMembership row is one continuous membership
    period; rejoining after `inactive` must create a new row instead
    (see test_rejoin_after_inactive_creates_new_membership_row).
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="inactive")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "active"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_membership_transition"


@requires_postgres
def test_rejoin_after_inactive_creates_new_membership_row(client: TestClient) -> None:
    """Issue #62 accepted decision: rejoining after `inactive` creates a
    new ClubMembership row rather than reactivating the old one. The old
    row keeps its `inactive` status and its original `left_at`; the new
    row is a fresh, independent period.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        old_membership = _make_club_membership(
            club, person, membership_type="member", status="active", joined_at=_utc(2026, 1, 1)
        )
        session.add(old_membership)
        session.commit()
        club_id, person_id, user_id = club.id, person.id, requester_user.id
        old_membership_id = old_membership.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    end_response = client.post(
        f"/api/v1/memberships/{old_membership_id}/status",
        json={"status": "inactive"},
        headers=_csrf_headers(client),
    )
    assert end_response.status_code == 200, end_response.text
    old_left_at = end_response.json()["left_at"]
    assert old_left_at is not None

    rejoin_response = client.post(
        "/api/v1/memberships",
        json={
            "person_id": str(person_id),
            "club_id": str(club_id),
            "membership_type": "member",
            "status": "active",
            "joined_at": "2026-09-01T00:00:00Z",
        },
        headers=_csrf_headers(client),
    )
    assert rejoin_response.status_code == 201, rejoin_response.text
    new_membership = rejoin_response.json()
    new_membership_id = new_membership["id"]

    assert new_membership_id != str(old_membership_id)
    assert new_membership["status"] == "active"
    assert new_membership["left_at"] is None
    assert new_membership["joined_at"] == "2026-09-01T00:00:00Z"

    # The old row is untouched: still inactive, with its original left_at.
    old_response = client.get(f"/api/v1/memberships/{old_membership_id}")
    assert old_response.status_code == 200, old_response.text
    old_body = old_response.json()
    assert old_body["status"] == "inactive"
    assert old_body["left_at"] == old_left_at

    # Both periods show up in the Person's membership history.
    list_response = client.get(f"/api/v1/persons/{person_id}/memberships")
    assert list_response.status_code == 200, list_response.text
    ids = {item["id"] for item in list_response.json()["items"]}
    assert {str(old_membership_id), new_membership_id} <= ids


# --- Membership: concurrent conflicting lifecycle updates ------------------


@requires_postgres
def test_concurrent_conflicting_status_transitions_only_one_succeeds(
    client: TestClient,
) -> None:
    """ADR-0022 §6's transactional-locking requirement, applied to
    ClubMembership lifecycle: `_get_authorized_membership_or_404(...,
    lock=True)` takes `SELECT ... FOR UPDATE` on the row for the status
    endpoint, so two concurrent transitions of the *same already-terminal
    target* must serialize rather than race — the second request's locked
    read must observe the first request's already-committed `archived`
    status and correctly reject its own `archived -> archived` attempt
    (not in ALLOWED_STATUS_TRANSITIONS), rather than both succeeding from
    a stale, pre-lock view of `active`.

    This also proves `left_at` is set exactly once and exactly one
    `membership.ended` audit row is emitted despite two concurrent
    requests attempting the same ending transition.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.manage", scope_type="all")
    _authenticate_as(user_id)

    start_gate = threading.Barrier(2, timeout=10)
    results: dict[str, int] = {}

    def attempt(name: str) -> None:
        start_gate.wait()
        response = client.post(
            f"/api/v1/memberships/{membership_id}/status",
            json={"status": "archived"},
            headers=_csrf_headers(client),
        )
        results[name] = response.status_code

    thread_a = threading.Thread(target=attempt, args=("a",))
    thread_b = threading.Thread(target=attempt, args=("b",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert sorted(results.values()) == [200, 409], results

    with session_scope() as verify:
        row = verify.get(ClubMembership, membership_id)
        assert row is not None
        assert row.status == "archived"
        assert row.left_at is not None

        ended_rows = (
            verify.execute(
                select(AuditLog).where(
                    AuditLog.action == "membership.ended", AuditLog.resource_id == membership_id
                )
            )
            .scalars()
            .all()
        )
        assert len(ended_rows) == 1


@requires_postgres
def test_transition_without_permission_returns_404(client: TestClient) -> None:
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _authenticate_as(user_id)

    response = client.post(
        f"/api/v1/memberships/{membership_id}/status",
        json={"status": "suspended"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404, response.text


@requires_postgres
def test_membership_history_endpoint_no_longer_exists(client: TestClient) -> None:
    """Issue #62's accepted decisions remove `/memberships/{id}/history`
    from this API slice entirely (no alias, no fallback to current
    state); `GET /persons/{person_id}/memberships` is the canonical
    membership-period history — see
    test_person_memberships_endpoint_returns_person_history.
    """
    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        requester_person = _make_person()
        requester_user = _make_user(requester_person)
        session.add_all([club, person, requester_person, requester_user])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id, user_id = membership.id, requester_user.id
    _grant_permission(user_id, "membership.read", scope_type="all")
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/memberships/{membership_id}/history")
    assert response.status_code == 404, response.text


# --- Audit: fail-closed transaction behavior ------------------------------


@requires_postgres
def test_person_create_rolls_back_when_audit_insert_fails() -> None:
    """Simulates ADR-0024 §5's fail-closed contract directly against the
    service layer: if the audit insert fails, the Person row must not
    survive either — mirroring tests/integration/test_audit_log.py's
    proof, applied to app.people.service.
    """
    from app.people.service import create_person

    with session_scope() as session:
        # actor_user_id references a nonexistent User -> the AuditLog
        # actor_user_id FK violates on flush inside create_person's own
        # try/except, forcing a rollback of the Person insert too.
        bogus_actor_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            create_person(
                session,
                first_name="Ghost",
                last_name="Person",
                middle_name=None,
                birth_date=None,
                phone=None,
                email=None,
                address=None,
                actor_user_id=bogus_actor_id,
            )

    with session_scope() as verify_session:
        remaining = verify_session.execute(
            select(Person).where(Person.first_name == "Ghost")
        ).scalar_one_or_none()
        assert remaining is None, "Person must not survive a rolled-back audit insert"


@requires_postgres
def test_transition_membership_status_rolls_back_when_audit_insert_fails() -> None:
    """Same fail-closed contract (ADR-0024 §5 / ADR-0035 §13) applied to
    `app.people.service.transition_membership_status`: if the
    `membership.status_changed` audit insert fails, the ClubMembership's
    `status`/`left_at` mutation must not survive either — both statements
    execute inside the same DB transaction and roll back together.
    """
    from app.people.service import transition_membership_status

    with session_scope() as session:
        club = _make_club()
        person = _make_person()
        session.add_all([club, person])
        session.commit()
        membership = _make_club_membership(club, person, status="active")
        session.add(membership)
        session.commit()
        membership_id = membership.id

        # actor_user_id references a nonexistent User -> the AuditLog
        # actor_user_id FK violates on flush inside
        # transition_membership_status's own try/except, forcing a
        # rollback of the status/left_at mutation too.
        bogus_actor_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            transition_membership_status(
                session,
                membership=membership,
                new_status="inactive",
                reason=None,
                actor_user_id=bogus_actor_id,
            )

    with session_scope() as verify_session:
        row = verify_session.get(ClubMembership, membership_id)
        assert row is not None
        assert row.status == "active", "status must not survive a rolled-back audit insert"
        assert row.left_at is None, "left_at must not survive a rolled-back audit insert"

        ended_rows = (
            verify_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "membership.ended", AuditLog.resource_id == membership_id
                )
            )
            .scalars()
            .all()
        )
        assert ended_rows == []


@requires_postgres
def test_record_audit_event_used_directly_still_respects_secret_prohibition() -> None:
    """Sanity check that app.people.service reuses the shared
    app.audit.service boundary (ADR-0024) rather than a parallel
    mechanism: the same secret-prohibition rule applies here too.
    """
    from app.audit.security import AuditDetailsError

    with session_scope() as session:
        with pytest.raises(AuditDetailsError):
            record_audit_event(
                session,
                action="person.created",
                actor_type="system",
                outcome="success",
                details={"password": "hunter2"},
            )
