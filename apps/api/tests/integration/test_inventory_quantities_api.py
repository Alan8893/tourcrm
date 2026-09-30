"""HTTP-level integration tests for Inventory Slice 3 — quantity stock and
movements (Issue #230; docs/04-domain/inventory.md §6, §11, §13, §16, §17),
against the real app and PostgreSQL:

    GET  /api/v1/inventory/stock
    GET  /api/v1/inventory/items/{item_id}/{stock,movements}
    POST /api/v1/inventory/items/{item_id}/{receipts,transfers,write-offs}
    POST /api/v1/inventory/items/{item_id}/write-offs/{movement_id}/reverse

Fixture helpers are local, following the suite's convention
(tests/integration/test_inventory_instances_api.py).
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.inventory import InventoryItemStock
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_BASE = "/api/v1/inventory"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


@dataclass
class World:
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


def _ok(response, code: int = 200):
    assert response.status_code == code, response.text
    return response.json()


def _error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code, response.text


def _item(client: TestClient, mode: str = "quantity", **extra) -> dict:
    category = _ok(
        _post(client, f"{_BASE}/categories", {"name": f"Кат {uuid.uuid4().hex[:6]}"}), 201
    )
    units = _ok(client.get(f"{_BASE}/units"))["items"]
    body = {
        "name": f"Карабин {uuid.uuid4().hex[:6]}",
        "category_id": category["id"],
        "unit_id": next(u["id"] for u in units if u["name"] == "шт"),
        "accounting_mode": mode,
        **extra,
    }
    return _ok(_post(client, f"{_BASE}/items", body), 201)


def _location(client: TestClient) -> dict:
    body = {"name": f"Полка {uuid.uuid4().hex[:6]}"}
    return _ok(_post(client, f"{_BASE}/storage-locations", body), 201)


def _url(item: dict, action: str) -> str:
    return f"{_BASE}/items/{item['id']}/{action}"


def _receive(client, item: dict, location: dict, quantity: int, **extra) -> dict:
    body = {"storage_location_id": location["id"], "quantity": quantity, **extra}
    return _ok(_post(client, _url(item, "receipts"), body), 201)


def _transfer(client, item: dict, source: dict, target: dict, quantity: int):
    body = {"from_location_id": source["id"], "to_location_id": target["id"], "quantity": quantity}
    return _post(client, _url(item, "transfers"), body)


def _write_off(client, item: dict, location: dict, quantity: int, comment: str = "Сломаны"):
    body = {"storage_location_id": location["id"], "quantity": quantity, "comment": comment}
    return _post(client, _url(item, "write-offs"), body)


def _reverse(client, item: dict, movement_id: str, body: dict | None = None):
    return _post(client, f"{_url(item, 'write-offs')}/{movement_id}/reverse", body)


def _stock(client, item: dict) -> dict[str, int]:
    rows = _ok(client.get(_url(item, "stock")))["items"]
    return {row["storage_location_id"]: row["quantity"] for row in rows}


def _history(client, item: dict, **params) -> list[dict]:
    return _ok(client.get(_url(item, "movements"), params={"page_size": 200, **params}))["items"]


@pytest.fixture
def stock(client, world) -> dict:
    _as(world.admin)
    item = _item(client)
    location = _location(client)
    _receive(client, item, location, 7)
    return {"item": item, "location": location}


# --- authorization ---------------------------------------------------------------------


def _all_requests(item_id: str, movement_id: str, location_id: str):
    base = f"{_BASE}/items/{item_id}"
    body = {"storage_location_id": location_id, "quantity": 1, "comment": "x"}
    return [
        ("GET", f"{_BASE}/stock", None),
        ("GET", f"{base}/stock", None),
        ("GET", f"{base}/movements", None),
        ("POST", f"{base}/receipts", body),
        (
            "POST",
            f"{base}/transfers",
            {"from_location_id": location_id, "to_location_id": location_id, "quantity": 1},
        ),
        ("POST", f"{base}/write-offs", body),
        ("POST", f"{base}/write-offs/{movement_id}/reverse", None),
    ]


@pytest.mark.parametrize("role", ["instructor", "member", "guardian"])
def test_non_administrators_are_forbidden_everywhere(client, world, stock, role) -> None:
    movement_id = _history(client, stock["item"])[0]["id"]
    _as(getattr(world, role))
    for method, url, body in _all_requests(
        stock["item"]["id"], movement_id, stock["location"]["id"]
    ):
        _error(client.request(method, url, json=body, headers=_csrf(client)), 403, "forbidden")
    for method, url, body in _all_requests(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())):
        assert client.request(method, url, json=body, headers=_csrf(client)).status_code == 403


def test_unauthenticated_requests_get_401(client, world, stock) -> None:
    movement_id = _history(client, stock["item"])[0]["id"]
    _as(None)
    for method, url, body in _all_requests(
        stock["item"]["id"], movement_id, stock["location"]["id"]
    ):
        assert client.request(method, url, json=body, headers=_csrf(client)).status_code == 401


def test_unknown_item_or_movement_is_404(client, world, stock) -> None:
    missing = uuid.uuid4()
    for method, url, body in _all_requests(str(missing), str(missing), stock["location"]["id"])[1:]:
        response = client.request(method, url, json=body, headers=_csrf(client))
        _error(response, 404, "not_found")
    _error(_reverse(client, stock["item"], str(missing)), 404, "not_found")


def test_no_direct_stock_mutation(client, world, stock) -> None:
    for method in ("PUT", "PATCH", "DELETE"):
        response = client.request(method, _url(stock["item"], "stock"), headers=_csrf(client))
        assert response.status_code == 405


# --- receipt ---------------------------------------------------------------------------


def test_receipt_increases_stock_and_records_the_movement(client, world) -> None:
    _as(world.admin)
    item = _item(client, current_cost_minor=10_000)
    location = _location(client)
    first = _receive(client, item, location, 100, unit_cost_minor=10_000, comment=" партия 1 ")
    assert first["movement_type"] == "receipt"
    assert first["item_id"] == item["id"]
    assert first["instance_id"] is None
    assert first["quantity"] == 100
    assert (first["from_location_id"], first["to_location_id"]) == (None, location["id"])
    assert first["unit_cost_minor"] == 10_000
    assert first["comment"] == "партия 1"
    assert first["created_by"] == str(world.admin)

    second = _receive(client, item, location, 50, unit_cost_minor=20_000)
    assert second["unit_cost_minor"] == 20_000
    assert _stock(client, item) == {location["id"]: 150}
    # Receipts never change the item's current cost (G2).
    assert _ok(client.get(f"{_BASE}/items/{item['id']}"))["current_cost_minor"] == 10_000


def test_receipt_cost_and_comment_are_optional(client, world) -> None:
    _as(world.admin)
    movement = _receive(client, _item(client), _location(client), 3)
    assert movement["unit_cost_minor"] is None and movement["comment"] is None


def test_receipt_rejects_instance_items(client, world) -> None:
    _as(world.admin)
    item = _item(client, "instance")
    response = _post(
        client,
        _url(item, "receipts"),
        {"storage_location_id": _location(client)["id"], "quantity": 1},
    )
    _error(response, 422, "item_not_quantity_mode")


def test_receipt_rejects_archived_item_and_locations(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    location = _location(client)
    archived_location = _location(client)
    _ok(_post(client, f"{_BASE}/storage-locations/{archived_location['id']}/archive"))
    url = _url(item, "receipts")
    _error(
        _post(client, url, {"storage_location_id": archived_location["id"], "quantity": 1}),
        422,
        "archived_reference",
    )
    _error(
        _post(client, url, {"storage_location_id": str(uuid.uuid4()), "quantity": 1}),
        422,
        "invalid_reference",
    )
    _ok(_post(client, f"{_BASE}/items/{item['id']}/archive"))
    _error(
        _post(client, url, {"storage_location_id": location["id"], "quantity": 1}),
        422,
        "archived_reference",
    )


@pytest.mark.parametrize(
    "body",
    [{"quantity": 0}, {"quantity": -1}, {"quantity": 1.5}, {"quantity": 1, "unit_cost_minor": -1}],
)
def test_receipt_rejects_invalid_values(client, world, stock, body) -> None:
    response = _post(
        client,
        _url(stock["item"], "receipts"),
        {"storage_location_id": stock["location"]["id"], **body},
    )
    assert response.status_code == 422
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 7}


def test_first_receipt_locks_accounting_mode_and_unit(client, world, stock) -> None:
    url = f"{_BASE}/items/{stock['item']['id']}"
    _error(
        client.patch(url, json={"accounting_mode": "instance"}, headers=_csrf(client)),
        409,
        "accounting_mode_locked",
    )
    units = _ok(client.get(f"{_BASE}/units"))["items"]
    metre = next(u["id"] for u in units if u["name"] == "м")
    _error(client.patch(url, json={"unit_id": metre}, headers=_csrf(client)), 409, "unit_locked")


# --- transfer --------------------------------------------------------------------------


def test_partial_and_full_transfer(client, world, stock) -> None:
    target = _location(client)
    movement = _ok(_transfer(client, stock["item"], stock["location"], target, 3), 201)
    assert movement["movement_type"] == "transfer"
    assert movement["quantity"] == 3
    assert movement["from_location_id"] == stock["location"]["id"]
    assert movement["to_location_id"] == target["id"]
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 4, target["id"]: 3}
    _ok(_transfer(client, stock["item"], stock["location"], target, 4), 201)
    assert _stock(client, stock["item"]) == {target["id"]: 7}
    # The reverse transfer is simply another transfer.
    _ok(_transfer(client, stock["item"], target, stock["location"], 2), 201)
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 2, target["id"]: 5}


def test_transfer_rejects_more_than_the_source_stock(client, world, stock) -> None:
    target = _location(client)
    _error(
        _transfer(client, stock["item"], stock["location"], target, 8), 409, "insufficient_stock"
    )
    _error(
        _transfer(client, stock["item"], target, stock["location"], 1), 409, "insufficient_stock"
    )
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 7}
    assert len(_history(client, stock["item"])) == 1


def test_transfer_rejects_bad_locations(client, world, stock) -> None:
    _error(
        _transfer(client, stock["item"], stock["location"], stock["location"], 1),
        422,
        "invalid_inventory_data",
    )
    archived = _location(client)
    _ok(_post(client, f"{_BASE}/storage-locations/{archived['id']}/archive"))
    _error(
        _transfer(client, stock["item"], stock["location"], archived, 1), 422, "archived_reference"
    )
    _error(
        _transfer(client, stock["item"], stock["location"], {"id": str(uuid.uuid4())}, 1),
        422,
        "invalid_reference",
    )
    _error(
        _transfer(client, stock["item"], stock["location"], _location(client), 0),
        422,
        "validation_error",
    )


# --- write-off -------------------------------------------------------------------------


def test_partial_write_off(client, world, stock) -> None:
    movement = _ok(_write_off(client, stock["item"], stock["location"], 2, " Сломаны "), 201)
    assert movement["movement_type"] == "write_off"
    assert movement["quantity"] == 2
    assert movement["from_location_id"] == stock["location"]["id"]
    assert movement["to_location_id"] is None
    assert movement["comment"] == "Сломаны"
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 5}


def test_write_off_requires_a_reason(client, world, stock) -> None:
    url = _url(stock["item"], "write-offs")
    body = {"storage_location_id": stock["location"]["id"], "quantity": 1}
    assert _post(client, url, body).status_code == 422
    _error(_post(client, url, {**body, "comment": "  "}), 422, "invalid_inventory_data")
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 7}


def test_write_off_rejects_more_than_the_stock(client, world, stock) -> None:
    _error(_write_off(client, stock["item"], stock["location"], 8), 409, "insufficient_stock")
    _error(_write_off(client, stock["item"], _location(client), 1), 409, "insufficient_stock")


# --- write-off reversal ------------------------------------------------------------------


def test_reversal_restores_the_full_quantity_to_the_original_location(client, world, stock) -> None:
    write_off = _ok(_write_off(client, stock["item"], stock["location"], 3), 201)
    reversal = _ok(_reverse(client, stock["item"], write_off["id"]), 201)
    assert reversal["movement_type"] == "writeoff_reversal"
    assert reversal["quantity"] == 3
    assert reversal["reverses_movement_id"] == write_off["id"]
    assert (reversal["from_location_id"], reversal["to_location_id"]) == (
        None,
        stock["location"]["id"],
    )
    assert _stock(client, stock["item"]) == {stock["location"]["id"]: 7}
    _error(_reverse(client, stock["item"], write_off["id"]), 409, "write_off_already_reversed")
    # The original write-off is unchanged in the history.
    original = next(m for m in _history(client, stock["item"]) if m["id"] == write_off["id"])
    assert original == write_off


def test_reversal_location_rules(client, world, stock) -> None:
    write_off = _ok(_write_off(client, stock["item"], stock["location"], 7), 201)
    other = _location(client)
    _error(
        _reverse(client, stock["item"], write_off["id"], {"storage_location_id": other["id"]}),
        422,
        "storage_location_not_allowed",
    )
    # The original location is now empty and can be archived.
    _ok(_post(client, f"{_BASE}/storage-locations/{stock['location']['id']}/archive"))
    _error(_reverse(client, stock["item"], write_off["id"]), 422, "storage_location_required")
    _error(
        _reverse(
            client, stock["item"], write_off["id"], {"storage_location_id": stock["location"]["id"]}
        ),
        422,
        "archived_reference",
    )
    assert _stock(client, stock["item"]) == {}
    reversal = _ok(
        _reverse(client, stock["item"], write_off["id"], {"storage_location_id": other["id"]}), 201
    )
    assert reversal["to_location_id"] == other["id"]
    assert _stock(client, stock["item"]) == {other["id"]: 7}


def test_reversal_accepts_the_original_location_explicitly(client, world, stock) -> None:
    write_off = _ok(_write_off(client, stock["item"], stock["location"], 1), 201)
    body = {"storage_location_id": stock["location"]["id"]}
    _ok(_reverse(client, stock["item"], write_off["id"], body), 201)


def test_only_a_quantity_write_off_of_the_item_can_be_reversed(client, world, stock) -> None:
    target = _location(client)
    transfer = _ok(_transfer(client, stock["item"], stock["location"], target, 1), 201)
    receipt = _history(client, stock["item"])[0]
    for movement in (receipt, transfer):
        _error(_reverse(client, stock["item"], movement["id"]), 422, "invalid_reversal_target")
    write_off = _ok(_write_off(client, stock["item"], stock["location"], 1), 201)
    reversal = _ok(_reverse(client, stock["item"], write_off["id"]), 201)
    _error(_reverse(client, stock["item"], reversal["id"]), 422, "invalid_reversal_target")
    # A write-off of another item is not found under this item.
    other = _item(client)
    _receive(client, other, stock["location"], 1)
    other_write_off = _ok(_write_off(client, other, stock["location"], 1), 201)
    _error(_reverse(client, stock["item"], other_write_off["id"]), 404, "not_found")


def test_instance_write_offs_stay_on_the_slice_2_endpoint(client, world, stock) -> None:
    item = _item(client, "instance")
    instance = _ok(
        _post(
            client,
            f"{_BASE}/instances",
            {"item_id": item["id"], "storage_location_id": stock["location"]["id"]},
        ),
        201,
    )
    _ok(_post(client, f"{_BASE}/instances/{instance['id']}/write-off", {"comment": "x"}))
    write_off = _ok(client.get(f"{_BASE}/instances/{instance['id']}/movements"))[-1]
    _error(_reverse(client, item, write_off["id"]), 422, "item_not_quantity_mode")
    restored = _ok(_post(client, f"{_BASE}/movements/{write_off['id']}/reverse"))
    assert restored["state"] == "available"


def test_reversal_of_an_archived_item_is_rejected(client, world, stock) -> None:
    write_off = _ok(_write_off(client, stock["item"], stock["location"], 7), 201)
    _ok(_post(client, f"{_BASE}/items/{stock['item']['id']}/archive"))
    _error(_reverse(client, stock["item"], write_off["id"]), 422, "archived_reference")


# --- archiving ---------------------------------------------------------------------------


def test_item_with_stock_cannot_be_archived(client, world, stock) -> None:
    url = f"{_BASE}/items/{stock['item']['id']}/archive"
    _error(_post(client, url), 409, "item_has_stock")
    _ok(_write_off(client, stock["item"], stock["location"], 7), 201)
    assert _ok(_post(client, url))["status"] == "archived"


def test_location_with_stock_cannot_be_archived(client, world, stock) -> None:
    url = f"{_BASE}/storage-locations/{stock['location']['id']}/archive"
    _error(_post(client, url), 409, "location_has_stock")
    _ok(_transfer(client, stock["item"], stock["location"], _location(client), 7), 201)
    assert _ok(_post(client, url))["status"] == "archived"


# --- stock and history ---------------------------------------------------------------------


def test_stock_lists_and_filters(client, world, stock) -> None:
    other_item = _item(client)
    other_location = _location(client)
    _receive(client, other_item, other_location, 2)
    _ok(_transfer(client, stock["item"], stock["location"], other_location, 7), 201)

    def rows(**params) -> set[tuple[str, str, int]]:
        items = _ok(client.get(f"{_BASE}/stock", params=params))["items"]
        return {(r["item_id"], r["storage_location_id"], r["quantity"]) for r in items}

    # Zero rows (the emptied original location) are not listed.
    assert rows() == {
        (stock["item"]["id"], other_location["id"], 7),
        (other_item["id"], other_location["id"], 2),
    }
    assert rows(item_id=other_item["id"]) == {(other_item["id"], other_location["id"], 2)}
    assert rows(storage_location_id=stock["location"]["id"]) == set()
    page = _ok(client.get(f"{_BASE}/stock", params={"page_size": 1}))
    assert page["pagination"]["total"] == 2 and len(page["items"]) == 1
    _error(client.get(_url(_item(client, "instance"), "stock")), 422, "item_not_quantity_mode")


def test_history_is_chronological_filterable_and_paginated(client, world, stock) -> None:
    target = _location(client)
    _ok(_transfer(client, stock["item"], stock["location"], target, 2), 201)
    write_off = _ok(_write_off(client, stock["item"], target, 1), 201)
    _ok(_reverse(client, stock["item"], write_off["id"]), 201)
    history = _history(client, stock["item"])
    assert [m["movement_type"] for m in history] == [
        "receipt",
        "transfer",
        "write_off",
        "writeoff_reversal",
    ]
    assert [m["created_at"] for m in history] == sorted(m["created_at"] for m in history)
    at_original = _history(client, stock["item"], storage_location_id=stock["location"]["id"])
    assert [m["movement_type"] for m in at_original] == ["receipt", "transfer"]
    page = _ok(client.get(_url(stock["item"], "movements"), params={"page": 2, "page_size": 3}))
    assert page["pagination"] == {"page": 2, "page_size": 3, "total": 4, "pages": 2}
    assert [m["movement_type"] for m in page["items"]] == ["writeoff_reversal"]


def test_stock_never_lists_rows_of_instance_items(client, world, stock) -> None:
    instance_item = _item(client, "instance")
    with session_scope() as session:
        session.add(
            InventoryItemStock(
                item_id=uuid.UUID(instance_item["id"]),
                storage_location_id=uuid.UUID(stock["location"]["id"]),
                quantity=3,
            )
        )
        session.commit()
    items = _ok(client.get(f"{_BASE}/stock"))["items"]
    assert {(r["item_id"], r["quantity"]) for r in items} == {(stock["item"]["id"], 7)}
    assert _ok(client.get(f"{_BASE}/stock", params={"item_id": instance_item["id"]}))["items"] == []


def test_history_rejects_instance_items(client, world, stock) -> None:
    instance_item = _item(client, "instance")
    _error(client.get(_url(instance_item, "movements")), 422, "item_not_quantity_mode")
    assert [m["movement_type"] for m in _history(client, stock["item"])] == ["receipt"]
