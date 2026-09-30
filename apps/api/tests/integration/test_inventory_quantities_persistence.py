"""Database guarantees, atomicity, concurrency and journal/projection
consistency of Inventory Slice 3 — quantity movements (Issue #230;
docs/04-domain/inventory.md §6, §13, §16, §17), against real PostgreSQL."""

import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from app.db.identity import Club, Person, User
from app.db.inventory import (
    InventoryCategory,
    InventoryInstance,
    InventoryItem,
    InventoryItemStock,
    InventoryMovement,
    InventoryMovementImmutableError,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.db.session import session_scope
from app.inventory import quantities, service
from app.inventory.lifecycle import (
    ArchivedReferenceError,
    InsufficientStockError,
    InventoryRecordArchivedError,
    ItemHasStockError,
    LocationHasStockError,
    WriteOffAlreadyReversedError,
)
from app.inventory.queries import get_record

from .conftest import requires_postgres

pytestmark = requires_postgres


class Setup:
    def __init__(self, club_id, user_id, item_id, source_id, target_id) -> None:
        self.club_id: uuid.UUID = club_id
        self.user_id: uuid.UUID = user_id
        self.item_id: uuid.UUID = item_id
        self.source_id: uuid.UUID = source_id
        self.target_id: uuid.UUID = target_id


def _location(session, club_id, user_id) -> InventoryStorageLocation:
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
            name="Карабин",
            category_id=category.id,
            unit_id=unit_id,
            accounting_mode="quantity",
            status="active",
            created_by=user.id,
        )
        session.add(item)
        source = _location(session, club.id, user.id)
        target = _location(session, club.id, user.id)
        session.commit()
        return Setup(club.id, user.id, item.id, source.id, target.id)


def _receive(
    setup: Setup, quantity: int, location_id: uuid.UUID | None = None
) -> InventoryMovement:
    with session_scope() as session:
        movement = quantities.receive(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            storage_location_id=location_id or setup.source_id,
            quantity=quantity,
            unit_cost_minor=None,
            comment=None,
            created_by=setup.user_id,
        )
        session.expunge(movement)
        return movement


def _transfer(setup: Setup, quantity: int, source=None, target=None) -> InventoryMovement:
    with session_scope() as session:
        movement = quantities.transfer(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            from_location_id=source or setup.source_id,
            to_location_id=target or setup.target_id,
            quantity=quantity,
            comment=None,
            created_by=setup.user_id,
        )
        session.expunge(movement)
        return movement


def _write_off(setup: Setup, quantity: int, location_id=None) -> InventoryMovement:
    with session_scope() as session:
        movement = quantities.write_off(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            storage_location_id=location_id or setup.source_id,
            quantity=quantity,
            comment="Сломаны",
            created_by=setup.user_id,
        )
        session.expunge(movement)
        return movement


def _reverse(
    setup: Setup, movement_id: uuid.UUID, location_id: uuid.UUID | None = None
) -> InventoryMovement:
    with session_scope() as session:
        movement = quantities.reverse_write_off(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            movement_id=movement_id,
            storage_location_id=location_id,
            created_by=setup.user_id,
        )
        session.expunge(movement)
        return movement


def _stock(setup: Setup) -> dict[uuid.UUID, int]:
    with session_scope() as session:
        rows = session.execute(
            sa.select(InventoryItemStock.storage_location_id, InventoryItemStock.quantity).where(
                InventoryItemStock.item_id == setup.item_id
            )
        ).all()
    return {location_id: quantity for location_id, quantity in rows if quantity}


def _journal_stock(setup: Setup) -> dict[uuid.UUID, int]:
    """Stock recomputed from the movement journal alone."""
    totals: dict[uuid.UUID, int] = defaultdict(int)
    with session_scope() as session:
        movements = session.execute(
            sa.select(InventoryMovement).where(InventoryMovement.item_id == setup.item_id)
        ).scalars()
        for movement in movements:
            assert movement.quantity is not None
            if movement.from_location_id is not None:
                totals[movement.from_location_id] -= movement.quantity
            if movement.to_location_id is not None:
                totals[movement.to_location_id] += movement.quantity
    return {location_id: quantity for location_id, quantity in totals.items() if quantity}


