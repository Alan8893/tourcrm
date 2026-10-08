"""The in-transaction outbox enqueue boundary (Issue #318, ADR-0045 §2.3).

`enqueue_outbox_job` never calls `session.commit()` or
`session.rollback()`: the job is inserted inside the caller's own open
transaction, next to the business mutation that requires it, and becomes
visible to the worker only when the caller commits —

    ... business mutation ...
    enqueue_outbox_job(session, job_type=..., payload=...)
    session.commit()

A rollback of the business transaction discards the job with it. No
provider call, worker or retry execution happens here.

Deduplication uses `INSERT ... ON CONFLICT DO NOTHING` rather than letting
the UNIQUE constraint raise: a duplicate is reported as `created=False`
and the caller's transaction (and its business mutation) stays usable —
a constraint violation would abort the whole PostgreSQL transaction.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.outbox import OutboxJob
from app.outbox.security import assert_safe_outbox_payload


class InvalidOutboxJobError(ValueError):
    """`job_type` or `deduplication_key` is blank."""


def enqueue_outbox_job(
    session: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    deduplication_key: Optional[str] = None,
    available_at: Optional[datetime] = None,
) -> tuple[OutboxJob, bool]:
    """Insert one pending job into the caller's open transaction.

    Returns `(job, created)`. With a `deduplication_key` that already
    exists, nothing is inserted and the existing job is returned with
    `created=False`. `available_at` is the earliest claim time (defaults
    to the database's `now()`, i.e. the transaction start time).

    Raises InvalidOutboxJobError for a blank `job_type`/`deduplication_key`
    and app.outbox.security.OutboxPayloadError for an unsafe payload; in
    both cases nothing is written.
    """
    if not job_type.strip():
        raise InvalidOutboxJobError("job_type must not be blank")
    if deduplication_key is not None and not deduplication_key.strip():
        raise InvalidOutboxJobError("deduplication_key must not be blank")
    assert_safe_outbox_payload(payload)

    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "job_type": job_type,
        "payload": payload,
        "deduplication_key": deduplication_key,
    }
    if available_at is not None:
        values["next_attempt_at"] = available_at

    # Pending ORM objects of the business mutation must reach the database
    # before this Core-level INSERT runs in the same transaction.
    session.flush()
    statement = insert(OutboxJob).values(**values)
    if deduplication_key is not None:
        statement = statement.on_conflict_do_nothing(index_elements=["deduplication_key"])
    inserted_id = session.execute(statement.returning(OutboxJob.id)).scalar_one_or_none()

    if inserted_id is not None:
        job = session.get(OutboxJob, inserted_id)
        assert job is not None
        return job, True

    existing = session.execute(
        select(OutboxJob).where(OutboxJob.deduplication_key == deduplication_key)
    ).scalar_one()
    return existing, False


__all__ = ["InvalidOutboxJobError", "enqueue_outbox_job"]
