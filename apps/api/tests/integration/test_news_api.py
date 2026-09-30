"""HTTP-level integration tests for News / Announcements (TH-0120 /
Issue #227; docs/04-ux/news.md), against the real app and PostgreSQL:

    GET/POST       /api/v1/news
    GET/PATCH      /api/v1/news/{news_id}
    POST           /api/v1/news/{news_id}/publish
    POST           /api/v1/news/{news_id}/archive
    GET/PUT/DELETE /api/v1/news/{news_id}/image

Covers Administrator-only mutation, backend-authoritative audience
visibility for all four roles (club / selected groups — Member via
GroupMembership, Guardian via child's GroupMembership, Instructor via
GroupInstructorAssignment), lifecycle, soft delete, Event linking, image
storage and existence hiding. Fixture helpers are local, following the
suite's convention (tests/integration/test_participant_export_api.py).
"""

import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.documents import File
from app.db.events import Event, EventGroupTarget
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.news import News, NewsGroupTarget
from app.db.session import session_scope
from app.main import app
from app.news import service as news_service
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres

pytestmark = requires_postgres

_URL = "/api/v1/news"


@pytest.fixture
def storage_root(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def client(storage_root) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=storage_root)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- factories ---------------------------------------------------------------


def _person(session, name: str) -> Person:
    person = Person(last_name="Тестов", first_name=f"{name}-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.flush()
    return person


def _user(session, person: Person) -> User:
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _assign_role(session, user: User, role_code: str, club: Club) -> None:
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all", club_id=club.id)
    )
    session.flush()


def _club_membership(session, club: Club, person: Person, status: str = "active"):
    membership = ClubMembership(
        club_id=club.id,
        person_id=person.id,
        membership_type="member",
        status=status,
        joined_at=_utc(2020, 1, 1),
    )
    session.add(membership)
    session.flush()
    return membership


def _group(session, club: Club, name: str) -> Group:
    group = Group(club_id=club.id, name=name, status="active", valid_from=_utc(2020, 1, 1))
    session.add(group)
    session.flush()
    return group


def _join_group(session, group: Group, membership: ClubMembership, **overrides) -> None:
    values: dict[str, object] = {
        "group_id": group.id,
        "club_membership_id": membership.id,
        "valid_from": _utc(2024, 1, 1),
        "membership_status": "active",
    }
    values.update(overrides)
    session.add(GroupMembership(**values))  # type: ignore[arg-type]
    session.flush()


def _guardian_of(session, guardian: Person, child: Person, **overrides) -> None:
    values: dict[str, object] = {
        "guardian_person_id": guardian.id,
        "child_person_id": child.id,
        "relationship_type": "parent",
        "status": "active",
        "valid_from": _utc(2020, 1, 1),
    }
    values.update(overrides)
    session.add(GuardianRelationship(**values))  # type: ignore[arg-type]
    session.flush()


def _instruct(session, group: Group, user: User, **overrides) -> None:
    values: dict[str, object] = {
        "group_id": group.id,
        "user_id": user.id,
        "role_in_group": "instructor",
        "valid_from": _utc(2024, 1, 1),
    }
    values.update(overrides)
    session.add(GroupInstructorAssignment(**values))  # type: ignore[arg-type]
    session.flush()


@dataclass
class World:
    club_id: uuid.UUID
    group_a: uuid.UUID
    group_b: uuid.UUID
    event_id: uuid.UUID
    admin: uuid.UUID
    member_in_a: uuid.UUID
    member_in_b: uuid.UUID
    member_ended_in_a: uuid.UUID
    guardian_child_in_a: uuid.UUID
    guardian_child_in_b: uuid.UUID
    guardian_revoked_child_in_a: uuid.UUID
    instructor_of_a: uuid.UUID
    instructor_of_b: uuid.UUID
    instructor_expired_a: uuid.UUID
    no_role_user: uuid.UUID


