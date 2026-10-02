"""Inventory Slice 4 — issue / return (docs/04-domain/inventory.md §14, §17;
Issue #236).

No authorization here: the API router has already required the
Administrator (app.inventory.authorization).

An issue is a document handing property to one recipient (Member ->
Person, Instructor -> User, Group). It is issued on creation. Each item
appears on one line; a quantity line is issued from the item's stock,
spread automatically over the locations (most stock first, ties by the
smaller location id — app.inventory.lifecycle.allocate_quantity); an
instance line holds specific instances (`available -> issued`). What is
still issued is derived from the `issue`/`return` movements referencing
the lines — nothing on a line is ever overwritten or deleted.

Every operation is one transaction and takes its locks in one order:

    issue document  FOR UPDATE (when it exists)
    -> items        FOR SHARE, by item id
    -> the return location FOR SHARE (returns, cancel, lost only)
    -> stock rows   FOR UPDATE, by item id then location id
    -> instances    FOR UPDATE, by instance id

which extends the Slice 3 order (item -> locations -> stock rows). Every
movement on a line goes through the document lock, so two returns, two
cancellations or a return and an issue on one document are serialized and
each re-reads the committed balance: nothing can be returned twice.
Issuing from stock locks the same stock rows a transfer or write-off does,
in the same order, so stock never goes negative. An instance is locked
before its state is checked, so it is never issued twice.

Slice 2 operations lock an instance first and a location after it, the
opposite of a return (location before instance). That cannot deadlock: a
return locks only instances still issued on its document, and every Slice
2 operation on an `issued` instance is refused before it asks for a
location lock.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authorization.club_ownership import (
    ACTIVE_CLUB_MEMBERSHIP_STATUS,
    user_has_active_club_membership,
)
from app.db.authorization import Role, UserRoleAssignment
from app.db.events import Event
from app.db.groups import Group
from app.db.identity import ClubMembership, Person, User
from app.db.inventory import (
    TEXT_MAX_LENGTH,
    InventoryInstance,
    InventoryIssue,
    InventoryIssueLine,
    InventoryItem,
    InventoryItemStock,
    InventoryMovement,
)
from app.inventory.lifecycle import (
    InstanceNotOutstandingError,
    InvalidInventoryDataError,
    InvalidRecipientError,
    allocate_quantity,
    ensure_instance_mode,
    ensure_issue_changeable,
    ensure_quantity_mode,
    ensure_returnable,
    ensure_selectable,
    next_instance_state,
    normalize_optional_text,
    outstanding_quantity,
    validate_quantity,
    validate_recipient_type,
)
from app.inventory.locking import selectable_location, share_locked_item
from app.inventory.quantities import locked_stocks
from app.inventory.service import KIND_ITEM, InventoryReferenceNotFoundError
from app.inventory.vocabulary import (
    ACCOUNTING_MODE_QUANTITY,
    ACTIVE,
    ISSUE_CANCELLED,
    ISSUE_ISSUED,
    MOVEMENT_ISSUE,
    MOVEMENT_RETURN,
    MOVEMENT_WRITE_OFF,
    RECIPIENT_INSTRUCTOR,
    RECIPIENT_MEMBER,
)

KIND_INSTANCE = "instance"
KIND_ISSUE_LINE = "issue line"
KIND_EVENT = "event"

_INSTRUCTOR_ROLE_CODE = "instructor"


class InventoryIssueNotFoundError(Exception):
    def __init__(self, issue_id: uuid.UUID) -> None:
        super().__init__(f"Inventory issue {issue_id} does not exist")
        self.issue_id = issue_id


# --- request shapes ------------------------------------------------------------------


@dataclass(frozen=True)
class IssueLineRequest:
    """One item to issue: a quantity for a quantity item, specific
    instances for an instance item."""

    item_id: uuid.UUID
    quantity: int | None = None
    instance_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class QuantityReturnRequest:
    line_id: uuid.UUID
    quantity: int


def normalize_line_requests(lines: Sequence[IssueLineRequest]) -> list[IssueLineRequest]:
    """At least one line; one line per item; a quantity xor instances; no
    instance twice."""
    if not lines:
        raise InvalidInventoryDataError("lines must not be empty")
    seen_items: set[uuid.UUID] = set()
    seen_instances: set[uuid.UUID] = set()
    for line in lines:
        if line.item_id in seen_items:
            raise InvalidInventoryDataError("each item may appear on one line only")
        seen_items.add(line.item_id)
        if (line.quantity is None) == (not line.instance_ids):
            raise InvalidInventoryDataError(
                "a line needs either quantity (quantity item) or instance_ids (instance item)"
            )
        if line.quantity is not None:
            validate_quantity(line.quantity)
        for instance_id in line.instance_ids:
            if instance_id in seen_instances:
                raise InvalidInventoryDataError("an instance may appear only once")
            seen_instances.add(instance_id)
    return list(lines)


# --- balances ------------------------------------------------------------------------


@dataclass
class LineBalance:
    """What a line issued and got back, derived from its movements. For an
    instance line the quantities count instance movements."""

    line_id: uuid.UUID
    item_id: uuid.UUID
    accounting_mode: str
    issued: int = 0
    returned: int = 0
    outstanding_instance_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def outstanding(self) -> int:
        return outstanding_quantity(issued=self.issued, returned=self.returned)


def build_line_balances(
    lines: Iterable[tuple[uuid.UUID, uuid.UUID, str]],
    movements: Iterable[tuple[uuid.UUID, uuid.UUID | None, str, int, int | None]],
) -> dict[uuid.UUID, LineBalance]:
    """Pure: `lines` are `(line_id, item_id, accounting_mode)`; `movements`
    are `(line_id, instance_id, movement_type, count, quantity_sum)` grouped
    `issue`/`return` movements. An instance is outstanding when it was
    issued on the line more often than returned to it."""
    balances = {
        line_id: LineBalance(line_id=line_id, item_id=item_id, accounting_mode=mode)
        for line_id, item_id, mode in lines
    }
    per_instance: dict[uuid.UUID, dict[uuid.UUID, int]] = defaultdict(lambda: defaultdict(int))
    for line_id, instance_id, movement_type, count, quantity_sum in movements:
        balance = balances[line_id]
        amount = count if instance_id is not None else (quantity_sum or 0)
        sign = 1 if movement_type == MOVEMENT_ISSUE else -1
        if movement_type == MOVEMENT_ISSUE:
            balance.issued += amount
        else:
            balance.returned += amount
        if instance_id is not None:
            per_instance[line_id][instance_id] += sign * count
    for line_id, instances in per_instance.items():
        balances[line_id].outstanding_instance_ids = sorted(
            instance_id for instance_id, net in instances.items() if net > 0
        )
    return balances


def line_balances(session: Session, issue_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LineBalance]:
    """Balances of every line of the given issues, keyed by line id."""
    if not issue_ids:
        return {}
    lines = session.execute(
        sa.select(InventoryIssueLine.id, InventoryIssueLine.item_id, InventoryItem.accounting_mode)
        .join(InventoryItem, InventoryItem.id == InventoryIssueLine.item_id)
        .where(InventoryIssueLine.issue_id.in_(issue_ids))
    ).all()
    movements = session.execute(
        sa.select(
            InventoryMovement.issue_line_id,
            InventoryMovement.instance_id,
            InventoryMovement.movement_type,
            sa.func.count(),
            sa.func.sum(InventoryMovement.quantity),
        )
        .join(InventoryIssueLine, InventoryIssueLine.id == InventoryMovement.issue_line_id)
        .where(
            InventoryIssueLine.issue_id.in_(issue_ids),
            InventoryMovement.movement_type.in_((MOVEMENT_ISSUE, MOVEMENT_RETURN)),
        )
        .group_by(
            InventoryMovement.issue_line_id,
            InventoryMovement.instance_id,
            InventoryMovement.movement_type,
        )
    ).all()
    return build_line_balances(
        [(row[0], row[1], row[2]) for row in lines],
        [(row[0], row[1], row[2], row[3], row[4]) for row in movements],
    )


def _issue_balances(session: Session, issue: InventoryIssue) -> dict[uuid.UUID, LineBalance]:
    return line_balances(session, [issue.id])


def issues_with_outstanding(session: Session, issue_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """The given issues that still hold issued property."""
    if not issue_ids:
        return set()
    balances = line_balances(session, issue_ids)
    line_issue: dict[uuid.UUID, uuid.UUID] = {
        row.id: row.issue_id
        for row in session.execute(
            sa.select(InventoryIssueLine.id, InventoryIssueLine.issue_id).where(
                InventoryIssueLine.issue_id.in_(issue_ids)
            )
        )
    }
    return {line_issue[line_id] for line_id, b in balances.items() if b.outstanding > 0}


# --- recipient / event ---------------------------------------------------------------


def _has_effective_instructor_role(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> bool:
    now = sa.func.now()
    return (
        session.execute(
            sa.select(UserRoleAssignment.id)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .where(
                UserRoleAssignment.user_id == user_id,
                Role.code == _INSTRUCTOR_ROLE_CODE,
                Role.is_system.is_(True),
                sa.or_(UserRoleAssignment.club_id.is_(None), UserRoleAssignment.club_id == club_id),
                UserRoleAssignment.valid_from <= now,
                sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
            )
            .limit(1)
        ).first()
        is not None
    )


def _person_has_active_club_membership(
    session: Session, *, person_id: uuid.UUID, club_id: uuid.UUID
) -> bool:
    return (
        session.execute(
            sa.select(ClubMembership.id)
            .where(
                ClubMembership.person_id == person_id,
                ClubMembership.club_id == club_id,
                ClubMembership.status == ACTIVE_CLUB_MEMBERSHIP_STATUS,
            )
            .with_for_update(read=True)
            .limit(1)
        ).first()
        is not None
    )


def recipient_columns(
    session: Session, *, club_id: uuid.UUID, recipient_type: str, recipient_id: uuid.UUID
) -> dict[str, uuid.UUID | None]:
    """Validates the recipient against the identity model and returns the
    issue's recipient columns:

    - Member: a Person with an active ClubMembership in the Club;
    - Instructor: a User holding a currently-effective `instructor` role
      assignment reaching the Club whose Person has an active
      ClubMembership in it;
    - Group: an active Group of the Club.
    """
    kind = validate_recipient_type(recipient_type)
    columns: dict[str, uuid.UUID | None] = {
        "recipient_person_id": None,
        "recipient_user_id": None,
        "recipient_group_id": None,
    }
    if kind == RECIPIENT_MEMBER:
        valid = session.get(Person, recipient_id) is not None and (
            _person_has_active_club_membership(session, person_id=recipient_id, club_id=club_id)
        )
        columns["recipient_person_id"] = recipient_id
    elif kind == RECIPIENT_INSTRUCTOR:
        valid = (
            session.get(User, recipient_id) is not None
            and _has_effective_instructor_role(session, user_id=recipient_id, club_id=club_id)
            and user_has_active_club_membership(session, user_id=recipient_id, club_id=club_id)
        )
        columns["recipient_user_id"] = recipient_id
    else:
        group = session.execute(
            sa.select(Group).where(Group.id == recipient_id).with_for_update(read=True)
        ).scalar_one_or_none()
        valid = group is not None and group.club_id == club_id and group.status == ACTIVE
        columns["recipient_group_id"] = recipient_id
    if not valid:
        raise InvalidRecipientError(kind)
    return columns


def _ensure_event(session: Session, *, club_id: uuid.UUID, event_id: uuid.UUID) -> None:
    """Any Event of the Club (§14: not limited to trips)."""
    event = session.get(Event, event_id)
    if event is None or event.club_id != club_id:
        raise InventoryReferenceNotFoundError(KIND_EVENT, event_id)


def recipient_id_of(issue: InventoryIssue) -> uuid.UUID:
    recipient = issue.recipient_person_id or issue.recipient_user_id or issue.recipient_group_id
    assert recipient is not None  # ck_inventory_issues_recipient_matches_type
    return recipient


# --- locks ---------------------------------------------------------------------------


def _locked_issue(session: Session, *, club_id: uuid.UUID, issue_id: uuid.UUID) -> InventoryIssue:
    issue = session.execute(
        sa.select(InventoryIssue)
        .where(InventoryIssue.id == issue_id, InventoryIssue.club_id == club_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if issue is None:
        raise InventoryIssueNotFoundError(issue_id)
    return issue


def _share_locked_items(
    session: Session, *, club_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, InventoryItem]:
    return {
        item_id: share_locked_item(session, club_id=club_id, item_id=item_id)
        for item_id in sorted(set(item_ids), key=str)
    }


def _locked_instances(
    session: Session, instance_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, InventoryInstance]:
    locked: dict[uuid.UUID, InventoryInstance] = {}
    for instance_id in sorted(set(instance_ids), key=str):
        instance = session.execute(
            sa.select(InventoryInstance)
            .where(InventoryInstance.id == instance_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if instance is not None:
            locked[instance_id] = instance
    return locked


# A concurrent transfer or receipt into a location the item never had
# stock in creates that stock row after it was read; a few attempts are
# enough for the set of rows to settle.
_STOCK_LOCK_ATTEMPTS = 5


def _stock_location_ids(session: Session, item_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        session.execute(
            sa.select(InventoryItemStock.storage_location_id).where(
                InventoryItemStock.item_id == item_id
            )
        ).scalars()
    )


def _locked_item_stock(
    session: Session, *, item_id: uuid.UUID
) -> dict[uuid.UUID, InventoryItemStock]:
    """Every stock row of the item, row-locked in location-id order (the
    order of app.inventory.quantities). Stock rows are never deleted, so the
    only rows the read can miss are ones created concurrently; if a fresh
    read after locking finds such a row, the attempt is rolled back to its
    savepoint (releasing its row locks) and repeated, so locks are never
    taken out of order."""
    for attempt in range(_STOCK_LOCK_ATTEMPTS):
        savepoint = session.begin_nested()
        location_ids = _stock_location_ids(session, item_id)
        stock = locked_stocks(session, item_id=item_id, location_ids=location_ids, create=False)
        last_attempt = attempt == _STOCK_LOCK_ATTEMPTS - 1
        if last_attempt or _stock_location_ids(session, item_id) <= set(stock):
            savepoint.commit()
            return stock
        savepoint.rollback()
    raise AssertionError("unreachable")  # pragma: no cover


def _movement(
    *,
    item_id: uuid.UUID,
    line_id: uuid.UUID,
    movement_type: str,
    created_by: uuid.UUID,
    **fields: Any,
) -> InventoryMovement:
    return InventoryMovement(
        id=uuid.uuid4(),
        item_id=item_id,
        issue_line_id=line_id,
        movement_type=movement_type,
        created_by=created_by,
        created_at=sa.func.clock_timestamp(),
        **fields,
    )


def _finish(session: Session, issue: InventoryIssue) -> InventoryIssue:
    session.commit()
    session.refresh(issue)
    return issue


# --- issuing -------------------------------------------------------------------------


def _issue_lines(
    session: Session,
    *,
    club_id: uuid.UUID,
    issue: InventoryIssue,
    requests: Sequence[IssueLineRequest],
    created_by: uuid.UUID,
) -> None:
    """Issues `requests` on `issue` (whose row the caller holds): an item
    already on the issue gets more `issue` movements on its existing line,
    any other item a new line."""
    items = _share_locked_items(session, club_id=club_id, item_ids=[r.item_id for r in requests])
    for request in requests:
        item = items[request.item_id]
        ensure_selectable(item.status, kind=KIND_ITEM)
        if request.quantity is not None:
            ensure_quantity_mode(item.accounting_mode)
        else:
            ensure_instance_mode(item.accounting_mode)

    # Stock rows, item by item, each in location-id order.
    allocations: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = {}
    for request in sorted(requests, key=lambda r: str(r.item_id)):
        if request.quantity is None:
            continue
        stock = _locked_item_stock(session, item_id=request.item_id)
        allocation = allocate_quantity(
            ((location_id, row.quantity) for location_id, row in stock.items()), request.quantity
        )
        for location_id, take in allocation:
            stock[location_id].quantity -= take
        allocations[request.item_id] = allocation

    wanted = {i: r.item_id for r in requests for i in r.instance_ids}
    instances = _locked_instances(session, wanted)
    for instance_id, item_id in wanted.items():
        instance = instances.get(instance_id)
        if instance is None or instance.item_id != item_id:
            raise InventoryReferenceNotFoundError(KIND_INSTANCE, instance_id)
        next_instance_state(instance.state, MOVEMENT_ISSUE)

    existing: dict[uuid.UUID, uuid.UUID] = {
        row.item_id: row.id
        for row in session.execute(
            sa.select(InventoryIssueLine.item_id, InventoryIssueLine.id).where(
                InventoryIssueLine.issue_id == issue.id
            )
        )
    }
    for request in requests:
        line_id = existing.get(request.item_id)
        if line_id is None:
            line = InventoryIssueLine(
                id=uuid.uuid4(), issue_id=issue.id, item_id=request.item_id, created_by=created_by
            )
            session.add(line)
            session.flush()
            line_id = line.id
        for location_id, take in allocations.get(request.item_id, []):
            session.add(
                _movement(
                    item_id=request.item_id,
                    line_id=line_id,
                    movement_type=MOVEMENT_ISSUE,
                    created_by=created_by,
                    quantity=take,
                    from_location_id=location_id,
                )
            )
        for instance_id in sorted(request.instance_ids):
            instance = instances[instance_id]
            session.add(
                _movement(
                    item_id=request.item_id,
                    line_id=line_id,
                    movement_type=MOVEMENT_ISSUE,
                    created_by=created_by,
                    instance_id=instance.id,
                    from_location_id=instance.storage_location_id,
                )
            )
            instance.state = next_instance_state(instance.state, MOVEMENT_ISSUE)
            instance.storage_location_id = None
            instance.updated_by = created_by
    session.flush()


def create_issue(
    session: Session,
    *,
    club_id: uuid.UUID,
    recipient_type: str,
    recipient_id: uuid.UUID,
    event_id: uuid.UUID | None,
    planned_return_date: date | None,
    comment: str | None,
    lines: Sequence[IssueLineRequest],
    created_by: uuid.UUID,
) -> InventoryIssue:
    """§14: the issue is issued on creation — every line is issued at once."""
    requests = normalize_line_requests(lines)
    note = normalize_optional_text(comment, max_length=TEXT_MAX_LENGTH, field="comment")
    columns = recipient_columns(
        session, club_id=club_id, recipient_type=recipient_type, recipient_id=recipient_id
    )
    if event_id is not None:
        _ensure_event(session, club_id=club_id, event_id=event_id)
    issue = InventoryIssue(
        id=uuid.uuid4(),
        club_id=club_id,
        recipient_type=recipient_type,
        event_id=event_id,
        planned_return_date=planned_return_date,
        comment=note,
        status=ISSUE_ISSUED,
        created_by=created_by,
        **columns,
    )
    session.add(issue)
    session.flush()
    _issue_lines(session, club_id=club_id, issue=issue, requests=requests, created_by=created_by)
    return _finish(session, issue)


def _changeable_issue(
    session: Session, *, club_id: uuid.UUID, issue_id: uuid.UUID
) -> tuple[InventoryIssue, dict[uuid.UUID, LineBalance]]:
    """The locked issue and its balances; refuses a cancelled or fully
    returned issue (both immutable)."""
    issue = _locked_issue(session, club_id=club_id, issue_id=issue_id)
    balances = _issue_balances(session, issue)
    ensure_issue_changeable(
        status=issue.status, has_outstanding=any(b.outstanding > 0 for b in balances.values())
    )
    return issue, balances


def add_lines(
    session: Session,
    *,
    club_id: uuid.UUID,
    issue_id: uuid.UUID,
    lines: Sequence[IssueLineRequest],
    created_by: uuid.UUID,
) -> InventoryIssue:
    requests = normalize_line_requests(lines)
    issue, _ = _changeable_issue(session, club_id=club_id, issue_id=issue_id)
    _issue_lines(session, club_id=club_id, issue=issue, requests=requests, created_by=created_by)
    issue.updated_by = created_by
    return _finish(session, issue)


UPDATABLE_ISSUE_FIELDS = frozenset({"recipient", "event_id", "planned_return_date", "comment"})


def update_issue(
    session: Session,
    *,
    club_id: uuid.UUID,
    issue_id: uuid.UUID,
    fields: Mapping[str, Any],
    updated_by: uuid.UUID,
) -> InventoryIssue:
    """§14: the header (recipient, Event, planned return date, comment) of
    an issue that is not cancelled and still holds issued property.
    `fields["recipient"]` is `(recipient_type, recipient_id)`; `None`
    clears the Event, the date or the comment."""
    unknown = set(fields) - UPDATABLE_ISSUE_FIELDS
    if unknown:  # pragma: no cover - the request schema forbids these
        raise ValueError(f"Not updatable: {sorted(unknown)}")
    issue, _ = _changeable_issue(session, club_id=club_id, issue_id=issue_id)
    if "recipient" in fields:
        recipient_type, recipient_id = fields["recipient"]
        columns = recipient_columns(
            session, club_id=club_id, recipient_type=recipient_type, recipient_id=recipient_id
        )
        issue.recipient_type = recipient_type
        for name, value in columns.items():
            setattr(issue, name, value)
    if "event_id" in fields:
        if fields["event_id"] is not None:
            _ensure_event(session, club_id=club_id, event_id=fields["event_id"])
        issue.event_id = fields["event_id"]
    if "planned_return_date" in fields:
        issue.planned_return_date = fields["planned_return_date"]
    if "comment" in fields:
        issue.comment = normalize_optional_text(
            fields["comment"], max_length=TEXT_MAX_LENGTH, field="comment"
        )
    issue.updated_by = updated_by
    return _finish(session, issue)


# --- returning -----------------------------------------------------------------------


def _return(
    session: Session,
    *,
    club_id: uuid.UUID,
    balances: Mapping[uuid.UUID, LineBalance],
    storage_location_id: uuid.UUID,
    quantities: Mapping[uuid.UUID, int],
    instance_lines: Mapping[uuid.UUID, uuid.UUID],
    comment: str | None,
    created_by: uuid.UUID,
) -> dict[uuid.UUID, InventoryInstance]:
    """Returns `quantities` (line id -> quantity) and the instances of
    `instance_lines` (instance id -> line id), already validated against
    `balances`, into one active location. The caller holds the issue lock.
    Returns the returned instances, now `available` in the location."""
    item_ids = [balances[line_id].item_id for line_id in quantities] + [
        balances[line_id].item_id for line_id in instance_lines.values()
    ]
    _share_locked_items(session, club_id=club_id, item_ids=item_ids)
    location = selectable_location(session, club_id=club_id, location_id=storage_location_id)
    for line_id in sorted(quantities, key=lambda line: str(balances[line].item_id)):
        balance = balances[line_id]
        stock = locked_stocks(
            session, item_id=balance.item_id, location_ids=[location.id], create=True
        )
        stock[location.id].quantity += quantities[line_id]
        session.add(
            _movement(
                item_id=balance.item_id,
                line_id=line_id,
                movement_type=MOVEMENT_RETURN,
                created_by=created_by,
                quantity=quantities[line_id],
                to_location_id=location.id,
                comment=comment,
            )
        )
    instances = _locked_instances(session, instance_lines)
    for instance_id in sorted(instance_lines):
        instance = instances[instance_id]
        new_state = next_instance_state(instance.state, MOVEMENT_RETURN)
        session.add(
            _movement(
                item_id=instance.item_id,
                line_id=instance_lines[instance_id],
                movement_type=MOVEMENT_RETURN,
                created_by=created_by,
                instance_id=instance.id,
                to_location_id=location.id,
                comment=comment,
            )
        )
        instance.state = new_state
        instance.storage_location_id = location.id
        instance.updated_by = created_by
    session.flush()
    return instances


def _instance_lines(
    balances: Mapping[uuid.UUID, LineBalance], instance_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """instance id -> the line it is still issued on; refuses an instance
    that is not."""
    outstanding = {
        instance_id: line_id
        for line_id, balance in balances.items()
        for instance_id in balance.outstanding_instance_ids
    }
    result: dict[uuid.UUID, uuid.UUID] = {}
    for instance_id in instance_ids:
        if instance_id not in outstanding:
            raise InstanceNotOutstandingError(instance_id)
        result[instance_id] = outstanding[instance_id]
    return result


def return_items(
    session: Session,
    *,
    club_id: uuid.UUID,
    issue_id: uuid.UUID,
    storage_location_id: uuid.UUID,
    quantities: Sequence[QuantityReturnRequest],
    instance_ids: Sequence[uuid.UUID],
    comment: str | None,
    created_by: uuid.UUID,
) -> InventoryIssue:
    """§14: partial or full return of quantities and/or specific instances
    into the active location the Administrator chose."""
    if not quantities and not instance_ids:
        raise InvalidInventoryDataError("nothing to return: give quantities or instance_ids")
    if len({q.line_id for q in quantities}) != len(quantities):
        raise InvalidInventoryDataError("each line may appear only once")
    if len(set(instance_ids)) != len(instance_ids):
        raise InvalidInventoryDataError("an instance may appear only once")
    note = normalize_optional_text(comment, max_length=TEXT_MAX_LENGTH, field="comment")
    issue, balances = _changeable_issue(session, club_id=club_id, issue_id=issue_id)
    requested: dict[uuid.UUID, int] = {}
    for entry in quantities:
        balance = balances.get(entry.line_id)
        if balance is None:
            raise InventoryReferenceNotFoundError(KIND_ISSUE_LINE, entry.line_id)
        ensure_quantity_mode(balance.accounting_mode)
        ensure_returnable(outstanding=balance.outstanding, requested=entry.quantity)
        requested[entry.line_id] = entry.quantity
    _return(
        session,
        club_id=club_id,
        balances=balances,
        storage_location_id=storage_location_id,
        quantities=requested,
        instance_lines=_instance_lines(balances, instance_ids),
        comment=note,
        created_by=created_by,
    )
    issue.updated_by = created_by
    return _finish(session, issue)


def cancel_issue(
    session: Session,
    *,
    club_id: uuid.UUID,
    issue_id: uuid.UUID,
    storage_location_id: uuid.UUID,
    cancelled_by: uuid.UUID,
) -> InventoryIssue:
    """§14: everything still issued returns into the given active location,
    then the issue is `cancelled` (immutable). A fully returned issue
    cannot be cancelled."""
    issue, balances = _changeable_issue(session, club_id=club_id, issue_id=issue_id)
    quantities = {
        line_id: b.outstanding
        for line_id, b in balances.items()
        if b.accounting_mode == ACCOUNTING_MODE_QUANTITY and b.outstanding > 0
    }
    instance_lines = {
        instance_id: line_id
        for line_id, b in balances.items()
        for instance_id in b.outstanding_instance_ids
    }
    _return(
        session,
        club_id=club_id,
        balances=balances,
        storage_location_id=storage_location_id,
        quantities=quantities,
        instance_lines=instance_lines,
        comment=None,
        created_by=cancelled_by,
    )
    issue.status = ISSUE_CANCELLED
    issue.cancelled_at = sa.func.clock_timestamp()
    issue.cancelled_by = cancelled_by
    issue.updated_by = cancelled_by
    return _finish(session, issue)


def report_lost(
    session: Session,
    *,
    club_id: uuid.UUID,
    issue_id: uuid.UUID,
    instance_id: uuid.UUID,
    storage_location_id: uuid.UUID,
    reason: str,
    comment: str | None,
    created_by: uuid.UUID,
) -> InventoryIssue:
    """§14 (lost instance): one action, one transaction — the issued
    instance is returned into the chosen active location (`return`, which
    carries the optional comment) and immediately written off from it
    (`write_off`, which carries the mandatory reason, as every write-off
    does). The instance ends `written_off`; both movements reference the
    issue line."""
    loss_reason = normalize_optional_text(reason, max_length=TEXT_MAX_LENGTH, field="reason")
    if loss_reason is None:
        raise InvalidInventoryDataError("reason is required")
    note = normalize_optional_text(comment, max_length=TEXT_MAX_LENGTH, field="comment")
    issue, balances = _changeable_issue(session, club_id=club_id, issue_id=issue_id)
    instance_lines = _instance_lines(balances, [instance_id])
    instances = _return(
        session,
        club_id=club_id,
        balances=balances,
        storage_location_id=storage_location_id,
        quantities={},
        instance_lines=instance_lines,
        comment=note,
        created_by=created_by,
    )
    instance = instances[instance_id]
    new_state = next_instance_state(instance.state, MOVEMENT_WRITE_OFF)
    session.add(
        _movement(
            item_id=instance.item_id,
            line_id=instance_lines[instance_id],
            movement_type=MOVEMENT_WRITE_OFF,
            created_by=created_by,
            instance_id=instance.id,
            from_location_id=instance.storage_location_id,
            comment=loss_reason,
        )
    )
    instance.state = new_state
    instance.storage_location_id = None
    issue.updated_by = created_by
    return _finish(session, issue)


__all__ = [
    "KIND_INSTANCE",
    "KIND_ISSUE_LINE",
    "KIND_EVENT",
    "InventoryIssueNotFoundError",
    "IssueLineRequest",
    "QuantityReturnRequest",
    "LineBalance",
    "UPDATABLE_ISSUE_FIELDS",
    "normalize_line_requests",
    "build_line_balances",
    "line_balances",
    "issues_with_outstanding",
    "recipient_columns",
    "recipient_id_of",
    "create_issue",
    "add_lines",
    "update_issue",
    "return_items",
    "cancel_issue",
    "report_lost",
]
