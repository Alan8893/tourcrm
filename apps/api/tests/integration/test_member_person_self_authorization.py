"""Member self Person authorization through the REAL seeded grants
(Issue #312; ADR-0035 §3 AUTH-2C; role-permission-scope-matrix.md
§3.3/§4/§4.1).

Every Member test runs through the migration-seeded `member ->
person.read -> self` and `member -> person.update -> self` grants
(migration 3b1fd730bb0d) on the canonical `member` role — no ad hoc grant
— so a missing seed can never hide behind a test role:

- a seeded Member reads (`GET /persons/{id}`, `GET /persons`) and updates
  (`PATCH /persons/{id}`) exactly their own Person;
- `person.update(self)` changes only first/last/middle name, phone,
  address and the avatar; `email` / `birth_date` -> 403, `id` and
  read-only/administrative fields are never changed;
- the existing profile-photo flow (`PUT`/`DELETE /persons/{id}/photo`)
  works for a seeded Member;
- another Person is existence-hidden (404) for read and update;
- the seeded `admin` (`all`), `instructor` and `guardian` roles keep their
  current People authorization unchanged;
- migration 3b1fd730bb0d upgrades/downgrades only its own grant scopes,
  idempotently.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration/test_member_person_self_authorization.py -v
"""

import datetime
import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app
from app.storage.local import LocalFileStorage, get_file_storage

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_SEED_REVISION = "3b1fd730bb0d"
_BEFORE_SEED_REVISION = "a7c3e5f19d24"
_SEEDED = {
    ("member", "person.read", "self"),
    ("member", "person.update", "self"),
}
_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=tmp_path)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories ----------------------------------------------------------------


def _club(session: Session) -> Club:
    club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
    session.add(club)
    session.flush()
    return club


def _person(session: Session, club: Club | None = None, **values: object) -> Person:
    person = Person(
        last_name="Ivanova",
        first_name=f"P-{uuid.uuid4().hex[:8]}",
        **values,  # type: ignore[arg-type]
    )
    session.add(person)
    session.flush()
    if club is not None:
        session.add(
            ClubMembership(
                club_id=club.id,
                person_id=person.id,
                membership_type="student",
                status="active",
                joined_at=_LONG_AGO,
            )
        )
        session.flush()
    return person


def _user_with_role(
    session: Session,
    club: Club,
    role_code: str,
    *,
    global_assignment: bool = False,
    **person_values: object,
) -> User:
    """A real user of a canonical, migration-seeded role: an active
    ClubMembership in `club` plus a `club`-scoped (or, with
    `global_assignment`, global) assignment of that role — no ad hoc
    grant."""
    person = _person(session, club, **person_values)
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(
            user_id=user.id, role_id=role.id, club_id=None if global_assignment else club.id
        )
    )
    session.flush()
    return user


