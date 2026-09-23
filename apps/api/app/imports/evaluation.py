"""Reading and evaluating an ImportJob's source file (TH-0118.2/TH-0118.3).

Canonical sources: docs/05-api/people-api.md §22 ("Source file format",
"Row normalization and validation", "Duplicate detection", "POST .../apply").

The one path both pipeline stages use, so preview and apply can never judge
the same immutable source file differently:

- `read_source` loads the stored source file through FileStorage and parses
  it (app.imports.parsing);
- `evaluate_source` normalizes and validates every record (app.imports.rows)
  and runs exact duplicate detection (app.imports.duplicates) against the
  Person/User data persisted *now* (app.imports.queries) — preview runs it
  at preview time, apply re-runs it at apply time.

`read_source`/`evaluate_source` write nothing; existing Person/User rows are only read.
"""

import uuid

from sqlalchemy.orm import Session

from app.db.documents import File
from app.db.imports import ImportJobError
from app.imports.duplicates import detect_duplicates
from app.imports.parsing import ParsedSource, parse_source
from app.imports.queries import find_existing_person_matches
from app.imports.rows import ImportIssue, NormalizedRow, normalize_and_validate
from app.storage.file_storage import FileStorage, FileStorageError


def read_source(
    session: Session, storage: FileStorage, *, source_file_id: uuid.UUID, source_format: str
) -> ParsedSource:
    """Raises FileStorageError if the stored file (or its `File` row) is
    unavailable, or app.imports.parsing.ImportParseError if it cannot be
    parsed."""
    source_file = session.get(File, source_file_id)
    if source_file is None:
        raise FileStorageError(f"File row {source_file_id} is missing")
    content = storage.get(source_file.storage_key)
    return parse_source(content, source_format)


def evaluate_source(
    session: Session, parsed: ParsedSource, *, source_format: str
) -> tuple[list[NormalizedRow], list[ImportIssue]]:
    """Every record's normalized row, plus all row-level validation errors
    and `duplicate_exact` warnings (against current persisted data and
    inside the file)."""
    rows: list[NormalizedRow] = []
    issues: list[ImportIssue] = []
    for record in parsed.records:
        row, row_issues = normalize_and_validate(record, source_format=source_format)
        rows.append(row)
        issues.extend(row_issues)
    issues.extend(detect_duplicates(rows, find_existing_person_matches(session, rows)))
    return rows, issues


def issue_to_error_row(import_job_id: uuid.UUID, issue: ImportIssue) -> ImportJobError:
    """The `ImportJobError` row persisting `issue` for `import_job_id`."""
    return ImportJobError(
        import_job_id=import_job_id,
        row_number=issue.row_number,
        field=issue.field,
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        matched_person_id=issue.matched_person_id,
    )


__all__ = ["read_source", "evaluate_source", "issue_to_error_row"]
