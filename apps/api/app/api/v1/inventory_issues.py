"""Inventory Slice 4 API — issue / return (Issue #236;
docs/04-domain/inventory.md §14, §17).

    GET   /inventory/issues                    list (status, recipient, event)
    POST  /inventory/issues                    create — issued at once
    GET   /inventory/issues/{issue_id}         detail with lines
    PATCH /inventory/issues/{issue_id}         recipient / Event / planned date / comment
    POST  /inventory/issues/{issue_id}/lines   issue more items (same item -> same line)
    GET   /inventory/issues/{issue_id}/lines   lines with issued / returned / outstanding
                                               (status=active|removed|all)
    DELETE /inventory/issues/{issue_id}/lines/{line_id}
                                               remove a line with nothing outstanding
    POST  /inventory/issues/{issue_id}/returns partial or full return into a location
    POST  /inventory/issues/{issue_id}/cancel  return everything outstanding, `cancelled`
    POST  /inventory/issues/{issue_id}/lost    lost instance: return + write-off
    GET   /inventory/issues/{issue_id}/movements chronological history of the issue

Issues are never deleted, and neither are line rows: removing a line only
takes it out of the issue's working composition (`removed_at`), its
movements stay. A cancelled or fully returned issue is immutable.

Authorization (inventory.md §3): every endpoint requires the Administrator
(app.inventory.authorization), checked before the issue is loaded, so a
non-Administrator gets the same 403 whether or not the id exists. An issue
of another Club is a 404.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.inventory_errors import INVENTORY_DOMAIN_ERRORS, raise_inventory_domain_error
from app.api.v1.inventory_instances_schemas import InventoryMovementOut
from app.api.v1.inventory_issues_schemas import (
    InventoryIssueAddLinesRequest,
    InventoryIssueCancelRequest,
    InventoryIssueCreateRequest,
    InventoryIssueDetailOut,
    InventoryIssueLineOut,
    InventoryIssueLineRequest,
    InventoryIssueLostRequest,
    InventoryIssueOut,
    InventoryIssueReturnRequest,
    InventoryIssueUpdateRequest,
    IssueStatusLiteral,
    LineStatusLiteral,
    RecipientTypeLiteral,
)
from app.db.inventory import InventoryIssue, InventoryIssueLine
from app.db.session import get_db
from app.inventory import issues as issue_service
from app.inventory.authorization import require_inventory_administrator
from app.inventory.queries import get_issue, list_issue_lines, list_issue_movements, list_issues

logger = logging.getLogger("tourcrm.api.inventory.issues")

router = APIRouter(prefix="/inventory", tags=["inventory"])

_PAGE = Query(default=1, ge=1)
_PAGE_SIZE = Query(default=50, ge=1, le=200)


def _not_found() -> APIError:
    return APIError(status.HTTP_404_NOT_FOUND, "not_found", "Inventory issue not found")


def _invalid(message: str) -> APIError:
    return APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_inventory_data", message)


def _issue_or_404(db: Session, *, issue_id: uuid.UUID, club_id: uuid.UUID) -> InventoryIssue:
    issue = get_issue(db, issue_id=issue_id, club_id=club_id)
    if issue is None:
        raise _not_found()
    return issue


def _pagination(page: int, page_size: int, total: int) -> Pagination:
    pages = (total + page_size - 1) // page_size if total else 0
    return Pagination(page=page, page_size=page_size, total=total, pages=pages)


def _line_requests(
    lines: list[InventoryIssueLineRequest],
) -> list[issue_service.IssueLineRequest]:
    return [
        issue_service.IssueLineRequest(
            item_id=line.item_id,
            quantity=line.quantity,
            instance_ids=tuple(line.instance_ids or ()),
        )
        for line in lines
    ]


def _issue_out(issue: InventoryIssue, *, has_outstanding: bool) -> InventoryIssueOut:
    return InventoryIssueOut(
        id=issue.id,
        recipient_type=issue.recipient_type,  # type: ignore[arg-type]
        recipient_id=issue_service.recipient_id_of(issue),
        event_id=issue.event_id,
        planned_return_date=issue.planned_return_date,
        comment=issue.comment,
        status=issue.status,  # type: ignore[arg-type]
        has_outstanding=has_outstanding,
        cancelled_at=issue.cancelled_at,
        cancelled_by=issue.cancelled_by,
        created_by=issue.created_by,
        created_at=issue.created_at,
        updated_by=issue.updated_by,
        updated_at=issue.updated_at,
    )


def _lines_out(
    db: Session, issue: InventoryIssue, *, line_status: str = "active"
) -> list[InventoryIssueLineOut]:
    balances = issue_service.line_balances(db, [issue.id])
    return [
        _line_out(line, balances[line.id])
        for line in list_issue_lines(db, issue_id=issue.id)
        if line_status == "all" or (line.removed_at is not None) == (line_status == "removed")
    ]


def _line_out(
    line: InventoryIssueLine, balance: issue_service.LineBalance
) -> InventoryIssueLineOut:
    return InventoryIssueLineOut(
        id=line.id,
        issue_id=line.issue_id,
        item_id=line.item_id,
        accounting_mode=balance.accounting_mode,  # type: ignore[arg-type]
        issued_quantity=balance.issued,
        returned_quantity=balance.returned,
        outstanding_quantity=balance.outstanding,
        outstanding_instance_ids=balance.outstanding_instance_ids,
        created_by=line.created_by,
        created_at=line.created_at,
        removed_at=line.removed_at,
        removed_by=line.removed_by,
    )


def _detail_out(db: Session, issue: InventoryIssue) -> InventoryIssueDetailOut:
    lines = _lines_out(db, issue)
    summary = _issue_out(
        issue, has_outstanding=any(line.outstanding_quantity > 0 for line in lines)
    )
    return InventoryIssueDetailOut(**summary.model_dump(), lines=lines)


@router.get("/issues", response_model=CollectionResponse[InventoryIssueOut])
def list_inventory_issues(
    issue_status: Optional[IssueStatusLiteral] = Query(default=None, alias="status"),
    recipient_type: Optional[RecipientTypeLiteral] = Query(default=None),
    recipient_id: Optional[uuid.UUID] = Query(default=None),
    event_id: Optional[uuid.UUID] = Query(default=None),
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryIssueOut]:
    """Issue documents, newest first."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    rows, total = list_issues(
        db,
        club_id=club_id,
        status=issue_status,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        event_id=event_id,
        page=page,
        page_size=page_size,
    )
    outstanding = issue_service.issues_with_outstanding(db, [row.id for row in rows])
    return CollectionResponse(
        items=[_issue_out(row, has_outstanding=row.id in outstanding) for row in rows],
        pagination=_pagination(page, page_size, total),
    )