def _member_world() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(member user id, member's own Person id, another Person id in the
    same Club)."""
    with session_scope() as session:
        club = _club(session)
        member = _user_with_role(
            session,
            club,
            "member",
            email="member@example.com",
            birth_date=datetime.date(2010, 5, 5),
        )
        other = _person(session, club, email="other@example.com")
        session.commit()
        return member.id, member.person_id, other.id


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _patch(client: TestClient, person_id: uuid.UUID, body: dict):  # type: ignore[no-untyped-def]
    return client.patch(f"/api/v1/persons/{person_id}", json=body, headers=_csrf_headers(client))


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _grant_scopes() -> set[tuple[str, str, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code, RolePermissionScope.scope_type)
            .select_from(RolePermissionScope)
            .join(RolePermission, RolePermission.id == RolePermissionScope.role_permission_id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        ).all()
        return {(r, p, s) for r, p, s in rows}


def _grant_pairs() -> set[tuple[str, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code)
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        ).all()
        return {(r, p) for r, p in rows}


# --- seeded grants --------------------------------------------------------------


@requires_postgres
def test_member_role_is_seeded_with_person_read_and_update_self_only() -> None:
    person_scopes = {
        (role, permission, scope)
        for role, permission, scope in _grant_scopes()
        if permission.startswith("person.") and role != "admin"
    }
    assert person_scopes == _SEEDED


# --- Member: own Person ---------------------------------------------------------


@requires_postgres
def test_seeded_member_reads_own_person(client: TestClient) -> None:
    user_id, own_id, other_id = _member_world()
    _authenticate_as(user_id)

    response = client.get(f"/api/v1/persons/{own_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(own_id)
    assert body["email"] == "member@example.com"

    listed = client.get("/api/v1/persons")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [str(own_id)]


@requires_postgres
def test_seeded_member_updates_every_allowed_own_field(client: TestClient) -> None:
    user_id, own_id, _ = _member_world()
    _authenticate_as(user_id)

    response = _patch(
        client,
        own_id,
        {
            "first_name": "Анна",
            "last_name": "Петрова",
            "middle_name": "Сергеевна",
            "phone": "+79990001122",
            "address": "Москва, ул. Лесная, 1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["first_name"] == "Анна"
    assert body["last_name"] == "Петрова"
    assert body["middle_name"] == "Сергеевна"
    assert body["phone"] == "+79990001122"
    assert body["address"] == "Москва, ул. Лесная, 1"
    # Restricted fields are untouched by an allowed-field update.
    assert body["email"] == "member@example.com"
    assert body["birth_date"] == "2010-05-05"

    cleared = _patch(client, own_id, {"middle_name": None, "phone": None, "address": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["middle_name"] is None
    assert cleared.json()["phone"] is None
    assert cleared.json()["address"] is None


@requires_postgres
@pytest.mark.parametrize(
    "body",
    [
        {"email": "new@example.com"},
        {"birth_date": "2000-01-01"},
        {"first_name": "Allowed", "email": "new@example.com"},
        {"phone": "+70000000000", "birth_date": "2000-01-01"},
    ],
    ids=["email", "birth_date", "allowed_plus_email", "allowed_plus_birth_date"],
)
def test_seeded_member_cannot_update_restricted_own_fields(
    client: TestClient, body: dict
) -> None:
    user_id, own_id, _ = _member_world()
    _authenticate_as(user_id)

    response = _patch(client, own_id, body)
    assert response.status_code == 403, response.text

    with session_scope() as session:
        person = session.get(Person, own_id)
        assert person is not None
        # The whole request is rejected — no partial update.
        assert person.email == "member@example.com"
        assert person.birth_date == datetime.date(2010, 5, 5)
        assert person.first_name != "Allowed"
        assert person.phone is None


@requires_postgres
def test_seeded_member_cannot_change_id_or_read_only_fields(client: TestClient) -> None:
    user_id, own_id, other_id = _member_world()
    _authenticate_as(user_id)
    with session_scope() as session:
        before = session.get(Person, own_id)
        assert before is not None
        created_at = before.created_at

    response = _patch(
        client,
        own_id,
        {
            "id": str(other_id),
            "role_codes": ["admin"],
            "created_at": "2000-01-01T00:00:00Z",
            "first_name": "Same",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(own_id)
    assert body["role_codes"] == ["member"]

    with session_scope() as session:
        assert session.get(Person, own_id).created_at == created_at  # type: ignore[union-attr]
        assert session.get(Person, other_id).first_name != "Same"  # type: ignore[union-attr]
        role_codes = session.execute(
            select(Role.code)
            .join(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
            .where(UserRoleAssignment.user_id == user_id)
        ).scalars().all()
        assert role_codes == ["member"]


@requires_postgres
def test_seeded_member_manages_own_profile_photo(client: TestClient) -> None:
    user_id, own_id, _ = _member_world()
    _authenticate_as(user_id)

    uploaded = client.put(
        f"/api/v1/persons/{own_id}/photo",
        files={"photo": ("p.png", _image_bytes(), "image/png")},
        headers=_csrf_headers(client),
    )
    assert uploaded.status_code == 200, uploaded.text
    photo_file_id = uploaded.json()["photo_file_id"]
    assert photo_file_id is not None

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["person"]["photo_file_id"] == photo_file_id
    assert client.get(f"/api/v1/persons/{own_id}/photo").status_code == 200

    removed = client.delete(f"/api/v1/persons/{own_id}/photo", headers=_csrf_headers(client))
    assert removed.status_code == 204, removed.text
    with session_scope() as session:
        assert session.get(Person, own_id).photo_file_id is None  # type: ignore[union-attr]


# --- Member: another Person -----------------------------------------------------


@requires_postgres
def test_seeded_member_cannot_read_or_update_another_person(client: TestClient) -> None:
    user_id, _, other_id = _member_world()
    _authenticate_as(user_id)

    assert client.get(f"/api/v1/persons/{other_id}").status_code == 404
    assert _patch(client, other_id, {"first_name": "Pwned"}).status_code == 404
    assert _patch(client, other_id, {"email": "pwned@example.com"}).status_code == 404
    photo = client.put(
        f"/api/v1/persons/{other_id}/photo",
        files={"photo": ("p.png", _image_bytes(), "image/png")},
        headers=_csrf_headers(client),
    )
    assert photo.status_code == 404
    assert (
        client.delete(f"/api/v1/persons/{other_id}/photo", headers=_csrf_headers(client))
    ).status_code == 404

    with session_scope() as session:
        other = session.get(Person, other_id)
        assert other is not None
        assert other.first_name != "Pwned"
        assert other.email == "other@example.com"
        assert other.photo_file_id is None


# --- other seeded roles: unchanged ----------------------------------------------


@requires_postgres
def test_seeded_admin_keeps_all_scope_with_all_fields(client: TestClient) -> None:
    with session_scope() as session:
        club = _club(session)
        # The global system admin (bootstrap shape, ADR-0035 §5): the only
        # holder allowed to change birth_date.
        admin = _user_with_role(session, club, "admin", global_assignment=True)
        target = _person(session, club)
        session.commit()
        admin_id, admin_person_id, target_id = admin.id, admin.person_id, target.id
    _authenticate_as(admin_id)

    assert client.get(f"/api/v1/persons/{target_id}").status_code == 200
    response = _patch(
        client, target_id, {"email": "fixed@example.com", "birth_date": "2011-02-03"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "fixed@example.com"
    assert response.json()["birth_date"] == "2011-02-03"
    own = _patch(client, admin_person_id, {"email": "admin-own@example.com"})
    assert own.status_code == 200, own.text


@requires_postgres
def test_seeded_instructor_and_guardian_person_access_is_unchanged(client: TestClient) -> None:
    """Neither seeded role holds any `person.*` grant at head (Issue #312
    deliberately adds none): own and other Persons stay existence-hidden,
    including a Guardian's linked child."""
    with session_scope() as session:
        club = _club(session)
        instructor = _user_with_role(session, club, "instructor")
        guardian = _user_with_role(session, club, "guardian")
        child = _person(session, club)
        session.add(
            GuardianRelationship(
                guardian_person_id=guardian.person_id,
                child_person_id=child.id,
                relationship_type="parent",
                status="active",
                valid_from=_LONG_AGO,
            )
        )
        session.commit()
        users = {
            "instructor": (instructor.id, instructor.person_id),
            "guardian": (guardian.id, guardian.person_id),
        }
        child_id = child.id

    for user_id, own_person_id in users.values():
        _authenticate_as(user_id)
        for target in (own_person_id, child_id):
            assert client.get(f"/api/v1/persons/{target}").status_code == 404
            assert _patch(client, target, {"phone": "+71112223344"}).status_code == 404


