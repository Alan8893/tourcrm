"""Evaluate participant Documents against an Event's declared
EventDocumentRequirement rows (TH-0117.4 / Issue #162; ADR-0040 §5,
docs/05-api/events-api.md §31).

Repository-touching orchestration layer: reads `EventDocumentRequirement`
and current-version `Document` rows (via `app.documents.queries`) and
combines them through the pure `app.documents.validity.document_check_result`
engine. Authorization is not decided here (mirrors every other module in
this package) — callers (the API router) establish what the acting user
may see, and pass any resulting participant restriction in as a plain
SQL predicate.

Single readiness implementation. `evaluate_document_requirements` is the
one batch evaluator every caller goes through — the single-participant
check, the Event document matrix and the competition package export
(`app.documents.package`). It runs a fixed number of queries (one for the
current Documents of every participant x requirement pair) regardless of
how many participants or requirements there are; `resolve_requirement`
is the one place the `valid`/`missing`/`expired` combination rule lives.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.documents import Document, EventDocumentRequirement
from app.db.events import EventParticipation
from app.db.identity import Person
from app.documents.queries import list_current_documents_for_persons_by_types
from app.documents.validity import document_check_result
from app.events.participation import REGISTERED_STATUS

RequirementCheckResult = Literal["valid", "missing", "expired"]


@dataclass(frozen=True)
class DocumentRequirementCheck:
    """One EventDocumentRequirement row's result for a specific Person —
    the DTO the API layer serializes; carries no storage/File detail of
    any kind."""

    document_type: str
    required: bool
    result: RequirementCheckResult


@dataclass(frozen=True)
class RequirementEvaluation:
    """One participant/requirement pair. `document` is the concrete
    current Document that made a `valid` result (deterministically the
    lowest `document_group_id` among the valid candidates), else `None` —
    needed only by package export to read the right binary; never
    serialized by the API."""

    document_type: str
    required: bool
    result: RequirementCheckResult
    document: Optional[Document]

    def as_check(self) -> DocumentRequirementCheck:
        return DocumentRequirementCheck(
            document_type=self.document_type, required=self.required, result=self.result
        )


@dataclass(frozen=True)
class EventDocumentMatrixRow:
    person_id: uuid.UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]
    requirements: list[DocumentRequirementCheck]


def resolve_requirement(
    documents: Sequence[Document], *, now: datetime
) -> tuple[RequirementCheckResult, Optional[Document]]:
    """`valid`/`missing`/`expired` for one participant/`document_type`
    pair from that pair's current-version Documents (ADR-0040 §5).

    A Person may have more than one current-version Document of the same
    `document_type` (independent `document_group_id`s — see
    `list_current_documents_for_person_by_type`'s own docstring); any one
    of them resolving to `valid` satisfies the requirement. This is not
    "a historical version compensating for the current one" (explicitly
    forbidden by ADR-0040 §5/§4 and never done here — each candidate is
    already itself a *current* version of its own, independent group).
    No candidates -> `missing`; candidates but none valid -> `expired`
    (`document_check_result` already maps a current `revoked` to
    `expired`).
    """
    if not documents:
        return "missing", None
    valid_candidates = sorted(
        (
            document
            for document in documents
            if document_check_result(
                status=document.status, expires_at=document.expires_at, now=now
            )
            == "valid"
        ),
        key=lambda document: document.document_group_id,
    )
    if valid_candidates:
        return "valid", valid_candidates[0]
    return "expired", None


def list_event_requirements(
    session: Session, *, event_id: uuid.UUID
) -> list[EventDocumentRequirement]:
    """Every requirement of the Event, ordered by `document_type` (the
    `(event_id, document_type)` uniqueness constraint makes this a stable
    overall order)."""
    return list(
        session.execute(
            sa.select(EventDocumentRequirement)
            .where(EventDocumentRequirement.event_id == event_id)
            .order_by(EventDocumentRequirement.document_type)
        )
        .scalars()
        .all()
    )


def evaluate_document_requirements(
    session: Session,
    *,
    requirements: Sequence[EventDocumentRequirement],
    person_ids: Sequence[uuid.UUID],
    now: datetime,
) -> dict[uuid.UUID, list[RequirementEvaluation]]:
    """Batch evaluator: every `person_ids` x `requirements` pair against
    one `now`, with exactly one Document query in total (none when either
    side is empty). Each person's evaluations follow `requirements`' own
    order."""
    documents = list_current_documents_for_persons_by_types(
        session,
        person_ids=person_ids,
        document_types=[requirement.document_type for requirement in requirements],
    )
    by_pair: dict[tuple[uuid.UUID, str], list[Document]] = defaultdict(list)
    for document in documents:
        by_pair[(document.person_id, document.document_type)].append(document)

    evaluations: dict[uuid.UUID, list[RequirementEvaluation]] = {}
    for person_id in person_ids:
        row: list[RequirementEvaluation] = []
        for requirement in requirements:
            result, valid_document = resolve_requirement(
                by_pair.get((person_id, requirement.document_type), []), now=now
            )
            row.append(
                RequirementEvaluation(
                    document_type=requirement.document_type,
                    required=requirement.required,
                    result=result,
                    document=valid_document,
                )
            )
        evaluations[person_id] = row
    return evaluations


def check_person_document_requirements(
    session: Session, *, event_id: uuid.UUID, person_id: uuid.UUID, now: Optional[datetime] = None
) -> list[DocumentRequirementCheck]:
    """Evaluate every `EventDocumentRequirement` declared for `event_id`
    against `person_id`'s current participant Documents, ordered by
    `document_type`."""
    requirements = list_event_requirements(session, event_id=event_id)
    evaluations = evaluate_document_requirements(
        session,
        requirements=requirements,
        person_ids=[person_id],
        now=now if now is not None else datetime.now(timezone.utc),
    )
    return [evaluation.as_check() for evaluation in evaluations[person_id]]


def evaluate_event_document_matrix(
    session: Session,
    *,
    event_id: uuid.UUID,
    participant_filter: sa.ColumnElement[bool],
    now: Optional[datetime] = None,
) -> list[EventDocumentMatrixRow]:
    """Readiness of every registered participant of `event_id` (ADR-0037
    `registered`, the same set as package export and
    `GET /events/{event_id}/participants`) for every requirement.

    `participant_filter` is the caller's authorization restriction over
    `Person`/`EventParticipation` columns, applied inside the roster query
    itself. Participants are ordered `last_name`, `first_name`, Person id.
    Three queries in total (requirements, roster, documents), independent
    of participant/requirement counts.
    """
    requirements = list_event_requirements(session, event_id=event_id)
    participants = session.execute(
        sa.select(Person.id, Person.first_name, Person.last_name, Person.middle_name)
        .select_from(EventParticipation)
        .join(Person, Person.id == EventParticipation.person_id)
        .where(
            EventParticipation.event_id == event_id,
            EventParticipation.registration_status == REGISTERED_STATUS,
            participant_filter,
        )
        .order_by(Person.last_name, Person.first_name, Person.id)
    ).all()
    evaluations = evaluate_document_requirements(
        session,
        requirements=requirements,
        person_ids=[row.id for row in participants],
        now=now if now is not None else datetime.now(timezone.utc),
    )
    return [
        EventDocumentMatrixRow(
            person_id=row.id,
            first_name=row.first_name,
            last_name=row.last_name,
            middle_name=row.middle_name,
            requirements=[evaluation.as_check() for evaluation in evaluations[row.id]],
        )
        for row in participants
    ]


__all__ = [
    "DocumentRequirementCheck",
    "EventDocumentMatrixRow",
    "RequirementEvaluation",
    "check_person_document_requirements",
    "evaluate_document_requirements",
    "evaluate_event_document_matrix",
    "list_event_requirements",
    "resolve_requirement",
]
