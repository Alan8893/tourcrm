"""EventSeries/EventOccurrence/EventOccurrenceException mutation service
layer (Issue #79, ADR-0028), matching app.role_assignments.service's and
app.groups.service's shape exactly: every mutating function here owns and
commits its own transaction, performs its business-rule checks, records
the ADR-0028 audit action inside that same transaction (fail-closed —
ADR-0024 §5), and returns the mutated row.

This module performs no authorization: the caller (the API router) must
already have resolved and checked the applicable canonical Event
permission (ADR-0028 §11: `event.read`/`event.update`/`event.manage`,
never a recurrence-specific permission) before invoking anything here —
see app.events.series_authorization.

`create_successor_version` ("this and following") is the one operation
that legitimately emits two audit records for one caller action
(`event_series.version_created` + `event_occurrence.series_rebound`) —
ADR-0028 §12 explicitly anticipates this.

Occurrence cancellation is exclusively reachable through
`set_occurrence_exception` (`exception_type="cancelled"`), never through
`transition_occurrence_status` — ADR-0028 §5 frames "cancelled" as an
exception type, distinct from the direct `scheduled -> in_progress ->
completed` operational progression, which is where
`transition_occurrence_status` fires `event_occurrence.status_changed`;
a cancellation instead fires `event_occurrence.exception_created`/
`.exception_changed`, per ADR-0028 §12's "Exception creation/change and
occurrence lifecycle transitions use their dedicated codes."
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.event_recurrence import EventOccurrence, EventOccurrenceException, EventSeries
from app.events.lifecycle import validate_event_type
from app.events.series_lifecycle import (
    EventSeriesDomainError,
    validate_exception_cancellation_reason,
    validate_exception_type,
    validate_occurrence_overrides,
    validate_occurrence_status_transition,
    validate_series_status_transition,
)
from app.events.versioning import create_successor_version as _create_successor_version

_DIRECT_OCCURRENCE_TRANSITIONS = frozenset({"in_progress", "completed"})


class EventSeriesServiceError(Exception):
    """Base class for this module's typed, expected failures."""


class OccurrenceCancellationRequiresExceptionError(EventSeriesServiceError):
    """`transition_occurrence_status` never accepts `cancelled` as a
    target — cancellation always goes through `set_occurrence_exception`
    (ADR-0028 §5), which alone accepts/requires a reason."""


