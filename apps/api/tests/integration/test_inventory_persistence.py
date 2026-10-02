"""Database-level guarantees of the Inventory Foundation (TH-0121 /
Issue #230; docs/04-domain/inventory.md), against real PostgreSQL:
seeded system units, CHECK/unique constraints behind the service rules,
and the immutable movement journal."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.identity import Club, Person, User
from app.db.inventory import (
    InventoryCategory,
    InventoryIssue,
    InventoryIssueLine,
    InventoryItem,
    InventoryMovement,
    InventoryMovementImmutableError,
    InventoryReversalTargetError,
    InventoryStorageLocation,
    InventoryUnit,
)
from app.db.session import session_scope
from app.inventory import service as inventory_service

from .conftest import requires_postgres

pytestmark = requires_postgres


@pytest.fixture
def owner() -> tuple[uuid.UUID, uuid.UUID]:
    """(club_id, user_id)."""
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
        session.commit()
        return club.id, user.id


def _system_unit_id(session, name: str) -> uuid.UUID:
    return session.execute(
        sa.select(InventoryUnit.id).where(InventoryUnit.is_system, InventoryUnit.name == name)
    ).scalar_one()


def _item(session, club_id: uuid.UUID, user_id: uuid.UUID, **overrides) -> InventoryItem:
    category = InventoryCategory(
        club_id=club_id,
        name=f"Кат {uuid.uuid4().hex[:6]}",
        status="active",
        created_by=user_id,
    )
    session.add(category)
    session.flush()
    values = {
        "club_id": club_id,
        "name": f"Позиция {uuid.uuid4().hex[:6]}",
        "category_id": category.id,
        "unit_id": _system_unit_id(session, "шт"),
        "accounting_mode": "quantity",
        "status": "active",
        "created_by": user_id,
        **overrides,
    }
    item = InventoryItem(**values)
    session.add(item)
    session.flush()
    return item


def _committed_item_with_movement(
    club_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        movement = InventoryMovement(
            quantity=1, item_id=item.id, movement_type="receipt", created_by=user_id
        )
        session.add(movement)
        session.commit()
        return item.id, movement.id


def test_migration_seeds_exactly_the_four_system_units() -> None:
    with session_scope() as session:
        rows = session.execute(
            sa.select(InventoryUnit.name, InventoryUnit.club_id, InventoryUnit.status).where(
                InventoryUnit.is_system
            )
        ).all()
    assert sorted(name for name, _, _ in rows) == sorted(["шт", "м", "комплект", "пара"])
    assert all(club_id is None and status == "active" for _, club_id, status in rows)


def test_system_unit_cannot_be_archived_even_bypassing_the_service() -> None:
    with session_scope() as session:
        unit = session.get(InventoryUnit, _system_unit_id(session, "м"))
        assert unit is not None
        unit.status = "archived"
        unit.archived_at = sa.func.now()
        with pytest.raises(IntegrityError):
            session.flush()


def test_status_and_archived_at_must_agree(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        session.add(
            InventoryCategory(club_id=club_id, name="X", status="archived", created_by=user_id)
        )
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize(
    "overrides",
    [{"accounting_mode": "batch"}, {"current_cost_minor": -1}, {"status": "deleted"}],
)
def test_item_check_constraints(owner, overrides) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        with pytest.raises(IntegrityError):
            _item(session, club_id, user_id, **overrides)


def test_item_active_name_unique_index(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        _item(session, club_id, user_id, name="Жумар")
        with pytest.raises(IntegrityError):
            _item(session, club_id, user_id, name="ЖУМАР")


def test_archived_item_does_not_occupy_the_unique_name(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        _item(session, club_id, user_id, name="Жумар", status="archived", archived_at=sa.func.now())
        _item(session, club_id, user_id, name="Жумар")


def test_location_cannot_reference_itself(owner) -> None:
    club_id, user_id = owner
    location_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            InventoryStorageLocation(
                id=location_id,
                club_id=club_id,
                parent_id=location_id,
                name="Сам себе",
                status="active",
                created_by=user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_movement_types_are_the_canonical_set(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        # Slice 4: `issue`/`return` belong to an issue line.
        recipient = Person(last_name="Получатель", first_name="Тест")
        session.add(recipient)
        session.flush()
        issue = InventoryIssue(
            club_id=club_id,
            recipient_type="member",
            recipient_person_id=recipient.id,
            status="issued",
            created_by=user_id,
        )
        session.add(issue)
        session.flush()
        line = InventoryIssueLine(issue_id=issue.id, item_id=item.id, created_by=user_id)
        session.add(line)
        session.flush()
        for movement_type in ("receipt", "transfer", "issue", "return", "write_off", "adjustment"):
            session.add(
                InventoryMovement(
                    quantity=1,
                    item_id=item.id,
                    movement_type=movement_type,
                    created_by=user_id,
                    issue_line_id=line.id if movement_type in ("issue", "return") else None,
                )
            )
        session.flush()
        session.add(
            InventoryMovement(
                quantity=1, item_id=item.id, movement_type="repair", created_by=user_id
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_movements_are_immutable(owner) -> None:
    item_id, movement_id = _committed_item_with_movement(*owner)

    with session_scope() as session:
        stored = session.get(InventoryMovement, movement_id)
        assert stored is not None
        stored.movement_type = "write_off"
        with pytest.raises(InventoryMovementImmutableError):
            session.flush()

    with session_scope() as session:
        stored = session.get(InventoryMovement, movement_id)
        session.delete(stored)
        with pytest.raises(InventoryMovementImmutableError):
            session.flush()

    with session_scope() as session:
        stored = session.get(InventoryMovement, movement_id)
        assert stored is not None
        assert stored.movement_type == "receipt"
        assert stored.item_id == item_id


def test_item_with_movements_cannot_be_physically_deleted(owner) -> None:
    item_id, _ = _committed_item_with_movement(*owner)
    with session_scope() as session:
        with pytest.raises(IntegrityError):
            session.execute(sa.delete(InventoryItem).where(InventoryItem.id == item_id))


def test_has_movements_reflects_the_journal(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        assert inventory_service.has_movements(session, item.id) is False
        session.add(
            InventoryMovement(
                quantity=1, item_id=item.id, movement_type="receipt", created_by=user_id
            )
        )
        session.flush()
        assert inventory_service.has_movements(session, item.id) is True


# --- B: category / unit name indexes ----------------------------------------------


def test_category_active_name_unique_index(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        session.add(
            InventoryCategory(club_id=club_id, name="Каски", status="active", created_by=user_id)
        )
        session.flush()
        session.add(
            InventoryCategory(
                club_id=club_id,
                name="каски",
                status="archived",
                archived_at=sa.func.now(),
                created_by=user_id,
            )
        )
        session.flush()
        session.add(
            InventoryCategory(club_id=club_id, name="КАСКИ", status="active", created_by=user_id)
        )
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("name", ["рулон", "РУЛОН", "шт"])
def test_unit_active_name_unique_index_covers_system_units(owner, name) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        session.add(
            InventoryUnit(
                club_id=club_id, name="Рулон", is_system=False, status="active", created_by=user_id
            )
        )
        session.flush()
        session.add(
            InventoryUnit(
                club_id=club_id, name=name, is_system=False, status="active", created_by=user_id
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


# --- E: write-off reversal foundation ---------------------------------------------


def _movement(session, item_id, user_id, movement_type, reverses=None) -> InventoryMovement:
    movement = InventoryMovement(
        quantity=1,
        item_id=item_id,
        movement_type=movement_type,
        reverses_movement_id=reverses,
        created_by=user_id,
    )
    session.add(movement)
    session.flush()
    return movement


def test_writeoff_reversal_references_the_write_off_once(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        write_off = _movement(session, item.id, user_id, "write_off")
        reversal = _movement(session, item.id, user_id, "writeoff_reversal", write_off.id)
        assert reversal.reverses_movement_id == write_off.id

        session.add(
            InventoryMovement(
                quantity=1,
                item_id=item.id,
                movement_type="writeoff_reversal",
                reverses_movement_id=write_off.id,
                created_by=user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_writeoff_reversal_requires_a_reference(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        session.add(
            InventoryMovement(
                quantity=1, item_id=item.id, movement_type="writeoff_reversal", created_by=user_id
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_only_a_writeoff_reversal_may_carry_a_reference(owner) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        write_off = _movement(session, item.id, user_id, "write_off")
        session.add(
            InventoryMovement(
                quantity=1,
                item_id=item.id,
                movement_type="adjustment",
                reverses_movement_id=write_off.id,
                created_by=user_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("target_type", ["receipt", "adjustment", "writeoff_reversal"])
def test_writeoff_reversal_may_only_reverse_a_write_off(owner, target_type) -> None:
    club_id, user_id = owner
    with session_scope() as session:
        item = _item(session, club_id, user_id)
        if target_type == "writeoff_reversal":
            write_off = _movement(session, item.id, user_id, "write_off")
            target = _movement(session, item.id, user_id, "writeoff_reversal", write_off.id)
        else:
            target = _movement(session, item.id, user_id, target_type)
        session.add(
            InventoryMovement(
                quantity=1,
                item_id=item.id,
                movement_type="writeoff_reversal",
                reverses_movement_id=target.id,
                created_by=user_id,
            )
        )
        with pytest.raises(InventoryReversalTargetError):
            session.flush()
