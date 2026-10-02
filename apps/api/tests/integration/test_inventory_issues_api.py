"""HTTP-level integration tests for Inventory Slice 4 — issue / return
(Issue #236; docs/04-domain/inventory.md §14, §17), against the real app
and PostgreSQL:

    GET/POST  /api/v1/inventory/issues
    GET/PATCH /api/v1/inventory/issues/{issue_id}
    GET/POST  /api/v1/inventory/issues/{issue_id}/lines
    POST      /api/v1/inventory/issues/{issue_id}/{returns,cancel,lost}
    GET       /api/v1/inventory/issues/{issue_id}/movements

Fixture helpers are local, following the suite's convention
(tests/integration/test_inventory_quantities_api.py).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.authorization import Role, UserRoleAssignment
from app.db.events import Event
from app.db.groups import Group
from app.db.identity import Club, ClubMembership, Person, User
from app.db.inventory import InventoryItemStock
from app.db.session import session_scope
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_BASE = "/api/v1/inventory"
_ISSUES = f"{_BASE}/issues"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


@dataclass
class World:
    admin: uuid.UUID
    instructor: uuid.UUID
    member_user: uuid.UUID
    guardian: uuid.UUID
    member_person: uuid.UUID
    outsider_person: uuid.UUID
    instructor_without_membership: uuid.UUID
    group: uuid.UUID
    archived_group: uuid.UUID
    event: uuid.UUID


_NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _person(session) -> Person:
    person = Person(last_name="Тестов", first_name=f"P-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.flush()
    return person


def _membership(session, club: Club, person: Person) -> None:
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="member",
            status="active",
            joined_at=_NOW - timedelta(days=30),
        )
    )
    session.flush()


def _user_with_role(session, club: Club, role_code: str, *, member: bool = True) -> uuid.UUID:
    person = _person(session)
    if member:
        _membership(session, club, person)
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


def _group(session, club: Club, status: str) -> uuid.UUID:
    group = Group(club_id=club.id, name=f"Группа {uuid.uuid4().hex[:6]}", status=status,
                  valid_from=_NOW - timedelta(days=30))
    session.add(group)
    session.flush()
    return group.id


@pytest.fixture
def world() -> World:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        member = _person(session)
        _membership(session, club, member)
        event = Event(
            club_id=club.id,
            event_type="training",
            title="Тренировка",
            start_at=_NOW,
            end_at=_NOW + timedelta(hours=2),
            timezone="Europe/Moscow",
            status="published",
        )
        session.add(event)
        session.flush()
        world = World(
            admin=_user_with_role(session, club, "admin"),
            instructor=_user_with_role(session, club, "instructor"),
            member_user=_user_with_role(session, club, "member"),
            guardian=_user_with_role(session, club, "guardian"),
            member_person=member.id,
            outsider_person=_person(session).id,
            instructor_without_membership=_user_with_role(
                session, club, "instructor", member=False
            ),
            group=_group(session, club, "active"),
            archived_group=_group(session, club, "archived"),
            event=event.id,
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


def _ok(response, code: int = 200):
    assert response.status_code == code, response.text
    return response.json()


def _error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code, response.text


def _item(client: TestClient, mode: str = "quantity") -> dict:
    category = _ok(
        _post(client, f"{_BASE}/categories", {"name": f"Кат {uuid.uuid4().hex[:6]}"}), 201
    )
    units = _ok(client.get(f"{_BASE}/units"))["items"]
    body = {
        "name": f"Позиция {uuid.uuid4().hex[:6]}",
        "category_id": category["id"],
        "unit_id": next(u["id"] for u in units if u["name"] == "шт"),
        "accounting_mode": mode,
    }
    return _ok(_post(client, f"{_BASE}/items", body), 201)


def _location(client: TestClient) -> dict:
    body = {"name": f"Полка {uuid.uuid4().hex[:6]}"}
    return _ok(_post(client, f"{_BASE}/storage-locations", body), 201)


def _receive(client, item: dict, location: dict, quantity: int) -> None:
    body = {"storage_location_id": location["id"], "quantity": quantity}
    _ok(_post(client, f"{_BASE}/items/{item['id']}/receipts", body), 201)


def _instance(client, item: dict, location: dict) -> dict:
    body = {"item_id": item["id"], "storage_location_id": location["id"]}
    return _ok(_post(client, f"{_BASE}/instances", body), 201)


def _stock(item: dict) -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(
            select(InventoryItemStock).where(InventoryItemStock.item_id == uuid.UUID(item["id"]))
        ).scalars()
        return {str(r.storage_location_id): r.quantity for r in rows if r.quantity}


def _issue(client, world: World, lines: list[dict], **extra) -> dict:
    body = {
        "recipient_type": "member",
        "recipient_id": str(world.member_person),
        "lines": lines,
        **extra,
    }
    return _ok(_post(client, _ISSUES, body), 201)


def _line(issue: dict, item: dict) -> dict:
    return next(line for line in issue["lines"] if line["item_id"] == item["id"])


@dataclass
class Stock:
    a: dict
    b: dict
    rope: dict
    stove: dict
    stoves: list[dict]


@pytest.fixture
def stock(client, world) -> Stock:
    """Rope: A = 7, B = 5 (quantity). Stove: three instances in A."""
    _as(world.admin)
    a, b = _location(client), _location(client)
    rope = _item(client)
    _receive(client, rope, a, 7)
    _receive(client, rope, b, 5)
    stove = _item(client, "instance")
    stoves = [_instance(client, stove, a) for _ in range(3)]
    return Stock(a=a, b=b, rope=rope, stove=stove, stoves=stoves)


# --- authorization ---------------------------------------------------------------------


@pytest.mark.parametrize("who", ["instructor", "member_user", "guardian"])
def test_non_administrators_get_403_before_existence(client, world, who) -> None:
    _as(getattr(world, who))
    missing = f"{_ISSUES}/{uuid.uuid4()}"
    assert client.get(_ISSUES).status_code == 403
    assert client.get(missing).status_code == 403
    assert client.get(f"{missing}/lines").status_code == 403
    assert client.get(f"{missing}/movements").status_code == 403
    some = str(uuid.uuid4())
    line = {"item_id": some, "quantity": 1}
    bodies = {
        "lines": {"lines": [line]},
        "returns": {"storage_location_id": some, "instance_ids": [some]},
        "cancel": {"storage_location_id": some},
        "lost": {"instance_id": some, "storage_location_id": some, "reason": "x"},
    }
    create = {"recipient_type": "member", "recipient_id": some, "lines": [line]}
    assert _post(client, _ISSUES, create).status_code == 403
    assert _patch(client, missing, {"comment": "x"}).status_code == 403
    for action, body in bodies.items():
        assert _post(client, f"{missing}/{action}", body).status_code == 403


def test_unknown_issue_is_404_for_the_administrator(client, world) -> None:
    _as(world.admin)
    missing = f"{_ISSUES}/{uuid.uuid4()}"
    _error(client.get(missing), 404, "not_found")
    _error(_post(client, f"{missing}/cancel", {"storage_location_id": str(uuid.uuid4())}), 404,
           "not_found")


# --- issue -----------------------------------------------------------------------------


def test_quantity_issue_allocates_largest_stock_first(client, world, stock) -> None:
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 10}])
    assert issue["status"] == "issued" and issue["has_outstanding"] is True
    line = _line(issue, stock.rope)
    assert (line["issued_quantity"], line["returned_quantity"], line["outstanding_quantity"]) == (
        10,
        0,
        10,
    )
    assert _stock(stock.rope) == {stock.b["id"]: 2}
    movements = _ok(client.get(f"{_ISSUES}/{issue['id']}/movements"))["items"]
    assert [(m["movement_type"], m["from_location_id"], m["quantity"]) for m in movements] == [
        ("issue", stock.a["id"], 7),
        ("issue", stock.b["id"], 3),
    ]
    assert all(m["issue_line_id"] == line["id"] for m in movements)


def test_quantity_issue_cannot_exceed_total_stock(client, world, stock) -> None:
    response = _post(
        client,
        _ISSUES,
        {
            "recipient_type": "member",
            "recipient_id": str(world.member_person),
            "lines": [{"item_id": stock.rope["id"], "quantity": 13}],
        },
    )
    _error(response, 409, "insufficient_stock")
    assert _stock(stock.rope) == {stock.a["id"]: 7, stock.b["id"]: 5}
    assert _ok(client.get(_ISSUES))["pagination"]["total"] == 0


def test_instance_issue_and_multi_line_issue(client, world, stock) -> None:
    first, second, _ = stock.stoves
    issue = _issue(
        client,
        world,
        [
            {"item_id": stock.rope["id"], "quantity": 2},
            {"item_id": stock.stove["id"], "instance_ids": [first["id"], second["id"]]},
        ],
    )
    assert len(issue["lines"]) == 2
    stove_line = _line(issue, stock.stove)
    assert stove_line["accounting_mode"] == "instance"
    assert stove_line["outstanding_quantity"] == 2
    assert set(stove_line["outstanding_instance_ids"]) == {first["id"], second["id"]}
    detail = _ok(client.get(f"{_BASE}/instances/{first['id']}"))
    assert detail["state"] == "issued" and detail["storage_location_id"] is None


def test_issued_instance_cannot_be_issued_again(client, world, stock) -> None:
    stove = stock.stoves[0]
    _issue(client, world, [{"item_id": stock.stove["id"], "instance_ids": [stove["id"]]}])
    response = _post(
        client,
        _ISSUES,
        {
            "recipient_type": "group",
            "recipient_id": str(world.group),
            "lines": [{"item_id": stock.stove["id"], "instance_ids": [stove["id"]]}],
        },
    )
    _error(response, 409, "invalid_state_transition")


def test_issued_instance_cannot_be_transferred_or_written_off(client, world, stock) -> None:
    stove = stock.stoves[0]
    _issue(client, world, [{"item_id": stock.stove["id"], "instance_ids": [stove["id"]]}])
    url = f"{_BASE}/instances/{stove['id']}"
    _error(_post(client, f"{url}/transfer", {"to_location_id": stock.b["id"]}), 409,
           "invalid_state_transition")
    _error(_post(client, f"{url}/write-off", {"comment": "x"}), 409, "invalid_state_transition")


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ({"quantity": 1, "instance_ids": None}, "invalid_inventory_data"),
        ({}, "invalid_inventory_data"),
    ],
)
def test_line_needs_quantity_or_instances(client, world, stock, line, code) -> None:
    body = {"item_id": stock.rope["id"], **line}
    if line.get("instance_ids") is None and "quantity" in line:
        body["instance_ids"] = [stock.stoves[0]["id"]]
    response = _post(
        client,
        _ISSUES,
        {"recipient_type": "member", "recipient_id": str(world.member_person), "lines": [body]},
    )
    _error(response, 422, code)


def test_one_line_per_item_in_a_request(client, world, stock) -> None:
    response = _post(
        client,
        _ISSUES,
        {
            "recipient_type": "member",
            "recipient_id": str(world.member_person),
            "lines": [
                {"item_id": stock.rope["id"], "quantity": 1},
                {"item_id": stock.rope["id"], "quantity": 2},
            ],
        },
    )
    _error(response, 422, "invalid_inventory_data")


def test_accounting_mode_must_match_the_line(client, world, stock) -> None:
    def issue(line: dict):
        return _post(
            client,
            _ISSUES,
            {"recipient_type": "member", "recipient_id": str(world.member_person),
             "lines": [line]},
        )

    _error(issue({"item_id": stock.stove["id"], "quantity": 1}), 422, "item_not_quantity_mode")
    _error(issue({"item_id": stock.rope["id"], "instance_ids": [stock.stoves[0]["id"]]}), 422,
           "item_not_instance_mode")
    other_stove = _instance(client, _item(client, "instance"), stock.a)
    _error(issue({"item_id": stock.stove["id"], "instance_ids": [other_stove["id"]]}), 422,
           "invalid_reference")


def test_archived_item_cannot_be_issued(client, world) -> None:
    _as(world.admin)
    item = _item(client)
    _ok(_post(client, f"{_BASE}/items/{item['id']}/archive"))
    response = _post(
        client,
        _ISSUES,
        {"recipient_type": "member", "recipient_id": str(world.member_person),
         "lines": [{"item_id": item["id"], "quantity": 1}]},
    )
    _error(response, 422, "archived_reference")


# --- recipients and Event --------------------------------------------------------------


@pytest.mark.parametrize(
    ("recipient_type", "attribute"),
    [("member", "member_person"), ("instructor", "instructor"), ("group", "group")],
)
def test_valid_recipients(client, world, stock, recipient_type, attribute) -> None:
    recipient_id = str(getattr(world, attribute))
    issue = _issue(
        client,
        world,
        [{"item_id": stock.rope["id"], "quantity": 1}],
        recipient_type=recipient_type,
        recipient_id=recipient_id,
    )
    assert (issue["recipient_type"], issue["recipient_id"]) == (recipient_type, recipient_id)
    listed = _ok(
        client.get(_ISSUES, params={"recipient_type": recipient_type, "recipient_id": recipient_id})
    )
    assert [i["id"] for i in listed["items"]] == [issue["id"]]


@pytest.mark.parametrize(
    ("recipient_type", "attribute"),
    [
        ("member", "outsider_person"),  # no active ClubMembership
        ("member", "instructor"),  # a User id is not a Person id
        ("instructor", "member_user"),  # no `instructor` role
        ("instructor", "instructor_without_membership"),
        ("group", "archived_group"),
        ("group", "member_person"),
    ],
)
def test_invalid_recipients(client, world, stock, recipient_type, attribute) -> None:
    response = _post(
        client,
        _ISSUES,
        {
            "recipient_type": recipient_type,
            "recipient_id": str(getattr(world, attribute)),
            "lines": [{"item_id": stock.rope["id"], "quantity": 1}],
        },
    )
    _error(response, 422, "invalid_recipient")
    assert _stock(stock.rope) == {stock.a["id"]: 7, stock.b["id"]: 5}


def test_unknown_recipient_type_is_422(client, world, stock) -> None:
    response = _post(
        client,
        _ISSUES,
        {"recipient_type": "guardian", "recipient_id": str(world.guardian),
         "lines": [{"item_id": stock.rope["id"], "quantity": 1}]},
    )
    assert response.status_code == 422


def test_group_issue_linked_to_an_event(client, world, stock) -> None:
    issue = _issue(
        client,
        world,
        [{"item_id": stock.rope["id"], "quantity": 3}],
        recipient_type="group",
        recipient_id=str(world.group),
        event_id=str(world.event),
        planned_return_date="2026-10-20",
        comment="Сборы",
    )
    assert issue["event_id"] == str(world.event)
    assert issue["planned_return_date"] == "2026-10-20"
    assert issue["comment"] == "Сборы"
    listed = _ok(client.get(_ISSUES, params={"event_id": str(world.event)}))
    assert [i["id"] for i in listed["items"]] == [issue["id"]]


def test_unknown_event_is_rejected(client, world, stock) -> None:
    response = _post(
        client,
        _ISSUES,
        {"recipient_type": "member", "recipient_id": str(world.member_person),
         "event_id": str(uuid.uuid4()), "lines": [{"item_id": stock.rope["id"], "quantity": 1}]},
    )
    _error(response, 422, "invalid_reference")


# --- adding lines ----------------------------------------------------------------------


def test_adding_an_item_already_on_the_issue_extends_its_line(client, world, stock) -> None:
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 4}])
    line = _line(issue, stock.rope)
    updated = _ok(
        _post(
            client,
            f"{_ISSUES}/{issue['id']}/lines",
            {
                "lines": [
                    {"item_id": stock.rope["id"], "quantity": 3},
                    {"item_id": stock.stove["id"], "instance_ids": [stock.stoves[0]["id"]]},
                ]
            },
        ),
        201,
    )
    assert len(updated["lines"]) == 2
    rope_line = _line(updated, stock.rope)
    assert rope_line["id"] == line["id"]
    assert rope_line["issued_quantity"] == 7
    movements = _ok(client.get(f"{_ISSUES}/{issue['id']}/movements"))["items"]
    # A = 7, B = 5: 4 from A, then B is the larger stock (5 > 3) for the next 3.
    assert [
        (m["from_location_id"], m["quantity"]) for m in movements if m["instance_id"] is None
    ] == [(stock.a["id"], 4), (stock.b["id"], 3)]
    lines = _ok(client.get(f"{_ISSUES}/{issue['id']}/lines"))
    assert lines["pagination"]["total"] == 2


# --- returns ---------------------------------------------------------------------------


def _return(client, issue: dict, location: dict, *, quantities=(), instance_ids=(), **extra):
    return _post(
        client,
        f"{_ISSUES}/{issue['id']}/returns",
        {
            "storage_location_id": location["id"],
            "quantities": list(quantities),
            "instance_ids": list(instance_ids),
            **extra,
        },
    )


def test_repeated_partial_returns_then_immutable(client, world, stock) -> None:
    """10 issued -> 6 -> 3 -> 1 returned; then the issue is fully returned
    and immutable."""
    c = _location(client)
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 10}])
    line = _line(issue, stock.rope)
    for step, left in ((6, 4), (3, 1), (1, 0)):
        entry = {"line_id": line["id"], "quantity": step}
        updated = _ok(_return(client, issue, c, quantities=[entry]))
        assert _line(updated, stock.rope)["outstanding_quantity"] == left
    assert updated["has_outstanding"] is False and updated["status"] == "issued"
    assert _stock(stock.rope) == {stock.b["id"]: 2, c["id"]: 10}

    _error(_return(client, issue, c, quantities=[{"line_id": line["id"], "quantity": 1}]), 409,
           "issue_fully_returned")
    _error(_patch(client, f"{_ISSUES}/{issue['id']}", {"comment": "x"}), 409,
           "issue_fully_returned")
    _error(
        _post(client, f"{_ISSUES}/{issue['id']}/lines",
              {"lines": [{"item_id": stock.rope["id"], "quantity": 1}]}),
        409,
        "issue_fully_returned",
    )
    _error(_post(client, f"{_ISSUES}/{issue['id']}/cancel", {"storage_location_id": c["id"]}),
           409, "issue_fully_returned")
    # The fully returned line stays on the document as history.
    assert _line(_ok(client.get(f"{_ISSUES}/{issue['id']}")), stock.rope)["issued_quantity"] == 10


def test_return_cannot_exceed_what_is_outstanding(client, world, stock) -> None:
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 4}])
    line = _line(issue, stock.rope)
    _error(_return(client, issue, stock.a, quantities=[{"line_id": line["id"], "quantity": 5}]),
           409, "return_exceeds_outstanding")
    _error(_return(client, issue, stock.a, quantities=[{"line_id": str(uuid.uuid4()),
                                                        "quantity": 1}]), 422, "invalid_reference")
    _error(_return(client, issue, stock.a), 422, "invalid_inventory_data")


def test_instance_return_into_the_chosen_location(client, world, stock) -> None:
    first, second, _ = stock.stoves
    issue = _issue(
        client, world,
        [{"item_id": stock.stove["id"], "instance_ids": [first["id"], second["id"]]}],
    )
    updated = _ok(_return(client, issue, stock.b, instance_ids=[first["id"]], comment="целый"))
    assert _line(updated, stock.stove)["outstanding_instance_ids"] == [second["id"]]
    detail = _ok(client.get(f"{_BASE}/instances/{first['id']}"))
    assert (detail["state"], detail["storage_location_id"]) == ("available", stock.b["id"])
    _error(_return(client, issue, stock.b, instance_ids=[first["id"]]), 409, "instance_not_issued")
    history = _ok(client.get(f"{_BASE}/instances/{first['id']}/movements"))
    assert [m["movement_type"] for m in history] == ["receipt", "issue", "return"]
    assert history[-1]["comment"] == "целый"


def test_return_into_an_archived_location_is_refused(client, world, stock) -> None:
    c = _location(client)
    _ok(_post(client, f"{_BASE}/storage-locations/{c['id']}/archive"))
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 1}])
    line = _line(issue, stock.rope)
    _error(_return(client, issue, c, quantities=[{"line_id": line["id"], "quantity": 1}]), 422,
           "archived_reference")


# --- edit ------------------------------------------------------------------------------


def test_edit_header_after_a_partial_return(client, world, stock) -> None:
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 4}])
    line = _line(issue, stock.rope)
    _ok(_return(client, issue, stock.a, quantities=[{"line_id": line["id"], "quantity": 1}]))
    updated = _ok(
        _patch(
            client,
            f"{_ISSUES}/{issue['id']}",
            {
                "recipient_type": "instructor",
                "recipient_id": str(world.instructor),
                "event_id": str(world.event),
                "planned_return_date": "2026-11-01",
                "comment": "Новый комментарий",
            },
        )
    )
    assert updated["recipient_type"] == "instructor"
    assert updated["recipient_id"] == str(world.instructor)
    assert updated["event_id"] == str(world.event)
    assert updated["updated_by"] == str(world.admin)
    cleared = _ok(
        _patch(
            client,
            f"{_ISSUES}/{issue['id']}",
            {"event_id": None, "planned_return_date": None, "comment": None},
        )
    )
    assert (cleared["event_id"], cleared["planned_return_date"], cleared["comment"]) == (
        None, None, None,
    )
    # Issue history is untouched by editing.
    assert _line(cleared, stock.rope)["issued_quantity"] == 4


def test_edit_validation(client, world, stock) -> None:
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 1}])
    url = f"{_ISSUES}/{issue['id']}"
    _error(_patch(client, url, {"recipient_type": "group"}), 422, "invalid_inventory_data")
    _error(_patch(client, url, {}), 422, "invalid_inventory_data")
    archived = {"recipient_type": "group", "recipient_id": str(world.archived_group)}
    _error(_patch(client, url, archived),
           422, "invalid_recipient")
    assert _patch(client, url, {"lines": []}).status_code == 422


# --- cancel ----------------------------------------------------------------------------


def test_cancel_returns_everything_outstanding(client, world, stock) -> None:
    first, second, _ = stock.stoves
    c = _location(client)
    issue = _issue(
        client,
        world,
        [
            {"item_id": stock.rope["id"], "quantity": 10},
            {"item_id": stock.stove["id"], "instance_ids": [first["id"], second["id"]]},
        ],
    )
    rope_line = _line(issue, stock.rope)
    _ok(_return(client, issue, stock.a, quantities=[{"line_id": rope_line["id"], "quantity": 6}],
                instance_ids=[first["id"]]))
    cancel_url = f"{_ISSUES}/{issue['id']}/cancel"
    cancelled = _ok(_post(client, cancel_url, {"storage_location_id": c["id"]}))
    assert cancelled["status"] == "cancelled" and cancelled["has_outstanding"] is False
    assert cancelled["cancelled_by"] == str(world.admin) and cancelled["cancelled_at"]
    assert _stock(stock.rope) == {stock.a["id"]: 6, stock.b["id"]: 2, c["id"]: 4}
    detail = _ok(client.get(f"{_BASE}/instances/{second['id']}"))
    assert (detail["state"], detail["storage_location_id"]) == ("available", c["id"])
    movements = _ok(client.get(f"{_ISSUES}/{issue['id']}/movements"))["items"]
    assert [m["movement_type"] for m in movements].count("return") == 4

    url = f"{_ISSUES}/{issue['id']}"
    _error(_post(client, f"{url}/cancel", {"storage_location_id": c["id"]}), 409, "issue_cancelled")
    _error(_patch(client, url, {"comment": "x"}), 409, "issue_cancelled")
    _error(_post(client, f"{url}/lines", {"lines": [{"item_id": stock.rope["id"], "quantity": 1}]}),
           409, "issue_cancelled")
    _error(_return(client, issue, c, quantities=[{"line_id": rope_line["id"], "quantity": 1}]),
           409, "issue_cancelled")
    assert _ok(client.get(_ISSUES, params={"status": "cancelled"}))["pagination"]["total"] == 1


def test_cancel_needs_an_active_location(client, world, stock) -> None:
    issue = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 1}])
    _error(_post(client, f"{_ISSUES}/{issue['id']}/cancel",
                 {"storage_location_id": str(uuid.uuid4())}), 422, "invalid_reference")
    assert _post(client, f"{_ISSUES}/{issue['id']}/cancel", {}).status_code == 422
    assert _ok(client.get(f"{_ISSUES}/{issue['id']}"))["status"] == "issued"


# --- lost ------------------------------------------------------------------------------


def test_lost_instance_is_returned_and_written_off(client, world, stock) -> None:
    first, second, _ = stock.stoves
    issue = _issue(
        client, world,
        [{"item_id": stock.stove["id"], "instance_ids": [first["id"], second["id"]]}],
    )
    updated = _ok(
        _post(
            client,
            f"{_ISSUES}/{issue['id']}/lost",
            {
                "instance_id": first["id"],
                "storage_location_id": stock.b["id"],
                "reason": "Утеряна в походе",
                "comment": "Сообщил инструктор",
            },
        )
    )
    line = _line(updated, stock.stove)
    assert line["outstanding_instance_ids"] == [second["id"]]
    assert (line["returned_quantity"], line["outstanding_quantity"]) == (1, 1)
    detail = _ok(client.get(f"{_BASE}/instances/{first['id']}"))
    assert (detail["state"], detail["storage_location_id"]) == ("written_off", None)
    history = _ok(client.get(f"{_BASE}/instances/{first['id']}/movements"))
    returned, written_off = history[-2], history[-1]
    assert (returned["movement_type"], returned["to_location_id"], returned["comment"]) == (
        "return", stock.b["id"], "Сообщил инструктор",
    )
    assert (written_off["movement_type"], written_off["from_location_id"],
            written_off["comment"]) == ("write_off", stock.b["id"], "Утеряна в походе")
    assert returned["issue_line_id"] == written_off["issue_line_id"] == line["id"]
    issue_movements = _ok(client.get(f"{_ISSUES}/{issue['id']}/movements"))["items"]
    assert [m["movement_type"] for m in issue_movements][-2:] == ["return", "write_off"]

    # The write-off of a lost instance is reversed like any other: back to
    # the location it was returned into.
    reversal = _ok(_post(client, f"{_BASE}/movements/{written_off['id']}/reverse"))
    assert (reversal["state"], reversal["storage_location_id"]) == ("available", stock.b["id"])


def test_lost_needs_a_reason_and_an_outstanding_instance(client, world, stock) -> None:
    first, _, third = stock.stoves
    issue = _issue(client, world, [{"item_id": stock.stove["id"], "instance_ids": [first["id"]]}])
    url = f"{_ISSUES}/{issue['id']}/lost"
    base = {"instance_id": first["id"], "storage_location_id": stock.a["id"]}
    _error(_post(client, url, {**base, "reason": "   "}), 422, "invalid_inventory_data")
    assert _post(client, url, base).status_code == 422
    _error(_post(client, url, {**base, "instance_id": third["id"], "reason": "x"}), 409,
           "instance_not_issued")
    detail = _ok(client.get(f"{_BASE}/instances/{first['id']}"))
    assert detail["state"] == "issued"


def test_lost_into_an_archived_location_changes_nothing(client, world, stock) -> None:
    first = stock.stoves[0]
    c = _location(client)
    _ok(_post(client, f"{_BASE}/storage-locations/{c['id']}/archive"))
    issue = _issue(client, world, [{"item_id": stock.stove["id"], "instance_ids": [first["id"]]}])
    response = _post(
        client,
        f"{_ISSUES}/{issue['id']}/lost",
        {"instance_id": first["id"], "storage_location_id": c["id"], "reason": "x"},
    )
    _error(response, 422, "archived_reference")
    assert _ok(client.get(f"{_BASE}/instances/{first['id']}"))["state"] == "issued"
    movements = _ok(client.get(f"{_ISSUES}/{issue['id']}/movements"))["items"]
    assert [m["movement_type"] for m in movements] == ["issue"]


# --- archive protection ----------------------------------------------------------------


def test_quantity_item_with_outstanding_issue_cannot_be_archived(client, world) -> None:
    _as(world.admin)
    a = _location(client)
    item = _item(client)
    _receive(client, item, a, 3)
    issue = _issue(client, world, [{"item_id": item["id"], "quantity": 3}])
    # Stock is zero now; what is still issued blocks archiving.
    assert _stock(item) == {}
    _error(_post(client, f"{_BASE}/items/{item['id']}/archive"), 409,
           "item_has_outstanding_issues")
    line = _line(issue, item)
    _ok(_return(client, issue, a, quantities=[{"line_id": line["id"], "quantity": 3}]))
    location_b = _location(client)
    _ok(_post(client, f"{_BASE}/items/{item['id']}/transfers",
              {"from_location_id": a["id"], "to_location_id": location_b["id"], "quantity": 3}),
        201)
    _ok(_post(client, f"{_BASE}/items/{item['id']}/write-offs",
              {"storage_location_id": location_b["id"], "quantity": 3, "comment": "износ"}), 201)
    assert _ok(_post(client, f"{_BASE}/items/{item['id']}/archive"))["status"] == "archived"


def test_instance_item_with_issued_instance_cannot_be_archived(client, world, stock) -> None:
    _issue(client, world, [{"item_id": stock.stove["id"], "instance_ids": [stock.stoves[0]["id"]]}])
    _error(_post(client, f"{_BASE}/items/{stock.stove['id']}/archive"), 409,
           "item_has_active_instances")


# --- list ------------------------------------------------------------------------------


def test_list_newest_first_with_outstanding_flag(client, world, stock) -> None:
    first = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 1}])
    second = _issue(client, world, [{"item_id": stock.rope["id"], "quantity": 1}])
    line = _line(first, stock.rope)
    _ok(_return(client, first, stock.a, quantities=[{"line_id": line["id"], "quantity": 1}]))
    listed = _ok(client.get(_ISSUES))
    assert [(i["id"], i["has_outstanding"]) for i in listed["items"]] == [
        (second["id"], True),
        (first["id"], False),
    ]
    assert _ok(client.get(_ISSUES, params={"status": "issued"}))["pagination"]["total"] == 2
    assert client.get(_ISSUES, params={"status": "returned"}).status_code == 422
