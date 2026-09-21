"""Evaluate one Person's participant Documents against an Event's declared
EventDocumentRequirement rows (TH-0117.4 / Issue #162; ADR-0040 §5,
docs/05-api/events-api.md §31).

Repository-touching orchestration layer: reads `EventDocumentRequirement`
and current-version `Document` rows (via `app.documents.queries`) and
combines them through the pure `app.documents.validity.document_check_result`
engine. Authorization is not decided here (mirrors every other module in
this package) — by the time `check_person_document_requirements` is
called, the caller (the API router) has already established that the
acting user is authorized to see both the Event and this Person's
document-derived data.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.documents import EventDocumentRequirement
from app.documents.queries import list_current_documents_for_person_by_type
from app.documents.validity import document_check_result

RequirementCheckResult = Literal["valid", "missing", "expired"]


@dataclass(frozen=True)
class DocumentRequirementCheck:
    """One EventDocumentRequirement row's result for a specific Person —
    the DTO the API layer serializes; carries no storage/File detail of
    any kind."""

    document_type: str
    required: bool
    result: RequirementCheckResult


def _evaluate_document_type(
    session: Session, *, person_id: uuid.UUID, document_type: str, now: Optional[datetime]
) -> RequirementCheckResult:
    """`valid`/`missing`/`expired` for one `document_type` (ADR-0040 §5).

    A Person may have more than one current-version Document of the same
    `document_type` (independent `document_group_id`s — see
    `list_current_documents_for_person_by_type`'s own docstring); any one
    of them resolving to `valid` satisfies the requirement. This is not
    "a historical version compensating for the current one" (explicitly
    forbidden by ADR-0040 §5/§4 and never done here — each candidate
    document here is already itself a *current* version of its own,
    independent group) — it is simply evaluating every current document
    of the requested type the participant actually has.
    """
    documents = list_current_documents_for_person_by_type(
        session, person_id=person_id, document_type=document_type
    )
    if not documents:
        return "missing"
    results = {
        document_check_result(status=document.status, expires_at=document.expires_at, now=now)
        for document in documents
    }
    return "valid" if "valid" in results else "expired"


def check_person_document_requirements(
    session: Session, *, event_id: uuid.UUID, person_id: uuid.UUID, now: Optional[datetime] = None
) -> list[DocumentRequirementCheck]:
    """Evaluate every `EventDocumentRequirement` declared for `event_id`
    against `person_id`'s current participant Documents. Deterministic,
    stable ordering by `document_type` (the `(event_id, document_type)`
    uniqueness constraint means at most one requirement row per type, so
    this is also a stable overall ordering).
    """
    requirements = (
        session.execute(
            sa.select(EventDocumentRequirement)
            .where(EventDocumentRequirement.event_id == event_id)
            .order_by(EventDocumentRequirement.document_type)
        )
        .scalars()
        .all()
    )
    return [
        DocumentRequirementCheck(
            document_type=requirement.document_type,
            required=requirement.required,
            result=_evaluate_document_type(
                session, person_id=person_id, document_type=requirement.document_type, now=now
            ),
        )
        for requirement in requirements
    ]


__all__ = ["DocumentRequirementCheck", "check_person_document_requirements"]
