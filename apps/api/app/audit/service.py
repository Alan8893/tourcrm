"""The reusable audit write boundary (ADR-0024 §3), Issue #59.

`record_audit_event` is the ONLY supported way to create an `AuditLog`
row. Domain modules must call it rather than constructing/inserting an
`AuditLog` row directly or inventing an incompatible parallel audit
mechanism — the same "one shared boundary, not ad-hoc per-caller logic"
shape already used for Club-ownership validation
(app.authorization.club_ownership, ADR-0022).

Transaction/fail-closed semantics (ADR-0024 §5): this function never
calls `session.commit()` or `session.rollback()`. For an audit-required
business mutation, the caller is expected to run:

    try:
        ... business mutation ...
        record_audit_event(session, ...)
        session.commit()
    except Exception:
        session.rollback()
        raise

`record_audit_event` calls `session.flush()` so a validation/constraint
failure surfaces immediately (inside the caller's still-open
transaction) rather than being deferred to an eventual `commit()` the
caller might not expect to fail. If the audit insert fails for any
reason, the business mutation earlier in the same transaction must be
rolled back too — an audit-required operation never gets to "succeed
anyway" without its audit record (fail-closed).

No asynchronous audit delivery exists or is introduced here: recording
stays synchronous and part of the caller's own transaction.
"""

import uuid
from typing import Any, Literal, Optional

from sqlalchemy.orm import Session

from app.audit.security import assert_safe_audit_details
from app.audit.vocabulary import (
    CANONICAL_ACTOR_TYPES,
    CANONICAL_AUDIT_ACTIONS,
    CANONICAL_AUDIT_OUTCOMES,
)
from app.db.audit import AuditLog

ActorType = Literal["user", "system"]
AuditOutcome = Literal["success", "failure"]


class AuditError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidAuditActionError(AuditError):
    """`action` is outside ADR-0024 §4's closed vocabulary."""

    def __init__(self, action: str) -> None:
        super().__init__(f"{action!r} is not a canonical ADR-0024 audit action")
        self.action = action


class InvalidAuditActorError(AuditError):
    """`actor_type`/`actor_user_id` are inconsistent, or `actor_type` is
    outside ADR-0024's closed vocabulary (ADR-0024 §2)."""


class InvalidAuditOutcomeError(AuditError):
    """`outcome` is outside ADR-0024's closed vocabulary."""

    def __init__(self, outcome: str) -> None:
        super().__init__(f"{outcome!r} is not a canonical audit outcome")
        self.outcome = outcome


class InvalidAuditResourceError(AuditError):
    """`resource_type`/`resource_id` were not both set or both omitted
    (ADR-0024 §1)."""


def record_audit_event(
    session: Session,
    *,
    action: str,
    actor_type: ActorType,
    outcome: AuditOutcome,
    actor_user_id: Optional[uuid.UUID] = None,
    club_id: Optional[uuid.UUID] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Create and flush one immutable AuditLog row inside the caller's
    current transaction. Does NOT commit or rollback — see module
    docstring.

    Raises InvalidAuditActionError, InvalidAuditActorError,
    InvalidAuditOutcomeError or InvalidAuditResourceError, and persists
    nothing, when the given values do not satisfy ADR-0024's contract.
    Raises app.audit.security.AuditDetailsError, and persists nothing,
    when `details` contains a prohibited secret/credential key or an
    unsafe (non-JSON) value.

    `club_id` is descriptive event context only (ADR-0024 §2) — this
    function performs no authorization check and is not a substitute for
    one; the caller's own authorization decision must already be made
    before this is called.
    """
    if action not in CANONICAL_AUDIT_ACTIONS:
        raise InvalidAuditActionError(action)

    if actor_type not in CANONICAL_ACTOR_TYPES:
        raise InvalidAuditActorError(f"{actor_type!r} is not a canonical actor_type")
    if actor_type == "user" and actor_user_id is None:
        raise InvalidAuditActorError("actor_type='user' requires actor_user_id")
    if actor_type == "system" and actor_user_id is not None:
        raise InvalidAuditActorError("actor_type='system' must not set actor_user_id")

    if outcome not in CANONICAL_AUDIT_OUTCOMES:
        raise InvalidAuditOutcomeError(outcome)

    if (resource_type is None) != (resource_id is None):
        raise InvalidAuditResourceError(
            "resource_type and resource_id must both be set or both be None"
        )

    # Redundant with AuditLog.details' own @validates hook, but checked
    # here too so the typed AuditDetailsError is raised before a session
    # attribute is even touched, not only once the ORM assigns it.
    assert_safe_audit_details(details)

    record = AuditLog(
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        club_id=club_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        correlation_id=correlation_id,
        details=details,
    )
    session.add(record)
    session.flush()
    return record


__all__ = [
    "AuditError",
    "InvalidAuditActionError",
    "InvalidAuditActorError",
    "InvalidAuditOutcomeError",
    "InvalidAuditResourceError",
    "record_audit_event",
]
