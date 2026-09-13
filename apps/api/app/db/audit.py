"""Canonical audit persistence (Issue #59).

Canonical source: docs/03-architecture/adr/ADR-0024-audit-infrastructure.md
(closes ODR-015), which this model follows field-for-field. See also
docs/03-architecture/data-model.md §19 and docs/03-architecture/
database-schema.md §19.

`AuditLog` rows are append-only: this module provides no update/delete
helper, and no other module may add one — the only supported way to
create a row is app.audit.service.record_audit_event (ADR-0024 §3).

No `created_at`/`updated_at`/`created_by`/`updated_by`: `occurred_at` is
the only timestamp this immutable record needs (ADR-0024 §1).

`actor_type='system'` uses NULL `actor_user_id` rather than a fabricated
"System" `User` row (ADR-0024 §2). `club_id` is descriptive event
context only, never an authorization mechanism. `action` is restricted
to the closed vocabulary in app.audit.vocabulary (ADR-0024 §4).

The `details` secret-prohibition boundary (ADR-0024 §6) is enforced here
via an `@validates` hook — the same defense-in-depth shape already used
for `Event.timezone` (app.events.lifecycle.validate_timezone) — so a
caller constructing/assigning to a row directly (bypassing
app.audit.service) cannot smuggle a secret through either.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.audit.security import assert_safe_audit_details
from app.audit.vocabulary import (
    CANONICAL_ACTOR_TYPES,
    CANONICAL_AUDIT_ACTIONS,
    CANONICAL_AUDIT_OUTCOMES,
)
from app.db.base import Base

_ACTOR_TYPE_VALUES = ",".join(f"'{value}'" for value in sorted(CANONICAL_ACTOR_TYPES))
_OUTCOME_VALUES = ",".join(f"'{value}'" for value in sorted(CANONICAL_AUDIT_OUTCOMES))
_ACTION_VALUES = ",".join(f"'{value}'" for value in sorted(CANONICAL_AUDIT_ACTIONS))


class AuditLog(Base):
    """One immutable audit record (ADR-0024).

    Construct rows only through app.audit.service.record_audit_event;
    this class is not a public write API on its own (ADR-0024 §3).
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    actor_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=True
    )
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    request_id: Mapped[Optional[str]] = mapped_column(sa.String(128), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(sa.String(128), nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            f"actor_type IN ({_ACTOR_TYPE_VALUES})", name="ck_audit_logs_actor_type_valid"
        ),
        sa.CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL) OR "
            "(actor_type = 'system' AND actor_user_id IS NULL)",
            name="ck_audit_logs_actor_user_id_consistent",
        ),
        sa.CheckConstraint(f"outcome IN ({_OUTCOME_VALUES})", name="ck_audit_logs_outcome_valid"),
        sa.CheckConstraint(f"action IN ({_ACTION_VALUES})", name="ck_audit_logs_action_valid"),
        sa.CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)",
            name="ck_audit_logs_resource_consistent",
        ),
        sa.Index("ix_audit_logs_occurred_at", "occurred_at"),
        sa.Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        sa.Index("ix_audit_logs_club_id", "club_id"),
        sa.Index("ix_audit_logs_resource_type_resource_id", "resource_type", "resource_id"),
        sa.Index("ix_audit_logs_request_id", "request_id"),
    )

    @validates("details")
    def _validate_details(
        self, key: str, value: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        # Fires on every attribute assignment (constructor kwarg included),
        # so a prohibited secret/credential key is rejected before the row
        # is even flushed — see module docstring.
        assert_safe_audit_details(value)
        return value


__all__ = ["AuditLog"]
