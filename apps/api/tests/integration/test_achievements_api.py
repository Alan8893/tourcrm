"""HTTP-level integration tests for `/api/v1/achievements` (Issue #220).

Against the REAL shipped app and a real PostgreSQL database, with
authorization coming only from the migration-seeded role matrix:

- Definition lifecycle (A1), Rule Version versioning / immutability /
  single active version (A4/A11), structured conditions and the approved
  metric catalog (A5/A8), Normative Requirement Set versions (§4/§5);
- manual Awards and revocation (§3, A2/A3/A12);
- Administrator-only authorization through `achievement.manage` /
  `achievement.award` / `achievement.read` (A10).

The Engine on canonical Trip/Event facts is covered in
test_achievement_engine.py. Self-contained factories, per this
codebase's convention of not importing helpers across test files.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.achievements import AchievementAward
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

from .conftest import requires_postgres

pytestmark = requires_postgres

_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_COMPLETED_TRIPS_1 = {"metric": "completed_trips", "operator": ">=", "value": 1}


def _trips(count: int) -> dict:
    return {"metric": "completed_trips", "operator": ">=", "value": count}


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories -------------------------------------------------------------------


def _person(session, last_name: str = "Ivanova") -> Person:  # type: ignore[no-untyped-def]
    person = Person(last_name=last_name, first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    return person


def _user_with_roles(session, club: Club, *role_codes: str) -> User:  # type: ignore[no-untyped-def]
    person = _person(session)
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="member",
            status="active",
            joined_at=_LONG_AGO,
        )
    )
    user = User(
        person=person,
        login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    for role_code in role_codes:
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()
    return user


@dataclass(frozen=True)
class World:
    admin: uuid.UUID
    instructor: uuid.UUID
    member: uuid.UUID
    guardian: uuid.UUID
    member_person: uuid.UUID
    other_member_person: uuid.UUID
    instructor_person: uuid.UUID
    guardian_person: uuid.UUID
    admin_person: uuid.UUID
    child_person_without_role: uuid.UUID


def _world() -> World:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        admin = _user_with_roles(session, club, "admin")
        instructor = _user_with_roles(session, club, "instructor")
        member = _user_with_roles(session, club, "member")
        other_member = _user_with_roles(session, club, "member")
        guardian = _user_with_roles(session, club, "guardian")
        child = _person(session, "Child")
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
        return World(
            admin=admin.id,
            instructor=instructor.id,
            member=member.id,
            guardian=guardian.id,
            member_person=member.person_id,
            other_member_person=other_member.person_id,
            instructor_person=instructor.person_id,
            guardian_person=guardian.person_id,
            admin_person=admin.person_id,
            child_person_without_role=child.id,
        )


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _post(client: TestClient, path: str, body: dict | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/achievements{path}", json=body or {}, headers=_csrf(client))


def _patch(client: TestClient, path: str, body: dict):  # type: ignore[no-untyped-def]
    return client.patch(f"/api/v1/achievements{path}", json=body, headers=_csrf(client))


def _get(client: TestClient, path: str):  # type: ignore[no-untyped-def]
    return client.get(f"/api/v1/achievements{path}")


def _definition(client: TestClient, *, activate: bool = True, **overrides: str) -> dict:
    body = {
        "code": f"def-{uuid.uuid4().hex[:8]}",
        "name": "Первый поход",
        "source": "club",
        "award_method": "manual",
        "repeatability": "non_repeatable",
        **overrides,
    }
    response = _post(client, "/definitions", body)
    assert response.status_code == 201, response.text
    definition = response.json()
    if activate:
        response = _post(client, f"/definitions/{definition['id']}/activate")
        assert response.status_code == 200, response.text
        definition = response.json()
    return definition


def _rule(
    client: TestClient,
    definition_id: str,
    condition: dict | None = None,
    *,
    normative_set_version_id: str | None = None,
    activate: bool = True,
) -> dict:
    body: dict = {"condition": condition or _COMPLETED_TRIPS_1}
    if normative_set_version_id is not None:
        body["normative_set_version_id"] = normative_set_version_id
    response = _post(client, f"/definitions/{definition_id}/rule-versions", body)
    assert response.status_code == 201, response.text
    rule = response.json()
    if activate:
        response = _post(client, f"/rule-versions/{rule['id']}/activate")
        assert response.status_code == 200, response.text
        rule = response.json()
    return rule


def _normative_version(client: TestClient, **overrides: object) -> dict:
    response = _post(
        client, "/normative-sets", {"code": f"fstr-{uuid.uuid4().hex[:6]}", "name": "ФСТР"}
    )
    assert response.status_code == 201, response.text
    body = {
        "source_organization": "ФСТР",
        "document_title": "Знаки отличия детско-юношеского туризма",
        "source_url": "https://tssr.ru/child/",
        "document_version": "test-1",
        "publication_date": "2025-01-10",
        "effective_from": "2025-02-01",
        **overrides,
    }
    response = _post(client, f"/normative-sets/{response.json()['id']}/versions", body)
    assert response.status_code == 201, response.text
    return response.json()


def _award(  # type: ignore[no-untyped-def]
    client: TestClient,
    definition_id: str,
    person_id: uuid.UUID,
    note: str | None = None,
    *,
    rule_version_id: str | None = None,
):
    body: dict = {"definition_id": definition_id, "person_id": str(person_id)}
    if note is not None:
        body["verification_note"] = note
    if rule_version_id is not None:
        body["rule_version_id"] = rule_version_id
    return _post(client, "/awards", body)


# --- authorization (A10) ----------------------------------------------------------


def test_permission_seed_grants_manage_and_award_to_admin_only() -> None:
    with session_scope() as session:
        rows = session.execute(
            select(Role.code, Permission.code, RolePermissionScope.scope_type)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .join(RolePermissionScope, RolePermissionScope.role_permission_id == RolePermission.id)
            .where(Permission.code.in_(["achievement.manage", "achievement.award"]))
        ).all()
    assert sorted(rows) == [
        ("admin", "achievement.award", "all"),
        ("admin", "achievement.manage", "all"),
    ]


@pytest.mark.parametrize("role", ["instructor", "member", "guardian"])
def test_non_administrators_cannot_manage_award_revoke_or_reconcile(
    client: TestClient, role: str
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, award_method="both")
    award = _award(client, definition["id"], world.member_person).json()
    normative = _normative_version(client)

    _authenticate_as(getattr(world, role))
    denied = [
        _get(client, "/definitions"),
        _get(client, f"/definitions/{definition['id']}"),
        _get(client, "/rule-catalog"),
        _post(client, "/definitions", {
            "code": "x", "name": "x", "source": "club",
            "award_method": "manual", "repeatability": "repeatable",
        }),
        _patch(client, f"/definitions/{definition['id']}", {"name": "hacked"}),
        _post(client, f"/definitions/{definition['id']}/deactivate"),
        _post(client, f"/definitions/{definition['id']}/rule-versions",
              {"condition": _COMPLETED_TRIPS_1}),
        _get(client, "/normative-sets"),
        _post(client, "/normative-sets", {"code": "n", "name": "n"}),
        _post(client, f"/normative-versions/{normative['id']}/activate"),
        _award(client, definition["id"], world.member_person),
        _post(client, f"/awards/{award['id']}/revoke", {"reason": "no"}),
        _get(client, "/awards"),
        _post(client, "/reconciliation"),
    ]
    assert [response.status_code for response in denied] == [403] * len(denied)

    # Nothing changed behind the denials.
    _authenticate_as(world.admin)
    assert _get(client, f"/definitions/{definition['id']}").json()["status"] == "active"
    assert _get(client, f"/awards/{award['id']}").json()["status"] == "active"


def test_denial_does_not_reveal_existence(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.instructor)
    assert _get(client, f"/definitions/{uuid.uuid4()}").status_code == 403
    assert _post(client, f"/awards/{uuid.uuid4()}/revoke", {"reason": "x"}).status_code == 403


def test_unauthenticated_and_missing_csrf_are_rejected(client: TestClient) -> None:
    world = _world()
    assert _get(client, "/definitions").status_code == 401
    _authenticate_as(world.admin)
    response = client.post(
        "/api/v1/achievements/definitions",
        json={"code": "c", "name": "n", "source": "club", "award_method": "manual",
              "repeatability": "repeatable"},
    )
    assert response.status_code == 403


def test_administrator_reads_rule_catalog_with_only_approved_metric(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    catalog = _get(client, "/rule-catalog").json()
    assert [metric["code"] for metric in catalog["metrics"]] == ["completed_trips"]
    assert catalog["logic_operators"] == ["AND", "OR"]
    assert ">=" in catalog["comparison_operators"]


# --- Definition lifecycle (A1) ------------------------------------------------------


def test_definition_create_activate_deactivate_reactivate(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    created = _definition(client, activate=False, description="  Описание  ")
    assert created["status"] == "inactive"
    assert created["description"] == "Описание"
    definition_id = created["id"]

    assert _post(client, f"/definitions/{definition_id}/activate").json()["status"] == "active"
    assert _post(client, f"/definitions/{definition_id}/deactivate").json()["status"] == "inactive"
    assert _post(client, f"/definitions/{definition_id}/activate").json()["status"] == "active"

    renamed = _patch(client, f"/definitions/{definition_id}", {"name": "Новое имя"}).json()
    assert renamed["name"] == "Новое имя"
    assert renamed["source"] == created["source"]

    listed = _get(client, "/definitions?status=active").json()
    assert [item["id"] for item in listed["items"]] == [definition_id]


def test_definition_cannot_be_deleted(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    response = client.delete(
        f"/api/v1/achievements/definitions/{definition['id']}", headers=_csrf(client)
    )
    assert response.status_code == 405
    assert _get(client, f"/definitions/{definition['id']}").status_code == 200


def test_definition_semantics_are_not_editable(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, repeatability="non_repeatable")
    response = _patch(client, f"/definitions/{definition['id']}", {
        "code": "changed", "source": "fstr", "award_method": "automatic",
        "repeatability": "repeatable",
    })
    assert response.status_code == 200
    semantic = ("code", "source", "award_method", "repeatability")
    assert {key: response.json()[key] for key in semantic} == {
        key: definition[key] for key in semantic
    }


def test_definition_validation_and_duplicate_code(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    duplicate = _post(client, "/definitions", {
        "code": definition["code"], "name": "x", "source": "club",
        "award_method": "manual", "repeatability": "repeatable",
    })
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_code"
    invalid = _post(client, "/definitions", {
        "code": "y", "name": "x", "source": "school",
        "award_method": "manual", "repeatability": "repeatable",
    })
    assert invalid.status_code == 422


def test_deactivation_does_not_change_existing_awards(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    award = _award(client, definition["id"], world.member_person).json()
    _post(client, f"/definitions/{definition['id']}/deactivate")
    assert _get(client, f"/awards/{award['id']}").json() == award


def test_inactive_definition_creates_no_new_award(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, activate=False)
    response = _award(client, definition["id"], world.member_person)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "definition_inactive"


# --- Rule Versions (A4/A5/A8/A11) ---------------------------------------------------


def test_rule_versions_are_numbered_and_only_one_is_active(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    v1 = _rule(client, definition["id"])
    v2 = _rule(
        client, definition["id"], {"metric": "completed_trips", "operator": ">=", "value": 3}
    )
    assert (v1["version_number"], v2["version_number"]) == (1, 2)

    versions = _get(client, f"/definitions/{definition['id']}/rule-versions").json()["items"]
    statuses = {item["version_number"]: item["status"] for item in versions}
    assert statuses == {1: "inactive", 2: "active"}

    _post(client, f"/rule-versions/{v1['id']}/activate")
    versions = _get(client, f"/definitions/{definition['id']}/rule-versions").json()["items"]
    assert {item["version_number"]: item["status"] for item in versions} == {
        1: "active",
        2: "inactive",
    }


def test_rule_with_unsupported_metric_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    response = _post(client, f"/definitions/{definition['id']}/rule-versions", {
        "condition": {
            "logic": "AND",
            "conditions": [
                _COMPLETED_TRIPS_1,
                {"metric": "tourism_regions", "operator": ">=", "value": 1},
            ],
        }
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_metric"
    assert response.json()["error"]["details"]["path"] == "$.conditions[1]"
    assert _get(client, f"/definitions/{definition['id']}/rule-versions").json()["items"] == []


def test_malformed_rule_is_rejected(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    response = _post(client, f"/definitions/{definition['id']}/rule-versions", {
        "condition": {"logic": "XOR", "conditions": [_COMPLETED_TRIPS_1]}
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_rule"


def test_nested_rule_is_stored_as_structured_data(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    condition = {
        "logic": "OR",
        "conditions": [
            {"logic": "AND", "conditions": [_COMPLETED_TRIPS_1]},
            {"metric": "completed_trips", "operator": "==", "value": 0},
        ],
    }
    rule = _rule(client, definition["id"], condition)
    assert _get(client, f"/rule-versions/{rule['id']}").json()["condition"] == condition


def test_fstr_rule_requires_and_references_exact_normative_version(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, source="fstr")
    response = _post(
        client, f"/definitions/{definition['id']}/rule-versions", {"condition": _COMPLETED_TRIPS_1}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "normative_reference_required"

    normative = _normative_version(client)
    rule = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    assert rule["normative_set_version_id"] == normative["id"]


def test_club_rule_may_have_no_normative_version(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, source="club")
    rule = _rule(client, definition["id"])
    assert rule["normative_set_version_id"] is None


def _rule_version_content(client: TestClient, rule_id: str) -> tuple:
    rule = _get(client, f"/rule-versions/{rule_id}").json()
    return rule["condition"], rule["normative_set_version_id"], rule["version_number"]


def test_rule_version_is_immutable_from_creation_even_when_unused(client: TestClient) -> None:
    """A13: no in-place change of an existing Rule Version — used or not."""
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, source="fstr")
    normative = _normative_version(client)
    other_normative = _normative_version(client)
    rule = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    assert rule["is_used"] is False
    before = _rule_version_content(client, rule["id"])

    condition_change = _patch(
        client, f"/rule-versions/{rule['id']}", {"condition": _trips(5)}
    )
    assert condition_change.status_code == 405
    assert _rule_version_content(client, rule["id"]) == before

    normative_change = _patch(
        client,
        f"/rule-versions/{rule['id']}",
        {"normative_set_version_id": other_normative["id"]},
    )
    assert normative_change.status_code == 405
    assert _rule_version_content(client, rule["id"]) == before


def test_rule_change_is_a_new_version_and_lifecycle_still_works(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    v1 = _rule(client, definition["id"], _trips(1))
    award = _award(client, definition["id"], world.member_person, rule_version_id=v1["id"]).json()

    v2 = _rule(client, definition["id"], _trips(2), activate=False)
    assert (v2["definition_id"], v2["version_number"], v2["condition"]) == (
        definition["id"],
        2,
        _trips(2),
    )
    assert _get(client, f"/rule-versions/{v1['id']}").json()["condition"] == _trips(1)

    # Lifecycle (A11) is not content mutation.
    assert _post(client, f"/rule-versions/{v2['id']}/activate").json()["status"] == "active"
    assert _get(client, f"/rule-versions/{v1['id']}").json()["status"] == "inactive"
    assert _post(client, f"/rule-versions/{v2['id']}/deactivate").json()["status"] == "inactive"
    assert _post(client, f"/rule-versions/{v1['id']}/activate").json()["status"] == "active"
    assert _get(client, f"/rule-versions/{v1['id']}").json()["condition"] == _trips(1)

    # The historical Award keeps its original Rule Version.
    assert _get(client, f"/awards/{award['id']}").json()["rule_version_id"] == v1["id"]


# --- Normative Requirement Sets (§4/§5) --------------------------------------------


def test_normative_version_metadata_versioning_and_lifecycle(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    v1 = _normative_version(client, effective_to="2026-12-31")
    assert v1["status"] == "inactive"
    assert v1["version_number"] == 1
    assert (v1["source_organization"], v1["source_url"], v1["document_version"]) == (
        "ФСТР",
        "https://tssr.ru/child/",
        "test-1",
    )
    assert (v1["publication_date"], v1["effective_from"], v1["effective_to"]) == (
        "2025-01-10",
        "2025-02-01",
        "2026-12-31",
    )
    set_id = v1["normative_set_id"]
    v2 = _post(client, f"/normative-sets/{set_id}/versions", {
        "source_organization": "ФСТР", "document_title": "t", "source_url": "https://tssr.ru",
        "document_version": "test-2", "effective_from": "2027-01-01",
    }).json()
    assert v2["version_number"] == 2

    assert _post(client, f"/normative-versions/{v1['id']}/activate").json()["status"] == "active"
    assert _post(client, f"/normative-versions/{v1['id']}/deactivate").json()["status"] == (
        "inactive"
    )
    listed = _get(client, f"/normative-sets/{set_id}/versions").json()["items"]
    assert [item["version_number"] for item in listed] == [2, 1]


def test_normative_version_validation(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    version = _normative_version(client)
    response = _patch(
        client, f"/normative-versions/{version['id']}", {"effective_to": "2020-01-01"}
    )
    assert response.status_code == 422
    missing = _post(client, f"/normative-sets/{version['normative_set_id']}/versions", {
        "source_organization": "ФСТР", "document_title": "t", "source_url": "u",
        "document_version": "v",
    })
    assert missing.status_code == 422


def test_used_normative_version_is_immutable(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client)
    edited = _patch(client, f"/normative-versions/{normative['id']}", {"document_version": "v2"})
    assert edited.status_code == 200

    definition = _definition(client, source="fstr")
    rule = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    award = _award(
        client,
        definition["id"],
        world.member_person,
        note="Проверено по маршрутке",
        rule_version_id=rule["id"],
    )
    assert award.status_code == 201
    assert award.json()["normative_set_version_id"] == normative["id"]

    response = _patch(client, f"/normative-versions/{normative['id']}", {"document_version": "v3"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_in_use"
    assert _get(client, f"/normative-versions/{normative['id']}").json()["document_version"] == "v2"


# --- manual Awards (§3, A3, A12) -----------------------------------------------------


def test_administrator_issues_manual_award_distinguishable_from_automatic(
    client: TestClient,
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, award_method="manual")
    response = _award(client, definition["id"], world.member_person, note="  Подтверждено  ")
    assert response.status_code == 201, response.text
    award = response.json()
    assert award["award_method"] == "manual"
    assert award["awarded_by_user_id"] == str(world.admin)
    assert award["verification_note"] == "Подтверждено"
    assert award["evaluation_trigger"] is None
    assert award["evaluated_metrics"] is None
    assert award["rule_version_id"] is None
    assert award["status"] == "active"
    assert award["definition_name"] == definition["name"]


def test_fstr_manual_verification_is_preserved(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client)
    definition = _definition(client, source="fstr", award_method="both")
    rule = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    award = _award(
        client, definition["id"], world.member_person, note="Маршрутная книжка",
        rule_version_id=rule["id"],
    ).json()
    assert award["award_method"] == "manual"
    assert award["awarded_by_user_id"] == str(world.admin)
    assert award["evaluation_trigger"] is None
    assert award["rule_version_id"] == rule["id"]
    assert award["normative_set_version_id"] == normative["id"]
    assert award["verification_note"] == "Маршрутная книжка"


def test_manual_award_is_refused_for_automatic_only_definition(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, award_method="automatic")
    response = _award(client, definition["id"], world.member_person)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "manual_award_not_allowed"


@pytest.mark.parametrize(
    "recipient",
    ["instructor_person", "guardian_person", "admin_person", "child_person_without_role"],
)
def test_only_members_receive_awards(client: TestClient, recipient: str) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    response = _award(client, definition["id"], getattr(world, recipient))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "recipient_not_member"


def test_non_repeatable_is_awarded_once(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, repeatability="non_repeatable")
    assert _award(client, definition["id"], world.member_person).status_code == 201
    second = _award(client, definition["id"], world.member_person)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_awarded"
    assert _award(client, definition["id"], world.other_member_person).status_code == 201


def test_repeatable_manual_award_is_allowed_multiple_times(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, repeatability="repeatable", award_method="both")
    first = _award(client, definition["id"], world.member_person)
    second = _award(client, definition["id"], world.member_person)
    assert (first.status_code, second.status_code) == (201, 201)
    history = _get(client, f"/awards?person_id={world.member_person}").json()
    assert history["pagination"]["total"] == 2


def test_unknown_person_or_definition_is_not_found(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    assert _award(client, definition["id"], uuid.uuid4()).status_code == 404
    assert _award(client, str(uuid.uuid4()), world.member_person).status_code == 404


# --- manual Award provenance (A15) ----------------------------------------------------


def test_manual_award_without_rule_version_never_gets_the_active_one(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client)
    _post(client, f"/normative-versions/{normative['id']}/activate")
    definition = _definition(client, award_method="both")
    active = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    assert active["status"] == "active"

    response = _post(client, "/awards", {
        "definition_id": definition["id"],
        "person_id": str(world.member_person),
        "rule_version_id": None,
        "verification_note": "Проверено",
    })
    assert response.status_code == 201, response.text
    award = response.json()
    assert award["rule_version_id"] is None
    assert award["normative_set_version_id"] is None
    assert award["award_method"] == "manual"


def test_manual_award_records_exactly_the_selected_rule_version(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client)
    definition = _definition(client, source="fstr", award_method="both")
    selected = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    award = _award(
        client, definition["id"], world.member_person, note="Маршрутная книжка",
        rule_version_id=selected["id"],
    ).json()
    assert award["rule_version_id"] == selected["id"]
    assert award["rule_version_number"] == 1
    assert award["normative_set_version_id"] == normative["id"]


def test_manual_award_keeps_an_explicit_inactive_historical_rule_version(
    client: TestClient,
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, repeatability="repeatable")
    historical = _rule(client, definition["id"], _trips(1))
    current = _rule(client, definition["id"], _trips(2))
    assert _get(client, f"/rule-versions/{historical['id']}").json()["status"] == "inactive"

    award = _award(
        client, definition["id"], world.member_person, rule_version_id=historical["id"]
    ).json()
    assert award["rule_version_id"] == historical["id"]
    assert award["rule_version_id"] != current["id"]


def test_manual_award_rejects_a_rule_version_of_another_definition(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    other = _definition(client)
    foreign_rule = _rule(client, other["id"])
    response = _award(
        client, definition["id"], world.member_person, rule_version_id=foreign_rule["id"]
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_version_definition_mismatch"
    assert _get(client, "/awards").json()["pagination"]["total"] == 0

    missing = _award(
        client, definition["id"], world.member_person, rule_version_id=str(uuid.uuid4())
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("note", [None, "", "   "])
def test_fstr_manual_award_requires_a_verification_note(
    client: TestClient, note: str | None
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, source="fstr")
    response = _award(client, definition["id"], world.member_person, note)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "verification_note_required"
    assert _get(client, "/awards").json()["pagination"]["total"] == 0


def test_normative_manual_verification_requires_a_note(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client)
    definition = _definition(client, source="club")
    rule = _rule(client, definition["id"], normative_set_version_id=normative["id"])
    response = _award(
        client, definition["id"], world.member_person, "  ", rule_version_id=rule["id"]
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "verification_note_required"


def test_fstr_manual_award_with_note_is_created(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client, source="fstr")
    response = _award(client, definition["id"], world.member_person, "  Маршрутная книжка  ")
    assert response.status_code == 201, response.text
    award = response.json()
    assert award["verification_note"] == "Маршрутная книжка"
    assert award["rule_version_id"] is None
    assert award["normative_set_version_id"] is None


# --- revocation (A2) ---------------------------------------------------------------


def test_revocation_requires_reason_and_preserves_the_award(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    rule = _rule(client, definition["id"])
    award = _award(
        client, definition["id"], world.member_person, note="ok", rule_version_id=rule["id"]
    ).json()

    for reason in ("", "   "):
        response = _post(client, f"/awards/{award['id']}/revoke", {"reason": reason})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "revocation_reason_required"

    revoked = _post(client, f"/awards/{award['id']}/revoke", {"reason": " Ошибка выдачи "}).json()
    assert revoked["status"] == "revoked"
    assert revoked["revocation_reason"] == "Ошибка выдачи"
    assert revoked["revoked_by_user_id"] == str(world.admin)
    assert revoked["revoked_at"] is not None
    unchanged = {
        key: value
        for key, value in revoked.items()
        if key not in {"status", "revocation_reason", "revoked_by_user_id", "revoked_at"}
    }
    original = {key: value for key, value in award.items() if key in unchanged}
    assert unchanged == original
    assert revoked["rule_version_id"] == rule["id"]

    again = _post(client, f"/awards/{award['id']}/revoke", {"reason": "again"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "award_already_revoked"

    with session_scope() as session:
        assert session.get(AchievementAward, uuid.UUID(award["id"])) is not None


def test_revoked_non_repeatable_award_still_blocks_a_second_award(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    award = _award(client, definition["id"], world.member_person).json()
    _post(client, f"/awards/{award['id']}/revoke", {"reason": "Ошибка"})
    response = _award(client, definition["id"], world.member_person)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_awarded"


def test_award_cannot_be_deleted(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    award = _award(client, definition["id"], world.member_person).json()
    response = client.delete(f"/api/v1/achievements/awards/{award['id']}", headers=_csrf(client))
    assert response.status_code == 405


def test_award_history_filters(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition = _definition(client)
    other = _definition(client)
    first = _award(client, definition["id"], world.member_person).json()
    _award(client, other["id"], world.member_person)
    _post(client, f"/awards/{first['id']}/revoke", {"reason": "x"})

    by_definition = _get(client, f"/awards?definition_id={definition['id']}").json()["items"]
    assert [item["id"] for item in by_definition] == [first["id"]]
    revoked = _get(client, "/awards?status=revoked").json()["items"]
    assert [item["id"] for item in revoked] == [first["id"]]
    manual = _get(client, "/awards?award_method=manual").json()["pagination"]["total"]
    assert manual == 2
    assert _get(client, "/awards?award_method=automatic").json()["pagination"]["total"] == 0
