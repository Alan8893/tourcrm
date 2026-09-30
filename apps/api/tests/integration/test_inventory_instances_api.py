"""HTTP-level integration tests for Inventory Slice 2 — instances (Issue
#230; docs/04-domain/inventory.md §7, §13, §16, §17), against the real app
and PostgreSQL:

    GET/POST   /api/v1/inventory/instances
    GET/PATCH  /api/v1/inventory/instances/{id}
    POST       /api/v1/inventory/instances/{id}/{transfer,repair-start,repair-end,write-off}
    GET        /api/v1/inventory/instances/{id}/movements
    POST       /api/v1/inventory/movements/{movement_id}/reverse

Fixture helpers are local, following the suite's convention
(tests/integration/test_inventory_api.py).
"""

import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Club, Person, User
from app.db.inventory import InventoryInstance, InventoryMovement
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_BASE = "/api/v1/inventory"
_INSTANCES = f"{_BASE}/instances"
_MOVEMENTS = f"{_BASE}/movements"


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


def _ok(response, code: int = 200) -> dict:
    assert response.status_code == code, response.text
    return response.json()


def _error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code, response.text


def _unit(client: TestClient) -> str:
    units = _ok(client.get(f"{_BASE}/units"))["items"]
    return next(u["id"] for u in units if u["name"] == "шт")


def _item(client: TestClient, mode: str = "instance") -> dict:
    category = _ok(
        _post(client, f"{_BASE}/categories", {"name": f"Кат {uuid.uuid4().hex[:6]}"}), 201
    )
    return _ok(
        _post(
            client,
            f"{_BASE}/items",
            {
                "name": f"Рюкзак {uuid.uuid4().hex[:6]}",
                "category_id": category["id"],
                "unit_id": _unit(client),
                "accounting_mode": mode,
            },
        ),
        201,
    )


def _location(client: TestClient, name: str | None = None) -> dict:
    body = {"name": name or f"Полка {uuid.uuid4().hex[:6]}"}
    return _ok(_post(client, f"{_BASE}/storage-locations", body), 201)


def _receive(client: TestClient, item: dict, location: dict, **extra) -> dict:
    body = {"item_id": item["id"], "storage_location_id": location["id"], **extra}
    return _ok(_post(client, _INSTANCES, body), 201)


def _history(client: TestClient, instance: dict) -> list[dict]:
    return _ok(client.get(f"{_INSTANCES}/{instance['id']}/movements"))


def _set_state_in_db(instance_id: str, state: str) -> None:
    """Puts an instance into a state Slice 2 cannot reach through the API
    (`issued`, which arrives with Slice 4)."""
    with session_scope() as session:
        instance = session.get(InventoryInstance, uuid.UUID(instance_id))
        assert instance is not None
        instance.state = state
        instance.storage_location_id = None
        session.commit()


@pytest.fixture
def stock(client, world) -> dict:
    _as(world.admin)
    item = _item(client)
    location = _location(client)
    return {"item": item, "location": location, "instance": _receive(client, item, location)}


# --- authorization -------------------------------------------------------------------


def _all_requests(
    instance_id: str, movement_id: str, ids: dict
) -> list[tuple[str, str, dict | None]]:
    base = f"{_INSTANCES}/{instance_id}"
    return [
        ("GET", _INSTANCES, None),
        ("POST", _INSTANCES, {"item_id": ids["item"], "storage_location_id": ids["location"]}),
        ("GET", base, None),
        ("PATCH", base, {"description": "x"}),
        ("POST", f"{base}/transfer", {"to_location_id": ids["location"]}),
        ("POST", f"{base}/repair-start", None),
        ("POST", f"{base}/repair-end", None),
        ("POST", f"{base}/write-off", {"comment": "x"}),
        ("GET", f"{base}/movements", None),
        ("POST", f"{_MOVEMENTS}/{movement_id}/reverse", None),
    ]