def _run_concurrently(workers: list[Callable[[], Any]], timeout: float = 60) -> list[Any]:
    """Starts every worker at once; returns each result or raised exception.
    Fails the test if any worker is still running after `timeout` seconds
    (a hang); daemon threads keep a hung worker from blocking the run."""
    barrier = threading.Barrier(len(workers))
    results: list[Any] = [None] * len(workers)

    def run(index: int, worker: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            results[index] = worker()
        except Exception as exc:  # collected for the assertions
            results[index] = exc

    threads = [
        threading.Thread(target=run, args=(i, w), daemon=True) for i, w in enumerate(workers)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    hung = [i for i, thread in enumerate(threads) if thread.is_alive()]
    assert not hung, f"workers {hung} still running after {timeout}s"
    return results


def _succeeded(results: list[Any]) -> int:
    return sum(isinstance(r, InventoryMovement) for r in results)


# --- constraints ------------------------------------------------------------------------


def test_stock_quantity_cannot_be_negative(setup) -> None:
    with session_scope() as session:
        session.add(
            InventoryItemStock(
                item_id=setup.item_id, storage_location_id=setup.source_id, quantity=-1
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_one_stock_row_per_item_and_location(setup) -> None:
    with session_scope() as session:
        for _ in range(2):
            session.add(
                InventoryItemStock(
                    item_id=setup.item_id, storage_location_id=setup.source_id, quantity=1
                )
            )
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("field", ["item_id", "storage_location_id"])
def test_stock_references_are_foreign_keys(setup, field) -> None:
    values = {"item_id": setup.item_id, "storage_location_id": setup.source_id, "quantity": 1}
    values[field] = uuid.uuid4()
    with session_scope() as session:
        session.add(InventoryItemStock(**values))
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("quantity", [None, 0, -3])
def test_quantity_movement_needs_a_positive_quantity(setup, quantity) -> None:
    with session_scope() as session:
        session.add(
            InventoryMovement(
                item_id=setup.item_id,
                movement_type="receipt",
                quantity=quantity,
                to_location_id=setup.source_id,
                created_by=setup.user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_instance_movement_carries_no_quantity(setup) -> None:
    with session_scope() as session:
        item = session.get(InventoryItem, setup.item_id)
        assert item is not None
        instance = InventoryInstance(
            item_id=item.id,
            inventory_number="INV-000900",
            state="available",
            storage_location_id=setup.source_id,
            created_by=setup.user_id,
        )
        session.add(instance)
        session.flush()
        session.add(
            InventoryMovement(
                item_id=item.id,
                instance_id=instance.id,
                movement_type="receipt",
                quantity=1,
                created_by=setup.user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_quantity_movements_are_immutable(setup) -> None:
    movement_id = _receive(setup, 5).id
    with session_scope() as session:
        stored = session.get(InventoryMovement, movement_id)
        assert stored is not None
        stored.quantity = 50
        with pytest.raises(InventoryMovementImmutableError):
            session.flush()


# --- atomicity ---------------------------------------------------------------------------


def test_failed_operations_leave_journal_and_stock_unchanged(setup) -> None:
    _receive(setup, 5)
    with pytest.raises(InsufficientStockError):
        _transfer(setup, 6)
    with pytest.raises(InsufficientStockError):
        _write_off(setup, 6)
    with session_scope() as session:
        count = session.execute(
            sa.select(sa.func.count()).where(InventoryMovement.item_id == setup.item_id)
        ).scalar_one()
    assert count == 1
    assert _stock(setup) == {setup.source_id: 5}


# --- concurrency ---------------------------------------------------------------------------


def test_parallel_receipts_into_one_pair(setup) -> None:
    results = _run_concurrently([lambda: _receive(setup, 1, setup.target_id) for _ in range(8)])
    assert _succeeded(results) == 8, results
    assert _stock(setup) == {setup.target_id: 8}


def test_parallel_transfers_never_overdraw(setup) -> None:
    _receive(setup, 5)
    results = _run_concurrently([lambda: _transfer(setup, 1) for _ in range(8)])
    assert _succeeded(results) == 5, results
    assert sum(isinstance(r, InsufficientStockError) for r in results) == 3
    assert _stock(setup) == {setup.target_id: 5}


def test_parallel_write_offs_never_overdraw(setup) -> None:
    _receive(setup, 5)
    results = _run_concurrently([lambda: _write_off(setup, 2) for _ in range(6)])
    assert _succeeded(results) == 2, results
    assert _stock(setup) == {setup.source_id: 1}


def test_transfer_and_write_off_compete_for_one_stock(setup) -> None:
    _receive(setup, 3)
    results = _run_concurrently([lambda: _transfer(setup, 3), lambda: _write_off(setup, 3)])
    assert _succeeded(results) == 1, results
    assert sum(_stock(setup).values()) in (0, 3)
    assert min(_stock(setup).values(), default=0) >= 0


def test_opposite_transfers_do_not_deadlock(setup) -> None:
    _receive(setup, 10, setup.source_id)
    _receive(setup, 10, setup.target_id)
    workers: list[Callable[[], Any]] = []
    for _ in range(5):
        workers.append(lambda: _transfer(setup, 1, setup.source_id, setup.target_id))
        workers.append(lambda: _transfer(setup, 1, setup.target_id, setup.source_id))
    results = _run_concurrently(workers)
    assert _succeeded(results) == 10, results
    assert _stock(setup) == {setup.source_id: 10, setup.target_id: 10}


def test_two_reversals_of_one_write_off(setup) -> None:
    _receive(setup, 4)
    write_off_id = _write_off(setup, 4).id
    results = _run_concurrently([lambda: _reverse(setup, write_off_id) for _ in range(4)])
    assert _succeeded(results) == 1, results
    assert sum(isinstance(r, WriteOffAlreadyReversedError) for r in results) == 3
    assert _stock(setup) == {setup.source_id: 4}


def _archive_item(setup: Setup) -> str:
    with session_scope() as session:
        item = get_record(
            session, InventoryItem, record_id=setup.item_id, club_id=setup.club_id, for_update=True
        )
        assert item is not None
        return service.archive_item(session, item=item, updated_by=setup.user_id).status


def _archive_location(setup: Setup, location_id: uuid.UUID) -> str:
    with session_scope() as session:
        location = get_record(
            session,
            InventoryStorageLocation,
            record_id=location_id,
            club_id=setup.club_id,
            for_update=True,
        )
        assert location is not None
        return service.archive_location(session, location=location, updated_by=setup.user_id).status


def test_archive_item_races_receipt(setup) -> None:
    results = _run_concurrently([lambda: _archive_item(setup), lambda: _receive(setup, 2)])
    archived, received = results
    # Either the receipt happened first (archive refused) or the archive did
    # (receipt refused) — never an archived item holding stock.
    if isinstance(received, InventoryMovement):
        assert isinstance(archived, ItemHasStockError)
    else:
        assert archived == "archived"
    with session_scope() as session:
        item = session.get(InventoryItem, setup.item_id)
        assert item is not None
        assert not (item.status == "archived" and sum(_stock(setup).values()) > 0)


def test_archive_item_races_transfer(setup) -> None:
    _receive(setup, 2)
    results = _run_concurrently([lambda: _archive_item(setup), lambda: _transfer(setup, 2)])
    assert isinstance(results[0], ItemHasStockError), results
    assert isinstance(results[1], InventoryMovement), results


def test_archive_location_races_receipt_and_transfer(setup) -> None:
    _receive(setup, 2)
    results = _run_concurrently(
        [
            lambda: _archive_location(setup, setup.target_id),
            lambda: _receive(setup, 1, setup.target_id),
            lambda: _transfer(setup, 1),
        ]
    )
    with session_scope() as session:
        location = session.get(InventoryStorageLocation, setup.target_id)
        assert location is not None
        archived = location.status == "archived"
    target_stock = _stock(setup).get(setup.target_id, 0)
    assert not (archived and target_stock > 0), results
    if not archived:
        assert isinstance(results[0], LocationHasStockError), results


# --- journal / projection consistency --------------------------------------------------------


def test_projection_equals_the_sum_of_movements(setup) -> None:
    with session_scope() as session:
        third = _location(session, setup.club_id, setup.user_id).id
        session.commit()
    _receive(setup, 10)
    _receive(setup, 4, setup.target_id)
    _transfer(setup, 3)
    _transfer(setup, 2, setup.target_id, third)
    first = _write_off(setup, 1)
    _write_off(setup, 2, third)
    _reverse(setup, first.id)
    _run_concurrently(
        [lambda: _transfer(setup, 1) for _ in range(3)]
        + [lambda: _receive(setup, 1) for _ in range(3)]
    )
    assert _stock(setup) == _journal_stock(setup)
    assert all(quantity > 0 for quantity in _stock(setup).values())


def _location_with_id(setup: Setup, location_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        session.add(
            InventoryStorageLocation(
                id=location_id,
                club_id=setup.club_id,
                name=f"Полка {uuid.uuid4().hex[:6]}",
                status="active",
                created_by=setup.user_id,
            )
        )
        session.commit()
    return location_id


@pytest.mark.parametrize("round_", range(5))
def test_reversal_to_a_new_location_and_transfer_do_not_deadlock(setup, round_) -> None:
    """The write-off's original location is archived, so the reversal goes
    to a new active location. The new location sorts before the original
    one — the reverse of the order the reversal names them in — while a
    transfer and archive attempts lock the same two rows concurrently."""
    new_id = _location_with_id(setup, uuid.UUID(f"0{uuid.uuid4().hex[1:]}"))
    original_id = _location_with_id(setup, uuid.UUID(f"f{uuid.uuid4().hex[1:]}"))
    _receive(setup, 2, original_id)
    write_off_id = _write_off(setup, 2, original_id).id
    assert _archive_location(setup, original_id) == "archived"
    _receive(setup, 5, new_id)

    results = _run_concurrently(
        [
            lambda: _reverse(setup, write_off_id, new_id),
            lambda: _transfer(setup, 1, new_id, original_id),
            lambda: _transfer(setup, 1, new_id, setup.target_id),
            lambda: _archive_location(setup, original_id),
            lambda: _archive_location(setup, new_id),
        ],
        timeout=30,
    )

    assert not [r for r in results if isinstance(r, DBAPIError)], results
    reversal, into_archived, transfer, archive_original, archive_new = results
    assert isinstance(reversal, InventoryMovement), results
    assert reversal.to_location_id == new_id and reversal.quantity == 2
    assert isinstance(into_archived, ArchivedReferenceError), results
    assert isinstance(transfer, InventoryMovement), results
    assert isinstance(archive_original, InventoryRecordArchivedError), results
    assert isinstance(archive_new, LocationHasStockError), results
    assert _stock(setup) == {new_id: 6, setup.target_id: 1}
    assert _stock(setup) == _journal_stock(setup)


def _wait_for_a_lock_wait(timeout: float = 10) -> None:
    """Returns once another backend waits on a storage-location row lock."""
    deadline = time.monotonic() + timeout
    with session_scope() as monitor:
        while time.monotonic() < deadline:
            waiting = monitor.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                    " AND datname = current_database()"
                    " AND query ILIKE '%inventory_storage_locations%'"
                )
            ).scalar_one()
            monitor.rollback()  # a fresh pg_stat_activity snapshot next time
            if waiting:
                return
            time.sleep(0.02)
    pytest.fail(f"no lock wait on a storage location within {timeout}s")


def test_reversal_locks_locations_in_the_same_order_as_transfer(setup) -> None:
    """Deterministic: with the (lower-id) new location held, the reversal
    must wait on it before touching the (higher-id) archived original —
    the id order `transfer` uses — so it holds no lock on the original."""
    new_id = _location_with_id(setup, uuid.UUID(f"0{uuid.uuid4().hex[1:]}"))
    original_id = _location_with_id(setup, uuid.UUID(f"f{uuid.uuid4().hex[1:]}"))
    _receive(setup, 2, original_id)
    write_off_id = _write_off(setup, 2, original_id).id
    assert _archive_location(setup, original_id) == "archived"
    results: list[Any] = []

    def reverse() -> None:
        try:
            results.append(_reverse(setup, write_off_id, new_id))
        except Exception as exc:  # collected for the assertions
            results.append(exc)

    reversal = threading.Thread(target=reverse, daemon=True)
    with session_scope() as blocker:
        blocker.execute(
            sa.select(InventoryStorageLocation.id)
            .where(InventoryStorageLocation.id == new_id)
            .with_for_update()
        )
        reversal.start()
        _wait_for_a_lock_wait()
        with session_scope() as probe:
            try:
                probe.execute(
                    sa.select(InventoryStorageLocation.id)
                    .where(InventoryStorageLocation.id == original_id)
                    .with_for_update(nowait=True)
                )
            except OperationalError:
                pytest.fail("the reversal locked the original location out of id order")
            probe.rollback()
        blocker.rollback()
    reversal.join(timeout=30)
    assert not reversal.is_alive(), "the reversal is still running after 30s"
    assert len(results) == 1 and isinstance(results[0], InventoryMovement), results
    assert _stock(setup) == {new_id: 2}
