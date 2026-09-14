"""Person/ClubMembership CRUD + audit service layer (Issue #62).

Canonical sources: ADR-0024 (audit infrastructure, as amended by ADR-0025
§1), Issue #62 §17-19.

Transaction/fail-closed semantics (ADR-0024 §5): every audit-required
mutation here follows

    BEGIN
      business mutation (session.add/flush, no commit)
      app.audit.service.record_audit_event(...) (session.add/flush, no commit)
    COMMIT (only after both steps succeed)

wrapped in `try/except: session.rollback(); raise`, so an audit-insert
failure (an invalid action/actor, a prohibited-secret `details` payload,
or a genuine DB constraint violation) rolls back the business mutation
too — this is a different shape from app.events.crud/app.groups.service,
whose functions each commit their own transaction immediately, because
neither of those has an audit step to combine with. ADR-0024 §5 itself
anticipates this exact shape for "a future People/Membership service that
must combine a business mutation with an audit record".

`details` passed to `record_audit_event` is always an explicit, hand-built
safe dict — never an ORM dump. For `Person`, the `phone`/`email`/`address`
fields are the same fields ADR-0025 §8 withholds from the API response;
this module extends that same caution to the audit trail by recording
only *that* one of these three fields changed, never its value — audit
records are not a back door around a withheld field. Non-sensitive Person
fields (`first_name`, `last_name`, `middle_name`, `birth_date`) get a full
`{"from": ..., "to": ...}` diff entry, matching ADR-0024's own example.

Validation (field values, status-transition graph) is delegated entirely
to app.people.lifecycle — never reimplemented here. This module performs
no authorization: the caller (the API router) must have already resolved
and checked authorization (via app.people.authorization) before invoking
any function here, exactly as app.events.crud/app.groups.service already
keep authorization as the router's job.

`GuardianRelationship` is out of scope (Issue #64).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.identity import ClubMembership, Person
from app.people.lifecycle import (
    ENDING_STATUSES,
    InvalidMembershipDatesError,
    OverlappingActiveMembershipError,
    validate_membership_status,
    validate_membership_status_transition,
)

_NO_OVERLAPPING_ACTIVE_CONSTRAINT = "ck_club_memberships_no_overlapping_active"
_LEFT_AT_AFTER_JOINED_AT_CONSTRAINT = "ck_club_memberships_left_at_after_joined_at"


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)

# Person fields ADR-0025 §8 withholds from the API response — the audit
# trail records only that one of these changed, never its value.
_SENSITIVE_PERSON_FIELDS = frozenset({"phone", "email", "address"})

# Person fields update_person() accepts; matches Issue #62 §8 exactly (no
# `status` — Person has none; no `photo_file_id` — no file domain yet).
UPDATABLE_PERSON_FIELDS = frozenset(
    {"first_name", "last_name", "middle_name", "birth_date", "phone", "email", "address"}
)


def _person_field_diff(*, before: Person, fields: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field_name, new_value in fields.items():
        old_value = getattr(before, field_name)
        if old_value == new_value:
            continue
        if field_name in _SENSITIVE_PERSON_FIELDS:
            changes[field_name] = {"changed": True}
        else:
            changes[field_name] = {
                "from": old_value.isoformat() if hasattr(old_value, "isoformat") else old_value,
                "to": new_value.isoformat() if hasattr(new_value, "isoformat") else new_value,
            }
    return {"changes": changes}


def create_person(
    session: Session,
    *,
    first_name: str,
    last_name: str,
    middle_name: Optional[str],
    birth_date,
    phone: Optional[str],
    email: Optional[str],
    address: Optional[str],
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> Person:
    """Create a Person and its `person.created` audit record in one
    transaction (fail-closed — see module docstring).
    """
    person = Person(
        first_name=first_name,
        last_name=last_name,
        middle_name=middle_name,
        birth_date=birth_date,
        phone=phone,
        email=email,
        address=address,
    )
    session.add(person)
    try:
        session.flush()
        record_audit_event(
            session,
            action="person.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="person",
            resource_id=person.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return person


def update_person(
    session: Session,
    *,
    person: Person,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
    **fields: Any,
) -> Person:
    """Apply a partial update (PATCH) of the client-writable Person
    fields and its `person.updated` audit record in one transaction.
    """
    unknown_fields = set(fields) - UPDATABLE_PERSON_FIELDS
    if unknown_fields:
        raise ValueError(f"Fields not updatable via update_person: {sorted(unknown_fields)}")

    diff = _person_field_diff(before=person, fields=fields)
    if not diff["changes"]:
        # Nothing actually changed; still a valid no-op PATCH, but no
        # audit-worthy mutation occurred.
        return person

    for field_name, value in fields.items():
        setattr(person, field_name, value)
    try:
        session.flush()
        record_audit_event(
            session,
            action="person.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="person",
            resource_id=person.id,
            outcome="success",
            request_id=request_id,
            details=diff,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return person


def create_membership(
    session: Session,
    *,
    person_id: uuid.UUID,
    club_id: uuid.UUID,
    membership_type: str,
    status: str,
    joined_at: datetime,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> ClubMembership:
    """Create a ClubMembership and its `membership.created` audit record
    in one transaction. `status` accepts any canonical value (Issue #62
    §8's request schema does not restrict creation to a fixed initial
    status the way Event does with `draft`)."""
    validate_membership_status(status)

    membership = ClubMembership(
        person_id=person_id,
        club_id=club_id,
        membership_type=membership_type,
        status=status,
        joined_at=joined_at,
        left_at=None,
    )
    session.add(membership)
    try:
        session.flush()
        record_audit_event(
            session,
            action="membership.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type="club_membership",
            resource_id=membership.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == _NO_OVERLAPPING_ACTIVE_CONSTRAINT:
            raise OverlappingActiveMembershipError(
                f"An overlapping active membership already exists for person "
                f"{person_id} and membership_type {membership_type!r}"
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise
    return membership


def update_membership_type(
    session: Session,
    *,
    membership: ClubMembership,
    membership_type: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> ClubMembership:
    """Update only `membership_type` (never `status`/`joined_at`/`left_at`
    — those are lifecycle changes, see `transition_membership_status`) and
    record a `membership.updated` audit event in the same transaction
    (fail-closed — see module docstring). No-ops (no mutation, no audit
    record) when the value is unchanged.
    """
    old_membership_type = membership.membership_type
    if old_membership_type == membership_type:
        return membership

    membership.membership_type = membership_type
    try:
        session.flush()
        record_audit_event(
            session,
            action="membership.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=membership.club_id,
            resource_type="club_membership",
            resource_id=membership.id,
            outcome="success",
            request_id=request_id,
            details={
                "changes": {
                    "membership_type": {"from": old_membership_type, "to": membership_type}
                }
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return membership


def transition_membership_status(
    session: Session,
    *,
    membership: ClubMembership,
    new_status: str,
    reason: Optional[str],
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> ClubMembership:
    """Transition `membership.status` per the accepted graph in
    app.people.lifecycle, and record the required audit action(s) in the
    same transaction as the mutation (Issue #62 accepted decisions):
    `membership.status_changed` is recorded for every lifecycle status
    change; `membership.ended` is *additionally* recorded when this
    transition sets `left_at` for the first time. A single transition
    (e.g. `active -> inactive`) therefore produces two audit rows.
    `membership.ended` is never emitted again once `left_at` is already
    set (e.g. a later `inactive -> archived`).

    `left_at` is set to now() the first time a transition reaches an
    ENDING_STATUSES value (`inactive`/`archived`) and is never
    automatically cleared: one ClubMembership row is one continuous
    membership period (Issue #62), and rejoining after `inactive` creates
    a new row (app.people.service.create_membership) rather than
    transitioning this one back to `active` — `inactive -> active` is not
    in the allowed transition graph.
    """
    validate_membership_status_transition(membership.status, new_status)

    old_status = membership.status
    ends_period = new_status in ENDING_STATUSES and membership.left_at is None
    if ends_period:
        membership.left_at = datetime.now(timezone.utc)
    membership.status = new_status

    details: dict[str, Any] = {"changes": {"status": {"from": old_status, "to": new_status}}}
    if reason:
        details["reason"] = reason

    try:
        session.flush()
        record_audit_event(
            session,
            action="membership.status_changed",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=membership.club_id,
            resource_type="club_membership",
            resource_id=membership.id,
            outcome="success",
            request_id=request_id,
            details=details,
        )
        if ends_period:
            record_audit_event(
                session,
                action="membership.ended",
                actor_type="user",
                actor_user_id=actor_user_id,
                club_id=membership.club_id,
                resource_type="club_membership",
                resource_id=membership.id,
                outcome="success",
                request_id=request_id,
                details=details,
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == _LEFT_AT_AFTER_JOINED_AT_CONSTRAINT:
            raise InvalidMembershipDatesError(
                "Setting left_at now would be earlier than joined_at"
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise
    return membership


__all__ = [
    "UPDATABLE_PERSON_FIELDS",
    "create_person",
    "update_person",
    "create_membership",
    "update_membership_type",
    "transition_membership_status",
]
