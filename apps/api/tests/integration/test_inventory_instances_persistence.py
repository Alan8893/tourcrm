"""Database guarantees and concurrency of Inventory Slice 2 — instances
(Issue #230; docs/04-domain/inventory.md §7, §13, §16), against real
PostgreSQL."""

import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.identity import Club, Person, User
from app.db.inventory import (
    InventoryCategory,
    InventoryInstance,
    InventoryInstanceIdentityError,
    InventoryItem,
    InventoryMovement,
    InventoryMovementImmutableError,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.db.session import session_scope
from app.inventory import instances as instance_service
from app.inventory.lifecycle import InvalidInstanceTransitionError, WriteOffAlreadyReversedError
from app.inventory.queries import get_instance

from .conftest import requires_postgres

pytestmark = requires_postgres


class Setup:
    def __init__(
        self, club_id: uuid.UUID, user_id: uuid.UUID, item_id: uuid.UUID, location_id: uuid.UUID
    ):
        self.club_id = club_id
        self.user_id = user_id
        self.item_id = item_id
        self.location_id = location_id


def _location(session, club_id: uuid.UUID, user_id: uuid.UUID) -> InventoryStorageLocation:
    location = InventoryStorageLocation(
        club_id=club_id, name=f"Полка {uuid.uuid4().hex[:6]}", status="active", created_by=user_id
    )
    session.add(location)
    session.flush()
    return location


@pytest.fixture
def setup() -> Setup:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        person = Person(last_name="Тестов", first_name="Админ")
        session.add_all([club, person])
        session.flush()
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add(user)
        session.flush()
        category = InventoryCategory(
            club_id=club.id, name="Снаряжение", status="active", created_by=user.id
        )
        session.add(category)
        session.flush()
        unit_id = session.execute(
            sa.select(InventoryUnit.id).where(InventoryUnit.is_system, InventoryUnit.name == "шт")
        ).scalar_one()
        item = InventoryItem(
            club_id=club.id,
            name="Жумар",
            category_id=category.id,
            unit_id=unit_id,
            accounting_mode="instance",
            status="active",
            created_by=user.id,
        )
        session.add(item)
        location = _location(session, club.id, user.id)
        session.commit()
        return Setup(club.id, user.id, item.id, location.id)


def _receive(setup: Setup) -> InventoryInstance:
    with session_scope() as session:
        instance = instance_service.receive_instance(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            storage_location_id=setup.location_id,
            unit_cost_minor=None,
            manufacturer_barcode=None,
            manufacturer_serial_number=None,
            description=None,
            created_by=setup.user_id,
        )
        session.expunge(instance)
        return instance


def _run_concurrently(workers: list[Callable[[], Any]]) -> list[Any]:
    """Starts every worker at once; returns each result or raised exception."""
    barrier = threading.Barrier(len(workers))
    results: list[Any] = [None] * len(workers)

    def run(index: int, worker: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            results[index] = worker()
        except Exception as exc:  # collected for the assertions
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


# --- DB constraints -------------------------------------------------------------------


def _raw_instance(setup: Setup, **overrides) -> InventoryInstance:
    values = {
        "item_id": setup.item_id,
        "inventory_number": f"INV-{uuid.uuid4().int % 10**6:06d}",
        "state": "available",
        "storage_location_id": setup.location_id,
        "created_by": setup.user_id,
        **overrides,
    }
    return InventoryInstance(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "available", "storage_location_id": None},
        {"state": "in_repair", "storage_location_id": None},
        {"state": "issued"},
        {"state": "written_off"},
        {"state": "lost"},
        {"inventory_number": "INV-12"},
        {"inventory_number": "inv-000001"},
    ],
)
def test_instance_check_constraints(setup, overrides) -> None:
    with session_scope() as session:
        session.add(_raw_instance(setup, **overrides))
        with pytest.raises(IntegrityError):
            session.flush()