@pytest.mark.parametrize("role", ["instructor", "member", "guardian"])
def test_non_administrators_are_forbidden_everywhere(client, world, stock, role) -> None:
    movement_id = _history(client, stock["instance"])[0]["id"]
    ids = {"item": stock["item"]["id"], "location": stock["location"]["id"]}
    _as(getattr(world, role))
    for method, url, body in _all_requests(stock["instance"]["id"], movement_id, ids):
        response = client.request(method, url, json=body, headers=_csrf(client))
        _error(response, 403, "forbidden")
    # Same answer for ids that do not exist (no existence probing).
    for method, url, body in _all_requests(str(uuid.uuid4()), str(uuid.uuid4()), ids):
        assert client.request(method, url, json=body, headers=_csrf(client)).status_code == 403


def test_unauthenticated_requests_get_401(client, world, stock) -> None:
    movement_id = _history(client, stock["instance"])[0]["id"]
    ids = {"item": stock["item"]["id"], "location": stock["location"]["id"]}
    _as(None)
    for method, url, body in _all_requests(stock["instance"]["id"], movement_id, ids):
        response = client.request(method, url, json=body, headers=_csrf(client))
        assert response.status_code == 401, (method, url)


def test_unknown_instance_or_movement_is_404_for_the_administrator(client, world) -> None:
    _as(world.admin)
    missing = uuid.uuid4()
    _error(client.get(f"{_INSTANCES}/{missing}"), 404, "not_found")
    _error(client.get(f"{_INSTANCES}/{missing}/movements"), 404, "not_found")
    _error(_post(client, f"{_INSTANCES}/{missing}/repair-start"), 404, "not_found")
    _error(_post(client, f"{_MOVEMENTS}/{missing}/reverse"), 404, "not_found")


def test_no_delete_endpoint(client, world, stock) -> None:
    response = client.delete(f"{_INSTANCES}/{stock['instance']['id']}", headers=_csrf(client))
    assert response.status_code == 405


# --- receipt -------------------------------------------------------------------------


