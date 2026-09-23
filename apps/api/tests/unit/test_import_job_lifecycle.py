"""Pure-Python unit tests for the TH-0118.1 / Issue #185 ImportJob
lifecycle module and source-format resolution — no database, no HTTP.
The real PostgreSQL CHECK constraints and the locked service-level
transition are covered in tests/integration/test_membership_imports_api.py.
"""

import itertools

import pytest

from app.db.imports import CANONICAL_IMPORT_JOB_STATUSES
from app.imports.lifecycle import (
    IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS,
    INITIAL_IMPORT_JOB_STATUS,
    TERMINAL_IMPORT_JOB_STATUSES,
    InvalidImportJobStatusError,
    InvalidImportJobStatusTransitionError,
    validate_import_job_status_transition,
)
from app.imports.service import UnsupportedImportFormatError, resolve_source_format

# people-api.md §22 "Import lifecycle" allowed-transition table, verbatim.
_CANONICAL_EDGES = {
    ("uploaded", "parsing"),
    ("uploaded", "cancelled"),
    ("uploaded", "failed"),
    ("parsing", "validating"),
    ("parsing", "failed"),
    ("validating", "preview_ready"),
    ("validating", "failed"),
    ("preview_ready", "approved"),
    ("preview_ready", "cancelled"),
    ("approved", "applying"),
    ("approved", "cancelled"),
    ("approved", "failed"),
    ("applying", "completed"),
    ("applying", "partially_completed"),
    ("applying", "failed"),
}
_CANONICAL_TERMINALS = {"completed", "partially_completed", "failed", "cancelled"}


def test_status_vocabulary_is_exactly_the_ten_canonical_values() -> None:
    assert CANONICAL_IMPORT_JOB_STATUSES == {
        "uploaded",
        "parsing",
        "validating",
        "preview_ready",
        "approved",
        "applying",
        "completed",
        "partially_completed",
        "failed",
        "cancelled",
    }


def test_initial_status_is_uploaded() -> None:
    assert INITIAL_IMPORT_JOB_STATUS == "uploaded"


def test_transition_table_matches_the_canonical_contract_exactly() -> None:
    assert set(IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS) == CANONICAL_IMPORT_JOB_STATUSES
    edges = {
        (source, target)
        for source, targets in IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS.items()
        for target in targets
    }
    assert edges == _CANONICAL_EDGES


def test_terminal_statuses_are_exactly_the_canonical_terminals() -> None:
    assert TERMINAL_IMPORT_JOB_STATUSES == _CANONICAL_TERMINALS


@pytest.mark.parametrize("from_status,to_status", sorted(_CANONICAL_EDGES))
def test_every_canonical_transition_is_allowed(from_status: str, to_status: str) -> None:
    validate_import_job_status_transition(from_status, to_status)  # must not raise


_FORBIDDEN_EDGES = sorted(
    (source, target)
    for source, target in itertools.product(sorted(CANONICAL_IMPORT_JOB_STATUSES), repeat=2)
    if (source, target) not in _CANONICAL_EDGES
)


@pytest.mark.parametrize("from_status,to_status", _FORBIDDEN_EDGES)
def test_every_other_transition_is_rejected(from_status: str, to_status: str) -> None:
    with pytest.raises(InvalidImportJobStatusTransitionError):
        validate_import_job_status_transition(from_status, to_status)


@pytest.mark.parametrize("terminal", sorted(_CANONICAL_TERMINALS))
@pytest.mark.parametrize("target", sorted(CANONICAL_IMPORT_JOB_STATUSES))
def test_terminal_statuses_allow_no_further_transition(terminal: str, target: str) -> None:
    with pytest.raises(InvalidImportJobStatusTransitionError):
        validate_import_job_status_transition(terminal, target)


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("uploaded", "preview_ready"),  # skipping parse/validate
        ("uploaded", "approved"),  # skipping preview
        ("uploaded", "applying"),  # upload is never apply
        ("uploaded", "completed"),
        ("preview_ready", "applying"),  # skipping approval
        ("preview_ready", "failed"),
        ("parsing", "cancelled"),
        ("applying", "cancelled"),
        ("uploaded", "uploaded"),
    ],
)
def test_notable_invalid_transitions_are_rejected(from_status: str, to_status: str) -> None:
    with pytest.raises(InvalidImportJobStatusTransitionError):
        validate_import_job_status_transition(from_status, to_status)


@pytest.mark.parametrize(
    "from_status,to_status",
    [("pending", "parsing"), ("uploaded", "done"), ("", "failed"), ("UPLOADED", "parsing")],
)
def test_non_canonical_status_is_rejected(from_status: str, to_status: str) -> None:
    with pytest.raises(InvalidImportJobStatusError):
        validate_import_job_status_transition(from_status, to_status)


@pytest.mark.parametrize(
    "original_name,expected",
    [
        ("members.csv", "csv"),
        ("MEMBERS.CSV", "csv"),
        ("members.xlsx", "xlsx"),
        ("Members.XLSX", "xlsx"),
        ("archive.2026.xlsx", "xlsx"),
    ],
)
def test_supported_formats_are_resolved_from_extension(original_name: str, expected: str) -> None:
    assert resolve_source_format(original_name) == expected


@pytest.mark.parametrize(
    "original_name",
    ["members.xls", "members.txt", "members.pdf", "members", "", "csv", "members.csv.exe"],
)
def test_unsupported_formats_are_rejected(original_name: str) -> None:
    with pytest.raises(UnsupportedImportFormatError):
        resolve_source_format(original_name)