def _world() -> World:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        group_a = _group(session, club, "Группа А")
        group_b = _group(session, club, "Группа Б")

        def user_with_role(name: str, role: str) -> tuple[User, Person]:
            person = _person(session, name)
            user = _user(session, person)
            _assign_role(session, user, role, club)
            return user, person

        admin, _ = user_with_role("admin", "admin")

        member_a, member_a_person = user_with_role("member-a", "member")
        _join_group(session, group_a, _club_membership(session, club, member_a_person))
        member_b, member_b_person = user_with_role("member-b", "member")
        _join_group(session, group_b, _club_membership(session, club, member_b_person))
        member_ended, member_ended_person = user_with_role("member-ended", "member")
        _join_group(
            session,
            group_a,
            _club_membership(session, club, member_ended_person),
            valid_from=_utc(2022, 1, 1),
            valid_to=_utc(2023, 1, 1),
            membership_status="ended",
        )

        child_a = _person(session, "child-a")
        _join_group(session, group_a, _club_membership(session, club, child_a))
        child_b = _person(session, "child-b")
        _join_group(session, group_b, _club_membership(session, club, child_b))

        guardian_a, guardian_a_person = user_with_role("guardian-a", "guardian")
        _club_membership(session, club, guardian_a_person)
        _guardian_of(session, guardian_a_person, child_a)
        guardian_b, guardian_b_person = user_with_role("guardian-b", "guardian")
        _guardian_of(session, guardian_b_person, child_b)
        guardian_revoked, guardian_revoked_person = user_with_role("guardian-rev", "guardian")
        _guardian_of(session, guardian_revoked_person, child_a, status="revoked")

        instructor_a, _ = user_with_role("instructor-a", "instructor")
        _instruct(session, group_a, instructor_a)
        instructor_b, _ = user_with_role("instructor-b", "instructor")
        _instruct(session, group_b, instructor_b)
        instructor_expired, _ = user_with_role("instructor-exp", "instructor")
        _instruct(
            session,
            group_a,
            instructor_expired,
            valid_from=_utc(2022, 1, 1),
            valid_to=_utc(2023, 1, 1),
        )

        no_role = _user(session, _person(session, "no-role"))

        event = Event(
            club_id=club.id,
            event_type="trip",
            title="Осенний поход",
            start_at=_utc(2026, 10, 10, 9, 0),
            end_at=_utc(2026, 10, 10, 18, 0),
            timezone="Europe/Moscow",
            status="published",
        )
        session.add(event)
        session.flush()
        session.add(
            EventGroupTarget(event_id=event.id, group_id=group_a.id, valid_from=_utc(2024, 1, 1))
        )
        session.commit()
        return World(
            club_id=club.id,
            group_a=group_a.id,
            group_b=group_b.id,
            event_id=event.id,
            admin=admin.id,
            member_in_a=member_a.id,
            member_in_b=member_b.id,
            member_ended_in_a=member_ended.id,
            guardian_child_in_a=guardian_a.id,
            guardian_child_in_b=guardian_b.id,
            guardian_revoked_child_in_a=guardian_revoked.id,
            instructor_of_a=instructor_a.id,
            instructor_of_b=instructor_b.id,
            instructor_expired_a=instructor_expired.id,
            no_role_user=no_role.id,
        )


@pytest.fixture
def world() -> World:
    return _world()


def _as(user_id: uuid.UUID | None) -> None:
    if user_id is None:
        app.dependency_overrides.pop(get_current_principal, None)
        return
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _create(client: TestClient, world: World, **payload) -> dict:
    _as(world.admin)
    body = {"title": "Новость", "body": "Текст", "audience_type": "club", **payload}
    response = client.post(_URL, json=body, headers=_csrf(client))
    assert response.status_code == 201, response.text
    return response.json()


