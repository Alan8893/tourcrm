"""Read queries for ImportJob and its errors (TH-0118.1 / Issue #185).

Authorization is not decided here: by the time any function below is
called, the API router has already established that the requester may
access the job (app.imports.authorization.can_access_import_job). Every
error query is filtered by exactly one `import_job_id`, so one job's
errors are never listed under — or mixed with — another's.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.imports import ImportJob, ImportJobError


def get_import_job(session: Session, *, import_job_id: uuid.UUID) -> ImportJob | None:
    return session.get(ImportJob, import_job_id)


def count_import_job_errors(session: Session, *, import_job_id: uuid.UUID) -> int:
    """The job's `error_count` statistic — always derived from its own
    recorded error rows, never stored separately."""
    return session.execute(
        sa.select(sa.func.count())
        .select_from(ImportJobError)
        .where(ImportJobError.import_job_id == import_job_id)
    ).scalar_one()


def list_import_job_errors_page(
    session: Session, *, import_job_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[ImportJobError], int]:
    """One page of `import_job_id`'s errors in source order: by
    `row_number` (errors not tied to a row first), then insertion time,
    then `id` as a stable tie-breaker so pages never overlap or skip."""
    total = count_import_job_errors(session, import_job_id=import_job_id)
    rows = (
        session.execute(
            sa.select(ImportJobError)
            .where(ImportJobError.import_job_id == import_job_id)
            .order_by(
                ImportJobError.row_number.asc().nulls_first(),
                ImportJobError.created_at,
                ImportJobError.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


__all__ = [
    "get_import_job",
    "count_import_job_errors",
    "list_import_job_errors_page",
]
