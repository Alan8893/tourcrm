"""Read queries for participant Documents (TH-0117.3 / Issue #160).

Canonical sources: ADR-0040 §4, docs/05-api/people-api.md §32.

Authorization is not decided here (see package docstring): by the time
either function below is called, the API router has already established
that the acting user is authorized (`document.read`) to see `person_id`'s
documents at all. `Document` has no authorization scope of its own beyond
its owning Person (ADR-0040 §2 — explicit, non-polymorphic `person_id`
association, no other subject) — so once that one check has passed, every
row for that Person is visible; no further per-row filtering applies.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.documents import Document


def list_current_documents_for_person(
    session: Session, *, person_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[Document], int]:
    """Current versions only (ADR-0040 §4: the row with `MAX(version_number)`
    per `document_group_id`) — never historical versions, per
    people-api.md §32.
    """
    current_versions = (
        sa.select(
            Document.document_group_id,
            sa.func.max(Document.version_number).label("max_version"),
        )
        .where(Document.person_id == person_id)
        .group_by(Document.document_group_id)
        .subquery()
    )
    stmt = (
        sa.select(Document)
        .join(
            current_versions,
            sa.and_(
                Document.document_group_id == current_versions.c.document_group_id,
                Document.version_number == current_versions.c.max_version,
            ),
        )
        .where(Document.person_id == person_id)
    )

    total = session.execute(sa.select(sa.func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        session.execute(
            stmt.order_by(Document.document_type, Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_current_documents_for_person_by_type(
    session: Session, *, person_id: uuid.UUID, document_type: str
) -> list[Document]:
    """Current versions of `document_type` only (ADR-0040 §4: the row
    with `MAX(version_number)` per `document_group_id`), unpaginated —
    used by app.documents.event_requirements to evaluate one
    EventDocumentRequirement, never by a client-facing list endpoint.

    Ordinarily returns at most one row (a participant document normally
    has a single logical group per type), but a Person is not prevented
    from having more than one independent `document_group_id` of the
    same `document_type` (TH-0117.3's create endpoint always starts a new
    group; there is no "attach to an existing group" operation) — this
    function returns every such current version so the caller can decide
    how to combine them, rather than silently picking one.
    """
    current_versions = (
        sa.select(
            Document.document_group_id,
            sa.func.max(Document.version_number).label("max_version"),
        )
        .where(Document.person_id == person_id, Document.document_type == document_type)
        .group_by(Document.document_group_id)
        .subquery()
    )
    stmt = (
        sa.select(Document)
        .join(
            current_versions,
            sa.and_(
                Document.document_group_id == current_versions.c.document_group_id,
                Document.version_number == current_versions.c.max_version,
            ),
        )
        .where(Document.person_id == person_id, Document.document_type == document_type)
        .order_by(Document.created_at)
    )
    return list(session.execute(stmt).scalars().all())


def get_current_document_version(session: Session, *, document_group_id: uuid.UUID) -> Document:
    """The row with `MAX(version_number)` for `document_group_id`
    (ADR-0040 §4) — used by `app.documents.service.replace_document` to
    determine whether a given Document is still the current version of
    its group before replacing it. Assumes at least one version already
    exists for `document_group_id`; callers only ever reach this with an
    already-loaded Document belonging to that group.
    """
    return session.execute(
        sa.select(Document)
        .where(Document.document_group_id == document_group_id)
        .order_by(Document.version_number.desc())
        .limit(1)
    ).scalar_one()


def get_document_for_person(
    session: Session, *, person_id: uuid.UUID, document_id: uuid.UUID
) -> Document | None:
    """A single version — current or historical (people-api.md §32's
    detail endpoint explicitly covers both) — addressed by its own
    `Document.id`, scoped to `person_id` so a document belonging to a
    different Person can never be reached through this Person's path.
    """
    return session.execute(
        sa.select(Document).where(Document.id == document_id, Document.person_id == person_id)
    ).scalar_one_or_none()


__all__ = [
    "list_current_documents_for_person",
    "list_current_documents_for_person_by_type",
    "get_current_document_version",
    "get_document_for_person",
]
