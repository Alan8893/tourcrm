"""GuardianRelationship CRUD + audit service layer (Issue #64).

Canonical sources: ADR-0024 (audit infrastructure), ADR-0023 §3
(persistence/constraints), ADR-0025 §3 (terminate always -> revoked),
Issue #64 §18-19.

Same transaction/fail-closed shape as app.people.service (see that
module's docstring): business mutation + `record_audit_event` in one
transaction, `try/except: session.rollback(); raise` so an audit-insert
failure rolls back the business mutation too.

This module performs no authorization: the caller (the API router) must
have already resolved and checked authorization (via
app.people.guardian_authorization) before invoking any function here.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.identity import GuardianRelationship
from app.people.guardian_lifecycle import (
    CREATABLE_GUARDIAN_RELATIONSHIP_STATUS,
    AlreadyRevokedError,
    DuplicateActiveGuardianRelationshipError,
    DuplicatePrimaryContactError,
    SelfLinkNotAllowedError,
    validate_guardian_child_distinct,
)

_NO_OVERLAPPING_ACTIVE_CONSTRAINT = "ck_guardian_relationships_no_overlapping_active"
_NO_OVERLAPPING_PRIMARY_CONTACT_CONSTRAINT = (
    "ck_guardian_relationships_no_overlapping_primary_contact"
)
_GUARDIAN_CHILD_DISTINCT_CONSTRAINT = "ck_guardian_relationships_guardian_child_distinct"

# Fields update_guardian_relationship() accepts — matches Issue #64 §8
# exactly (no `status`: changed only via terminate/natural expiry).
UPDATABLE_GUARDIAN_RELATIONSHIP_FIELDS = frozenset({"relationship_type", "is_primary_contact"})


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _raise_for_constraint(exc: IntegrityError) -> None:
    """Raise the typed domain error matching `exc`'s constraint name, or
    return normally if it names neither known constraint — the caller is
    responsible for re-raising `exc` itself in that case (never swallowed
    here). `_GUARDIAN_CHILD_DISTINCT_CONSTRAINT` is defense-in-depth only
    — `validate_guardian_child_distinct` already rejects a self-link
    before any insert is attempted — so this DB-level mapping never leaks
    a raw 500 even if that changes.
    """
    name = _constraint_name(exc)
    if name == _NO_OVERLAPPING_ACTIVE_CONSTRAINT:
        raise DuplicateActiveGuardianRelationshipError(
            "An overlapping active relationship already exists for this "
            "guardian/child/relationship_type"
        ) from exc
    if name == _NO_OVERLAPPING_PRIMARY_CONTACT_CONSTRAINT:
        raise DuplicatePrimaryContactError(
            "An overlapping active primary-contact relationship already exists for this child"
        ) from exc
    if name == _GUARDIAN_CHILD_DISTINCT_CONSTRAINT:
        raise SelfLinkNotAllowedError(
            "guardian_person_id and child_person_id must not be the same Person"
        ) from exc


def create_guardian_relationship(
    session: Session,
    *,
    guardian_person_id: uuid.UUID,
    child_person_id: uuid.UUID,
    relationship_type: str,
    is_primary_contact: bool,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> GuardianRelationship:
    """Create a GuardianRelationship (always `status = 'active'` — Issue
    #64 §8/§16, enforced at the schema layer via a Literal type) and its
    `guardian_relationship.created` audit record in one transaction.
    """
    validate_guardian_child_distinct(guardian_person_id, child_person_id)

    relationship = GuardianRelationship(
        guardian_person_id=guardian_person_id,
        child_person_id=child_person_id,
        relationship_type=relationship_type,
        status=CREATABLE_GUARDIAN_RELATIONSHIP_STATUS,
        is_primary_contact=is_primary_contact,
        valid_from=datetime.now(timezone.utc),
        valid_to=None,
    )
    session.add(relationship)
    try:
        session.flush()
        record_audit_event(
            session,
            action="guardian_relationship.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="guardian_relationship",
            resource_id=relationship.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _raise_for_constraint(exc)
        raise
    except Exception:
        session.rollback()
        raise
    return relationship


def update_guardian_relationship(
    session: Session,
    *,
    relationship: GuardianRelationship,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
    **fields: Any,
) -> GuardianRelationship:
    """Apply a partial update (PATCH) of `relationship_type`/
    `is_primary_contact` only (never `status`) and its
    `guardian_relationship.updated` audit record in one transaction.
    No-ops (no mutation, no audit record) when nothing actually changes.
    """
    unknown_fields = set(fields) - UPDATABLE_GUARDIAN_RELATIONSHIP_FIELDS
    if unknown_fields:
        raise ValueError(
            f"Fields not updatable via update_guardian_relationship: {sorted(unknown_fields)}"
        )

    changes: dict[str, Any] = {}
    for field_name, new_value in fields.items():
        old_value = getattr(relationship, field_name)
        if old_value == new_value:
            continue
        changes[field_name] = {"from": old_value, "to": new_value}
        setattr(relationship, field_name, new_value)

    if not changes:
        return relationship

    try:
        session.flush()
        record_audit_event(
            session,
            action="guardian_relationship.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="guardian_relationship",
            resource_id=relationship.id,
            outcome="success",
            request_id=request_id,
            details={"changes": changes},
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _raise_for_constraint(exc)
        raise
    except Exception:
        session.rollback()
        raise
    return relationship


def terminate_guardian_relationship(
    session: Session,
    *,
    relationship: GuardianRelationship,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> GuardianRelationship:
    """Set `status = 'revoked'` — always, no alternative outcome
    (ADR-0025 §3) — and record `guardian_relationship.revoked` in the
    same transaction. Rejects an already-`revoked` (stored status)
    relationship: `revoked` is a terminal stored state, mirroring
    app.people.service.transition_membership_status's `archived`-is-
    terminal precedent.
    """
    if relationship.status == "revoked":
        raise AlreadyRevokedError("This GuardianRelationship is already revoked")

    old_status = relationship.status
    relationship.status = "revoked"
    try:
        session.flush()
        record_audit_event(
            session,
            action="guardian_relationship.revoked",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="guardian_relationship",
            resource_id=relationship.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"status": {"from": old_status, "to": "revoked"}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return relationship


__all__ = [
    "UPDATABLE_GUARDIAN_RELATIONSHIP_FIELDS",
    "create_guardian_relationship",
    "update_guardian_relationship",
    "terminate_guardian_relationship",
]
