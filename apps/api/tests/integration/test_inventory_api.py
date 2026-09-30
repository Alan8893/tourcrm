"""HTTP-level integration tests for the Inventory Foundation (TH-0121 /
Issue #230; docs/04-domain/inventory.md), against the real app and
PostgreSQL:

    GET/POST   /api/v1/inventory/{categories,units,storage-locations,items}
    GET/PATCH  /api/v1/inventory/{...}/{id}
    POST       /api/v1/inventory/{...}/{id}/archive

Covers Administrator-only access (Instructor/Member/Guardian 403,
unauthenticated 401), system units (G11), archiving of used references
(G12), read-only archived records (G13), unit/accounting-mode lock after
the first movement (G3/G14), active-name uniqueness (G15), the storage
location tree and the cost field. Fixture helpers are local, following the
suite's convention (tests/integration/test_news_api.py).
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.inventory import InventoryItem, InventoryMovement
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_BASE = "/api/v1/inventory"
_CATEGORIES = f"{_BASE}/categories"
_UNITS = f"{_BASE}/units"
_LOCATIONS = f"{_BASE}/storage-locations"
_ITEMS = f"{_BASE}/items"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


@dataclass
class World:
    club_id: uuid.UUID
    admin: uuid.UUID
    instructor: uuid.UUID
    member: uuid.UUID
    guardian: uuid.UUID


def _user_with_role(session, club: Club, role_code: str) -> uuid.UUID:
    person = Person(last_name="Тестов", first_name=f"{role_code}-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.flush()
    user = User(
        person=person,
        login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
        status="active",
    )
    session.add(user)
    session.flush()
    role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    session.add(
        UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type="all", club_id=club.id)
    )
    session.flush()
    return user.id


@pytest.fixture
def world() -> World:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        world = World(
            club_id=club.id,
            admin=_user_with_role(session, club, "admin"),
            instructor=_user_with_role(session, club, "instructor"),
            member=_user_with_role(session, club, "member"),
            guardian=_user_with_role(session, club, "guardian"),
        )
        session.commit()
        return world


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


def _post(client: TestClient, url: str, body: dict | None = None):
    return client.post(url, json=body, headers=_csrf(client))


def _patch(client: TestClient, url: str, body: dict):
    return client.patch(url, json=body, headers=_csrf(client))


def _created(response) -> dict:
    assert response.status_code == 201, response.text
    return response.json()


def _system_unit(client: TestClient, name: str) -> dict:
    response = client.get(_UNITS, params={"status": "all"})
    assert response.status_code == 200, response.text
    return next(u for u in response.json()["items"] if u["is_system"] and u["name"] == name)


def _category(client: TestClient, name: str = "Снаряжение") -> dict:
    return _created(_post(client, _CATEGORIES, {"name": name}))


def _item(client: TestClient, **overrides) -> dict:
    body = {
        "name": f"Карабин {uuid.uuid4().hex[:6]}",
        "category_id": _category(client)["id"],
        "unit_id": _system_unit(client, "шт")["id"],
        "accounting_mode": "quantity",
        **overrides,
    }
    return _created(_post(client, _ITEMS, body))


def _record_first_movement(item_id: str, created_by: uuid.UUID) -> None:
    """Persists a movement journal row directly — the Foundation has no
    movement workflow; this only establishes "the item has a movement"."""
    with session_scope() as session:
        session.add(
            InventoryMovement(
                item_id=uuid.UUID(item_id), movement_type="receipt", created_by=created_by
            )
        )
        session.commit()


# --- authorization -------------------------------------------------------------


def _all_requests(ids: dict[str, str]) -> list[tuple[str, str, dict | None]]:
    requests: list[tuple[str, str, dict | None]] = []
    for collection, record_id, create_body in (
        (_CATEGORIES, ids["category"], {"name": "X"}),
        (_UNITS, ids["unit"], {"name": "рулон"}),
        (_LOCATIONS, ids["location"], {"name": "Склад"}),
        (
            _ITEMS,
            ids["item"],
            {
                "name": "Y",
                "category_id": ids["category"],
                "unit_id": ids["unit"],
                "accounting_mode": "quantity",
            },
        ),
    ):
        requests += [
            ("GET", collection, None),
            ("POST", collection, create_body),
            ("GET", f"{collection}/{record_id}", None),
            ("PATCH", f"{collection}/{record_id}", {"name": "Z"}),
            ("POST", f"{collection}/{record_id}/archive", None),
        ]
    return requests


@pytest.fixture
def existing_ids(client, world) -> dict[str, str]:
    _as(world.admin)
    item = _item(client)
    return {
        "category": item["category_id"],
        "unit": _created(_post(client, _UNITS, {"name": "упаковка"}))["id"],
        "location": _created(_post(client, _LOCATIONS, {"name": "Склад"}))["id"],
        "item": item["id"],
    }


@pytest.mark.parametrize("role", ["instructor", "member", "guardian"])
def test_non_administrators_are_forbidden_everywhere(client, world, existing_ids, role) -> None:
    _as(getattr(world, role))
    for method, url, body in _all_requests(existing_ids):
        response = client.request(method, url, json=body, headers=_csrf(client))
        assert response.status_code == 403, (method, url, response.text)
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("role", ["instructor", "member", "guardian"])
def test_forbidden_regardless_of_whether_the_id_exists(client, world, role) -> None:
    _as(getattr(world, role))
    missing = uuid.uuid4()
    assert client.get(f"{_ITEMS}/{missing}").status_code == 403
    assert _post(client, f"{_ITEMS}/{missing}/archive").status_code == 403


def test_unauthenticated_requests_get_401(client, world, existing_ids) -> None:
    _as(None)
    for method, url, body in _all_requests(existing_ids):
        response = client.request(method, url, json=body, headers=_csrf(client))
        assert response.status_code == 401, (method, url, response.text)


def test_administrator_has_access(client, world, existing_ids) -> None:
    _as(world.admin)
    for collection, key in (
        (_CATEGORIES, "category"),
        (_UNITS, "unit"),
        (_LOCATIONS, "location"),
        (_ITEMS, "item"),
    ):
        assert client.get(collection).status_code == 200
        assert client.get(f"{collection}/{existing_ids[key]}").status_code == 200


def test_mutations_require_csrf(client, world) -> None:
    _as(world.admin)
    client.cookies.clear()
    response = client.post(_CATEGORIES, json={"name": "Без CSRF"})
    assert response.status_code == 403


def test_no_physical_delete_endpoint(client, world, existing_ids) -> None:
    _as(world.admin)
    headers = _csrf(client)
    for collection, key in (
        (_CATEGORIES, "category"),
        (_UNITS, "unit"),
        (_LOCATIONS, "location"),
        (_ITEMS, "item"),
    ):
        response = client.delete(f"{collection}/{existing_ids[key]}", headers=headers)
        assert response.status_code == 405


# --- units ---------------------------------------------------------------------


def test_system_units_exist_and_are_read_only(client, world) -> None:
    _as(world.admin)
    response = client.get(_UNITS)
    assert response.status_code == 200
    system = {u["name"]: u for u in response.json()["items"] if u["is_system"]}
    assert set(system) == {"шт", "м", "комплект", "пара"}
    for unit in system.values():
        assert unit["status"] == "active"
        assert unit["created_by"] is None

    unit_id = system["шт"]["id"]
    renamed = _patch(client, f"{_UNITS}/{unit_id}", {"name": "штука"})
    assert renamed.status_code == 409
    assert renamed.json()["error"]["code"] == "system_unit_immutable"
    archived = _post(client, f"{_UNITS}/{unit_id}/archive")
    assert archived.status_code == 409
    assert archived.json()["error"]["code"] == "system_unit_immutable"
    assert client.delete(f"{_UNITS}/{unit_id}", headers=_csrf(client)).status_code == 405
    assert client.get(f"{_UNITS}/{unit_id}").json()["name"] == "шт"


def test_custom_unit_lifecycle(client, world) -> None:
    _as(world.admin)
    unit = _created(_post(client, _UNITS, {"name": "  рулон "}))
    assert unit["name"] == "рулон"
    assert unit["is_system"] is False
    assert unit["created_by"] == str(world.admin)

    renamed = _patch(client, f"{_UNITS}/{unit['id']}", {"name": "мешок"})
    assert renamed.status_code == 200
    assert renamed.json()["updated_by"] == str(world.admin)

    archived = _post(client, f"{_UNITS}/{unit['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None

    # Archived: readable, read-only, irreversible, not selectable.
    assert client.get(f"{_UNITS}/{unit['id']}").json()["status"] == "archived"
    assert unit["id"] not in {u["id"] for u in client.get(_UNITS).json()["items"]}
    assert unit["id"] in {
        u["id"] for u in client.get(_UNITS, params={"status": "archived"}).json()["items"]
    }
    edit = _patch(client, f"{_UNITS}/{unit['id']}", {"name": "рулон"})
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "inventory_record_archived"
    assert _post(client, f"{_UNITS}/{unit['id']}/archive").status_code == 409

    response = _post(
        client,
        _ITEMS,
        {
            "name": "Верёвка",
            "category_id": _category(client)["id"],
            "unit_id": unit["id"],
            "accounting_mode": "quantity",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "archived_reference"


def test_archiving_a_used_unit_keeps_existing_items_referencing_it(client, world) -> None:
    _as(world.admin)
    unit = _created(_post(client, _UNITS, {"name": "упаковка"}))
    item = _item(client, unit_id=unit["id"])
    assert _post(client, f"{_UNITS}/{unit['id']}/archive").status_code == 200

    reloaded = client.get(f"{_ITEMS}/{item['id']}").json()
    assert reloaded["unit_id"] == unit["id"]
    # Unrelated edits of the item stay possible; the unit is simply kept.
    updated = _patch(client, f"{_ITEMS}/{item['id']}", {"current_cost_minor": 1200})
    assert updated.status_code == 200
    assert updated.json()["unit_id"] == unit["id"]


# --- categories ------------------------------------------------------------------


def test_category_crud_and_archive(client, world) -> None:
    _as(world.admin)
    category = _category(client, "Палатки")
    assert category["status"] == "active"
    assert client.get(f"{_CATEGORIES}/{category['id']}").json()["name"] == "Палатки"

    renamed = _patch(client, f"{_CATEGORIES}/{category['id']}", {"name": "Тенты"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Тенты"

    item = _item(client, category_id=category["id"])
    archived = _post(client, f"{_CATEGORIES}/{category['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    # Still readable and still referenced by the existing item.
    assert client.get(f"{_CATEGORIES}/{category['id']}").status_code == 200
    assert client.get(f"{_ITEMS}/{item['id']}").json()["category_id"] == category["id"]
    listed = client.get(_CATEGORIES, params={"status": "all"}).json()["items"]
    assert category["id"] in {c["id"] for c in listed}

    # Read-only.
    edit = _patch(client, f"{_CATEGORIES}/{category['id']}", {"name": "Палатки"})
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "inventory_record_archived"

    # Not selectable for new nomenclature, nor as a new category of an item.
    create = _post(
        client,
        _ITEMS,
        {
            "name": "Палатка",
            "category_id": category["id"],
            "unit_id": _system_unit(client, "шт")["id"],
            "accounting_mode": "instance",
        },
    )
    assert create.status_code == 422
    assert create.json()["error"]["code"] == "archived_reference"
    other = _item(client)
    move = _patch(client, f"{_ITEMS}/{other['id']}", {"category_id": category["id"]})
    assert move.status_code == 422
    assert move.json()["error"]["code"] == "archived_reference"


def test_empty_name_is_rejected(client, world) -> None:
    _as(world.admin)
    response = _post(client, _CATEGORIES, {"name": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_inventory_data"


# --- nomenclature -----------------------------------------------------------------


def test_create_quantity_and_instance_items(client, world) -> None:
    _as(world.admin)
    category = _category(client)
    metre = _system_unit(client, "м")
    piece = _system_unit(client, "шт")

    rope = _created(
        _post(
            client,
            _ITEMS,
            {
                "name": "Верёвка 30 м",
                "category_id": category["id"],
                "unit_id": metre["id"],
                "accounting_mode": "quantity",
                "current_cost_minor": 650_000,
            },
        )
    )
    backpack = _created(
        _post(
            client,
            _ITEMS,
            {
                "name": "Рюкзак X",
                "category_id": category["id"],
                "unit_id": piece["id"],
                "accounting_mode": "instance",
            },
        )
    )

    assert rope["accounting_mode"] == "quantity"
    assert rope["unit_id"] == metre["id"]
    assert rope["category_id"] == category["id"]
    assert rope["current_cost_minor"] == 650_000
    assert rope["status"] == "active"
    assert rope["created_by"] == str(world.admin)
    assert backpack["accounting_mode"] == "instance"
    assert backpack["current_cost_minor"] is None
    # The nomenclature never exposes a stock/quantity/state/location field.
    assert set(rope) == {
        "id",
        "name",
        "category_id",
        "unit_id",
        "accounting_mode",
        "current_cost_minor",
        "status",
        "archived_at",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    }


def test_item_request_rejects_stock_fields(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    for field in ("quantity", "stock", "status", "location_id"):
        response = _patch(client, f"{_ITEMS}/{item['id']}", {field: 5})
        assert response.status_code == 422, field


def test_cost_is_stored_updated_and_cleared(client, world) -> None:
    _as(world.admin)
    item = _item(client, current_cost_minor=500_000)
    assert item["current_cost_minor"] == 500_000
    updated = _patch(client, f"{_ITEMS}/{item['id']}", {"current_cost_minor": 650_000})
    assert updated.json()["current_cost_minor"] == 650_000
    cleared = _patch(client, f"{_ITEMS}/{item['id']}", {"current_cost_minor": None})
    assert cleared.json()["current_cost_minor"] is None
    negative = _patch(client, f"{_ITEMS}/{item['id']}", {"current_cost_minor": -1})
    assert negative.status_code == 422
    fractional = _patch(client, f"{_ITEMS}/{item['id']}", {"current_cost_minor": 10.5})
    assert fractional.status_code == 422


def test_unknown_or_foreign_references_are_rejected(client, world) -> None:
    _as(world.admin)
    response = _post(
        client,
        _ITEMS,
        {
            "name": "Жумар",
            "category_id": str(uuid.uuid4()),
            "unit_id": _system_unit(client, "шт")["id"],
            "accounting_mode": "instance",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"
    bad_mode = _post(
        client,
        _ITEMS,
        {
            "name": "Жумар",
            "category_id": _category(client)["id"],
            "unit_id": _system_unit(client, "шт")["id"],
            "accounting_mode": "batch",
        },
    )
    assert bad_mode.status_code == 422


def test_non_nullable_item_fields_cannot_be_nulled(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    for field in ("name", "category_id", "unit_id", "accounting_mode"):
        response = _patch(client, f"{_ITEMS}/{item['id']}", {field: None})
        assert response.status_code == 422, field


def test_item_archive_is_irreversible_and_read_only(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    archived = _post(client, f"{_ITEMS}/{item['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert client.get(f"{_ITEMS}/{item['id']}").json()["status"] == "archived"
    edit = _patch(client, f"{_ITEMS}/{item['id']}", {"current_cost_minor": 1})
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "inventory_record_archived"
    assert _post(client, f"{_ITEMS}/{item['id']}/archive").status_code == 409


def test_item_name_unique_among_active_case_insensitive(client, world) -> None:
    _as(world.admin)
    first = _item(client, name="Карабин")
    duplicate = _post(
        client,
        _ITEMS,
        {
            "name": "  КАРАБИН ",
            "category_id": first["category_id"],
            "unit_id": first["unit_id"],
            "accounting_mode": "quantity",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "name_conflict"

    other = _item(client, name="Жумар")
    rename = _patch(client, f"{_ITEMS}/{other['id']}", {"name": "карабин"})
    assert rename.status_code == 409

    # Archived items do not block their old name.
    assert _post(client, f"{_ITEMS}/{first['id']}/archive").status_code == 200
    reused = _item(client, name="Карабин")
    assert reused["name"] == "Карабин"


# --- G3 / G14: unit and accounting mode after the first movement ---------------------


def test_unit_and_mode_can_change_before_the_first_movement(client, world) -> None:
    _as(world.admin)
    item = _item(client, accounting_mode="quantity")
    pair = _system_unit(client, "пара")
    response = _patch(
        client,
        f"{_ITEMS}/{item['id']}",
        {"unit_id": pair["id"], "accounting_mode": "instance"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["unit_id"] == pair["id"]
    assert response.json()["accounting_mode"] == "instance"


def test_unit_and_mode_are_locked_after_the_first_movement(client, world) -> None:
    _as(world.admin)
    item = _item(client, accounting_mode="quantity")
    _record_first_movement(item["id"], world.admin)

    unit_change = _patch(
        client, f"{_ITEMS}/{item['id']}", {"unit_id": _system_unit(client, "м")["id"]}
    )
    assert unit_change.status_code == 409
    assert unit_change.json()["error"]["code"] == "unit_locked"

    mode_change = _patch(client, f"{_ITEMS}/{item['id']}", {"accounting_mode": "instance"})
    assert mode_change.status_code == 409
    assert mode_change.json()["error"]["code"] == "accounting_mode_locked"

    # Re-sending the current values and editing other fields stay possible.
    same = _patch(
        client,
        f"{_ITEMS}/{item['id']}",
        {"unit_id": item["unit_id"], "accounting_mode": "quantity", "current_cost_minor": 100},
    )
    assert same.status_code == 200, same.text
    with session_scope() as session:
        stored = session.get(InventoryItem, uuid.UUID(item["id"]))
        assert stored is not None
        assert str(stored.unit_id) == item["unit_id"]


# --- storage locations ------------------------------------------------------------


def test_location_hierarchy(client, world) -> None:
    _as(world.admin)
    warehouse = _created(_post(client, _LOCATIONS, {"name": "Склад"}))
    rack = _created(_post(client, _LOCATIONS, {"name": "Стеллаж 1", "parent_id": warehouse["id"]}))
    shelf = _created(_post(client, _LOCATIONS, {"name": "Полка 1", "parent_id": rack["id"]}))
    cell = _created(_post(client, _LOCATIONS, {"name": "Ячейка A", "parent_id": shelf["id"]}))
    assert warehouse["parent_id"] is None
    assert rack["parent_id"] == warehouse["id"]
    assert cell["parent_id"] == shelf["id"]

    listed = {loc["id"]: loc["parent_id"] for loc in client.get(_LOCATIONS).json()["items"]}
    assert listed == {
        warehouse["id"]: None,
        rack["id"]: warehouse["id"],
        shelf["id"]: rack["id"],
        cell["id"]: shelf["id"],
    }


def test_location_sibling_name_uniqueness(client, world) -> None:
    _as(world.admin)
    warehouse = _created(_post(client, _LOCATIONS, {"name": "Склад"}))
    rack_1 = _created(
        _post(client, _LOCATIONS, {"name": "Стеллаж 1", "parent_id": warehouse["id"]})
    )
    rack_2 = _created(
        _post(client, _LOCATIONS, {"name": "Стеллаж 2", "parent_id": warehouse["id"]})
    )

    duplicate = _post(client, _LOCATIONS, {"name": "полка 1", "parent_id": rack_1["id"]})
    assert duplicate.status_code == 201
    again = _post(client, _LOCATIONS, {"name": "Полка 1", "parent_id": rack_1["id"]})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "name_conflict"

    # Same name in another branch is fine.
    assert (
        _post(client, _LOCATIONS, {"name": "Полка 1", "parent_id": rack_2["id"]}).status_code == 201
    )
    # Root siblings are unique too.
    assert _post(client, _LOCATIONS, {"name": "склад"}).status_code == 409

    # Renaming into a sibling's name is rejected.
    rename = _patch(client, f"{_LOCATIONS}/{rack_2['id']}", {"name": "Стеллаж 1"})
    assert rename.status_code == 409


def test_archived_location_does_not_block_its_name(client, world) -> None:
    _as(world.admin)
    warehouse = _created(_post(client, _LOCATIONS, {"name": "Склад"}))
    old = _created(_post(client, _LOCATIONS, {"name": "Шкаф", "parent_id": warehouse["id"]}))
    assert _post(client, f"{_LOCATIONS}/{old['id']}/archive").status_code == 200
    new = _post(client, _LOCATIONS, {"name": "Шкаф", "parent_id": warehouse["id"]})
    assert new.status_code == 201


def test_location_archive_rules(client, world) -> None:
    _as(world.admin)
    warehouse = _created(_post(client, _LOCATIONS, {"name": "Склад"}))
    rack = _created(_post(client, _LOCATIONS, {"name": "Стеллаж", "parent_id": warehouse["id"]}))

    blocked = _post(client, f"{_LOCATIONS}/{warehouse['id']}/archive")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "location_has_active_children"

    assert _post(client, f"{_LOCATIONS}/{rack['id']}/archive").status_code == 200
    archived = _post(client, f"{_LOCATIONS}/{warehouse['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    # Archived: read-only, not selectable as a parent.
    edit = _patch(client, f"{_LOCATIONS}/{warehouse['id']}", {"name": "Новый склад"})
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "inventory_record_archived"
    child = _post(client, _LOCATIONS, {"name": "Полка", "parent_id": warehouse["id"]})
    assert child.status_code == 422
    assert child.json()["error"]["code"] == "archived_reference"
    assert client.get(f"{_LOCATIONS}/{warehouse['id']}").status_code == 200


def test_location_parent_cannot_be_itself_or_a_descendant(client, world) -> None:
    _as(world.admin)
    warehouse = _created(_post(client, _LOCATIONS, {"name": "Склад"}))
    rack = _created(_post(client, _LOCATIONS, {"name": "Стеллаж", "parent_id": warehouse["id"]}))
    shelf = _created(_post(client, _LOCATIONS, {"name": "Полка", "parent_id": rack["id"]}))

    # Itself, its child, its grandchild.
    for parent in (warehouse, rack, shelf):
        response = _patch(client, f"{_LOCATIONS}/{warehouse['id']}", {"parent_id": parent["id"]})
        assert response.status_code == 422, parent["name"]
        assert response.json()["error"]["code"] == "invalid_parent"

    missing = _post(client, _LOCATIONS, {"name": "Ячейка", "parent_id": str(uuid.uuid4())})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "invalid_reference"


def test_location_can_be_reparented_and_made_root(client, world) -> None:
    _as(world.admin)
    warehouse_a = _created(_post(client, _LOCATIONS, {"name": "Склад А"}))
    warehouse_b = _created(_post(client, _LOCATIONS, {"name": "Склад Б"}))
    rack = _created(_post(client, _LOCATIONS, {"name": "Стеллаж", "parent_id": warehouse_a["id"]}))

    moved = _patch(client, f"{_LOCATIONS}/{rack['id']}", {"parent_id": warehouse_b["id"]})
    assert moved.status_code == 200
    assert moved.json()["parent_id"] == warehouse_b["id"]
    root = _patch(client, f"{_LOCATIONS}/{rack['id']}", {"parent_id": None})
    assert root.status_code == 200
    assert root.json()["parent_id"] is None
    null_name = _patch(client, f"{_LOCATIONS}/{rack['id']}", {"name": None})
    assert null_name.status_code == 422
