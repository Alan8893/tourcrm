"""Synchronous import preview: parse -> validate -> detect duplicates
(TH-0118.2).

Canonical sources: docs/05-api/people-api.md §22
("POST /memberships/imports/{import_id}/preview", "Preview"),
docs/04-modules/people-and-membership.md §11.3/§11.4.

Authorization is not decided here — the API router has already checked
the ImportJob object policy (app.imports.authorization) before calling
`run_import_preview`.

Lifecycle (only through app.imports.service.transition_import_job_status,
each step committed):

    uploaded -> parsing -> validating -> preview_ready
    parsing -> failed      (the source file cannot be read/parsed)
    validating -> failed   (validation could not be completed)

A job not in `uploaded` is rejected with `ImportJobNotUploadedError`
(the lifecycle only allows `uploaded -> parsing`, and the transition
re-reads the job under `SELECT ... FOR UPDATE`, so two concurrent preview
requests cannot both start). The reason a job failed is recorded as a
file-level `error` ImportJobError row.

Row-level validation errors do not fail the job: they make their row
invalid and the job still reaches `preview_ready`. The preview is the
job's counters plus its ImportJobError rows. This is a dry-run: the only
rows written are the job's own status/counters and its ImportJobError
rows — Person/User/ClubMembership/RoleAssignment/GroupMembership/
GuardianRelationship (or any other domain entity) are never created or
changed, and existing Person/User rows are only read.
"""

import logging

from sqlalchemy.orm import Session

from app.db.documents import File
from app.db.imports import ImportJob, ImportJobError
from app.imports.duplicates import detect_duplicates
from app.imports.lifecycle import InvalidImportJobStatusTransitionError
from app.imports.parsing import ImportParseError, parse_source
from app.imports.queries import find_existing_person_matches
from app.imports.rows import SEVERITY_ERROR, ImportIssue, NormalizedRow, normalize_and_validate
from app.imports.service import transition_import_job_status
from app.storage.file_storage import FileStorage, FileStorageError

logger = logging.getLogger("tourcrm.imports")

IMPORT_VALIDATION_FAILED_CODE = "import_validation_failed"


class ImportJobNotUploadedError(Exception):
    """Preview can only start from `uploaded` (people-api.md §22)."""

    def __init__(self, status: str) -> None:
        super().__init__(f"Import job is {status!r}, not 'uploaded'")
        self.status = status


def _to_row(job: ImportJob, issue: ImportIssue) -> ImportJobError:
    return ImportJobError(
        import_job_id=job.id,
        row_number=issue.row_number,
        field=issue.field,
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        matched_person_id=issue.matched_person_id,
    )


def _fail(session: Session, job: ImportJob, issue: ImportIssue) -> ImportJob:
    """Discard any partial preview work, record why the job failed, and
    move it to `failed` — in one transaction."""
    session.rollback()
    session.add(_to_row(job, issue))
    session.flush()
    return transition_import_job_status(session, job=job, new_status="failed")


def _unexpected_failure(session: Session, job: ImportJob, stage: str) -> ImportJob:
    # Roll back first: the failed transaction may be unusable, and reading
    # even `job.id` from an expired instance would need it.
    session.rollback()
    logger.exception("imports.preview.failed import_id=%s stage=%s", job.id, stage)
    return _fail(
        session,
        job,
        ImportIssue(
            code=IMPORT_VALIDATION_FAILED_CODE,
            message="The import file could not be processed",
            severity=SEVERITY_ERROR,
        ),
    )


def run_import_preview(session: Session, storage: FileStorage, *, job: ImportJob) -> ImportJob:
    """Run parsing, validation and duplicate detection for `job` and leave
    it in `preview_ready` or `failed`. Raises ImportJobNotUploadedError
    (touching nothing) if the job is not in `uploaded`."""
    try:
        transition_import_job_status(session, job=job, new_status="parsing")
    except InvalidImportJobStatusTransitionError as exc:
        raise ImportJobNotUploadedError(exc.from_status) from exc

    # --- parsing -----------------------------------------------------------
    try:
        source_file = session.get(File, job.source_file_id)
        if source_file is None:
            raise FileStorageError(f"File row {job.source_file_id} is missing")
        content = storage.get(source_file.storage_key)
        parsed = parse_source(content, job.source_format)
    except ImportParseError as exc:
        return _fail(
            session,
            job,
            ImportIssue(
                code=exc.code,
                message=exc.message,
                severity=SEVERITY_ERROR,
                row_number=exc.row_number,
                field=exc.field,
            ),
        )
    except FileStorageError:
        session.rollback()
        logger.exception("imports.preview.source_unavailable import_id=%s", job.id)
        return _fail(
            session,
            job,
            ImportIssue(
                code="import_file_unreadable",
                message="The uploaded source file could not be read",
                severity=SEVERITY_ERROR,
            ),
        )
    except Exception:
        return _unexpected_failure(session, job, "parsing")

    transition_import_job_status(session, job=job, new_status="validating")

    # --- validating (normalization, validation, duplicate detection) ----------
    try:
        rows: list[NormalizedRow] = []
        issues: list[ImportIssue] = []
        for record in parsed.records:
            row, row_issues = normalize_and_validate(record, source_format=job.source_format)
            rows.append(row)
            issues.extend(row_issues)
        issues.extend(detect_duplicates(rows, find_existing_person_matches(session, rows)))

        invalid_rows = {issue.row_number for issue in issues if issue.severity == SEVERITY_ERROR}
        session.add_all(_to_row(job, issue) for issue in issues)
        job.total_records = len(rows)
        job.invalid_records = len(invalid_rows)
        job.valid_records = len(rows) - len(invalid_rows)
        session.flush()
        return transition_import_job_status(session, job=job, new_status="preview_ready")
    except Exception:
        return _unexpected_failure(session, job, "validating")


__all__ = [
    "IMPORT_VALIDATION_FAILED_CODE",
    "ImportJobNotUploadedError",
    "run_import_preview",
]
