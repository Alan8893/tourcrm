"""ImportJob creation and lifecycle transitions (TH-0118.1 / Issue #185).

Canonical sources: docs/05-api/people-api.md §22,
docs/04-modules/people-and-membership.md §11.1/§11.2.

Authorization is not decided here — by the time `create_import_job` is
called, the API router has already resolved the job's Club and checked
`membership.import` for it (app.imports.authorization), exactly as
app.documents.service/app.people.service keep authorization as the
router's job.

`create_import_job` only stores the uploaded source file and creates the
job in `uploaded`: it never reads, parses or validates the file's content
and never creates Person/User/ClubMembership (or any other participant)
records — upload is not apply (people-and-membership.md §11.3). It is not
audit-required (people-api.md §22 "Import security and audit"), so no
audit record is written.

Storage/transaction shape mirrors app.documents.service.create_document
(the binary lives outside PostgreSQL and there is no distributed
transaction spanning the two):

    storage.put(storage_key, content)      # outside any DB transaction
    try:
        BEGIN
          File row (session.add/flush)
          ImportJob row (session.add/flush)
        COMMIT
    except Exception:
        ROLLBACK
        storage.delete(storage_key)        # best-effort orphan cleanup
        raise
"""

import hashlib
import uuid
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.db.documents import File
from app.db.imports import CANONICAL_IMPORT_SOURCE_FORMATS, ImportJob
from app.imports.lifecycle import (
    INITIAL_IMPORT_JOB_STATUS,
    validate_import_job_status_transition,
)
from app.storage.file_storage import FileStorage, FileStorageError


class UnsupportedImportFormatError(Exception):
    """The uploaded file is not one of the canonical import source formats
    (people-api.md §22: `csv` or `xlsx`)."""

    def __init__(self, original_name: str) -> None:
        super().__init__(f"Unsupported import file format: {original_name!r}")
        self.original_name = original_name


def resolve_source_format(original_name: str) -> str:
    """people-api.md §22: the server determines `source_format` from the
    uploaded file's extension — `.csv` -> `csv`, `.xlsx` -> `xlsx`
    (case-insensitive). Anything else, including no extension at all,
    raises UnsupportedImportFormatError. Content is never inspected here:
    reading the file is the later parse stage's job, not upload's."""
    suffix = PurePosixPath(original_name.replace("\\", "/")).suffix.lower().lstrip(".")
    if suffix not in CANONICAL_IMPORT_SOURCE_FORMATS:
        raise UnsupportedImportFormatError(original_name)
    return suffix


def _storage_key_for(club_id: uuid.UUID, file_id: uuid.UUID) -> str:
    """Server-generated only — a client never supplies or influences
    `storage_key` (ADR-0040 §3), and it is never exposed in any API
    response."""
    return f"imports/memberships/{club_id}/{file_id}"


def create_import_job(
    session: Session,
    storage: FileStorage,
    *,
    club_id: uuid.UUID,
    content: bytes,
    original_name: str,
    mime_type: str,
    actor_user_id: uuid.UUID,
) -> ImportJob:
    """Store the source file through FileStorage, record its `File`
    metadata, and create the ImportJob in `uploaded` referencing it.

    Raises UnsupportedImportFormatError before touching storage or the
    database if `original_name` is not a canonical import format.
    """
    source_format = resolve_source_format(original_name)

    file_id = uuid.uuid4()
    storage_key = _storage_key_for(club_id, file_id)

    storage.put(storage_key, content)
    try:
        file_row = File(
            id=file_id,
            storage_key=storage_key,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
            storage_backend=storage.backend_name,
            created_by=actor_user_id,
        )
        session.add(file_row)
        session.flush()

        job = ImportJob(
            club_id=club_id,
            created_by_user_id=actor_user_id,
            source_file_id=file_row.id,
            source_format=source_format,
            status=INITIAL_IMPORT_JOB_STATUS,
        )
        session.add(job)
        session.flush()
        session.commit()
    except Exception:
        session.rollback()
        try:
            storage.delete(storage_key)
        except FileStorageError:
            pass
        raise
    session.refresh(job)
    return job


def transition_import_job_status(session: Session, *, job: ImportJob, new_status: str) -> ImportJob:
    """Move `job` to `new_status` iff people-api.md §22's transition table
    allows it; raises app.imports.lifecycle.InvalidImportJobStatusError/
    InvalidImportJobStatusTransitionError (leaving the job unchanged)
    otherwise. The row is re-read under `SELECT ... FOR UPDATE` first, so
    two concurrent transitions of the same job are validated one after the
    other against the actually-current status, never both against the
    same stale one.

    This is the only sanctioned way to change `ImportJob.status`: no
    endpoint exposes arbitrary status changes, and the later pipeline
    stages (parse/validate/preview/approve/apply) go through here.
    """
    session.refresh(job, with_for_update=True)
    try:
        validate_import_job_status_transition(job.status, new_status)
    except Exception:
        session.rollback()
        raise
    job.status = new_status
    session.flush()
    session.commit()
    session.refresh(job)
    return job


__all__ = [
    "UnsupportedImportFormatError",
    "resolve_source_format",
    "create_import_job",
    "transition_import_job_status",
]
