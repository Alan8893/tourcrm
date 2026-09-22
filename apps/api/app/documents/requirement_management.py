"""Manage `EventDocumentRequirement` records (TH-0117.5 / Issue #164;
ADR-0040 §5, docs/05-api/events-api.md §31.1).

Authorization is not decided here (mirrors every other module in this
package) — by the time any function below is called, the API router has
already established that the acting user holds the required Event and
Document permissions.

Audit: events-api.md §31.4 is explicit that a dedicated audit action for
this concept is not currently defined by ADR-0040, and that this task
must not invent one or repurpose a `document.*` action. No
`record_audit_event` call exists in this module, mirroring the identical,
already-shipped precedent in `app.events.service` (`EventStaffAssignment`/
`EventGroupTarget` create — a sibling Event-relationship entity with no
audit action of its own either).

Transaction shape mirrors `app.events.service.create_event_staff_
assignment`'s identical "write, commit, translate the one expected
IntegrityError" pattern: the `(event_id, document_type)` uniqueness
constraint is the only invariant the database itself must enforce, so no
pre-check/lock is needed before the write — the constraint violation
(if any) is caught and translated after the fact, and the failed insert
is rolled back before the typed error is raised.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.documents import EventDocumentRequirement

_UNIQUE_EVENT_DOCUMENT_TYPE_CONSTRAINT = "uq_event_document_requirements_event_id_type"


class EventDocumentRequirementError(Exception):
    """Base class for this module's typed, expected failures."""


class DuplicateEventDocumentRequirementError(EventDocumentRequirementError):
    """`(event_id, document_type)` already has a requirement row (the
    `uq_event_document_requirements_event_id_type` DB constraint,
    ADR-0040 §5)."""

    def __init__(self, *, event_id: uuid.UUID, document_type: str) -> None:
        super().__init__(
            f"Event {event_id} already has a document requirement for {document_type!r}"
        )
        self.event_id = event_id
        self.document_type = document_type


def _is_duplicate_violation(exc: IntegrityError) -> bool:
    """True only for a violation of
    `uq_event_document_requirements_event_id_type` — never for an
    unrelated IntegrityError, which must keep propagating unchanged.
    """
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _UNIQUE_EVENT_DOCUMENT_TYPE_CONSTRAINT


def list_event_document_requirements(
    session: Session, *, event_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[EventDocumentRequirement], int]:
    conditions = [EventDocumentRequirement.event_id == event_id]
    total = session.execute(
        sa.select(sa.func.count()).select_from(EventDocumentRequirement).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(EventDocumentRequirement)
            .where(*conditions)
            .order_by(EventDocumentRequirement.document_type)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def get_event_document_requirement(
    session: Session, *, event_id: uuid.UUID, requirement_id: uuid.UUID
) -> EventDocumentRequirement | None:
    """Scoped to `event_id` so a requirement belonging to a different
    Event can never be reached through this Event's path."""
    return session.execute(
        sa.select(EventDocumentRequirement).where(
            EventDocumentRequirement.id == requirement_id,
            EventDocumentRequirement.event_id == event_id,
        )
    ).scalar_one_or_none()


def create_event_document_requirement(
    session: Session, *, event_id: uuid.UUID, document_type: str, required: bool
) -> EventDocumentRequirement:
    """`required` is persisted exactly as supplied — never inferred
    (Issue #164). `document_type` remains an open string (ADR-0040 §1);
    no closed vocabulary/registry is introduced here.
    """
    requirement = EventDocumentRequirement(
        event_id=event_id, document_type=document_type, required=required
    )
    session.add(requirement)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_duplicate_violation(exc):
            raise DuplicateEventDocumentRequirementError(
                event_id=event_id, document_type=document_type
            ) from exc
        raise
    return requirement


def update_event_document_requirement(
    session: Session, *, requirement: EventDocumentRequirement, required: bool
) -> EventDocumentRequirement:
    """Changes only `required` — `document_type` is immutable
    (events-api.md §31.1: changing it means delete the existing
    requirement and create a new one, never a second mutation model)."""
    requirement.required = required
    session.commit()
    return requirement


def delete_event_document_requirement(
    session: Session, *, requirement: EventDocumentRequirement
) -> None:
    session.delete(requirement)
    session.commit()


__all__ = [
    "EventDocumentRequirementError",
    "DuplicateEventDocumentRequirementError",
    "list_event_document_requirements",
    "get_event_document_requirement",
    "create_event_document_requirement",
    "update_event_document_requirement",
    "delete_event_document_requirement",
]