def create_series(
    session: Session,
    *,
    club_id: uuid.UUID,
    name: str,
    description: Optional[str],
    event_type: str,
    series_start_at,
    series_end_at,
    occurrence_limit: Optional[int],
    recurrence_rule: str,
    timezone: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventSeries:
    """Create version 1 of a new logical EventSeries
    (`root_series_id == id`, `supersedes_series_id IS NULL`), always
    starting `active`. Records `event_series.created`.
    """
    validate_event_type(event_type)

    series_id = uuid.uuid4()
    series = EventSeries(
        id=series_id,
        root_series_id=series_id,
        supersedes_series_id=None,
        version=1,
        club_id=club_id,
        name=name,
        description=description,
        event_type=event_type,
        series_start_at=series_start_at,
        series_end_at=series_end_at,
        occurrence_limit=occurrence_limit,
        recurrence_rule=recurrence_rule,
        timezone=timezone,
        status="active",
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(series)
    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=club_id,
            resource_type="event_series",
            resource_id=series.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return series


def update_series_metadata(
    session: Session,
    *,
    series: EventSeries,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
    **fields,
) -> EventSeries:
    """Apply a non-versioning update — `name`/`description` only.

    Recurrence-affecting fields (`recurrence_rule`, `series_start_at`,
    `series_end_at`, `occurrence_limit`, `timezone`) are never accepted
    here: changing the recurrence definition always goes through
    `create_successor_version` ("this and following"), per ADR-0028 §3 —
    the current version's own recurrence definition is otherwise
    immutable in place. Records `event_series.updated`.
    """
    allowed_fields = frozenset({"name", "description"})
    unknown_fields = set(fields) - allowed_fields
    if unknown_fields:
        raise ValueError(
            f"Fields not updatable via update_series_metadata: {sorted(unknown_fields)}"
        )

    for field_name, value in fields.items():
        setattr(series, field_name, value)
    series.updated_by = actor_user_id

    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=series.club_id,
            resource_type="event_series",
            resource_id=series.id,
            outcome="success",
            request_id=request_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return series


def transition_series_status(
    session: Session,
    *,
    series: EventSeries,
    new_status: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventSeries:
    """ADR-0028 §6: `active <-> paused`, `active -> cancelled ->
    archived`. Records `event_series.status_changed`. Raises
    app.events.series_lifecycle.InvalidSeriesStatusTransitionError
    (persisting nothing) for any other transition — pausing/cancelling
    never touches already-materialized EventOccurrence rows.
    """
    validate_series_status_transition(series.status, new_status)
    old_status = series.status
    series.status = new_status
    series.updated_by = actor_user_id

    try:
        session.flush()
        record_audit_event(
            session,
            action="event_series.status_changed",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=series.club_id,
            resource_type="event_series",
            resource_id=series.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"status": {"from": old_status, "to": new_status}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return series


def create_successor_version(
    session: Session,
    *,
    source_series_id: uuid.UUID,
    boundary_occurrence_id: uuid.UUID,
    name: str,
    description: Optional[str],
    event_type: str,
    series_start_at,
    series_end_at,
    occurrence_limit: Optional[int],
    recurrence_rule: str,
    timezone: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[EventSeries, EventOccurrence]:
    """"This and following" — see app.events.versioning for the
    transactional locking/stale-version/boundary-validation algorithm.
    Records `event_series.version_created` and
    `event_occurrence.series_rebound` in the same transaction (ADR-0028
    §12 explicitly anticipates one caller action producing both).
    """
    validate_event_type(event_type)

    try:
        successor, rebound = _create_successor_version(
            session,
            source_series_id=source_series_id,
            boundary_occurrence_id=boundary_occurrence_id,
            name=name,
            description=description,
            event_type=event_type,
            series_start_at=series_start_at,
            series_end_at=series_end_at,
            occurrence_limit=occurrence_limit,
            recurrence_rule=recurrence_rule,
            timezone=timezone,
            updated_by=actor_user_id,
        )
        record_audit_event(
            session,
            action="event_series.version_created",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=successor.club_id,
            resource_type="event_series",
            resource_id=successor.id,
            outcome="success",
            request_id=request_id,
            details={"supersedes_series_id": str(successor.supersedes_series_id)},
        )
        record_audit_event(
            session,
            action="event_occurrence.series_rebound",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=rebound.club_id,
            resource_type="event_occurrence",
            resource_id=rebound.id,
            outcome="success",
            request_id=request_id,
            details={"new_series_id": str(successor.id)},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return successor, rebound


def transition_occurrence_status(
    session: Session,
    *,
    occurrence: EventOccurrence,
    new_status: str,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrence:
    """The direct operational progression `scheduled -> in_progress ->
    completed` only — never `cancelled` (see module docstring; raises
    OccurrenceCancellationRequiresExceptionError, persisting nothing, for
    that target). Records `event_occurrence.status_changed`.
    """
    if new_status not in _DIRECT_OCCURRENCE_TRANSITIONS:
        raise OccurrenceCancellationRequiresExceptionError(
            "Cancellation must go through set_occurrence_exception"
        )
    validate_occurrence_status_transition(occurrence.status, new_status)
    old_status = occurrence.status
    occurrence.status = new_status
    occurrence.updated_by = actor_user_id

    try:
        session.flush()
        record_audit_event(
            session,
            action="event_occurrence.status_changed",
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=occurrence.club_id,
            resource_type="event_occurrence",
            resource_id=occurrence.id,
            outcome="success",
            request_id=request_id,
            details={"changes": {"status": {"from": old_status, "to": new_status}}},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return occurrence


def _validate_overrides_values(overrides: Optional[dict]) -> None:
    if not overrides:
        return
    if "event_type" in overrides:
        validate_event_type(overrides["event_type"])
    if "name" in overrides and not overrides["name"]:
        raise EventSeriesDomainError("name override must not be empty")


def set_occurrence_exception(
    session: Session,
    *,
    occurrence: EventOccurrence,
    exception_type: str,
    effective_start_at=None,
    effective_end_at=None,
    overrides: Optional[dict] = None,
    cancellation_reason: Optional[str] = None,
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventOccurrenceException:
    """Create or update the occurrence's current exception (ADR-0028 §5):
    `rescheduled` keeps `status="scheduled"` and updates the occurrence's
    effective `starts_at`/`ends_at`/snapshot fields in place; `cancelled`
    transitions the occurrence to `status="cancelled"` (terminal) and
    requires `cancellation_reason`. The occurrence's `id` is never
    changed and no replacement occurrence is ever created.

    `overrides` is validated against the allow-list (ADR-0028 §5) and,
    for each present key, the same domain rules as an ordinary Event
    update. Records `event_occurrence.exception_created` (no prior
    exception existed) or `event_occurrence.exception_changed`
    (overwriting the existing one) — never both.
    """
    validate_exception_type(exception_type)
    validate_exception_cancellation_reason(exception_type, cancellation_reason)
    validate_occurrence_overrides(overrides)
    _validate_overrides_values(overrides)

    existing = session.execute(
        select(EventOccurrenceException).where(
            EventOccurrenceException.occurrence_id == occurrence.id
        )
    ).scalar_one_or_none()
    is_new = existing is None

    if exception_type == "cancelled":
        validate_occurrence_status_transition(
            occurrence.status, "cancelled", cancellation_reason=cancellation_reason
        )
        occurrence.status = "cancelled"
        occurrence.cancellation_reason = cancellation_reason
    else:
        if effective_start_at is not None:
            occurrence.starts_at = effective_start_at
        if effective_end_at is not None:
            occurrence.ends_at = effective_end_at
    if overrides:
        for field_name, value in overrides.items():
            setattr(occurrence, field_name, value)
    occurrence.updated_by = actor_user_id

    if existing is None:
        exception = EventOccurrenceException(
            occurrence_id=occurrence.id,
            exception_type=exception_type,
            original_start_at=occurrence.recurrence_anchor_at,
            effective_start_at=effective_start_at,
            effective_end_at=effective_end_at,
            overrides=overrides,
            cancellation_reason=cancellation_reason,
            created_by=actor_user_id,
        )
        session.add(exception)
    else:
        exception = existing
        exception.exception_type = exception_type
        exception.effective_start_at = effective_start_at
        exception.effective_end_at = effective_end_at
        exception.overrides = overrides
        exception.cancellation_reason = cancellation_reason

    try:
        session.flush()
        record_audit_event(
            session,
            action=(
                "event_occurrence.exception_created"
                if is_new
                else "event_occurrence.exception_changed"
            ),
            actor_type="user",
            actor_user_id=actor_user_id,
            club_id=occurrence.club_id,
            resource_type="event_occurrence",
            resource_id=occurrence.id,
            outcome="success",
            request_id=request_id,
            details={"exception_type": exception_type},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return exception


__all__ = [
    "EventSeriesServiceError",
    "OccurrenceCancellationRequiresExceptionError",
    "create_series",
    "update_series_metadata",
    "transition_series_status",
    "create_successor_version",
    "transition_occurrence_status",
    "set_occurrence_exception",
]
