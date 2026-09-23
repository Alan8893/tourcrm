"""Participant import job persistence foundation (TH-0118.1 / Issue #185).

Canonical sources: docs/05-api/people-api.md §22 ("Import job model",
"Import lifecycle", "Import authorization and object access"),
docs/04-modules/people-and-membership.md §11.1/§11.2,
docs/05-api/endpoint-inventory.md §4.1.

`ImportJob` is Club-bound (`club_id`), records its creating User
(`created_by_user_id`) and references its uploaded source file through
the existing, domain-neutral `files` table (`source_file_id`, ADR-0040
§1/§3) — the binary itself lives in `FileStorage`, never inline here.

`ImportJobError` is the validation/application error storage foundation
behind `GET /memberships/imports/{import_id}/errors`: every row belongs to
exactly one job (`import_job_id`), which is what keeps one job's errors
from ever being listed under another. No parser/validator/apply stage
writes rows yet (Issue #185 scope) — this slice only establishes the
storage and its read path.

Shape mirrors app.db.documents/app.db.groups: plain FK columns, no ORM
`relationship()` objects, RESTRICT foreign keys, closed status
vocabularies enforced by a CHECK constraint.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# people-api.md §22 "Import lifecycle": the closed, ten-value persisted
# status vocabulary. Transition rules live in app.imports.lifecycle.
CANONICAL_IMPORT_JOB_STATUSES: frozenset[str] = frozenset(
    {
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
)

# people-api.md §22 "Import job model": `source_format` — `csv` or `xlsx`
# (people-and-membership.md §11.1: the first version's formats).
CANONICAL_IMPORT_SOURCE_FORMATS: frozenset[str] = frozenset({"csv", "xlsx"})

_IMPORT_JOB_STATUS_VALUES = ",".join(
    f"'{value}'" for value in sorted(CANONICAL_IMPORT_JOB_STATUSES)
)
_IMPORT_SOURCE_FORMAT_VALUES = ",".join(
    f"'{value}'" for value in sorted(CANONICAL_IMPORT_SOURCE_FORMATS)
)

# Aggregate record counters (people-api.md §22: "aggregate counters
# required by the status/report contract"). NULL means "not computed yet"
# — no pipeline stage has produced the figure — which is deliberately
# distinct from a real, computed 0. The job's error count is not stored:
# it is always derived from its own `import_job_errors` rows, so it can
# never drift from the errors actually recorded.
IMPORT_JOB_RECORD_COUNTERS: tuple[str, ...] = (
    "total_records",
    "valid_records",
    "invalid_records",
    "created_records",
    "updated_records",
    "skipped_records",
)


class ImportJob(Base):
    """One participant import job (people-api.md §22)."""

    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # RESTRICT: the source File must not be deletable out from under a job
    # that still references it (same reasoning as documents.file_id,
    # ADR-0040 §3).
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="RESTRICT"), nullable=False
    )
    source_format: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    total_records: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    valid_records: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    invalid_records: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    created_records: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    updated_records: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    skipped_records: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.CheckConstraint(
            f"status IN ({_IMPORT_JOB_STATUS_VALUES})",
            name="ck_import_jobs_status_valid",
        ),
        sa.CheckConstraint(
            f"source_format IN ({_IMPORT_SOURCE_FORMAT_VALUES})",
            name="ck_import_jobs_source_format_valid",
        ),
        *(
            sa.CheckConstraint(
                f"{counter} IS NULL OR {counter} >= 0",
                name=f"ck_import_jobs_{counter}_non_negative",
            )
            for counter in IMPORT_JOB_RECORD_COUNTERS
        ),
        sa.Index("ix_import_jobs_club_id", "club_id"),
        sa.Index("ix_import_jobs_created_by_user_id", "created_by_user_id"),
        sa.Index("ix_import_jobs_source_file_id", "source_file_id"),
    )


class ImportJobError(Base):
    """One validation/application error recorded against an ImportJob
    (people-api.md §22 `GET .../errors`).

    `row_number` (1-based source record) and `field` (source column) are
    both optional: an error need not be tied to one row or one column
    (e.g. a file-level failure). `code` is a machine-readable error code;
    `message` is the human-readable text.
    """

    __tablename__ = "import_job_errors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    row_number: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    field: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    message: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            "row_number IS NULL OR row_number >= 1",
            name="ck_import_job_errors_row_number_positive",
        ),
        sa.Index("ix_import_job_errors_import_job_id", "import_job_id"),
    )


__all__ = [
    "CANONICAL_IMPORT_JOB_STATUSES",
    "CANONICAL_IMPORT_SOURCE_FORMATS",
    "IMPORT_JOB_RECORD_COUNTERS",
    "ImportJob",
    "ImportJobError",
]