# --- migration 3b1fd730bb0d -----------------------------------------------------


@requires_postgres
def test_seed_migration_upgrade_downgrade_upgrade_is_exact_and_idempotent(
    database_url: str,
) -> None:
    at_head = _grant_scopes()
    assert _SEEDED <= at_head
    with session_scope() as session:
        rows_at_head = session.execute(select(func.count()).select_from(RolePermission)).scalar()
        scopes_at_head = session.execute(
            select(func.count()).select_from(RolePermissionScope)
        ).scalar()

    try:
        downgrade = run_alembic("downgrade", _BEFORE_SEED_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        # Exactly this migration's scopes and grants are gone; nothing else.
        assert _grant_scopes() == at_head - _SEEDED
        assert not {(r, p) for r, p, _ in _SEEDED} & _grant_pairs()

        # A pre-existing extra scope on one of the same grants must survive
        # the migration's own upgrade/downgrade round trip.
        with session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO role_permissions (id, role_id, permission_id)
                    SELECT gen_random_uuid(), r.id, p.id FROM roles r, permissions p
                    WHERE r.code = 'member' AND p.code = 'person.read'
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO role_permission_scopes (id, role_permission_id, scope_type)
                    SELECT gen_random_uuid(), rp.id, 'none' FROM role_permissions rp
                    JOIN roles r ON r.id = rp.role_id
                    JOIN permissions p ON p.id = rp.permission_id
                    WHERE r.code = 'member' AND p.code = 'person.read'
                    """
                )
            )
            session.commit()

        upgrade = run_alembic("upgrade", _SEED_REVISION, database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr
        assert _grant_scopes() == at_head | {("member", "person.read", "none")}

        downgrade = run_alembic("downgrade", _BEFORE_SEED_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        assert ("member", "person.read", "none") in _grant_scopes()
        assert ("member", "person.read", "self") not in _grant_scopes()
        assert ("member", "person.read") in _grant_pairs()
        assert ("member", "person.update") not in _grant_pairs()
    finally:
        restored = run_alembic("upgrade", "head", database_url=database_url)
        assert restored.returncode == 0, restored.stderr
        with session_scope() as session:
            session.execute(
                text(
                    """
                    DELETE FROM role_permission_scopes rps
                    USING role_permissions rp, roles r, permissions p
                    WHERE rps.role_permission_id = rp.id AND rp.role_id = r.id
                      AND rp.permission_id = p.id AND r.code = 'member'
                      AND p.code = 'person.read' AND rps.scope_type = 'none'
                    """
                )
            )
            session.commit()

    # Re-upgrading restored exactly the head state: no duplicate rows.
    assert _grant_scopes() == at_head
    with session_scope() as session:
        assert (
            session.execute(select(func.count()).select_from(RolePermission)).scalar()
            == rows_at_head
        )
        assert (
            session.execute(select(func.count()).select_from(RolePermissionScope)).scalar()
            == scopes_at_head
        )

    # A scope-less (member, person.*) grant already present before the
    # upgrade is reused, never duplicated.
    try:
        rerun = run_alembic("downgrade", _BEFORE_SEED_REVISION, database_url=database_url)
        assert rerun.returncode == 0, rerun.stderr
        with session_scope() as session:
            for _, permission_code, _ in sorted(_SEEDED):
                session.execute(
                    text(
                        """
                        INSERT INTO role_permissions (id, role_id, permission_id)
                        SELECT gen_random_uuid(), r.id, p.id FROM roles r, permissions p
                        WHERE r.code = 'member' AND p.code = :permission
                        """
                    ),
                    {"permission": permission_code},
                )
            session.commit()
    finally:
        restored = run_alembic("upgrade", "head", database_url=database_url)
        assert restored.returncode == 0, restored.stderr
    assert _grant_scopes() == at_head
    with session_scope() as session:
        assert (
            session.execute(select(func.count()).select_from(RolePermission)).scalar()
            == rows_at_head
        )
