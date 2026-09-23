"""Import approval and synchronous apply (TH-0118.3 / Issue #193).

Canonical sources: docs/05-api/people-api.md §22
("POST .../approve", "POST .../apply", "Import report"),
docs/04-modules/people-and-membership.md §11.5/§11.5.1,
docs/05-api/auth-and-authorization.md §5.3,
docs/03-architecture/adr/ADR-0024-audit-infrastructure.md
(`membership.import.applied`).

Authorization is not decided here — the API router has already applied
the ImportJob object policy (app.imports.authorization).

Every status change goes through
app.imports.service.transition_import_job_status (re-reads the job under
`SELECT ... FOR UPDATE`), so two concurrent approve/apply requests are
serialized on the job row and only one of them can leave `preview_ready`/
`approved`; the other gets ImportJobStatusConflictError.

Approve: `preview_ready -> approved`. Nothing else is written.

Apply: `approved -> applying -> completed | partially_completed | failed`,
synchronously within the request:

1. the immutable source file is re-read, re-normalized, re-validated and
   exact duplicates are re-detected against the data persisted *now*
   (app.imports.evaluation — the same code path as preview);
2. invalid rows and `duplicate_exact` rows are skipped
   (app.imports.apply_plan); an existing Person/User is never touched;
3. every other row is applied in its own transaction (`_apply_row`):
   a. the transaction-scoped import lock is taken (`_lock_import_apply`);
   b. the row is re-checked against the exact duplicate rules, inside the
      transaction, immediately before creation — a match makes the row
      `duplicate_exact` -> skipped, creating nothing;
   c. otherwise it creates exactly Person + ClubMembership (app.people.
      service.create_person_with_membership: `member`/`active`/
      `joined_at = now`) and User (app.authentication.
      account_provisioning.create_user_for_person with
      `issue_first_access=False`: active with `login_identifier = email`
      and no first-access challenge, or a pending stub without email),
      together with those functions' own domain audit records — composed
      with the Person-creation wizard's deferred-commit proxy, so a
      failure anywhere rolls back that row only. A login uniqueness
      violation there is the email duplicate case (`duplicate_exact`),
      not an application failure;
4. the final status, the aggregate counters and exactly one
   `membership.import.applied` audit record commit together.

Concurrency guarantee (people-api.md §22 "POST .../apply"): the lock in
3a serializes the re-check + creation of every row across concurrently
applied import jobs, and the re-check reads committed data after the lock
is acquired (READ COMMITTED), so two overlapping import jobs never both
create the same participant. The guarantee is import-only: no other
Person-creation workflow takes this lock (manual creation still allows
duplicates, ADR-0025 §9).

The ClubMembership's Club is the job's `club_id`: the installation's sole
Club, resolved server-side when the job was created — never a client
choice.

Import issues no first-access credential (auth-and-authorization.md
§5.3): first access is issued later by an administrator through
`POST /persons/{person_id}/account/password-reset`.
"""

import logging
import uuid
from typing import Optional, Sequence, cast

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authentication import account_provisioning
from app.db.identity import User
from app.db.imports import ImportJob, ImportJobError
from app.imports.apply_plan import (
    APPLY_FAILED,
    plan_import_apply,
    resolve_final_import_status,
)
from app.imports.duplicates import DUPLICATE_EXACT_CODE, detect_duplicates
from app.imports.evaluation import evaluate_source, issue_to_error_row, read_source
from app.imports.lifecycle import InvalidImportJobStatusTransitionError
from app.imports.parsing import ImportParseError
from app.imports.queries import find_existing_person_matches
from app.imports.rows import SEVERITY_ERROR, SEVERITY_WARNING, ImportIssue, NormalizedRow
from app.imports.service import transition_import_job_status
from app.people import service as people_service
from app.people.wizard import _DeferredCommitSession
from app.storage.file_storage import FileStorage, FileStorageError

logger = logging.getLogger("tourcrm.imports")

IMPORT_APPLIED_AUDIT_ACTION = "membership.import.applied"
IMPORT_JOB_AUDIT_RESOURCE_TYPE = "import_job"

# Recorded (severity `error`) for an unexpected application failure: with
# `row_number` for one row whose transaction was rolled back, without it
# when the batch itself could not be completed.
IMPORT_APPLY_FAILED_CODE = "import_apply_failed"

