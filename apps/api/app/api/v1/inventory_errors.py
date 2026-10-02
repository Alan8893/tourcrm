"""Inventory domain errors -> canonical API errors (ADR-0014), shared by the
Inventory routers (app.api.v1.inventory, app.api.v1.inventory_instances,
app.api.v1.inventory_quantities, app.api.v1.inventory_issues).

409 — the request conflicts with the current state of a record;
422 — the request itself is invalid (bad/unusable reference or value).
"""

from typing import NoReturn

from fastapi import status

from app.api.errors import APIError
from app.inventory import service as inventory_service
from app.inventory.lifecycle import (
    AccountingModeLockedError,
    ArchivedReferenceError,
    InstanceNotOutstandingError,
    InstanceWrittenOffError,
    InsufficientStockError,
    InvalidInstanceTransitionError,
    InvalidInventoryDataError,
    InvalidLocationParentError,
    InvalidRecipientError,
    InvalidReversalTargetError,
    InventoryDomainError,
    InventoryRecordArchivedError,
    IssueCancelledError,
    IssueFullyReturnedError,
    IssueLineOutstandingError,
    IssueLineRemovedError,
    ItemHasActiveInstancesError,
    ItemHasOutstandingIssuesError,
    ItemHasStockError,
    ItemNotInstanceModeError,
    ItemNotQuantityModeError,
    LocationHasActiveChildrenError,
    LocationHasInstancesError,
    LocationHasStockError,
    ReturnExceedsOutstandingError,
    ReversalLocationNotAllowedError,
    ReversalLocationRequiredError,
    SystemUnitImmutableError,
    UnitLockedError,
    WriteOffAlreadyReversedError,
)

INVENTORY_DOMAIN_ERRORS = (
    InventoryDomainError,
    inventory_service.InventoryReferenceNotFoundError,
    inventory_service.InventoryNameConflictError,
)

_CONFLICTS: tuple[tuple[type[Exception], str], ...] = (
    (InventoryRecordArchivedError, "inventory_record_archived"),
    (SystemUnitImmutableError, "system_unit_immutable"),
    (AccountingModeLockedError, "accounting_mode_locked"),
    (UnitLockedError, "unit_locked"),
    (LocationHasActiveChildrenError, "location_has_active_children"),
    (LocationHasInstancesError, "location_has_instances"),
    (ItemHasActiveInstancesError, "item_has_active_instances"),
    (ItemHasStockError, "item_has_stock"),
    (ItemHasOutstandingIssuesError, "item_has_outstanding_issues"),
    (IssueCancelledError, "issue_cancelled"),
    (IssueFullyReturnedError, "issue_fully_returned"),
    (ReturnExceedsOutstandingError, "return_exceeds_outstanding"),
    (InstanceNotOutstandingError, "instance_not_issued"),
    (IssueLineOutstandingError, "issue_line_outstanding"),
    (IssueLineRemovedError, "issue_line_removed"),
    (LocationHasStockError, "location_has_stock"),
    (InsufficientStockError, "insufficient_stock"),
    (InvalidInstanceTransitionError, "invalid_state_transition"),
    (InstanceWrittenOffError, "instance_written_off"),
    (WriteOffAlreadyReversedError, "write_off_already_reversed"),
    (inventory_service.InventoryNameConflictError, "name_conflict"),
)

_UNPROCESSABLE: tuple[tuple[type[Exception], str], ...] = (
    (ArchivedReferenceError, "archived_reference"),
    (inventory_service.InventoryReferenceNotFoundError, "invalid_reference"),
    (InvalidLocationParentError, "invalid_parent"),
    (ItemNotInstanceModeError, "item_not_instance_mode"),
    (ItemNotQuantityModeError, "item_not_quantity_mode"),
    (InvalidReversalTargetError, "invalid_reversal_target"),
    (InvalidRecipientError, "invalid_recipient"),
    (ReversalLocationRequiredError, "storage_location_required"),
    (ReversalLocationNotAllowedError, "storage_location_not_allowed"),
    (InvalidInventoryDataError, "invalid_inventory_data"),
)


def raise_inventory_domain_error(exc: Exception) -> NoReturn:
    for error_type, code in _CONFLICTS:
        if isinstance(exc, error_type):
            raise APIError(status.HTTP_409_CONFLICT, code, str(exc)) from exc
    for error_type, code in _UNPROCESSABLE:
        if isinstance(exc, error_type):
            raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, code, str(exc)) from exc
    raise exc


__all__ = ["INVENTORY_DOMAIN_ERRORS", "raise_inventory_domain_error"]
