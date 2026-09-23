"""ImportJob lifecycle validation (TH-0118.1 / Issue #185).

Canonical source: docs/05-api/people-api.md §22 "Import lifecycle" /
docs/04-modules/people-and-membership.md §11.2 — the allowed-transition
table below is that table, edge for edge:

    uploaded       -> parsing, cancelled, failed
    parsing        -> validating, failed
    validating     -> preview_ready, failed
    preview_ready  -> approved, cancelled
    approved       -> applying, cancelled, failed
    applying       -> completed, partially_completed, failed
    completed, partially_completed, failed, cancelled -> terminal

A new job starts in `uploaded` (`INITIAL_IMPORT_JOB_STATUS`).

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.groups.lifecycle/app.people.lifecycle.
"""

from app.db.imports import CANONICAL_IMPORT_JOB_STATUSES

INITIAL_IMPORT_JOB_STATUS = "uploaded"

IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "uploaded": frozenset({"parsing", "cancelled", "failed"}),
    "parsing": frozenset({"validating", "failed"}),
    "validating": frozenset({"preview_ready", "failed"}),
    "preview_ready": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"applying", "cancelled", "failed"}),
    "applying": frozenset({"completed", "partially_completed", "failed"}),
    "completed": frozenset(),
    "partially_completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

TERMINAL_IMPORT_JOB_STATUSES: frozenset[str] = frozenset(
    status for status, targets in IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS.items() if not targets
)


class ImportJobLifecycleError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidImportJobStatusError(ImportJobLifecycleError):
    """`value` is not one of CANONICAL_IMPORT_JOB_STATUSES."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical ImportJob status")
        self.value = value


class InvalidImportJobStatusTransitionError(ImportJobLifecycleError):
    """people-api.md §22: the transition table does not allow this
    (from_status -> to_status) transition — including any transition out
    of a terminal status and any same-status "transition"."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"{from_status!r} -> {to_status!r} is not an allowed ImportJob transition")
        self.from_status = from_status
        self.to_status = to_status


def validate_import_job_status_transition(current_status: str, new_status: str) -> None:
    """Raises InvalidImportJobStatusError if either status is not
    canonical, or InvalidImportJobStatusTransitionError if the transition
    is not one of the allowed edges."""
    if current_status not in CANONICAL_IMPORT_JOB_STATUSES:
        raise InvalidImportJobStatusError(current_status)
    if new_status not in CANONICAL_IMPORT_JOB_STATUSES:
        raise InvalidImportJobStatusError(new_status)
    if new_status not in IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidImportJobStatusTransitionError(current_status, new_status)


__all__ = [
    "INITIAL_IMPORT_JOB_STATUS",
    "IMPORT_JOB_ALLOWED_STATUS_TRANSITIONS",
    "TERMINAL_IMPORT_JOB_STATUSES",
    "ImportJobLifecycleError",
    "InvalidImportJobStatusError",
    "InvalidImportJobStatusTransitionError",
    "validate_import_job_status_transition",
]
