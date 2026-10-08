"""Generic transactional outbox persistence (Issue #318, ADR-0045 §2.3,
ADR-0046 §5).

`outbox_jobs` is the single durable source of pending asynchronous work.
A row is inserted in the SAME PostgreSQL transaction as the business
mutation that requires it (app.outbox.service.enqueue_outbox_job never
commits), so a rolled-back business change never leaves a job behind and
a committed one never loses it.

The table is deliberately provider-neutral: `job_type` names the job and
`payload` carries only identifiers/context (never secrets — see
app.outbox.security). There is no email/telegram/auth-specific outbox.

Worker contract the columns support (ADR-0046 §5; the worker itself is a
separate Issue):

- eligible work is `status = 'pending' AND next_attempt_at <= now()` or
  `status = 'processing' AND locked_until < now()` (expired lease), claimed
  in deterministic `(next_attempt_at, id)` order with
  `FOR UPDATE SKIP LOCKED`, atomically with setting the lease
  (`locked_by`, `locked_until`, `status = 'processing'`). The two partial
  indexes below serve exactly those two predicates;
- every attempt increments `attempts`; a retryable failure returns the job
  to `pending` with a future `next_attempt_at`; a permanent failure ends in
  the terminal, observable `dead` state with the last safe error.

Shape mirrors the rest of app.db: plain columns, no ORM `relationship()`,
closed status vocabulary enforced by a CHECK constraint.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.outbox.security import assert_safe_outbox_payload
from app.outbox.vocabulary import (
    CANONICAL_OUTBOX_STATUSES,
    OUTBOX_PENDING,
    OUTBOX_PROCESSING,
    TERMINAL_OUTBOX_STATUSES,
)

_STATUS_VALUES = ",".join(f"'{value}'" for value in sorted(CANONICAL_OUTBOX_STATUSES))
_TERMINAL_VALUES = ",".join(f"'{value}'" for value in sorted(TERMINAL_OUTBOX_STATUSES))


class OutboxJob(Base):
    """One durable asynchronous job (ADR-0046 §5)."""

    __tablename__ = "outbox_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Optional producer-supplied identity: at most one job per key, so a
    # re-processed business event cannot enqueue the same work twice.
    deduplication_key: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=OUTBOX_PENDING, server_default=OUTBOX_PENDING
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    # Earliest time the job may be claimed: the scheduled time for a new
    # job, the retry time after a retryable failure.
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    # Lease (ADR-0046 §5.3): set together with status = 'processing'.
    locked_by: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(sa.String(1024), nullable=True)
    # Set once the job reaches a terminal state (completed / dead).
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
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
        sa.UniqueConstraint("deduplication_key", name="uq_outbox_jobs_deduplication_key"),
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_outbox_jobs_status_valid"),
        sa.CheckConstraint("btrim(job_type) <> ''", name="ck_outbox_jobs_job_type_not_blank"),
        sa.CheckConstraint(
            "deduplication_key IS NULL OR btrim(deduplication_key) <> ''",
            name="ck_outbox_jobs_deduplication_key_not_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_outbox_jobs_payload_is_object"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_jobs_attempts_non_negative"),
        sa.CheckConstraint(
            f"(status = '{OUTBOX_PROCESSING}') = (locked_until IS NOT NULL)",
            name="ck_outbox_jobs_lease_iff_processing",
        ),
        sa.CheckConstraint(
            "(locked_by IS NULL) = (locked_until IS NULL)",
            name="ck_outbox_jobs_lease_owner_and_expiry_together",
        ),
        sa.CheckConstraint(
            f"(status IN ({_TERMINAL_VALUES})) = (finished_at IS NOT NULL)",
            name="ck_outbox_jobs_finished_at_iff_terminal",
        ),
        # Claim scan for new/retryable work, in claim order.
        sa.Index(
            "ix_outbox_jobs_pending_next_attempt_at",
            "next_attempt_at",
            "id",
            postgresql_where=sa.text(f"status = '{OUTBOX_PENDING}'"),
        ),
        # Recovery scan for expired leases of crashed workers.
        sa.Index(
            "ix_outbox_jobs_processing_locked_until",
            "locked_until",
            postgresql_where=sa.text(f"status = '{OUTBOX_PROCESSING}'"),
        ),
    )

    @validates("payload")
    def _validate_payload(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        # Fires on every attribute assignment (constructor kwarg included),
        # so a direct construction cannot bypass the secret guard either.
        assert_safe_outbox_payload(value)
        return value


__all__ = ["OutboxJob"]
