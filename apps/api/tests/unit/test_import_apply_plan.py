"""Unit tests for app.imports.apply_plan (TH-0118.3) — pure, no DB/HTTP."""

import uuid

import pytest

from app.imports.apply_plan import (
    APPLY_COMPLETED,
    APPLY_FAILED,
    APPLY_PARTIALLY_COMPLETED,
    plan_import_apply,
    resolve_final_import_status,
)
from app.imports.rows import SEVERITY_ERROR, SEVERITY_WARNING, ImportIssue, NormalizedRow


def _row(row_number: int) -> NormalizedRow:
    return NormalizedRow(
        row_number=row_number,
        first_name="Anna",
        last_name="Ivanova",
        middle_name=None,
        birth_date=None,
        phone=None,
        email=None,
        external_id=None,
    )


def _issue(row_number: int | None, code: str, severity: str) -> ImportIssue:
    return ImportIssue(code=code, message="m", severity=severity, row_number=row_number)


def test_rows_without_issues_are_all_candidates() -> None:
    rows = [_row(2), _row(3)]

    plan = plan_import_apply(rows, [])

    assert plan.candidates == tuple(rows)
    assert plan.skipped_count == 0


def test_invalid_and_duplicate_rows_are_skipped() -> None:
    rows = [_row(2), _row(3), _row(4), _row(5)]
    issues = [
        _issue(2, "required_field_missing", SEVERITY_ERROR),
        _issue(2, "invalid_email", SEVERITY_ERROR),
        _issue(3, "duplicate_exact", SEVERITY_WARNING),
        ImportIssue(
            code="duplicate_exact",
            message="m",
            severity=SEVERITY_WARNING,
            row_number=3,
            matched_person_id=uuid.uuid4(),
        ),
    ]

    plan = plan_import_apply(rows, issues)

    assert [row.row_number for row in plan.candidates] == [4, 5]
    assert plan.invalid_row_numbers == {2}
    assert plan.duplicate_row_numbers == {3}
    assert plan.skipped_count == 2


def test_invalid_duplicate_row_is_counted_once_as_invalid() -> None:
    issues = [
        _issue(2, "invalid_birth_date", SEVERITY_ERROR),
        _issue(2, "duplicate_exact", SEVERITY_WARNING),
    ]

    plan = plan_import_apply([_row(2)], issues)

    assert plan.candidates == ()
    assert plan.invalid_row_numbers == {2}
    assert plan.duplicate_row_numbers == frozenset()
    assert plan.skipped_count == 1


def test_file_level_issue_without_row_skips_no_row() -> None:
    plan = plan_import_apply([_row(2)], [_issue(None, "something", SEVERITY_ERROR)])

    assert [row.row_number for row in plan.candidates] == [2]


def test_other_warnings_do_not_skip_a_row() -> None:
    plan = plan_import_apply([_row(2)], [_issue(2, "some_other_warning", SEVERITY_WARNING)])

    assert [row.row_number for row in plan.candidates] == [2]


@pytest.mark.parametrize(
    ("created", "failed", "aborted", "expected"),
    [
        (3, 0, False, APPLY_COMPLETED),
        (0, 0, False, APPLY_COMPLETED),  # everything skipped — still completed
        (2, 1, False, APPLY_PARTIALLY_COMPLETED),
        (0, 2, False, APPLY_FAILED),
        (0, 0, True, APPLY_FAILED),
        (2, 0, True, APPLY_PARTIALLY_COMPLETED),
    ],
)
def test_final_status(created: int, failed: int, aborted: bool, expected: str) -> None:
    assert resolve_final_import_status(created=created, failed=failed, aborted=aborted) == expected
