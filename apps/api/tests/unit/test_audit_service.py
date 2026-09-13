"""Pure-Python unit tests for app.audit.service.record_audit_event's
validation (ADR-0024 §2/§4/§5) — no database, no HTTP.

Every case here is rejected before the function ever touches the
session (no AuditLog is constructed, no flush is attempted), so a `None`
session stands in for a real one. See tests/integration/test_audit_log.py
for the real-Postgres creation/constraint/transaction tests.
"""

import uuid

import pytest

from app.audit.security import AuditDetailsError
from app.audit.service import (
    InvalidAuditActionError,
    InvalidAuditActorError,
    InvalidAuditOutcomeError,
    InvalidAuditResourceError,
    record_audit_event,
)
from app.audit.vocabulary import CANONICAL_AUDIT_ACTIONS


@pytest.mark.parametrize("action", sorted(CANONICAL_AUDIT_ACTIONS))
def test_every_canonical_action_passes_the_action_check(action: str) -> None:
    # Reaches the (deliberately failing, to stay DB-free) actor check
    # only after the action check passes — proves the action itself was
    # accepted.
    with pytest.raises(InvalidAuditActorError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action=action,
            actor_type="not-a-real-actor-type",  # type: ignore[arg-type]
            outcome="success",
        )


@pytest.mark.parametrize(
    "action", ["", "event.deleted", "EVENT.CREATED", "user.deleted", "custom.action"]
)
def test_non_canonical_action_is_rejected(action: str) -> None:
    with pytest.raises(InvalidAuditActionError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action=action,
            actor_type="system",
            outcome="success",
        )


def test_user_actor_without_actor_user_id_is_rejected() -> None:
    with pytest.raises(InvalidAuditActorError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="user",
            outcome="success",
            actor_user_id=None,
        )


def test_system_actor_with_actor_user_id_is_rejected() -> None:
    with pytest.raises(InvalidAuditActorError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="system",
            outcome="success",
            actor_user_id=uuid.uuid4(),
        )


def test_non_canonical_actor_type_is_rejected() -> None:
    with pytest.raises(InvalidAuditActorError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="admin",  # type: ignore[arg-type]
            outcome="success",
        )


def test_non_canonical_outcome_is_rejected() -> None:
    with pytest.raises(InvalidAuditOutcomeError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="system",
            outcome="ok",  # type: ignore[arg-type]
        )


def test_resource_type_without_resource_id_is_rejected() -> None:
    with pytest.raises(InvalidAuditResourceError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="system",
            outcome="success",
            resource_type="user",
            resource_id=None,
        )


def test_resource_id_without_resource_type_is_rejected() -> None:
    with pytest.raises(InvalidAuditResourceError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="system",
            outcome="success",
            resource_type=None,
            resource_id=uuid.uuid4(),
        )


def test_prohibited_details_key_is_rejected_before_touching_the_session() -> None:
    with pytest.raises(AuditDetailsError):
        record_audit_event(
            None,  # type: ignore[arg-type]
            action="user.created",
            actor_type="system",
            outcome="success",
            details={"password": "hunter2"},
        )


def test_service_exposes_no_update_or_delete_operation() -> None:
    # ADR-0024 §3: AuditLog is append-only; the service boundary must
    # never grow an update/delete helper.
    from app.audit import service

    exported = set(service.__all__)
    assert not any("update" in name.lower() for name in exported)
    assert not any("delete" in name.lower() for name in exported)
    assert "record_audit_event" in exported
