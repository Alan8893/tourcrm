"""Pure decisions for applying an approved participant import (TH-0118.3).

Canonical sources: docs/05-api/people-api.md §22
("POST /api/v1/memberships/imports/{import_id}/apply"),
docs/04-modules/people-and-membership.md §11.5/§11.5.1.

Pure Python — no FastAPI, no database session, no ORM: the evaluated rows
and issues (app.imports.evaluation) go in, the plan/final status come out.

- A row with at least one `error` is skipped (invalid).
- A row with a `duplicate_exact` warning is skipped (duplicate) — never
  merged, updated, overwritten or linked to the matched Person/User.
- Every other row is a candidate: it creates exactly Person + User +
  ClubMembership.

Final status (people-api.md §22):

- `completed` — no unexpected row-level application failure (rows are
  created or skipped as invalid/duplicate; an all-skipped batch is still
  `completed`);
- `partially_completed` — at least one row created and at least one
  unexpected failure;
- `failed` — no row created, and the application failed (every candidate
  failed, or the batch itself could not be completed).
"""

from dataclasses import dataclass
from typing import Sequence

from app.imports.duplicates import DUPLICATE_EXACT_CODE
from app.imports.rows import SEVERITY_ERROR, ImportIssue, NormalizedRow

APPLY_COMPLETED = "completed"
APPLY_PARTIALLY_COMPLETED = "partially_completed"
APPLY_FAILED = "failed"


@dataclass(frozen=True)
class ImportApplyPlan:
    candidates: tuple[NormalizedRow, ...]
    invalid_row_numbers: frozenset[int]
    duplicate_row_numbers: frozenset[int]

    @property
    def skipped_count(self) -> int:
        """Invalid rows plus exact-duplicate rows — each row counted once."""
        return len(self.invalid_row_numbers) + len(self.duplicate_row_numbers)


def plan_import_apply(
    rows: Sequence[NormalizedRow], issues: Sequence[ImportIssue]
) -> ImportApplyPlan:
    """Split `rows` into candidates and skipped rows. A row that is both
    invalid and a duplicate is counted as invalid only."""
    invalid = frozenset(
        issue.row_number
        for issue in issues
        if issue.severity == SEVERITY_ERROR and issue.row_number is not None
    )
    duplicate = (
        frozenset(
            issue.row_number
            for issue in issues
            if issue.code == DUPLICATE_EXACT_CODE and issue.row_number is not None
        )
        - invalid
    )
    candidates = tuple(
        row for row in rows if row.row_number not in invalid and row.row_number not in duplicate
    )
    return ImportApplyPlan(
        candidates=candidates,
        invalid_row_numbers=invalid,
        duplicate_row_numbers=duplicate,
    )


def resolve_final_import_status(*, created: int, failed: int, aborted: bool = False) -> str:
    """`failed` counts unexpected row-level application failures;
    `aborted` means the batch itself could not be completed."""
    if failed == 0 and not aborted:
        return APPLY_COMPLETED
    if created > 0:
        return APPLY_PARTIALLY_COMPLETED
    return APPLY_FAILED


__all__ = [
    "APPLY_COMPLETED",
    "APPLY_PARTIALLY_COMPLETED",
    "APPLY_FAILED",
    "ImportApplyPlan",
    "plan_import_apply",
    "resolve_final_import_status",
]