def test_receipt_creates_an_available_instance_with_a_receipt_movement(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    location = _location(client)
    instance = _receive(
        client,
        item,
        location,
        unit_cost_minor=650_000,
        manufacturer_barcode=" 4607001234567 ",
        manufacturer_serial_number="SN-1",
        description="Синий",
    )
    assert instance["item_id"] == item["id"]
    assert instance["inventory_number"] == "INV-000001"
    assert instance["state"] == "available"
    assert instance["storage_location_id"] == location["id"]
    assert instance["manufacturer_barcode"] == "4607001234567"
    assert instance["manufacturer_serial_number"] == "SN-1"
    assert instance["description"] == "Синий"
    assert instance["created_by"] == str(world.admin)
    assert instance["id"] != instance["inventory_number"]

    [receipt] = _history(client, instance)
    assert receipt["movement_type"] == "receipt"
    assert receipt["instance_id"] == instance["id"]
    assert receipt["item_id"] == item["id"]
    assert receipt["from_location_id"] is None
    assert receipt["to_location_id"] == location["id"]
    assert receipt["unit_cost_minor"] == 650_000
    assert receipt["created_by"] == str(world.admin)


def test_optional_fields_may_be_omitted(client, world) -> None:
    _as(world.admin)
    instance = _receive(client, _item(client), _location(client))
    assert instance["manufacturer_barcode"] is None
    assert instance["manufacturer_serial_number"] is None
    assert instance["description"] is None
    assert _history(client, instance)[0]["unit_cost_minor"] is None


def test_inventory_numbers_are_sequential_across_items(client, world) -> None:
    _as(world.admin)
    location = _location(client)
    first, second = _item(client), _item(client)
    numbers = [
        _receive(client, first, location)["inventory_number"],
        _receive(client, second, location)["inventory_number"],
        _receive(client, first, location)["inventory_number"],
    ]
    assert numbers == ["INV-000001", "INV-000002", "INV-000003"]


def test_receipt_rejects_quantity_items(client, world) -> None:
    _as(world.admin)
    response = _post(
        client,
        _INSTANCES,
        {
            "item_id": _item(client, "quantity")["id"],
            "storage_location_id": _location(client)["id"],
        },
    )
    _error(response, 422, "item_not_instance_mode")


def test_receipt_rejects_archived_items(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    _ok(_post(client, f"{_BASE}/items/{item['id']}/archive"))
    response = _post(
        client, _INSTANCES, {"item_id": item["id"], "storage_location_id": _location(client)["id"]}
    )
    _error(response, 422, "archived_reference")


def test_receipt_rejects_unknown_references(client, world) -> None:
    _as(world.admin)
    location = _location(client)
    _error(
        _post(
            client,
            _INSTANCES,
            {"item_id": str(uuid.uuid4()), "storage_location_id": location["id"]},
        ),
        422,
        "invalid_reference",
    )
    _error(
        _post(
            client,
            _INSTANCES,
            {"item_id": _item(client)["id"], "storage_location_id": str(uuid.uuid4())},
        ),
        422,
        "invalid_reference",
    )


def test_receipt_rejects_archived_locations(client, world) -> None:
    _as(world.admin)
    location = _location(client)
    _ok(_post(client, f"{_BASE}/storage-locations/{location['id']}/archive"))
    response = _post(
        client, _INSTANCES, {"item_id": _item(client)["id"], "storage_location_id": location["id"]}
    )
    _error(response, 422, "archived_reference")


@pytest.mark.parametrize(
    "field", [{"state": "issued"}, {"inventory_number": "INV-000042"}, {"unit_cost_minor": -1}]
)
def test_receipt_rejects_client_controlled_fields(client, world, field) -> None:
    _as(world.admin)
    body = {"item_id": _item(client)["id"], "storage_location_id": _location(client)["id"], **field}
    assert _post(client, _INSTANCES, body).status_code == 422


def test_first_receipt_locks_the_items_accounting_mode(client, world, stock) -> None:
    response = _patch(
        client, f"{_BASE}/items/{stock['item']['id']}", {"accounting_mode": "quantity"}
    )
    _error(response, 409, "accounting_mode_locked")


# --- PATCH ---------------------------------------------------------------------------


def test_patch_updates_only_the_editable_fields(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    updated = _ok(
        _patch(
            client,
            url,
            {
                "manufacturer_barcode": "B-1",
                "manufacturer_serial_number": "S-1",
                "description": "Новый",
            },
        )
    )
    assert (updated["manufacturer_barcode"], updated["manufacturer_serial_number"]) == (
        "B-1",
        "S-1",
    )
    assert updated["description"] == "Новый"
    assert updated["updated_by"] == str(world.admin)
    cleared = _ok(_patch(client, url, {"description": None, "manufacturer_barcode": "  "}))
    assert cleared["description"] is None
    assert cleared["manufacturer_barcode"] is None
    assert cleared["manufacturer_serial_number"] == "S-1"
    # Only the receipt: editing fields is not a movement.
    assert len(_history(client, stock["instance"])) == 1


@pytest.mark.parametrize(
    "field",
    [
        {"item_id": str(uuid.uuid4())},
        {"inventory_number": "INV-999999"},
        {"state": "in_repair"},
        {"storage_location_id": str(uuid.uuid4())},
        {"created_by": str(uuid.uuid4())},
    ],
)
def test_patch_rejects_protected_fields(client, world, stock, field) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    assert _patch(client, url, field).status_code == 422
    assert _ok(client.get(url)) == stock["instance"]


def test_manufacturer_codes_are_not_unique(client, world, stock) -> None:
    body = {"manufacturer_barcode": "SAME", "manufacturer_serial_number": "SAME"}
    other = _receive(client, stock["item"], stock["location"], **body)
    _ok(_patch(client, f"{_INSTANCES}/{stock['instance']['id']}", body))
    assert other["manufacturer_barcode"] == "SAME"


def test_written_off_instance_cannot_be_edited(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    _ok(_post(client, f"{url}/write-off", {"comment": "Порван"}))
    _error(_patch(client, url, {"description": "x"}), 409, "instance_written_off")


# --- transfer ------------------------------------------------------------------------


def test_transfer_moves_an_available_instance(client, world, stock) -> None:
    target = _location(client)
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    moved = _ok(
        _post(client, f"{url}/transfer", {"to_location_id": target["id"], "comment": "Шкаф"})
    )
    assert moved["state"] == "available"
    assert moved["storage_location_id"] == target["id"]
    transfer = _history(client, stock["instance"])[-1]
    assert transfer["movement_type"] == "transfer"
    assert transfer["from_location_id"] == stock["location"]["id"]
    assert transfer["to_location_id"] == target["id"]
    assert transfer["comment"] == "Шкаф"


def test_transfer_keeps_the_in_repair_state(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    _ok(_post(client, f"{url}/repair-start"))
    target = _location(client)
    moved = _ok(_post(client, f"{url}/transfer", {"to_location_id": target["id"]}))
    assert (moved["state"], moved["storage_location_id"]) == ("in_repair", target["id"])


def test_transfer_rejects_bad_targets(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}/transfer"
    archived = _location(client)
    _ok(_post(client, f"{_BASE}/storage-locations/{archived['id']}/archive"))
    _error(_post(client, url, {"to_location_id": archived["id"]}), 422, "archived_reference")
    _error(_post(client, url, {"to_location_id": str(uuid.uuid4())}), 422, "invalid_reference")
    _error(
        _post(client, url, {"to_location_id": stock["location"]["id"]}),
        422,
        "invalid_inventory_data",
    )
    assert len(_history(client, stock["instance"])) == 1


@pytest.mark.parametrize("state", ["issued", "written_off"])
def test_transfer_rejects_instances_not_in_storage(client, world, stock, state) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    if state == "written_off":
        _ok(_post(client, f"{url}/write-off", {"comment": "Сломан"}))
    else:
        _set_state_in_db(stock["instance"]["id"], state)
    response = _post(client, f"{url}/transfer", {"to_location_id": _location(client)["id"]})
    _error(response, 409, "invalid_state_transition")


# --- repair --------------------------------------------------------------------------


def test_repair_start_and_end_keep_the_location(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    started = _ok(_post(client, f"{url}/repair-start"))
    assert (started["state"], started["storage_location_id"]) == (
        "in_repair",
        stock["location"]["id"],
    )
    _error(_post(client, f"{url}/repair-start"), 409, "invalid_state_transition")
    ended = _ok(_post(client, f"{url}/repair-end"))
    assert (ended["state"], ended["storage_location_id"]) == ("available", stock["location"]["id"])
    _error(_post(client, f"{url}/repair-end"), 409, "invalid_state_transition")
    movements = _history(client, stock["instance"])
    assert [m["movement_type"] for m in movements] == ["receipt", "repair_start", "repair_end"]
    for movement in movements[1:]:
        assert movement["from_location_id"] is None and movement["to_location_id"] is None


# --- write-off -----------------------------------------------------------------------


def test_write_off_requires_a_reason(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}/write-off"
    assert _post(client, url, {}).status_code == 422
    _error(_post(client, url, {"comment": "   "}), 422, "invalid_inventory_data")
    assert _ok(client.get(f"{_INSTANCES}/{stock['instance']['id']}"))["state"] == "available"


@pytest.mark.parametrize("from_repair", [False, True])
def test_write_off_is_terminal(client, world, stock, from_repair) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    if from_repair:
        _ok(_post(client, f"{url}/repair-start"))
    written_off = _ok(_post(client, f"{url}/write-off", {"comment": " Утерян в походе "}))
    assert written_off["state"] == "written_off"
    assert written_off["storage_location_id"] is None
    movement = _history(client, stock["instance"])[-1]
    assert movement["movement_type"] == "write_off"
    assert movement["from_location_id"] == stock["location"]["id"]
    assert movement["to_location_id"] is None
    assert movement["comment"] == "Утерян в походе"

    _error(
        _post(client, f"{url}/write-off", {"comment": "ещё раз"}), 409, "invalid_state_transition"
    )
    _error(_post(client, f"{url}/repair-start"), 409, "invalid_state_transition")


# --- write-off reversal --------------------------------------------------------------


def _write_off(client, instance: dict) -> str:
    _ok(_post(client, f"{_INSTANCES}/{instance['id']}/write-off", {"comment": "Ошибка"}))
    return _history(client, instance)[-1]["id"]


def test_reversal_restores_the_original_location(client, world, stock) -> None:
    write_off_id = _write_off(client, stock["instance"])
    restored = _ok(_post(client, f"{_MOVEMENTS}/{write_off_id}/reverse"))
    assert restored["state"] == "available"
    assert restored["storage_location_id"] == stock["location"]["id"]
    reversal = _history(client, stock["instance"])[-1]
    assert reversal["movement_type"] == "writeoff_reversal"
    assert reversal["reverses_movement_id"] == write_off_id
    assert reversal["from_location_id"] is None
    assert reversal["to_location_id"] == stock["location"]["id"]
    # The original write-off is untouched.
    original = next(m for m in _history(client, stock["instance"]) if m["id"] == write_off_id)
    assert original["movement_type"] == "write_off" and original["comment"] == "Ошибка"


def test_reversal_accepts_the_original_location_explicitly(client, world, stock) -> None:
    write_off_id = _write_off(client, stock["instance"])
    body = {"storage_location_id": stock["location"]["id"]}
    assert _ok(_post(client, f"{_MOVEMENTS}/{write_off_id}/reverse", body))["state"] == "available"


def test_reversal_rejects_another_location_while_the_original_is_active(
    client, world, stock
) -> None:
    write_off_id = _write_off(client, stock["instance"])
    body = {"storage_location_id": _location(client)["id"]}
    _error(
        _post(client, f"{_MOVEMENTS}/{write_off_id}/reverse", body),
        422,
        "storage_location_not_allowed",
    )


def test_reversal_into_another_location_when_the_original_is_archived(client, world, stock) -> None:
    write_off_id = _write_off(client, stock["instance"])
    _ok(_post(client, f"{_BASE}/storage-locations/{stock['location']['id']}/archive"))
    url = f"{_MOVEMENTS}/{write_off_id}/reverse"

    _error(_post(client, url), 422, "storage_location_required")
    _error(
        _post(client, url, {"storage_location_id": stock["location"]["id"]}),
        422,
        "archived_reference",
    )
    _error(_post(client, url, {"storage_location_id": str(uuid.uuid4())}), 422, "invalid_reference")
    # Failed attempts leave nothing behind (atomicity).
    assert _ok(client.get(f"{_INSTANCES}/{stock['instance']['id']}"))["state"] == "written_off"
    assert _history(client, stock["instance"])[-1]["movement_type"] == "write_off"

    target = _location(client)
    restored = _ok(_post(client, url, {"storage_location_id": target["id"]}))
    assert (restored["state"], restored["storage_location_id"]) == ("available", target["id"])


def test_a_write_off_can_be_reversed_only_once(client, world, stock) -> None:
    write_off_id = _write_off(client, stock["instance"])
    _ok(_post(client, f"{_MOVEMENTS}/{write_off_id}/reverse"))
    _error(_post(client, f"{_MOVEMENTS}/{write_off_id}/reverse"), 409, "write_off_already_reversed")
    # A later write-off of the same instance is a new, reversible write-off.
    second = _write_off(client, stock["instance"])
    assert _ok(_post(client, f"{_MOVEMENTS}/{second}/reverse"))["state"] == "available"


def test_only_a_write_off_can_be_reversed(client, world, stock) -> None:
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    _ok(_post(client, f"{url}/repair-start"))
    for movement in _history(client, stock["instance"]):
        response = _post(client, f"{_MOVEMENTS}/{movement['id']}/reverse")
        _error(response, 422, "invalid_reversal_target")
    write_off_id = _write_off(client, stock["instance"])
    _ok(_post(client, f"{_MOVEMENTS}/{write_off_id}/reverse"))
    reversal_id = _history(client, stock["instance"])[-1]["id"]
    _error(_post(client, f"{_MOVEMENTS}/{reversal_id}/reverse"), 422, "invalid_reversal_target")


# --- archiving blockers ----------------------------------------------------------------


@pytest.mark.parametrize("state", ["available", "in_repair", "issued"])
def test_item_with_non_written_off_instances_cannot_be_archived(
    client, world, stock, state
) -> None:
    if state == "in_repair":
        _ok(_post(client, f"{_INSTANCES}/{stock['instance']['id']}/repair-start"))
    elif state == "issued":
        _set_state_in_db(stock["instance"]["id"], "issued")
    response = _post(client, f"{_BASE}/items/{stock['item']['id']}/archive")
    _error(response, 409, "item_has_active_instances")


def test_item_with_only_written_off_instances_can_be_archived(client, world, stock) -> None:
    _write_off(client, stock["instance"])
    archived = _ok(_post(client, f"{_BASE}/items/{stock['item']['id']}/archive"))
    assert archived["status"] == "archived"


@pytest.mark.parametrize("state", ["available", "in_repair"])
def test_location_holding_instances_cannot_be_archived(client, world, stock, state) -> None:
    if state == "in_repair":
        _ok(_post(client, f"{_INSTANCES}/{stock['instance']['id']}/repair-start"))
    response = _post(client, f"{_BASE}/storage-locations/{stock['location']['id']}/archive")
    _error(response, 409, "location_has_instances")


def test_location_can_be_archived_once_its_instances_are_moved(client, world, stock) -> None:
    location_url = f"{_BASE}/storage-locations/{stock['location']['id']}/archive"
    target = _location(client)
    _ok(
        _post(
            client,
            f"{_INSTANCES}/{stock['instance']['id']}/transfer",
            {"to_location_id": target["id"]},
        )
    )
    assert _ok(_post(client, location_url))["status"] == "archived"


def test_written_off_instances_do_not_block_their_last_location(client, world, stock) -> None:
    _write_off(client, stock["instance"])
    location_url = f"{_BASE}/storage-locations/{stock['location']['id']}/archive"
    assert _ok(_post(client, location_url))["status"] == "archived"


# --- list and history ------------------------------------------------------------------


def test_list_hides_written_off_unless_filtered(client, world, stock) -> None:
    other_item = _item(client)
    other_location = _location(client)
    other = _receive(client, other_item, other_location)
    written_off = _receive(client, stock["item"], stock["location"])
    _write_off(client, written_off)

    def ids(**params) -> list[str]:
        return [i["id"] for i in _ok(client.get(_INSTANCES, params=params))["items"]]

    assert ids() == [stock["instance"]["id"], other["id"]]
    assert ids(state="written_off") == [written_off["id"]]
    assert ids(item_id=stock["item"]["id"]) == [stock["instance"]["id"]]
    assert ids(storage_location_id=other_location["id"]) == [other["id"]]
    assert client.get(_INSTANCES, params={"state": "lost"}).status_code == 422


def test_history_is_chronological_and_per_instance(client, world, stock) -> None:
    other = _receive(client, stock["item"], stock["location"])
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    _ok(_post(client, f"{url}/transfer", {"to_location_id": _location(client)["id"]}))
    _ok(_post(client, f"{url}/repair-start"))
    _ok(_post(client, f"{url}/repair-end"))
    write_off_id = _write_off(client, stock["instance"])
    _ok(_post(client, f"{_MOVEMENTS}/{write_off_id}/reverse"))
    _ok(_post(client, f"{_INSTANCES}/{other['id']}/repair-start"))

    movements = _history(client, stock["instance"])
    assert [m["movement_type"] for m in movements] == [
        "receipt",
        "transfer",
        "repair_start",
        "repair_end",
        "write_off",
        "writeoff_reversal",
    ]
    assert {m["instance_id"] for m in movements} == {stock["instance"]["id"]}
    assert [m["created_at"] for m in movements] == sorted(m["created_at"] for m in movements)


def test_history_rows_are_never_updated(client, world, stock) -> None:
    before = _history(client, stock["instance"])
    url = f"{_INSTANCES}/{stock['instance']['id']}"
    _ok(_patch(client, url, {"description": "x"}))
    _ok(_post(client, f"{url}/transfer", {"to_location_id": _location(client)["id"]}))
    assert _history(client, stock["instance"])[: len(before)] == before
    with session_scope() as session:
        count = session.execute(
            sa.select(sa.func.count()).where(
                InventoryMovement.instance_id == uuid.UUID(stock["instance"]["id"])
            )
        ).scalar_one()
    assert count == 2
