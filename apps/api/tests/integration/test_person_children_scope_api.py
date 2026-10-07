"""AUTH-1 — Guardian -> Person through scope `children`
(docs/02-requirements/role-permission-scope-matrix.md §3.4/§4).

- `person.read(children)`: a Guardian reads a Person only through an
  active, interval-valid GuardianRelationship from the Guardian's Person to
  that Person; anything else keeps the existing existence-hiding 404.
- `person.update(children)`: same reach; only first/last/middle name,
  phone, address and the avatar may change. email / birth_date -> 403.
- The field restrictions apply when the Person is reachable solely through
  `children` and/or `self` (Issue #312) — `all` keeps its ordinary rules.

Self-contained factories, per this codebase's convention of not importing
helpers across test files.
"""

import datetime
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.authentication.bootstrap import ADMIN_ROLE_CODE
from app.db.authorization import (
    Permission,
    Role,
    RolePermission,
    RolePermissionScope,
    UserRoleAssignment,
)
from app.db.identity import GuardianRelationship, Person, User
from app.db.session import session_scope
from app.main import app
from app.people.authorization import CHILD_UPDATABLE_PERSON_FIELDS
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres


@pytest.fixture
def client(tmp_path) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=tmp_path)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _grant(user_id: uuid.UUID, permission_code: str, scope_type: str) -> None:
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
        session.add(UserRoleAssignment(user_id=user_id, role_id=role.id))
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _seed(
    *,
    relationship_status: str | None = "active",
    valid_to: datetime.datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """(guardian_user_id, guardian_person_id, child_person_id,
    unrelated_person_id). `relationship_status=None` -> no relationship."""
    with session_scope() as session:
        guardian = Person(last_name="Guardian", first_name=f"G-{uuid.uuid4().hex[:6]}")
        child = Person(
            last_name="Child",
            first_name="Original",
            email="child@example.com",
            birth_date=datetime.date(2015, 5, 5),
            phone="+70000000000",
        )
        unrelated = Person(last_name="Stranger", first_name=f"S-{uuid.uuid4().hex[:6]}")
        guardian_user = User(
            person=guardian,
            login_identifier=f"guardian-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([guardian, child, unrelated, guardian_user])
        session.flush()
        if relationship_status is not None:
            session.add(
                GuardianRelationship(
                    guardian_person_id=guardian.id,
                    child_person_id=child.id,
                    relationship_type="parent",
                    status=relationship_status,
                    valid_from=_utc(2020, 1, 1),
                    valid_to=valid_to,
                )
            )
        session.commit()
        return guardian_user.id, guardian.id, child.id, unrelated.id


# Every "the child is not reachable" state: none, unrelated handled separately.
_HIDDEN_RELATIONSHIPS = {
    "inactive": {"relationship_status": "inactive"},
    "revoked": {"relationship_status": "revoked", "valid_to": _utc(2021, 1, 1)},
    "expired": {"relationship_status": "active", "valid_to": _utc(2021, 1, 1)},
}


def _public(response) -> dict:  # type: ignore[no-untyped-def]
    """The error envelope minus the per-request `request_id`."""
    body = response.json()
    body["error"].pop("request_id", None)
    return body


def _stored(person_id: uuid.UUID) -> Person:
    with session_scope() as session:
        person = session.get(Person, person_id)
        assert person is not None
        session.expunge(person)
        return person


# --- READ -----------------------------------------------------------------------


@requires_postgres
def test_read_children_active_child_is_visible(client: TestClient) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.read", "children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/persons/{child_id}")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(child_id)


@requires_postgres
def test_read_children_list_contains_only_own_children(client: TestClient) -> None:
    guardian_user_id, _, child_id, unrelated_id = _seed()
    _grant(guardian_user_id, "person.read", "children")
    _authenticate_as(guardian_user_id)

    response = client.get("/api/v1/persons")

    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert ids == {str(child_id)}
    assert str(unrelated_id) not in ids


@requires_postgres
def test_read_children_unrelated_person_is_hidden(client: TestClient) -> None:
    guardian_user_id, _, _, unrelated_id = _seed()
    _grant(guardian_user_id, "person.read", "children")
    _authenticate_as(guardian_user_id)

    unrelated = client.get(f"/api/v1/persons/{unrelated_id}")
    missing = client.get(f"/api/v1/persons/{uuid.uuid4()}")

    assert unrelated.status_code == missing.status_code == 404
    assert _public(unrelated) == _public(missing)


@requires_postgres
@pytest.mark.parametrize("state", sorted(_HIDDEN_RELATIONSHIPS))
def test_read_children_non_active_relationship_is_hidden(client: TestClient, state: str) -> None:
    guardian_user_id, _, child_id, _ = _seed(**_HIDDEN_RELATIONSHIPS[state])  # type: ignore[arg-type]
    _grant(guardian_user_id, "person.read", "children")
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/persons/{child_id}")

    assert response.status_code == 404, response.text


@requires_postgres
def test_read_without_person_read_keeps_existing_404(client: TestClient) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.update", "children")  # not person.read
    _authenticate_as(guardian_user_id)

    response = client.get(f"/api/v1/persons/{child_id}")

    assert response.status_code == 404, response.text


@requires_postgres
def test_read_self_scope_does_not_reach_child(client: TestClient) -> None:
    """A GuardianRelationship alone grants nothing: `self` stays `self`."""
    guardian_user_id, guardian_person_id, child_id, _ = _seed()
    _grant(guardian_user_id, "person.read", "self")
    _authenticate_as(guardian_user_id)

    assert client.get(f"/api/v1/persons/{child_id}").status_code == 404
    assert client.get(f"/api/v1/persons/{guardian_person_id}").status_code == 200


# --- UPDATE: allowed fields ---------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize(
    "payload",
    [
        {"first_name": "Renamed", "last_name": "Family", "middle_name": "Middle"},
        {"phone": "+79991234567"},
        {"address": "Moscow, Tverskaya 1"},
        {"photo_file_id": str(uuid.uuid4())},
    ],
    ids=["full_name", "phone", "address", "photo_file_id"],
)
def test_update_children_allowed_fields_succeed(client: TestClient, payload: dict) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    response = client.patch(
        f"/api/v1/persons/{child_id}", json=payload, headers=_csrf_headers(client)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    for field, value in payload.items():
        assert body[field] == value


@requires_postgres
def test_update_children_can_upload_avatar(client: TestClient) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    response = client.put(
        f"/api/v1/persons/{child_id}/photo",
        files={"photo": ("avatar.png", _png(), "image/png")},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert _stored(child_id).photo_file_id is not None


# --- UPDATE: forbidden fields ---------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize(
    "payload",
    [
        {"email": "new@example.com"},
        {"birth_date": "2010-01-01"},
        {"first_name": "Renamed", "email": "new@example.com"},
    ],
    ids=["email", "birth_date", "allowed_plus_email"],
)
def test_update_children_forbidden_fields_are_403_and_nothing_changes(
    client: TestClient, payload: dict
) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    response = client.patch(
        f"/api/v1/persons/{child_id}", json=payload, headers=_csrf_headers(client)
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    stored = _stored(child_id)
    assert stored.email == "child@example.com"
    assert stored.birth_date == datetime.date(2015, 5, 5)
    assert stored.first_name == "Original"


def test_child_updatable_fields_exclude_email_and_birth_date() -> None:
    """The child allowlist itself — `birth_date` is additionally guarded by
    the pre-existing admin-only check, so only this pins the allowlist."""
    assert CHILD_UPDATABLE_PERSON_FIELDS == {
        "first_name",
        "last_name",
        "middle_name",
        "phone",
        "address",
        "photo_file_id",
    }
    assert "email" not in CHILD_UPDATABLE_PERSON_FIELDS
    assert "birth_date" not in CHILD_UPDATABLE_PERSON_FIELDS


# --- UPDATE: reach ------------------------------------------------------------------


@requires_postgres
def test_update_children_unrelated_person_is_hidden(client: TestClient) -> None:
    guardian_user_id, _, _, unrelated_id = _seed()
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    unrelated = client.patch(
        f"/api/v1/persons/{unrelated_id}", json={"phone": "+71"}, headers=_csrf_headers(client)
    )
    missing = client.patch(
        f"/api/v1/persons/{uuid.uuid4()}", json={"phone": "+71"}, headers=_csrf_headers(client)
    )

    assert unrelated.status_code == missing.status_code == 404
    assert _public(unrelated) == _public(missing)


@requires_postgres
@pytest.mark.parametrize("state", sorted(_HIDDEN_RELATIONSHIPS))
def test_update_children_non_active_relationship_is_hidden(client: TestClient, state: str) -> None:
    guardian_user_id, _, child_id, _ = _seed(**_HIDDEN_RELATIONSHIPS[state])  # type: ignore[arg-type]
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    response = client.patch(
        f"/api/v1/persons/{child_id}", json={"phone": "+71"}, headers=_csrf_headers(client)
    )

    assert response.status_code == 404, response.text
    assert _stored(child_id).phone == "+70000000000"


@requires_postgres
def test_update_without_person_update_keeps_existing_404(client: TestClient) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.read", "children")  # read only
    _authenticate_as(guardian_user_id)

    response = client.patch(
        f"/api/v1/persons/{child_id}", json={"phone": "+71"}, headers=_csrf_headers(client)
    )

    assert response.status_code == 404, response.text


# --- Multiple scopes: self restricted too, all keeps its ordinary rules ------------


@requires_postgres
def test_self_scope_restricts_own_email_like_children(client: TestClient) -> None:
    """A user holding `self` + `children` edits their own Person under the
    `self` field rules (role-permission-scope-matrix.md §4.1, Issue #312):
    `email` is outside the restricted field set through `self` exactly as
    through `children`, while an allowed field still updates."""
    guardian_user_id, guardian_person_id, child_id, _ = _seed()
    _grant(guardian_user_id, "person.update", "self")
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    own = client.patch(
        f"/api/v1/persons/{guardian_person_id}",
        json={"email": "guardian-new@example.com"},
        headers=_csrf_headers(client),
    )
    child = client.patch(
        f"/api/v1/persons/{child_id}",
        json={"email": "child-new@example.com"},
        headers=_csrf_headers(client),
    )
    own_phone = client.patch(
        f"/api/v1/persons/{guardian_person_id}",
        json={"phone": "+70000000000"},
        headers=_csrf_headers(client),
    )

    assert own.status_code == 403, own.text
    assert child.status_code == 403, child.text
    assert own_phone.status_code == 200, own_phone.text


@requires_postgres
def test_all_scope_plus_children_keeps_all_field_rules(client: TestClient) -> None:
    """`all` reaches the child too, so ordinary `all` rules apply (email
    editable) even though a `children` grant also matches."""
    guardian_user_id, _, child_id, _ = _seed()
    _grant(guardian_user_id, "person.update", "all")
    _grant(guardian_user_id, "person.update", "children")
    _authenticate_as(guardian_user_id)

    response = client.patch(
        f"/api/v1/persons/{child_id}",
        json={"email": "child-new@example.com"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["email"] == "child-new@example.com"


@requires_postgres
def test_system_admin_can_still_update_child_birth_date(client: TestClient) -> None:
    guardian_user_id, _, child_id, _ = _seed()
    with session_scope() as session:
        admin_person = Person(last_name="Admin", first_name="A")
        admin_user = User(
            person=admin_person,
            login_identifier=f"admin-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([admin_person, admin_user])
        session.flush()
        admin_role = session.execute(
            select(Role).where(Role.code == ADMIN_ROLE_CODE, Role.is_system.is_(True))
        ).scalar_one()
        session.add(
            UserRoleAssignment(user_id=admin_user.id, role_id=admin_role.id, scope_type="all")
        )
        session.commit()
        admin_user_id = admin_user.id
    _authenticate_as(admin_user_id)

    response = client.patch(
        f"/api/v1/persons/{child_id}",
        json={"birth_date": "2014-04-04", "email": "admin-set@example.com"},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["birth_date"] == "2014-04-04"