# Key of the transaction-scoped PostgreSQL advisory lock serializing the
# per-row re-check + creation of concurrently applied import jobs. One key
# for the whole participant-import subsystem (TourCRM has exactly one
# Club); nothing outside app.imports takes it.
_IMPORT_APPLY_LOCK_KEY = 118_300_193


class ImportJobStatusConflictError(Exception):
    """The job is not in the status the operation requires (approve:
    `preview_ready`; apply: `approved`)."""

    def __init__(self, *, status: str, required_status: str) -> None:
        super().__init__(f"Import job is {status!r}, not {required_status!r}")
        self.status = status
        self.required_status = required_status


def approve_import_job(session: Session, *, job: ImportJob) -> ImportJob:
    """`preview_ready -> approved`. Creates or changes no domain entity."""
    try:
        return transition_import_job_status(session, job=job, new_status="approved")
    except InvalidImportJobStatusTransitionError as exc:
        raise ImportJobStatusConflictError(
            status=exc.from_status, required_status="preview_ready"
        ) from exc


def _record_apply_time_issues(
    session: Session, *, import_job_id: uuid.UUID, issues: Sequence[ImportIssue]
) -> None:
    """Keep the row-level report explaining every skip: add the issues
    found now that preview did not already record (in practice a
    `duplicate_exact` against data persisted after preview). Existing
    entries are never removed or changed."""
    recorded = set(
        session.execute(
            sa.select(
                ImportJobError.row_number,
                ImportJobError.field,
                ImportJobError.code,
                ImportJobError.severity,
                ImportJobError.matched_person_id,
            ).where(ImportJobError.import_job_id == import_job_id)
        ).tuples()
    )
    for issue in issues:
        key = (issue.row_number, issue.field, issue.code, issue.severity, issue.matched_person_id)
        if key not in recorded:
            recorded.add(key)
            session.add(issue_to_error_row(import_job_id, issue))
    session.flush()


def _lock_import_apply(session: Session) -> None:
    """Take the import-apply advisory lock for the current transaction; it
    is released by that transaction's commit or rollback."""
    session.execute(sa.select(sa.func.pg_advisory_xact_lock(_IMPORT_APPLY_LOCK_KEY)))


def _recheck_duplicates(session: Session, row: NormalizedRow) -> list[ImportIssue]:
    """The row's `duplicate_exact` issues against data committed now — the
    same exact rules as preview (external_id is in-file only and was
    already decided for the whole file)."""
    return detect_duplicates([row], find_existing_person_matches(session, [row]))


def _login_duplicate(session: Session, row: NormalizedRow) -> list[ImportIssue]:
    """A login uniqueness violation is the email exact-duplicate case: the
    `duplicate_exact` issue against the User now holding that login."""
    assert row.email is not None
    person_id = session.execute(
        sa.select(User.person_id).where(User.normalized_login_identifier == row.email)
    ).scalar_one_or_none()
    matches = {row.row_number: [(person_id, "email")]} if person_id is not None else {}
    issues = detect_duplicates([row], matches)
    if issues:
        return issues
    # The conflicting User is gone again by now — still the email duplicate.
    return [
        ImportIssue(
            code=DUPLICATE_EXACT_CODE,
            message="Exact duplicate of an existing person by email",
            severity=SEVERITY_WARNING,
            row_number=row.row_number,
            field="email",
        )
    ]