@router.post(
    "/issues", status_code=status.HTTP_201_CREATED, response_model=InventoryIssueDetailOut
)
def create_inventory_issue(
    payload: InventoryIssueCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    try:
        issue = issue_service.create_issue(
            db,
            club_id=club_id,
            recipient_type=payload.recipient_type,
            recipient_id=payload.recipient_id,
            event_id=payload.event_id,
            planned_return_date=payload.planned_return_date,
            comment=payload.comment,
            lines=_line_requests(payload.lines),
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.create.success issue_id=%s", issue.id)
    return _detail_out(db, issue)


@router.get("/issues/{issue_id}", response_model=InventoryIssueDetailOut)
def get_inventory_issue(
    issue_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> InventoryIssueDetailOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    return _detail_out(db, _issue_or_404(db, issue_id=issue_id, club_id=club_id))


@router.patch("/issues/{issue_id}", response_model=InventoryIssueDetailOut)
def update_inventory_issue(
    issue_id: uuid.UUID,
    payload: InventoryIssueUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    sent = payload.model_fields_set
    fields: dict[str, object] = {
        name: getattr(payload, name)
        for name in ("event_id", "planned_return_date", "comment")
        if name in sent
    }
    if {"recipient_type", "recipient_id"} & sent:
        if payload.recipient_type is None or payload.recipient_id is None:
            raise _invalid("recipient_type and recipient_id must be given together")
        fields["recipient"] = (payload.recipient_type, payload.recipient_id)
    if not fields:
        raise _invalid("nothing to update")
    try:
        issue = issue_service.update_issue(
            db, club_id=club_id, issue_id=issue_id, fields=fields, updated_by=principal.user_id
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.update.success issue_id=%s", issue.id)
    return _detail_out(db, issue)


@router.post(
    "/issues/{issue_id}/lines",
    status_code=status.HTTP_201_CREATED,
    response_model=InventoryIssueDetailOut,
)
def add_inventory_issue_lines(
    issue_id: uuid.UUID,
    payload: InventoryIssueAddLinesRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    """Issues more items; an item already on the issue gets more `issue`
    movements on its existing line."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    try:
        issue = issue_service.add_lines(
            db,
            club_id=club_id,
            issue_id=issue_id,
            lines=_line_requests(payload.lines),
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.lines.success issue_id=%s", issue.id)
    return _detail_out(db, issue)


@router.get("/issues/{issue_id}/lines", response_model=CollectionResponse[InventoryIssueLineOut])
def list_inventory_issue_lines(
    issue_id: uuid.UUID,
    line_status: LineStatusLiteral = Query(default="active", alias="status"),
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryIssueLineOut]:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    issue = _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    lines = _lines_out(db, issue, line_status=line_status)
    start = (page - 1) * page_size
    return CollectionResponse(
        items=lines[start : start + page_size],
        pagination=_pagination(page, page_size, len(lines)),
    )


@router.delete("/issues/{issue_id}/lines/{line_id}", response_model=InventoryIssueDetailOut)
def remove_inventory_issue_line(
    issue_id: uuid.UUID,
    line_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    """Removes a line with nothing outstanding from the issue's working
    composition; the line and its movements stay as history. Returns the
    issue."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    try:
        issue = issue_service.remove_line(
            db, club_id=club_id, issue_id=issue_id, line_id=line_id, removed_by=principal.user_id
        )
    except issue_service.InventoryIssueLineNotFoundError as exc:
        raise APIError(
            status.HTTP_404_NOT_FOUND, "not_found", "Inventory issue line not found"
        ) from exc
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.line_remove.success issue_id=%s line_id=%s", issue.id, line_id)
    return _detail_out(db, issue)


@router.post("/issues/{issue_id}/returns", response_model=InventoryIssueDetailOut)
def return_inventory_issue_items(
    issue_id: uuid.UUID,
    payload: InventoryIssueReturnRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    try:
        issue = issue_service.return_items(
            db,
            club_id=club_id,
            issue_id=issue_id,
            storage_location_id=payload.storage_location_id,
            quantities=[
                issue_service.QuantityReturnRequest(line_id=q.line_id, quantity=q.quantity)
                for q in payload.quantities
            ],
            instance_ids=payload.instance_ids,
            comment=payload.comment,
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.return.success issue_id=%s", issue.id)
    return _detail_out(db, issue)


@router.post("/issues/{issue_id}/cancel", response_model=InventoryIssueDetailOut)
def cancel_inventory_issue(
    issue_id: uuid.UUID,
    payload: InventoryIssueCancelRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    try:
        issue = issue_service.cancel_issue(
            db,
            club_id=club_id,
            issue_id=issue_id,
            storage_location_id=payload.storage_location_id,
            cancelled_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.cancel.success issue_id=%s", issue.id)
    return _detail_out(db, issue)


@router.post("/issues/{issue_id}/lost", response_model=InventoryIssueDetailOut)
def report_inventory_issue_lost_instance(
    issue_id: uuid.UUID,
    payload: InventoryIssueLostRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> InventoryIssueDetailOut:
    """Lost instance: returned into the location and written off in one
    transaction; the reason is the write-off reason."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    try:
        issue = issue_service.report_lost(
            db,
            club_id=club_id,
            issue_id=issue_id,
            instance_id=payload.instance_id,
            storage_location_id=payload.storage_location_id,
            reason=payload.reason,
            comment=payload.comment,
            created_by=principal.user_id,
        )
    except INVENTORY_DOMAIN_ERRORS as exc:
        raise_inventory_domain_error(exc)
    logger.info("inventory.issue.lost.success issue_id=%s", issue.id)
    return _detail_out(db, issue)


@router.get(
    "/issues/{issue_id}/movements", response_model=CollectionResponse[InventoryMovementOut]
)
def list_inventory_issue_movements(
    issue_id: uuid.UUID,
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[InventoryMovementOut]:
    """Chronological, immutable history of the issue (§13)."""
    club_id = require_inventory_administrator(db, user_id=principal.user_id)
    issue = _issue_or_404(db, issue_id=issue_id, club_id=club_id)
    rows, total = list_issue_movements(db, issue_id=issue.id, page=page, page_size=page_size)
    return CollectionResponse(
        items=[InventoryMovementOut.model_validate(row, from_attributes=True) for row in rows],
        pagination=_pagination(page, page_size, total),
    )