def test_inventory_number_is_unique(setup) -> None:
    with session_scope() as session:
        session.add(_raw_instance(setup, inventory_number="INV-000777"))
        session.flush()
        session.add(_raw_instance(setup, inventory_number="INV-000777"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_item_and_inventory_number_are_immutable(setup) -> None:
    instance_id = _receive(setup).id
    for attribute, value in (("inventory_number", "INV-999999"), ("item_id", uuid.uuid4())):
        with session_scope() as session:
            stored = session.get(InventoryInstance, instance_id)
            assert stored is not None
            setattr(stored, attribute, value)
            with pytest.raises(InventoryInstanceIdentityError):
                session.flush()


def test_repair_movements_require_an_instance(setup) -> None:
    with session_scope() as session:
        session.add(
            InventoryMovement(
                quantity=1,
                item_id=setup.item_id,
                movement_type="repair_start",
                created_by=setup.user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize(("movement_type", "cost"), [("transfer", 100), ("receipt", -1)])
def test_unit_cost_only_on_receipt_and_non_negative(setup, movement_type, cost) -> None:
    with session_scope() as session:
        session.add(
            InventoryMovement(
                quantity=1,
                item_id=setup.item_id,
                movement_type=movement_type,
                unit_cost_minor=cost,
                created_by=setup.user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_instance_movements_stay_immutable(setup) -> None:
    instance_id = _receive(setup).id
    with session_scope() as session:
        receipt = session.execute(
            sa.select(InventoryMovement).where(InventoryMovement.instance_id == instance_id)
        ).scalar_one()
        receipt.comment = "подделка"
        with pytest.raises(InventoryMovementImmutableError):
            session.flush()


def test_instance_cannot_be_physically_deleted(setup) -> None:
    instance_id = _receive(setup).id
    with session_scope() as session:
        with pytest.raises(IntegrityError):
            session.execute(sa.delete(InventoryInstance).where(InventoryInstance.id == instance_id))


# --- concurrency ------------------------------------------------------------------------


def test_concurrent_receipts_get_distinct_sequential_numbers(setup) -> None:
    results = _run_concurrently([lambda: _receive(setup) for _ in range(8)])
    assert all(isinstance(r, InventoryInstance) for r in results), results
    numbers = sorted(r.inventory_number for r in results)
    assert numbers == [f"INV-{n:06d}" for n in range(1, 9)]


def _operate(setup: Setup, instance_id: uuid.UUID, operation: Callable[..., Any]) -> str:
    with session_scope() as session:
        instance = get_instance(
            session, instance_id=instance_id, club_id=setup.club_id, for_update=True
        )
        assert instance is not None
        return operation(session, instance).state


def test_concurrent_write_offs_of_one_instance_succeed_once(setup) -> None:
    instance_id = _receive(setup).id

    def write_off(session, instance):
        return instance_service.write_off_instance(
            session, instance=instance, comment="Сломан", created_by=setup.user_id
        )

    results = _run_concurrently([lambda: _operate(setup, instance_id, write_off) for _ in range(4)])
    assert results.count("written_off") == 1, results
    assert sum(isinstance(r, InvalidInstanceTransitionError) for r in results) == 3
    with session_scope() as session:
        count = session.execute(
            sa.select(sa.func.count()).where(
                InventoryMovement.instance_id == instance_id,
                InventoryMovement.movement_type == "write_off",
            )
        ).scalar_one()
    assert count == 1


def test_concurrent_reversals_of_one_write_off_succeed_once(setup) -> None:
    instance_id = _receive(setup).id
    with session_scope() as session:
        instance = get_instance(
            session, instance_id=instance_id, club_id=setup.club_id, for_update=True
        )
        assert instance is not None
        instance_service.write_off_instance(
            session, instance=instance, comment="Ошибка", created_by=setup.user_id
        )
        write_off_id = session.execute(
            sa.select(InventoryMovement.id).where(
                InventoryMovement.instance_id == instance_id,
                InventoryMovement.movement_type == "write_off",
            )
        ).scalar_one()

    def reverse() -> str:
        with session_scope() as session:
            return instance_service.reverse_write_off(
                session,
                club_id=setup.club_id,
                movement_id=write_off_id,
                storage_location_id=None,
                created_by=setup.user_id,
            ).state

    results = _run_concurrently([reverse for _ in range(4)])
    assert results.count("available") == 1, results
    assert sum(isinstance(r, WriteOffAlreadyReversedError) for r in results) == 3