def _apply_row(
    session: Session,
    *,
    row: NormalizedRow,
    club_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: Optional[str],
) -> list[ImportIssue]:
    """One row's transaction: under the import lock, re-check duplicates,
    then create Person + ClubMembership + User and their domain audit
    records, committed together or not at all.

    Returns `[]` when the row was created, or its `duplicate_exact` issues
    when it is a duplicate (nothing created). Any other failure propagates
    after the row's transaction has been rolled back."""
    assert row.first_name is not None and row.last_name is not None  # valid row
    deferred = cast(Session, _DeferredCommitSession(session))
    try:
        _lock_import_apply(session)
        duplicates = _recheck_duplicates(session, row)
        if duplicates:
            session.rollback()
            return duplicates
        person, _membership = people_service.create_person_with_membership(
            deferred,
            first_name=row.first_name,
            last_name=row.last_name,
            middle_name=row.middle_name,
            birth_date=row.birth_date,
            phone=row.phone,
            email=row.email,
            address=None,
            club_id=club_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        account_provisioning.create_user_for_person(
            deferred,
            person_id=person.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            issue_first_access=False,
        )
        session.commit()
    except account_provisioning.DuplicateLoginIdentifierError:
        session.rollback()
        return _login_duplicate(session, row)
    except Exception:
        session.rollback()
        raise
    return []


def _record_failure(session: Session, *, import_job_id: uuid.UUID, issue: ImportIssue) -> None:
    session.add(issue_to_error_row(import_job_id, issue))
    session.commit()


def run_import_apply(
    session: Session,
    storage: FileStorage,
    *,
    job: ImportJob,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> ImportJob:
    """Apply an `approved` job and leave it in its terminal status. Raises
    ImportJobStatusConflictError (touching nothing) if the job is not
    `approved`."""
    try:
        transition_import_job_status(session, job=job, new_status="applying")
    except InvalidImportJobStatusTransitionError as exc:
        raise ImportJobStatusConflictError(
            status=exc.from_status, required_status="approved"
        ) from exc

    # Plain values: every row commit/rollback below expires `job`.
    import_job_id = job.id
    club_id = job.club_id
    source_file_id = job.source_file_id
    source_format = job.source_format

    created = 0
    failed = 0
    skipped: Optional[int] = None
    aborted = False
    duplicates: list[ImportIssue]
    try:
        parsed = read_source(
            session, storage, source_file_id=source_file_id, source_format=source_format
        )
        rows, issues = evaluate_source(session, parsed, source_format=source_format)
        plan = plan_import_apply(rows, issues)
        _record_apply_time_issues(session, import_job_id=import_job_id, issues=issues)
        session.commit()
        skipped = plan.skipped_count

        for row in plan.candidates:
            try:
                duplicates = _apply_row(
                    session,
                    row=row,
                    club_id=club_id,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
            except Exception:
                logger.exception(
                    "imports.apply.row_failed import_id=%s row_number=%s",
                    import_job_id,
                    row.row_number,
                )
                failed += 1
                _record_failure(
                    session,
                    import_job_id=import_job_id,
                    issue=ImportIssue(
                        code=IMPORT_APPLY_FAILED_CODE,
                        message="The row could not be applied",
                        severity=SEVERITY_ERROR,
                        row_number=row.row_number,
                    ),
                )
            else:
                if duplicates:
                    skipped += 1
                    _record_apply_time_issues(
                        session, import_job_id=import_job_id, issues=duplicates
                    )
                    session.commit()
                else:
                    created += 1
    except Exception as exc:
        session.rollback()
        logger.exception("imports.apply.failed import_id=%s", import_job_id)
        aborted = True
        if isinstance(exc, ImportParseError):
            issue = ImportIssue(
                code=exc.code,
                message=exc.message,
                severity=SEVERITY_ERROR,
                row_number=exc.row_number,
                field=exc.field,
            )
        elif isinstance(exc, FileStorageError):
            issue = ImportIssue(
                code="import_file_unreadable",
                message="The uploaded source file could not be read",
                severity=SEVERITY_ERROR,
            )
        else:
            issue = ImportIssue(
                code=IMPORT_APPLY_FAILED_CODE,
                message="The import could not be applied",
                severity=SEVERITY_ERROR,
            )
        _record_failure(session, import_job_id=import_job_id, issue=issue)

    final_status = resolve_final_import_status(created=created, failed=failed, aborted=aborted)
    try:
        job = session.get_one(ImportJob, import_job_id)
        job.created_records = created
        job.updated_records = 0
        job.skipped_records = skipped
        session.flush()
        record_audit_event(
            session,
            action=IMPORT_APPLIED_AUDIT_ACTION,
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type=IMPORT_JOB_AUDIT_RESOURCE_TYPE,
            resource_id=import_job_id,
            outcome="failure" if final_status == APPLY_FAILED else "success",
            request_id=request_id,
            details={
                "status": final_status,
                "created_records": created,
                "updated_records": 0,
                "skipped_records": skipped,
                "failed_records": failed,
            },
        )
        # Commits the counters and the audit record with the status.
        return transition_import_job_status(session, job=job, new_status=final_status)
    except Exception:
        session.rollback()
        raise


__all__ = [
    "IMPORT_APPLIED_AUDIT_ACTION",
    "IMPORT_JOB_AUDIT_RESOURCE_TYPE",
    "IMPORT_APPLY_FAILED_CODE",
    "ImportJobStatusConflictError",
    "approve_import_job",
    "run_import_apply",
]
