"""Read queries for ImportJob, its errors/warnings, and the existing-data
lookup used by duplicate detection (TH-0118.1 / Issue #185; TH-0118.2).

Authorization is not decided here: by the time any function below is
called, the API router has already established that the requester may
access the job (app.imports.authorization.can_access_import_job). Every
error query is filtered by exactly one `import_job_id`, so one job's
errors are never listed under — or mixed with — another's.

`find_existing_person_matches` only reads Person/User rows; it never
writes to them.
"""

import uuid
from collections import defaultdict
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.identity import Person, User
from app.db.imports import ImportJob, ImportJobError
from app.imports.rows import NormalizedRow


def get_import_job(session: Session, *, import_job_id: uuid.UUID) -> ImportJob | None:
    return session.get(ImportJob, import_job_id)


def _errors_filter(import_job_id: uuid.UUID, severity: str | None) -> list[sa.ColumnElement[bool]]:
    conditions = [ImportJobError.import_job_id == import_job_id]
    if severity is not None:
        conditions.append(ImportJobError.severity == severity)
    return conditions


def count_import_job_errors(
    session: Session, *, import_job_id: uuid.UUID, severity: str | None = None
) -> int:
    return session.execute(
        sa.select(sa.func.count())
        .select_from(ImportJobError)
        .where(*_errors_filter(import_job_id, severity))
    ).scalar_one()


def list_import_job_errors_page(
    session: Session,
    *,
    import_job_id: uuid.UUID,
    page: int,
    page_size: int,
    severity: str | None = None,
) -> tuple[list[ImportJobError], int]:
    """One page of `import_job_id`'s errors/warnings (optionally only one
    severity) in source order: by `row_number` (file-level entries first),
    then insertion time, then `id` as a stable tie-breaker so pages never
    overlap or skip."""
    total = count_import_job_errors(session, import_job_id=import_job_id, severity=severity)
    rows = (
        session.execute(
            sa.select(ImportJobError)
            .where(*_errors_filter(import_job_id, severity))
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


def find_existing_person_matches(
    session: Session, rows: Sequence[NormalizedRow]
) -> dict[int, list[tuple[uuid.UUID, str]]]:
    """For each row, the existing Persons it exactly matches and the rule
    that matched: `email` (Person.email or the Person's User login
    identifier, both compared as `normalize_login_identifier()` would
    produce them), `phone` (trimmed Person.phone) and `name_birth_date`
    (trimmed first/last name + birth_date). `external_id` has no persisted
    counterpart and is never looked up here."""
    matches: dict[int, list[tuple[uuid.UUID, str]]] = defaultdict(list)

    rows_by_email: dict[str, list[int]] = defaultdict(list)
    rows_by_phone: dict[str, list[int]] = defaultdict(list)
    rows_by_name: dict[tuple, list[int]] = defaultdict(list)
    for row in rows:
        if row.email:
            rows_by_email[row.email].append(row.row_number)
        if row.phone:
            rows_by_phone[row.phone].append(row.row_number)
        if row.first_name and row.last_name and row.birth_date:
            rows_by_name[(row.first_name, row.last_name, row.birth_date)].append(row.row_number)

    if rows_by_email:
        person_email = sa.func.lower(sa.func.trim(Person.email))
        found = list(
            session.execute(
                sa.select(Person.id, person_email).where(person_email.in_(list(rows_by_email)))
            ).all()
        )
        found += session.execute(
            sa.select(User.person_id, User.normalized_login_identifier).where(
                User.normalized_login_identifier.in_(list(rows_by_email))
            )
        ).all()
        for person_id, email in found:
            for row_number in rows_by_email[email]:
                matches[row_number].append((person_id, "email"))

    if rows_by_phone:
        person_phone = sa.func.trim(Person.phone)
        for person_id, phone in session.execute(
            sa.select(Person.id, person_phone).where(person_phone.in_(list(rows_by_phone)))
        ).all():
            for row_number in rows_by_phone[phone]:
                matches[row_number].append((person_id, "phone"))

    if rows_by_name:
        first_name = sa.func.trim(Person.first_name)
        last_name = sa.func.trim(Person.last_name)
        for person_id, first, last, birth_date in session.execute(
            sa.select(Person.id, first_name, last_name, Person.birth_date).where(
                sa.tuple_(first_name, last_name, Person.birth_date).in_(list(rows_by_name))
            )
        ).all():
            for row_number in rows_by_name[(first, last, birth_date)]:
                matches[row_number].append((person_id, "name_birth_date"))

    return dict(matches)


__all__ = [
    "get_import_job",
    "count_import_job_errors",
    "list_import_job_errors_page",
    "find_existing_person_matches",
]
