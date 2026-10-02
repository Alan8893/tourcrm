"""Database guarantees, atomicity, concurrency and journal/projection
consistency of Inventory Slice 4 — issue / return (Issue #236;
docs/04-domain/inventory.md §14, §17), against real PostgreSQL.

Concurrency tests are deterministic where the outcome depends on lock
order: a blocker transaction holds a row lock, the operation under test is
started in a thread, the test waits until PostgreSQL reports that thread's
backend waiting on a lock, then releases the blocker and checks the result.
"""

import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from app.db.events import Event
from app.db.groups import Group
from app.db.identity import Club, ClubMembership, Person, User
from app.db.inventory import (
    InventoryCategory,
    InventoryInstance,
    InventoryIssue,
    InventoryIssueLine,
    InventoryIssueLineImmutableError,
    InventoryItem,
    InventoryItemStock,
    InventoryMovement,
    InventoryMovementImmutableError,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.db.session import session_scope
from app.inventory import instances, issues, quantities, service
from app.inventory.issues import IssueLineRequest, QuantityReturnRequest
from app.inventory.lifecycle import (
    InsufficientStockError,
    InvalidInstanceTransitionError,
    InvalidRecipientError,
    IssueCancelledError,
    IssueFullyReturnedError,
    IssueLineOutstandingError,
    IssueLineRemovedError,
    ItemHasOutstandingIssuesError,
    ReturnExceedsOutstandingError,
)
from app.inventory.queries import get_record
from app.inventory.service import InventoryReferenceNotFoundError

from .conftest import requires_postgres

pytestmark = requires_postgres

_NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


class Setup:
    def __init__(self, **ids: uuid.UUID) -> None:
        self.club_id: uuid.UUID = ids["club_id"]
        self.user_id: uuid.UUID = ids["user_id"]
        self.member_id: uuid.UUID = ids["member_id"]
        self.item_id: uuid.UUID = ids["item_id"]
        self.instance_item_id: uuid.UUID = ids["instance_item_id"]
        self.category_id: uuid.UUID = ids["category_id"]
        self.unit_id: uuid.UUID = ids["unit_id"]


def _location_row(setup: Setup, location_id: uuid.UUID | None = None) -> uuid.UUID:
    with session_scope() as session:
        location = InventoryStorageLocation(
            id=location_id or uuid.uuid4(),
            club_id=setup.club_id,
            name=f"Полка {uuid.uuid4().hex[:6]}",
            status="active",
            created_by=setup.user_id,
        )
        session.add(location)
        session.commit()
        return location.id


@pytest.fixture
def setup() -> Setup:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        admin_person = Person(last_name="Тестов", first_name="Админ")
        member = Person(last_name="Тестов", first_name="Участник")
        session.add_all([club, admin_person, member])
        session.flush()
        session.add(
            ClubMembership(
                club_id=club.id,
                person_id=member.id,
                membership_type="member",
                status="active",
                joined_at=_NOW - timedelta(days=1),
            )
        )
        user = User(
            person=admin_person,
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
        items = [
            InventoryItem(
                club_id=club.id,
                name=f"Позиция {mode}",
                category_id=category.id,
                unit_id=unit_id,
                accounting_mode=mode,
                status="active",
                created_by=user.id,
            )
            for mode in ("quantity", "instance")
        ]
        session.add_all(items)
        session.commit()
        return Setup(
            club_id=club.id,
            user_id=user.id,
            member_id=member.id,
            item_id=items[0].id,
            instance_item_id=items[1].id,
            category_id=category.id,
            unit_id=unit_id,
        )


# --- operations ------------------------------------------------------------------------


def _receive(setup: Setup, location_id: uuid.UUID, quantity: int, item_id=None) -> None:
    with session_scope() as session:
        quantities.receive(
            session,
            club_id=setup.club_id,
            item_id=item_id or setup.item_id,
            storage_location_id=location_id,
            quantity=quantity,
            unit_cost_minor=None,
            comment=None,
            created_by=setup.user_id,
        )


def _instance(setup: Setup, location_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        return instances.receive_instance(
            session,
            club_id=setup.club_id,
            item_id=setup.instance_item_id,
            storage_location_id=location_id,
            unit_cost_minor=None,
            manufacturer_barcode=None,
            manufacturer_serial_number=None,
            description=None,
            created_by=setup.user_id,
        ).id


def _issue(setup: Setup, *lines: IssueLineRequest) -> uuid.UUID:
    with session_scope() as session:
        return issues.create_issue(
            session,
            club_id=setup.club_id,
            recipient_type="member",
            recipient_id=setup.member_id,
            event_id=None,
            planned_return_date=None,
            comment=None,
            lines=list(lines),
            created_by=setup.user_id,
        ).id


def _quantity(setup: Setup, quantity: int) -> IssueLineRequest:
    return IssueLineRequest(item_id=setup.item_id, quantity=quantity)


def _instances(setup: Setup, *instance_ids: uuid.UUID) -> IssueLineRequest:
    return IssueLineRequest(item_id=setup.instance_item_id, instance_ids=tuple(instance_ids))


def _add(setup: Setup, issue_id: uuid.UUID, *lines: IssueLineRequest) -> str:
    with session_scope() as session:
        return issues.add_lines(
            session, club_id=setup.club_id, issue_id=issue_id, lines=list(lines),
            created_by=setup.user_id,
        ).status


def _lines(issue_id: uuid.UUID) -> dict[uuid.UUID, issues.LineBalance]:
    with session_scope() as session:
        return {b.item_id: b for b in issues.line_balances(session, [issue_id]).values()}


def _return(
    setup: Setup,
    issue_id: uuid.UUID,
    location_id: uuid.UUID,
    quantity: int = 0,
    instance_ids: tuple[uuid.UUID, ...] = (),
) -> str:
    entries = []
    if quantity:
        line = _lines(issue_id)[setup.item_id]
        entries.append(QuantityReturnRequest(line_id=line.line_id, quantity=quantity))
    with session_scope() as session:
        return issues.return_items(
            session,
            club_id=setup.club_id,
            issue_id=issue_id,
            storage_location_id=location_id,
            quantities=entries,
            instance_ids=list(instance_ids),
            comment=None,
            created_by=setup.user_id,
        ).status


def _cancel(setup: Setup, issue_id: uuid.UUID, location_id: uuid.UUID) -> str:
    with session_scope() as session:
        return issues.cancel_issue(
            session,
            club_id=setup.club_id,
            issue_id=issue_id,
            storage_location_id=location_id,
            cancelled_by=setup.user_id,
        ).status


def _lost(setup: Setup, issue_id, instance_id, location_id) -> str:
    with session_scope() as session:
        return issues.report_lost(
            session,
            club_id=setup.club_id,
            issue_id=issue_id,
            instance_id=instance_id,
            storage_location_id=location_id,
            reason="Утерян",
            comment=None,
            created_by=setup.user_id,
        ).status


def _transfer(setup: Setup, source, target, quantity: int) -> str:
    with session_scope() as session:
        return quantities.transfer(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            from_location_id=source,
            to_location_id=target,
            quantity=quantity,
            comment=None,
            created_by=setup.user_id,
        ).movement_type


def _write_off(setup: Setup, location_id, quantity: int) -> str:
    with session_scope() as session:
        return quantities.write_off(
            session,
            club_id=setup.club_id,
            item_id=setup.item_id,
            storage_location_id=location_id,
            quantity=quantity,
            comment="износ",
            created_by=setup.user_id,
        ).movement_type


def _archive_item(setup: Setup, item_id=None) -> str:
    with session_scope() as session:
        item = get_record(
            session, InventoryItem, record_id=item_id or setup.item_id, club_id=setup.club_id,
            for_update=True,
        )
        assert item is not None
        return service.archive_item(session, item=item, updated_by=setup.user_id).status


def _stock(setup: Setup) -> dict[uuid.UUID, int]:
    with session_scope() as session:
        rows = session.execute(
            sa.select(InventoryItemStock).where(InventoryItemStock.item_id == setup.item_id)
        ).scalars()
        return {row.storage_location_id: row.quantity for row in rows if row.quantity}


def _journal_stock(setup: Setup) -> dict[uuid.UUID, int]:
    """Stock recomputed from the journal alone: every quantity movement
    leaves its `from` location and enters its `to` location."""
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


def _instance_state(instance_id: uuid.UUID) -> tuple[str, uuid.UUID | None]:
    with session_scope() as session:
        instance = session.get(InventoryInstance, instance_id)
        assert instance is not None
        return instance.state, instance.storage_location_id


def _run_concurrently(workers: list[Callable[[], Any]], timeout: float = 60) -> list[Any]:
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


class Background:
    """Runs one operation in a thread; `result()` joins it."""

    def __init__(self, operation: Callable[[], Any]) -> None:
        self._result: list[Any] = []

        def run() -> None:
            try:
                self._result.append(operation())
            except Exception as exc:  # collected for the assertions
                self._result.append(exc)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def result(self, timeout: float = 30) -> Any:
        self._thread.join(timeout=timeout)
        assert not self._thread.is_alive(), f"still running after {timeout}s"
        return self._result[0]


def _wait_for_lock_waiters(count: int = 1, timeout: float = 10) -> None:
    """Returns once `count` other backends wait on a lock."""
    deadline = time.monotonic() + timeout
    with session_scope() as monitor:
        while time.monotonic() < deadline:
            waiting = monitor.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                    " AND datname = current_database()"
                )
            ).scalar_one()
            monitor.rollback()
            if waiting >= count:
                return
            time.sleep(0.02)
    pytest.fail(f"fewer than {count} lock waiters within {timeout}s")


def _lock_stock_row(session, setup: Setup, location_id: uuid.UUID) -> None:
    session.execute(
        sa.select(InventoryItemStock.id)
        .where(
            InventoryItemStock.item_id == setup.item_id,
            InventoryItemStock.storage_location_id == location_id,
        )
        .with_for_update()
    )


def _lock_issue_row(session, issue_id: uuid.UUID) -> None:
    session.execute(
        sa.select(InventoryIssue.id).where(InventoryIssue.id == issue_id).with_for_update()
    )


# --- constraints -----------------------------------------------------------------------


def _issue_row(setup: Setup, **fields) -> InventoryIssue:
    values = {
        "club_id": setup.club_id,
        "recipient_type": "member",
        "recipient_person_id": setup.member_id,
        "status": "issued",
        "created_by": setup.user_id,
        **fields,
    }
    return InventoryIssue(**values)


@pytest.mark.parametrize(
    "fields",
    [
        {"recipient_type": "guardian"},
        {"recipient_type": "instructor"},  # person column set for an instructor
        {"recipient_person_id": None},
        {"recipient_group_id": "group"},  # two recipient columns
        {"status": "returned"},
        {"status": "cancelled"},  # without cancelled_at/by
        {"cancelled_at": _NOW},  # issued with a cancellation
    ],
)
def test_issue_checks(setup, fields) -> None:
    with session_scope() as session:
        if fields.get("recipient_group_id") == "group":
            group = Group(club_id=setup.club_id, name="Г", status="active", valid_from=_NOW)
            session.add(group)
            session.flush()
            fields = {"recipient_group_id": group.id}
        session.add(_issue_row(setup, **fields))
        with pytest.raises(IntegrityError):
            session.flush()


def test_one_line_per_item_and_issue(setup) -> None:
    with session_scope() as session:
        issue = _issue_row(setup)
        session.add(issue)
        session.flush()
        for _ in range(2):
            session.add(
                InventoryIssueLine(
                    issue_id=issue.id, item_id=setup.item_id, created_by=setup.user_id
                )
            )
        with pytest.raises(IntegrityError) as raised:
            session.flush()
        assert "uq_inventory_issue_lines_active_issue_id_item_id" in str(raised.value)


def _movement_on_line(setup: Setup, movement_type: str, *, line: bool, item_id=None):
    with session_scope() as session:
        issue = _issue_row(setup)
        session.add(issue)
        session.flush()
        issue_line = InventoryIssueLine(
            issue_id=issue.id, item_id=setup.item_id, created_by=setup.user_id
        )
        session.add(issue_line)
        session.flush()
        session.add(
            InventoryMovement(
                item_id=item_id or setup.item_id,
                movement_type=movement_type,
                quantity=1,
                issue_line_id=issue_line.id if line else None,
                created_by=setup.user_id,
            )
        )
        session.flush()


@pytest.mark.parametrize("movement_type", ["issue", "return"])
def test_issue_and_return_need_a_line(setup, movement_type) -> None:
    with pytest.raises(IntegrityError) as raised:
        _movement_on_line(setup, movement_type, line=False)
    assert "ck_inventory_movements_issue_line_reference" in str(raised.value)
    _movement_on_line(setup, movement_type, line=True)


@pytest.mark.parametrize(
    "movement_type", ["receipt", "transfer", "adjustment", "writeoff_reversal"]
)
def test_other_movements_carry_no_line(setup, movement_type) -> None:
    with pytest.raises(IntegrityError):
        _movement_on_line(setup, movement_type, line=True)


def test_write_off_may_carry_a_line(setup) -> None:
    _movement_on_line(setup, "write_off", line=True)


def test_movement_line_must_be_of_the_same_item(setup) -> None:
    with pytest.raises(IntegrityError) as raised:
        _movement_on_line(setup, "issue", line=True, item_id=setup.instance_item_id)
    assert "fk_inventory_movements_issue_line_id_item_id" in str(raised.value)


def test_issue_lines_and_issue_movements_are_immutable(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 3)
    issue_id = _issue(setup, _quantity(setup, 2))
    with session_scope() as session:
        line = session.execute(
            sa.select(InventoryIssueLine).where(InventoryIssueLine.issue_id == issue_id)
        ).scalar_one()
        session.delete(line)
        with pytest.raises(InventoryIssueLineImmutableError):
            session.flush()
    with session_scope() as session:
        movement = session.execute(
            sa.select(InventoryMovement).where(InventoryMovement.movement_type == "issue")
        ).scalar_one()
        movement.quantity = 1
        with pytest.raises(InventoryMovementImmutableError):
            session.flush()
    with session_scope() as session:
        movement = session.execute(
            sa.select(InventoryMovement).where(InventoryMovement.movement_type == "issue")
        ).scalar_one()
        session.delete(movement)
        with pytest.raises(InventoryMovementImmutableError):
            session.flush()
    assert _lines(issue_id)[setup.item_id].issued == 2


def test_issue_rows_cannot_be_deleted_while_referenced(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 1)
    issue_id = _issue(setup, _quantity(setup, 1))
    with session_scope() as session:
        with pytest.raises(IntegrityError):
            session.execute(sa.delete(InventoryIssue).where(InventoryIssue.id == issue_id))


# --- atomicity / scoping ---------------------------------------------------------------


def test_failed_issue_leaves_nothing_behind(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 5)
    available = _instance(setup, location)
    written_off = _instance(setup, location)
    with session_scope() as session:
        instances.write_off_instance(
            session,
            instance=session.get(InventoryInstance, written_off),
            comment="x",
            created_by=setup.user_id,
        )
    with pytest.raises(InvalidInstanceTransitionError):
        _issue(setup, _quantity(setup, 2), _instances(setup, available, written_off))
    assert _stock(setup) == {location: 5}
    assert _instance_state(available) == ("available", location)
    with session_scope() as session:
        assert session.execute(sa.select(sa.func.count()).select_from(InventoryIssue)).scalar() == 0
        assert (
            session.execute(
                sa.select(sa.func.count())
                .select_from(InventoryMovement)
                .where(InventoryMovement.movement_type == "issue")
            ).scalar()
            == 0
        )


def test_failed_lost_leaves_the_instance_issued(setup) -> None:
    location = _location_row(setup)
    instance_id = _instance(setup, location)
    issue_id = _issue(setup, _instances(setup, instance_id))
    with pytest.raises(InventoryReferenceNotFoundError):
        _lost(setup, issue_id, instance_id, uuid.uuid4())
    assert _instance_state(instance_id) == ("issued", None)


def test_references_of_another_club_are_rejected(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 5)
    with session_scope() as session:
        other = Club(name=f"Other {uuid.uuid4().hex[:6]}", status="active")
        session.add(other)
        session.flush()
        other_item = InventoryItem(
            club_id=other.id, name="Чужая", category_id=setup.category_id,
            unit_id=setup.unit_id, accounting_mode="quantity", status="active",
            created_by=setup.user_id,
        )
        other_group = Group(club_id=other.id, name="Чужая", status="active", valid_from=_NOW)
        other_event = Event(
            club_id=other.id, event_type="trip", title="Поход", start_at=_NOW,
            end_at=_NOW + timedelta(days=1), timezone="Europe/Moscow", status="published",
        )
        session.add_all([other_item, other_group, other_event])
        session.commit()
        other_item_id = other_item.id
        other_group_id, other_event_id = other_group.id, other_event.id

    with pytest.raises(InventoryReferenceNotFoundError):
        _issue(setup, IssueLineRequest(item_id=other_item_id, quantity=1))

    def create(**fields):
        with session_scope() as session:
            values = {
                "recipient_type": "member",
                "recipient_id": setup.member_id,
                "event_id": None,
                **fields,
            }
            issues.create_issue(
                session, club_id=setup.club_id, planned_return_date=None, comment=None,
                lines=[_quantity(setup, 1)], created_by=setup.user_id, **values,
            )

    with pytest.raises(InvalidRecipientError):
        create(recipient_type="group", recipient_id=other_group_id)
    with pytest.raises(InventoryReferenceNotFoundError):
        create(event_id=other_event_id)
    create(event_id=None)
    assert _stock(setup) == {location: 4}


# --- journal / projection consistency --------------------------------------------------


def test_projection_equals_the_journal_after_issue_return_and_cancel(setup) -> None:
    a, b, c = (_location_row(setup) for _ in range(3))
    _receive(setup, a, 7)
    _receive(setup, b, 5)
    first = _issue(setup, _quantity(setup, 10))
    assert _stock(setup) == {b: 2}
    _return(setup, first, c, 6)
    second = _issue(setup, _quantity(setup, 7))
    _return(setup, first, a, 3)
    _cancel(setup, second, b)
    _return(setup, first, c, 1)
    assert _stock(setup) == _journal_stock(setup)
    assert sum(_stock(setup).values()) == 12
    assert _lines(first)[setup.item_id].outstanding == 0


# --- concurrency -----------------------------------------------------------------------


def test_concurrent_issues_of_one_stock_never_overdraw(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 5)
    results = _run_concurrently([lambda: _issue(setup, _quantity(setup, 4)) for _ in range(3)])
    assert sum(isinstance(r, uuid.UUID) for r in results) == 1, results
    assert sum(isinstance(r, InsufficientStockError) for r in results) == 2, results
    assert _stock(setup) == {location: 1}
    assert _stock(setup) == _journal_stock(setup)


def test_issue_waits_for_a_stock_lock_and_rereads(setup) -> None:
    """Deterministic: the stock row is held; the issue waits on it, and
    after the holder took 3 units the issue sees only what is left."""
    location = _location_row(setup)
    _receive(setup, location, 5)
    with session_scope() as blocker:
        _lock_stock_row(blocker, setup, location)
        blocker.execute(
            sa.update(InventoryItemStock)
            .where(InventoryItemStock.item_id == setup.item_id)
            .values(quantity=InventoryItemStock.quantity - 3)
        )
        issue = Background(lambda: _issue(setup, _quantity(setup, 4)))
        _wait_for_lock_waiters()
        blocker.rollback()  # nothing taken after all
    assert isinstance(issue.result(), uuid.UUID)
    assert _stock(setup) == {location: 1}


def test_concurrent_issues_of_one_instance(setup) -> None:
    location = _location_row(setup)
    instance_id = _instance(setup, location)
    results = _run_concurrently(
        [lambda: _issue(setup, _instances(setup, instance_id)) for _ in range(3)]
    )
    assert sum(isinstance(r, uuid.UUID) for r in results) == 1, results
    assert sum(isinstance(r, InvalidInstanceTransitionError) for r in results) == 2, results
    assert _instance_state(instance_id) == ("issued", None)


def test_issue_of_an_instance_waits_for_its_lock(setup) -> None:
    location = _location_row(setup)
    instance_id = _instance(setup, location)
    with session_scope() as blocker:
        blocker.execute(
            sa.select(InventoryInstance.id)
            .where(InventoryInstance.id == instance_id)
            .with_for_update()
        )
        first = Background(lambda: _issue(setup, _instances(setup, instance_id)))
        _wait_for_lock_waiters()
        blocker.commit()
    assert isinstance(first.result(), uuid.UUID)
    with pytest.raises(InvalidInstanceTransitionError):
        _issue(setup, _instances(setup, instance_id))


def test_issue_locks_stock_rows_in_location_id_order(setup) -> None:
    """Deterministic: the allocation takes the larger high-id stock first,
    but the locks are taken in id order — with the low-id row held, the
    issue waits there and holds no lock on the high-id row."""
    low = _location_row(setup, uuid.UUID(f"0{uuid.uuid4().hex[1:]}"))
    high = _location_row(setup, uuid.UUID(f"f{uuid.uuid4().hex[1:]}"))
    _receive(setup, low, 2)
    _receive(setup, high, 5)
    with session_scope() as blocker:
        _lock_stock_row(blocker, setup, low)
        issue = Background(lambda: _issue(setup, _quantity(setup, 6)))
        _wait_for_lock_waiters()
        with session_scope() as probe:
            try:
                probe.execute(
                    sa.select(InventoryItemStock.id)
                    .where(
                        InventoryItemStock.item_id == setup.item_id,
                        InventoryItemStock.storage_location_id == high,
                    )
                    .with_for_update(nowait=True)
                )
            except OperationalError:
                pytest.fail("the issue locked the high-id stock row out of order")
            probe.rollback()
        blocker.rollback()
    assert isinstance(issue.result(), uuid.UUID)
    assert _stock(setup) == {low: 1}


def test_issue_sees_stock_moved_concurrently_into_a_new_location(setup) -> None:
    """Deterministic: while the issue waits on the source row, the holder
    moves all of it into a location that had no stock row yet. The issue
    re-reads the item's stock rows and takes the stock there instead of
    failing with insufficient stock."""
    source = _location_row(setup, uuid.UUID(f"0{uuid.uuid4().hex[1:]}"))
    target = _location_row(setup, uuid.UUID(f"f{uuid.uuid4().hex[1:]}"))
    _receive(setup, source, 5)
    with session_scope() as blocker:
        _lock_stock_row(blocker, setup, source)
        issue = Background(lambda: _issue(setup, _quantity(setup, 5)))
        _wait_for_lock_waiters()
        blocker.execute(
            sa.update(InventoryItemStock)
            .where(InventoryItemStock.item_id == setup.item_id)
            .values(quantity=0)
        )
        blocker.add(
            InventoryItemStock(item_id=setup.item_id, storage_location_id=target, quantity=5)
        )
        blocker.add(
            InventoryMovement(
                item_id=setup.item_id, movement_type="transfer", quantity=5,
                from_location_id=source, to_location_id=target, created_by=setup.user_id,
            )
        )
        blocker.commit()
    assert isinstance(issue.result(), uuid.UUID), issue.result()
    assert _stock(setup) == {}
    assert _stock(setup) == _journal_stock(setup)


def test_issue_versus_transfer_and_write_off(setup) -> None:
    a, b = _location_row(setup), _location_row(setup)
    _receive(setup, a, 6)
    _receive(setup, b, 2)
    results = _run_concurrently(
        [
            lambda: _issue(setup, _quantity(setup, 5)),
            lambda: _transfer(setup, a, b, 4),
            lambda: _transfer(setup, b, a, 2),
            lambda: _write_off(setup, a, 3),
            lambda: _write_off(setup, b, 3),
        ]
    )
    assert not [r for r in results if isinstance(r, DBAPIError)], results
    assert all(r is not None for r in results)
    assert all(q >= 0 for q in _stock(setup).values())
    assert _stock(setup) == _journal_stock(setup)


def test_issue_versus_write_off_of_the_same_stock(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 5)
    with session_scope() as blocker:
        _lock_stock_row(blocker, setup, location)
        issue = Background(lambda: _issue(setup, _quantity(setup, 5)))
        write_off = Background(lambda: _write_off(setup, location, 5))
        _wait_for_lock_waiters(2)
        blocker.rollback()
    results = [issue.result(), write_off.result()]
    assert sum(isinstance(r, InsufficientStockError) for r in results) == 1, results
    assert _stock(setup) == {}
    assert _stock(setup) == _journal_stock(setup)


def test_double_return_is_impossible(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 4)
    instance_id = _instance(setup, location)
    issue_id = _issue(setup, _quantity(setup, 4), _instances(setup, instance_id))
    with session_scope() as blocker:
        _lock_issue_row(blocker, issue_id)
        first = Background(lambda: _return(setup, issue_id, location, 4, (instance_id,)))
        second = Background(lambda: _return(setup, issue_id, location, 4, (instance_id,)))
        _wait_for_lock_waiters(2)
        blocker.rollback()
    results = [first.result(), second.result()]
    assert results.count("issued") == 1, results
    assert sum(isinstance(r, IssueFullyReturnedError) for r in results) == 1, results
    assert _stock(setup) == {location: 4}
    assert _instance_state(instance_id) == ("available", location)
    with session_scope() as session:
        returns = session.execute(
            sa.select(sa.func.count())
            .select_from(InventoryMovement)
            .where(InventoryMovement.movement_type == "return")
        ).scalar()
    assert returns == 2


def test_concurrent_partial_returns_never_exceed_the_issued_quantity(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 10)
    issue_id = _issue(setup, _quantity(setup, 10))
    results = _run_concurrently([lambda: _return(setup, issue_id, location, 4) for _ in range(3)])
    assert results.count("issued") == 2, results
    assert sum(isinstance(r, ReturnExceedsOutstandingError) for r in results) == 1, results
    assert _lines(issue_id)[setup.item_id].outstanding == 2
    assert _stock(setup) == {location: 8}


def test_double_cancel_is_impossible(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 3)
    issue_id = _issue(setup, _quantity(setup, 3))
    with session_scope() as blocker:
        _lock_issue_row(blocker, issue_id)
        first = Background(lambda: _cancel(setup, issue_id, location))
        second = Background(lambda: _cancel(setup, issue_id, location))
        _wait_for_lock_waiters(2)
        blocker.rollback()
    results = [first.result(), second.result()]
    assert results.count("cancelled") == 1, results
    assert sum(isinstance(r, IssueCancelledError) for r in results) == 1, results
    assert _stock(setup) == {location: 3}


def test_cancel_versus_return_on_one_issue(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 5)
    issue_id = _issue(setup, _quantity(setup, 5))
    results = _run_concurrently(
        [lambda: _cancel(setup, issue_id, location), lambda: _return(setup, issue_id, location, 5)]
    )
    assert sum(isinstance(r, str) for r in results) == 1, results
    assert _stock(setup) == {location: 5}
    assert _stock(setup) == _journal_stock(setup)


def test_return_and_issue_on_one_issue_are_serialized(setup) -> None:
    """Deterministic: both wait on the held issue row; both then succeed
    one after the other with consistent balances."""
    location = _location_row(setup)
    _receive(setup, location, 6)
    issue_id = _issue(setup, _quantity(setup, 4))
    with session_scope() as blocker:
        _lock_issue_row(blocker, issue_id)
        returned = Background(lambda: _return(setup, issue_id, location, 4))
        added = Background(lambda: _add(setup, issue_id, _quantity(setup, 2)))
        _wait_for_lock_waiters(2)
        blocker.rollback()
    results = [returned.result(), added.result()]
    balance = _lines(issue_id)[setup.item_id]
    if all(r == "issued" for r in results):
        assert (balance.issued, balance.returned) == (6, 4)
        assert _stock(setup) == {location: 4}
    else:
        # The return ran first and closed the issue; adding is refused.
        assert isinstance(results[1], IssueFullyReturnedError), results
        assert (balance.issued, balance.returned) == (4, 4)
        assert _stock(setup) == {location: 6}
    assert _stock(setup) == _journal_stock(setup)


def test_return_versus_issue_from_another_document(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 5)
    first = _issue(setup, _quantity(setup, 5))
    results = _run_concurrently(
        [lambda: _return(setup, first, location, 5), lambda: _issue(setup, _quantity(setup, 5))]
    )
    assert results[0] == "issued", results
    assert isinstance(results[1], (uuid.UUID, InsufficientStockError)), results
    assert _stock(setup) == _journal_stock(setup)
    expected = {} if isinstance(results[1], uuid.UUID) else {location: 5}
    assert _stock(setup) == expected


def test_return_waits_for_a_location_being_archived(setup) -> None:
    """Deterministic: archiving holds the location row; the return waits
    on it and then fails on the archived location — nothing is returned
    into an archived location."""
    source, target = _location_row(setup), _location_row(setup)
    _receive(setup, source, 2)
    issue_id = _issue(setup, _quantity(setup, 2))
    with session_scope() as blocker:
        location = get_record(
            blocker, InventoryStorageLocation, record_id=target, club_id=setup.club_id,
            for_update=True,
        )
        returned = Background(lambda: _return(setup, issue_id, target, 2))
        _wait_for_lock_waiters()
        assert location is not None
        service.archive_location(blocker, location=location, updated_by=setup.user_id)
    assert type(returned.result()).__name__ == "ArchivedReferenceError"
    assert _lines(issue_id)[setup.item_id].outstanding == 2


def test_archive_item_versus_active_issue(setup) -> None:
    """Deterministic: the issue holds the item's share lock while it waits
    on a stock row; archiving waits on the item, then sees the issued
    quantity and is refused."""
    location = _location_row(setup)
    _receive(setup, location, 3)
    with session_scope() as blocker:
        _lock_stock_row(blocker, setup, location)
        issue = Background(lambda: _issue(setup, _quantity(setup, 3)))
        _wait_for_lock_waiters()
        archive = Background(lambda: _archive_item(setup))
        _wait_for_lock_waiters(2)
        blocker.rollback()
    assert isinstance(issue.result(), uuid.UUID)
    assert isinstance(archive.result(), ItemHasOutstandingIssuesError)
    with session_scope() as session:
        item = session.get(InventoryItem, setup.item_id)
        assert item is not None and item.status == "active"


def test_archive_item_races_issue_and_return(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 2)
    results = _run_concurrently(
        [lambda: _archive_item(setup), lambda: _issue(setup, _quantity(setup, 2))]
    )
    with session_scope() as session:
        item = session.get(InventoryItem, setup.item_id)
        assert item is not None
        archived = item.status == "archived"
    assert not archived, results  # stock or issued quantity always blocks it
    assert isinstance(results[1], uuid.UUID), results


def test_lost_versus_return_of_one_instance(setup) -> None:
    location = _location_row(setup)
    instance_id = _instance(setup, location)
    issue_id = _issue(setup, _instances(setup, instance_id))
    results = _run_concurrently(
        [
            lambda: _lost(setup, issue_id, instance_id, location),
            lambda: _return(setup, issue_id, location, 0, (instance_id,)),
        ]
    )
    assert sum(isinstance(r, str) for r in results) == 1, results
    state, _ = _instance_state(instance_id)
    assert state == ("written_off" if isinstance(results[0], str) else "available")


def test_mixed_operations_do_not_deadlock(setup) -> None:
    """Two items, two locations, issues naming the items in both orders,
    returns, transfers and write-offs at once: every operation finishes
    without a deadlock and stock equals the journal."""
    a = _location_row(setup, uuid.UUID(f"0{uuid.uuid4().hex[1:]}"))
    b = _location_row(setup, uuid.UUID(f"f{uuid.uuid4().hex[1:]}"))
    _receive(setup, a, 20)
    _receive(setup, b, 20)
    stoves = [_instance(setup, a) for _ in range(4)]
    open_issue = _issue(setup, _quantity(setup, 6), _instances(setup, stoves[0]))
    results = _run_concurrently(
        [
            lambda: _issue(setup, _quantity(setup, 3), _instances(setup, stoves[1])),
            lambda: _issue(setup, _instances(setup, stoves[2]), _quantity(setup, 3)),
            lambda: _return(setup, open_issue, b, 2, (stoves[0],)),
            lambda: _add(setup, open_issue, _instances(setup, stoves[3])),
            lambda: _transfer(setup, a, b, 2),
            lambda: _transfer(setup, b, a, 2),
            lambda: _write_off(setup, b, 1),
        ],
        timeout=60,
    )
    assert not [r for r in results if isinstance(r, (DBAPIError, Exception))], results
    assert _stock(setup) == _journal_stock(setup)


# --- removing a line -------------------------------------------------------------------


def _remove(setup: Setup, issue_id: uuid.UUID, line_id: uuid.UUID) -> str:
    with session_scope() as session:
        return issues.remove_line(
            session,
            club_id=setup.club_id,
            issue_id=issue_id,
            line_id=line_id,
            removed_by=setup.user_id,
        ).status


def _line_row(line_id: uuid.UUID) -> InventoryIssueLine:
    with session_scope() as session:
        line = session.get(InventoryIssueLine, line_id)
        assert line is not None
        session.expunge(line)
        return line


def _two_line_issue(setup: Setup) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An issue whose quantity line is fully returned and whose instance
    line is still outstanding (so the issue itself stays editable)."""
    location = _location_row(setup)
    _receive(setup, location, 3)
    instance_id = _instance(setup, location)
    issue_id = _issue(setup, _quantity(setup, 3), _instances(setup, instance_id))
    _return(setup, issue_id, location, 3)
    return issue_id, _lines(issue_id)[setup.item_id].line_id, location


@pytest.mark.parametrize("fields", [{"removed_at": _NOW}, {"removed_by": "user"}])
def test_removed_at_and_removed_by_go_together(setup, fields) -> None:
    with session_scope() as session:
        issue = _issue_row(setup)
        session.add(issue)
        session.flush()
        if fields.get("removed_by") == "user":
            fields = {"removed_by": setup.user_id}
        session.add(
            InventoryIssueLine(
                issue_id=issue.id, item_id=setup.item_id, created_by=setup.user_id, **fields
            )
        )
        with pytest.raises(IntegrityError) as raised:
            session.flush()
        assert "ck_inventory_issue_lines_removed_by_matches_removed_at" in str(raised.value)


def test_one_active_line_per_item_but_removed_lines_do_not_count(setup) -> None:
    with session_scope() as session:
        issue = _issue_row(setup)
        session.add(issue)
        session.flush()
        for _ in range(2):
            session.add(
                InventoryIssueLine(
                    issue_id=issue.id,
                    item_id=setup.item_id,
                    created_by=setup.user_id,
                    removed_at=_NOW,
                    removed_by=setup.user_id,
                )
            )
        session.add(
            InventoryIssueLine(issue_id=issue.id, item_id=setup.item_id, created_by=setup.user_id)
        )
        session.flush()


def test_a_line_is_removed_once_and_otherwise_immutable(setup) -> None:
    issue_id, line_id, _ = _two_line_issue(setup)
    assert _remove(setup, issue_id, line_id) == "issued"
    removed = _line_row(line_id)
    assert removed.removed_at is not None and removed.removed_by == setup.user_id
    with session_scope() as session:
        line = session.get(InventoryIssueLine, line_id)
        assert line is not None
        line.removed_at = _NOW
        with pytest.raises(InventoryIssueLineImmutableError):
            session.flush()
    with session_scope() as session:
        line = session.get(InventoryIssueLine, line_id)
        assert line is not None
        line.item_id = setup.instance_item_id
        with pytest.raises(InventoryIssueLineImmutableError):
            session.flush()
    with session_scope() as session:
        session.delete(session.get(InventoryIssueLine, line_id))
        with pytest.raises(InventoryIssueLineImmutableError):
            session.flush()
    assert _line_row(line_id).removed_at == removed.removed_at


def test_removal_keeps_every_movement_of_the_line(setup) -> None:
    issue_id, line_id, _ = _two_line_issue(setup)

    def movements() -> list[tuple[str, int | None]]:
        with session_scope() as session:
            rows = session.execute(
                sa.select(InventoryMovement)
                .where(InventoryMovement.issue_line_id == line_id)
                .order_by(InventoryMovement.created_at)
            ).scalars()
            return [(m.movement_type, m.quantity) for m in rows]

    before = movements()
    assert before == [("issue", 3), ("return", 3)]
    _remove(setup, issue_id, line_id)
    assert movements() == before
    assert _stock(setup) == _journal_stock(setup)
    balance = _lines(issue_id)[setup.item_id]
    assert balance.removed and (balance.issued, balance.returned) == (3, 3)


def test_removal_failures_change_nothing(setup) -> None:
    location = _location_row(setup)
    _receive(setup, location, 10)
    issue_id = _issue(setup, _quantity(setup, 10))
    _return(setup, issue_id, location, 6)
    line_id = _lines(issue_id)[setup.item_id].line_id
    with pytest.raises(IssueLineOutstandingError) as raised:
        _remove(setup, issue_id, line_id)
    assert raised.value.outstanding == 4
    with pytest.raises(issues.InventoryIssueLineNotFoundError):
        _remove(setup, issue_id, uuid.uuid4())
    assert _line_row(line_id).removed_at is None


def test_removal_waits_for_the_issue_lock_and_rereads(setup) -> None:
    """Deterministic: an issue on the fully returned line is queued first
    (it puts 2 back on the line); the removal queued behind it must see the
    new outstanding quantity and be refused."""
    issue_id, line_id, location = _two_line_issue(setup)
    with session_scope() as blocker:
        _lock_issue_row(blocker, issue_id)
        added = Background(lambda: _add(setup, issue_id, _quantity(setup, 2)))
        _wait_for_lock_waiters(1)
        removed = Background(lambda: _remove(setup, issue_id, line_id))
        _wait_for_lock_waiters(2)
        blocker.rollback()
    assert added.result() == "issued"
    assert isinstance(removed.result(), IssueLineOutstandingError), removed.result()
    line = _line_row(line_id)
    assert line.removed_at is None
    assert _lines(issue_id)[setup.item_id].outstanding == 2


def test_removal_and_issue_race_never_leave_property_on_a_removed_line(setup) -> None:
    issue_id, line_id, _ = _two_line_issue(setup)
    results = _run_concurrently(
        [
            lambda: _remove(setup, issue_id, line_id),
            lambda: _add(setup, issue_id, _quantity(setup, 2)),
        ]
    )
    assert results[1] == "issued", results
    with session_scope() as session:
        balances = issues.line_balances(session, [issue_id])
    assert not any(b.removed and b.outstanding for b in balances.values())
    active = [b for b in balances.values() if b.item_id == setup.item_id and not b.removed]
    assert len(active) == 1 and active[0].outstanding == 2
    if results[0] == "issued":  # removed first: the issue opened a new line
        assert active[0].line_id != line_id
    else:
        assert isinstance(results[0], IssueLineOutstandingError), results
        assert active[0].line_id == line_id


def test_return_and_removal_race(setup) -> None:
    """Removal succeeds only if the full return committed before it."""
    location = _location_row(setup)
    _receive(setup, location, 2)
    instance_id = _instance(setup, location)
    issue_id = _issue(setup, _quantity(setup, 2), _instances(setup, instance_id))
    line_id = _lines(issue_id)[setup.item_id].line_id
    results = _run_concurrently(
        [lambda: _return(setup, issue_id, location, 2), lambda: _remove(setup, issue_id, line_id)]
    )
    assert results[0] == "issued", results
    removed = _line_row(line_id).removed_at is not None
    assert removed == (results[1] == "issued"), results
    if not removed:
        assert isinstance(results[1], IssueLineOutstandingError), results
    assert _stock(setup) == {location: 2}


def test_double_removal(setup) -> None:
    issue_id, line_id, _ = _two_line_issue(setup)
    results = _run_concurrently([lambda: _remove(setup, issue_id, line_id) for _ in range(3)])
    assert results.count("issued") == 1, results
    assert sum(isinstance(r, IssueLineRemovedError) for r in results) == 2, results


def test_line_rows_cannot_be_physically_deleted(setup) -> None:
    """PO rule 2: neither the ORM nor a raw SQL DELETE removes a line row —
    its movements reference it (FK RESTRICT) — removed or not."""
    issue_id, line_id, _ = _two_line_issue(setup)
    _remove(setup, issue_id, line_id)
    for target in (line_id, _lines(issue_id)[setup.instance_item_id].line_id):
        with session_scope() as session:
            with pytest.raises(IntegrityError):
                session.execute(
                    sa.delete(InventoryIssueLine).where(InventoryIssueLine.id == target)
                )
    assert _line_row(line_id).removed_at is not None