def _visible_ids(client: TestClient, user_id: uuid.UUID) -> set[str]:
    _as(user_id)
    response = client.get(_URL, params={"page_size": 100})
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 600), (20, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


# --- Authorization -----------------------------------------------------------


def test_administrator_can_create_update_publish_and_archive(client, world) -> None:
    created = _create(client, world, title="  Поход  ", location="Парк", event_date="2026-10-10")
    assert created["title"] == "Поход"
    assert created["status"] == "draft"
    assert created["published_at"] is None
    assert created["group_ids"] == []
    news_id = created["id"]

    response = client.patch(
        f"{_URL}/{news_id}",
        json={"body": "Новый текст", "audience_type": "groups", "group_ids": [str(world.group_a)]},
        headers=_csrf(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["body"] == "Новый текст"
    assert response.json()["group_ids"] == [str(world.group_a)]
    assert response.json()["updated_by"] == str(world.admin)

    published = client.post(f"{_URL}/{news_id}/publish", headers=_csrf(client))
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    archived = client.post(f"{_URL}/{news_id}/archive", headers=_csrf(client))
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None


def test_create_directly_as_published(client, world) -> None:
    created = _create(client, world, status="published")
    assert created["status"] == "published"
    assert created["published_at"] is not None


@pytest.mark.parametrize(
    "role_user", ["member_in_a", "guardian_child_in_a", "instructor_of_a", "no_role_user"]
)
def test_non_administrator_cannot_mutate(client, world, role_user) -> None:
    news_id = _create(client, world, status="published")["id"]
    _as(getattr(world, role_user))
    headers = _csrf(client)
    random_id = uuid.uuid4()

    assert (
        client.post(
            _URL, json={"title": "x", "body": "y", "audience_type": "club"}, headers=headers
        ).status_code
        == 403
    )
    for target in (news_id, random_id):
        # Same 403 for an existing and a nonexistent id: no existence probing.
        assert (
            client.patch(f"{_URL}/{target}", json={"title": "x"}, headers=headers).status_code
            == 403
        )
        assert client.post(f"{_URL}/{target}/publish", headers=headers).status_code == 403
        assert client.post(f"{_URL}/{target}/archive", headers=headers).status_code == 403
        assert (
            client.put(
                f"{_URL}/{target}/image",
                files={"image": ("i.png", _png(), "image/png")},
                headers=headers,
            ).status_code
            == 403
        )
        assert client.delete(f"{_URL}/{target}/image", headers=headers).status_code == 403

    with session_scope() as session:
        news = session.get(News, uuid.UUID(news_id))
        assert news is not None and news.title == "Новость" and news.status == "published"


def test_unauthenticated_cannot_read_or_mutate(client, world) -> None:
    news_id = _create(client, world, status="published")["id"]
    _as(None)
    headers = _csrf(client)
    assert client.get(_URL).status_code == 401
    assert client.get(f"{_URL}/{news_id}").status_code == 401
    assert client.get(f"{_URL}/{news_id}/image").status_code == 401
    assert (
        client.post(
            _URL, json={"title": "x", "body": "y", "audience_type": "club"}, headers=headers
        ).status_code
        == 401
    )
    assert (
        client.patch(f"{_URL}/{news_id}", json={"title": "x"}, headers=headers).status_code == 401
    )
    assert client.post(f"{_URL}/{news_id}/publish", headers=headers).status_code == 401
    assert client.post(f"{_URL}/{news_id}/archive", headers=headers).status_code == 401


def test_mutation_requires_csrf(client, world) -> None:
    _as(world.admin)
    client.cookies.clear()
    response = client.post(_URL, json={"title": "x", "body": "y", "audience_type": "club"})
    assert response.status_code == 403


# --- Visibility --------------------------------------------------------------


def test_club_news_is_visible_to_all_four_roles(client, world) -> None:
    news_id = _create(client, world, status="published")["id"]
    for reader in (
        world.admin,
        world.instructor_of_a,
        world.instructor_of_b,
        world.member_in_a,
        world.member_in_b,
        world.guardian_child_in_a,
        world.guardian_child_in_b,
    ):
        assert news_id in _visible_ids(client, reader)
        _as(reader)
        assert client.get(f"{_URL}/{news_id}").status_code == 200


def test_club_news_is_not_visible_to_user_without_any_role(client, world) -> None:
    news_id = _create(client, world, status="published")["id"]
    assert news_id not in _visible_ids(client, world.no_role_user)
    _as(world.no_role_user)
    assert client.get(f"{_URL}/{news_id}").status_code == 404


def test_group_news_visibility_follows_existing_relationships(client, world) -> None:
    news_id = _create(
        client, world, status="published", audience_type="groups", group_ids=[str(world.group_a)]
    )["id"]

    visible = {
        world.admin,
        world.member_in_a,
        world.guardian_child_in_a,
        world.instructor_of_a,
    }
    invisible = {
        world.member_in_b,
        world.member_ended_in_a,
        world.guardian_child_in_b,
        world.guardian_revoked_child_in_a,
        world.instructor_of_b,
        world.instructor_expired_a,
        world.no_role_user,
    }
    for reader in visible:
        assert news_id in _visible_ids(client, reader), reader
        _as(reader)
        assert client.get(f"{_URL}/{news_id}").status_code == 200
    for reader in invisible:
        assert news_id not in _visible_ids(client, reader), reader
        _as(reader)
        # Direct URL / known id: answered exactly like a nonexistent News.
        response = client.get(f"{_URL}/{news_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_group_news_with_several_groups(client, world) -> None:
    news_id = _create(
        client,
        world,
        status="published",
        audience_type="groups",
        group_ids=[str(world.group_a), str(world.group_b), str(world.group_a)],
    )
    # Duplicates are collapsed; both selected Groups are stored.
    assert sorted(news_id["group_ids"]) == sorted([str(world.group_a), str(world.group_b)])
    for reader in (world.member_in_b, world.guardian_child_in_b, world.instructor_of_b):
        assert news_id["id"] in _visible_ids(client, reader)


def test_non_admin_reader_never_receives_audience_configuration(client, world) -> None:
    news_id = _create(
        client, world, status="published", audience_type="groups", group_ids=[str(world.group_a)]
    )["id"]
    _as(world.member_in_a)
    detail = client.get(f"{_URL}/{news_id}").json()
    assert detail["group_ids"] is None
    assert detail["audience_type"] == "groups"


# --- Lifecycle ---------------------------------------------------------------


def test_draft_is_hidden_from_ordinary_users_but_visible_to_admin(client, world) -> None:
    news_id = _create(client, world)["id"]
    for reader in (world.member_in_a, world.guardian_child_in_a, world.instructor_of_a):
        _as(reader)
        assert client.get(f"{_URL}/{news_id}").status_code == 404
        for status_value in ("draft", "all", "published"):
            items = client.get(_URL, params={"status": status_value}).json()["items"]
            assert news_id not in {item["id"] for item in items}

    _as(world.admin)
    assert client.get(f"{_URL}/{news_id}").status_code == 200
    # The ordinary (default = published) list excludes drafts for admin too.
    assert news_id not in {item["id"] for item in client.get(_URL).json()["items"]}
    drafts = client.get(_URL, params={"status": "draft"}).json()["items"]
    assert news_id in {item["id"] for item in drafts}


def test_published_visible_then_archived_hidden_and_preserved(client, world) -> None:
    news_id = _create(client, world, status="published")["id"]
    assert news_id in _visible_ids(client, world.member_in_a)

    _as(world.admin)
    assert client.post(f"{_URL}/{news_id}/archive", headers=_csrf(client)).status_code == 200

    assert news_id not in _visible_ids(client, world.member_in_a)
    _as(world.member_in_a)
    assert client.get(f"{_URL}/{news_id}").status_code == 404

    _as(world.admin)
    assert news_id not in {item["id"] for item in client.get(_URL).json()["items"]}
    archived = client.get(_URL, params={"status": "archived"}).json()["items"]
    assert news_id in {item["id"] for item in archived}
    assert client.get(f"{_URL}/{news_id}").json()["status"] == "archived"

    # Soft delete: the row is never physically removed.
    with session_scope() as session:
        assert session.get(News, uuid.UUID(news_id)) is not None


def test_there_is_no_physical_delete_endpoint(client, world) -> None:
    news_id = _create(client, world)["id"]
    response = client.delete(f"{_URL}/{news_id}", headers=_csrf(client))
    assert response.status_code == 405
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(News)).scalar_one() == 1


def test_invalid_lifecycle_transitions(client, world) -> None:
    news_id = _create(client, world, status="published")["id"]
    headers = _csrf(client)
    response = client.post(f"{_URL}/{news_id}/publish", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_status_transition"

    assert client.post(f"{_URL}/{news_id}/archive", headers=headers).status_code == 200
    for action in ("publish", "archive"):
        response = client.post(f"{_URL}/{news_id}/{action}", headers=headers)
        assert response.status_code == 409
    response = client.patch(f"{_URL}/{news_id}", json={"title": "x"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "news_archived"


def test_draft_can_be_archived(client, world) -> None:
    news_id = _create(client, world)["id"]
    response = client.post(f"{_URL}/{news_id}/archive", headers=_csrf(client))
    assert response.status_code == 200
    assert response.json()["published_at"] is None


def test_create_cannot_start_archived(client, world) -> None:
    _as(world.admin)
    response = client.post(
        _URL,
        json={"title": "x", "body": "y", "audience_type": "club", "status": "archived"},
        headers=_csrf(client),
    )
    assert response.status_code == 422


def test_list_is_newest_first_and_paginated(client, world) -> None:
    ids = [_create(client, world, title=f"N{i}", status="published")["id"] for i in range(6)]
    _as(world.member_in_a)
    page = client.get(_URL, params={"page_size": 5}).json()
    assert [item["id"] for item in page["items"]] == list(reversed(ids))[:5]
    assert page["pagination"] == {"page": 1, "page_size": 5, "total": 6, "pages": 2}


# --- Validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"audience_type": "groups", "group_ids": []}, "invalid_audience"),
        ({"audience_type": "club", "group_ids": ["__A__"]}, "invalid_audience"),
        ({"audience_type": "groups", "group_ids": ["__RANDOM__"]}, "invalid_group_id"),
        ({"event_id": "__RANDOM__"}, "invalid_event_id"),
        ({"title": "   "}, "invalid_news_data"),
        ({"body": " "}, "invalid_news_data"),
    ],
)
def test_create_validation(client, world, payload, code) -> None:
    def resolve(value):
        if value == "__A__":
            return str(world.group_a)
        if value == "__RANDOM__":
            return str(uuid.uuid4())
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    body = {"title": "t", "body": "b", "audience_type": "club"}
    body.update({key: resolve(value) for key, value in payload.items()})
    _as(world.admin)
    response = client.post(_URL, json=body, headers=_csrf(client))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(News)).scalar_one() == 0


def test_invalid_audience_type_is_rejected(client, world) -> None:
    _as(world.admin)
    response = client.post(
        _URL,
        json={"title": "t", "body": "b", "audience_type": "roles"},
        headers=_csrf(client),
    )
    assert response.status_code == 422


def test_update_validation_and_audience_switching(client, world) -> None:
    news_id = _create(client, world, audience_type="groups", group_ids=[str(world.group_a)])["id"]
    headers = _csrf(client)

    response = client.patch(f"{_URL}/{news_id}", json={"group_ids": []}, headers=headers)
    assert response.json()["error"]["code"] == "invalid_audience"
    response = client.patch(f"{_URL}/{news_id}", json={"title": None}, headers=headers)
    assert response.status_code == 422
    response = client.patch(
        f"{_URL}/{news_id}", json={"group_ids": [str(uuid.uuid4())]}, headers=headers
    )
    assert response.json()["error"]["code"] == "invalid_group_id"
    assert client.get(f"{_URL}/{news_id}").json()["group_ids"] == [str(world.group_a)]

    # Switching to club clears the selected groups.
    response = client.patch(f"{_URL}/{news_id}", json={"audience_type": "club"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["group_ids"] == []
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(NewsGroupTarget)).scalar_one() == 0

    response = client.patch(f"{_URL}/{news_id}", json={"audience_type": "groups"}, headers=headers)
    assert response.json()["error"]["code"] == "invalid_audience"


def test_patch_on_unknown_news_is_404_for_admin(client, world) -> None:
    _as(world.admin)
    response = client.patch(f"{_URL}/{uuid.uuid4()}", json={"title": "x"}, headers=_csrf(client))
    assert response.status_code == 404


def test_groups_and_event_of_another_club_are_rejected(world) -> None:
    """Service-level: the API itself always binds News to the one Club, so
    a second Club cannot be created without breaking that invariant for
    the HTTP layer; the ownership validation is exercised directly."""
    with session_scope() as session:
        other_club = Club(name=f"Other {uuid.uuid4().hex[:6]}", status="active")
        session.add(other_club)
        session.flush()
        foreign_group = _group(session, other_club, "Чужая")
        session.commit()
        foreign_group_id = foreign_group.id
        other_club_id = other_club.id

    with session_scope() as session:
        with pytest.raises(news_service.NewsGroupClubMismatchError):
            news_service.create_news(
                session,
                club_id=world.club_id,
                title="t",
                body="b",
                status="draft",
                audience_type="groups",
                group_ids=[foreign_group_id],
                event_date=None,
                location=None,
                event_id=None,
                created_by=world.admin,
            )
    with session_scope() as session:
        with pytest.raises(news_service.NewsEventNotFoundError):
            news_service.create_news(
                session,
                club_id=other_club_id,
                title="t",
                body="b",
                status="draft",
                audience_type="club",
                group_ids=[],
                event_date=None,
                location=None,
                event_id=world.event_id,
                created_by=world.admin,
            )


# --- Event link ------------------------------------------------------------------


def test_event_link_and_navigation(client, world) -> None:
    created = _create(client, world, status="published", event_id=str(world.event_id))
    news_id = created["id"]
    assert created["linked_event"]["id"] == str(world.event_id)
    assert created["linked_event"]["title"] == "Осенний поход"

    # Guardian may read the Event (event.read `children`: the child's group
    # is targeted) — the detail exposes the Event for navigation.
    _as(world.guardian_child_in_a)
    detail = client.get(f"{_URL}/{news_id}").json()
    assert detail["linked_event"]["id"] == str(world.event_id)

    # A reader who cannot read the Event never learns about it via News.
    _as(world.guardian_child_in_b)
    assert client.get(f"{_URL}/{news_id}").json()["linked_event"] is None

    # Unlink.
    _as(world.admin)
    response = client.patch(f"{_URL}/{news_id}", json={"event_id": None}, headers=_csrf(client))
    assert response.json()["linked_event"] is None


def test_event_link_can_be_set_on_update(client, world) -> None:
    news_id = _create(client, world)["id"]
    response = client.patch(
        f"{_URL}/{news_id}", json={"event_id": str(world.event_id)}, headers=_csrf(client)
    )
    assert response.status_code == 200
    assert response.json()["linked_event"]["id"] == str(world.event_id)
    response = client.patch(
        f"{_URL}/{news_id}", json={"event_id": str(uuid.uuid4())}, headers=_csrf(client)
    )
    assert response.json()["error"]["code"] == "invalid_event_id"


# --- Image -------------------------------------------------------------------------


def test_image_upload_replace_read_and_remove(client, world, storage_root) -> None:
    news_id = _create(client, world, status="published")["id"]
    headers = _csrf(client)
    assert client.get(f"{_URL}/{news_id}/image").status_code == 404

    first = client.put(
        f"{_URL}/{news_id}/image", files={"image": ("a.png", _png(), "image/png")}, headers=headers
    )
    assert first.status_code == 200, first.text
    first_file_id = first.json()["image_file_id"]
    assert first_file_id

    _as(world.member_in_a)
    image = client.get(f"{_URL}/{news_id}/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    assert image.headers["etag"] == f'"{first_file_id}"'
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.size == (1200, 600)

    _as(world.admin)
    second = client.put(
        f"{_URL}/{news_id}/image", files={"image": ("b.png", _png(), "image/png")}, headers=headers
    )
    second_file_id = second.json()["image_file_id"]
    assert second_file_id != first_file_id
    with session_scope() as session:
        assert session.get(File, uuid.UUID(first_file_id)) is None
        assert session.get(File, uuid.UUID(second_file_id)) is not None
    stored = [path for path in storage_root.rglob("*") if path.is_file()]
    assert len(stored) == 1

    removed = client.delete(f"{_URL}/{news_id}/image", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["image_file_id"] is None
    assert client.get(f"{_URL}/{news_id}/image").status_code == 404
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []


def test_invalid_image_is_rejected(client, world) -> None:
    news_id = _create(client, world)["id"]
    response = client.put(
        f"{_URL}/{news_id}/image",
        files={"image": ("a.png", b"not-an-image", "image/png")},
        headers=_csrf(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"


def test_image_follows_news_visibility_and_is_kept_after_archive(client, world) -> None:
    news_id = _create(
        client, world, status="published", audience_type="groups", group_ids=[str(world.group_a)]
    )["id"]
    headers = _csrf(client)
    client.put(
        f"{_URL}/{news_id}/image", files={"image": ("a.png", _png(), "image/png")}, headers=headers
    )

    _as(world.member_in_b)
    assert client.get(f"{_URL}/{news_id}/image").status_code == 404

    _as(world.admin)
    client.post(f"{_URL}/{news_id}/archive", headers=headers)
    # Archived: the image stays with the preserved record (admin only) and
    # can no longer be changed.
    assert client.get(f"{_URL}/{news_id}/image").status_code == 200
    response = client.put(
        f"{_URL}/{news_id}/image", files={"image": ("a.png", _png(), "image/png")}, headers=headers
    )
    assert response.status_code == 409
    _as(world.member_in_a)
    assert client.get(f"{_URL}/{news_id}/image").status_code == 404
