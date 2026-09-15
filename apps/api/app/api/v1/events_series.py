"""Event recurrence API — EventSeries/EventOccurrence (Issue #79,
ADR-0028).

Canonical sources: docs/05-api/event-recurrence-api.md (this router's own
endpoint list and semantics), docs/05-api/endpoint-inventory.md §9,
ADR-0028, ADR-0013 (scopes), ADR-0014 (response envelope), ADR-0024
(audit).

Mounted at `/events/series` and `/events/occurrences` — a sibling of
app.api.v1.events (Event itself), never a competing/parallel resource
namespace: `POST /events/series/{series_id}/exceptions` is the sole
canonical mutation endpoint for occurrence-level reschedule/cancellation/
allow-listed overrides; dedicated `/events/occurrences/{id}/cancel` and
`/events/occurrences/{id}/reschedule` endpoints are explicitly NOT
canonical per event-recurrence-api.md and are not implemented here.

Permission mapping (ADR-0028 §11 — no recurrence-specific permission
exists; reuses exactly the canonical Event permission set from
roles-and-permissions.md §4: `event.read`/`event.create`/`event.update`/
`event.cancel`/`event.manage`):

- reads -> `event.read`;
- series create -> `event.create` (mirrors app.api.v1.events.create_event);
- series metadata/"this and following" update, pause/resume,
  occurrence direct lifecycle progression, reschedule exception ->
  `event.update`;
- series cancel, cancel exception -> `event.cancel` (mirrors Event's own
  `_STATUS_TRANSITION_PERMISSIONS["cancelled"]`);
- series archive -> `event.manage` (mirrors Event's own dedicated archive
  endpoint, which alone requires `event.manage` — roles-and-permissions.md
  §12).

Existence-hiding matches app.api.v1.events/app.api.v1.role_assignments
exactly: a missing resource and an existing-but-unauthorized resource
receive an identical 404.
"""

import uuid
from datetime import datetime
from typing import NoReturn, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.events_series_schemas import (
    EventOccurrenceExceptionOut,
    EventOccurrenceOut,
    EventOccurrenceUpdateRequest,
    EventSeriesCreateRequest,
    EventSeriesExceptionRequest,
    EventSeriesOut,
    EventSeriesUpdateRequest,
    RecurrenceRuleInput,
)
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.event_recurrence import EventOccurrence, EventOccurrenceException, EventSeries
from app.db.identity import Club
from app.db.session import get_db
from app.events import series_service
from app.events.lifecycle import EventDomainError
from app.events.materialization import ensure_materialized
from app.events.rrule import RecurrenceError, RecurrenceInput, build_canonical_rrule
from app.events.series_authorization import (
    build_occurrence_resource_context,
    build_series_resource_context,
)
from app.events.series_lifecycle import EventSeriesDomainError
from app.events.versioning import (
    BoundaryOccurrenceNotInCurrentVersionError,
    StaleSeriesVersionError,
)

series_router = APIRouter(prefix="/events/series", tags=["event-recurrence"])
occurrences_router = APIRouter(prefix="/events/occurrences", tags=["event-recurrence"])

_SERIES_NOT_FOUND_CODE = "event_series_not_found"
_SERIES_NOT_FOUND_MESSAGE = "Event series not found"
_OCCURRENCE_NOT_FOUND_CODE = "event_occurrence_not_found"
_OCCURRENCE_NOT_FOUND_MESSAGE = "Event occurrence not found"

# Series lifecycle transitions each use the same permission Event's own
# equivalent status transition would (mirrors
# app.api.v1.events._STATUS_TRANSITION_PERMISSIONS).
_SERIES_LIFECYCLE_PERMISSIONS = {
    "paused": "event.update",
    "active": "event.update",  # resume
    "cancelled": "event.cancel",
    "archived": "event.manage",
}


def _series_out(series: EventSeries) -> EventSeriesOut:
    return EventSeriesOut(
        id=series.id,
        root_series_id=series.root_series_id,
        version=series.version,
        supersedes_series_id=series.supersedes_series_id,
        club_id=series.club_id,
        name=series.name,
        description=series.description,
        event_type=series.event_type,
        series_start_at=series.series_start_at,
        series_end_at=series.series_end_at,
        occurrence_limit=series.occurrence_limit,
        duration_minutes=series.duration_minutes,
        recurrence_rule=series.recurrence_rule,
        timezone=series.timezone,
        status=series.status,
        created_by=series.created_by,
        updated_by=series.updated_by,
        created_at=series.created_at,
        updated_at=series.updated_at,
    )


def _exception_out(exception: EventOccurrenceException) -> EventOccurrenceExceptionOut:
    return EventOccurrenceExceptionOut(
        id=exception.id,
        exception_type=exception.exception_type,
        original_start_at=exception.original_start_at,
        effective_start_at=exception.effective_start_at,
        effective_end_at=exception.effective_end_at,
        overrides=exception.overrides,
        cancellation_reason=exception.cancellation_reason,
    )


def _occurrence_out(
    occurrence: EventOccurrence, exception: Optional[EventOccurrenceException] = None
) -> EventOccurrenceOut:
    return EventOccurrenceOut(
        id=occurrence.id,
        series_id=occurrence.series_id,
        club_id=occurrence.club_id,
        name=occurrence.name,
        description=occurrence.description,
        event_type=occurrence.event_type,
        starts_at=occurrence.starts_at,
        ends_at=occurrence.ends_at,
        timezone=occurrence.timezone,
        status=occurrence.status,
        cancellation_reason=occurrence.cancellation_reason,
        exception=_exception_out(exception) if exception is not None else None,
    )


def _get_authorized_series_or_404(
    db: Session,
    *,
    series_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> EventSeries:
    stmt = select(EventSeries).where(EventSeries.id == series_id)
    if lock:
        stmt = stmt.with_for_update()
    series = db.execute(stmt).scalar_one_or_none()
    if series is None:
        raise APIError(status.HTTP_404_NOT_FOUND, _SERIES_NOT_FOUND_CODE, _SERIES_NOT_FOUND_MESSAGE)

    context = build_series_resource_context(series=series)
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        raise APIError(status.HTTP_404_NOT_FOUND, _SERIES_NOT_FOUND_CODE, _SERIES_NOT_FOUND_MESSAGE)
    return series


def _get_authorized_occurrence_or_404(
    db: Session,
    *,
    occurrence_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> EventOccurrence:
    stmt = select(EventOccurrence).where(EventOccurrence.id == occurrence_id)
    if lock:
        stmt = stmt.with_for_update()
    occurrence = db.execute(stmt).scalar_one_or_none()
    if occurrence is None:
        raise APIError(
            status.HTTP_404_NOT_FOUND, _OCCURRENCE_NOT_FOUND_CODE, _OCCURRENCE_NOT_FOUND_MESSAGE
        )

    context = build_occurrence_resource_context(db, occurrence=occurrence, user_id=user_id)
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        raise APIError(
            status.HTTP_404_NOT_FOUND, _OCCURRENCE_NOT_FOUND_CODE, _OCCURRENCE_NOT_FOUND_MESSAGE
        )
    return occurrence


def _current_exception_for(
    db: Session, occurrence_id: uuid.UUID
) -> Optional[EventOccurrenceException]:
    return db.execute(
        select(EventOccurrenceException).where(
            EventOccurrenceException.occurrence_id == occurrence_id
        )
    ).scalar_one_or_none()


def _build_recurrence(
    recurrence: RecurrenceRuleInput, *, series_end_at: Optional[datetime]
) -> tuple[str, Optional[datetime]]:
    try:
        return build_canonical_rrule(
            RecurrenceInput(
                frequency=recurrence.frequency,
                interval=recurrence.interval,
                by_day=tuple(recurrence.by_day),
                by_month_day=tuple(recurrence.by_month_day),
                by_month=tuple(recurrence.by_month),
                count=recurrence.count,
                until=recurrence.until,
            ),
            series_end_at=series_end_at,
        )
    except RecurrenceError as exc:
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "invalid_recurrence_rule", str(exc)
        ) from exc


def _raise_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, StaleSeriesVersionError):
        raise APIError(status.HTTP_409_CONFLICT, "stale_series_version", str(exc)) from exc
    if isinstance(exc, BoundaryOccurrenceNotInCurrentVersionError):
        raise APIError(status.HTTP_409_CONFLICT, "invalid_version_boundary", str(exc)) from exc
    if isinstance(exc, (EventSeriesDomainError, EventDomainError, RecurrenceError, ValueError)):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_event_series_data", str(exc)
        ) from exc
    raise exc


# --- Series -----------------------------------------------------------


@series_router.post("", status_code=status.HTTP_201_CREATED, response_model=EventSeriesOut)
def create_series(
    payload: EventSeriesCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventSeriesOut:
    if db.get(Club, payload.club_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_club_id", "club_id does not exist"
        )

    authorizer = Authorizer(session=db, user_id=principal.user_id, permission_code="event.create")
    authorizer.check(ResourceContext(club_id=payload.club_id))

    recurrence_rule, series_end_at = _build_recurrence(
        payload.recurrence, series_end_at=payload.series_end_at
    )

    try:
        series = series_service.create_series(
            db,
            club_id=payload.club_id,
            name=payload.name,
            description=payload.description,
            event_type=payload.event_type,
            series_start_at=payload.series_start_at,
            series_end_at=series_end_at,
            occurrence_limit=payload.occurrence_limit,
            duration_minutes=payload.duration_minutes,
            recurrence_rule=recurrence_rule,
            timezone=payload.timezone,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except (EventSeriesDomainError, EventDomainError) as exc:
        _raise_for_domain_error(exc)
    return _series_out(series)


@series_router.get("/{series_id}", response_model=EventSeriesOut)
def get_series(
    series_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> EventSeriesOut:
    series = _get_authorized_series_or_404(
        db, series_id=series_id, user_id=principal.user_id, permission_code="event.read"
    )
    return _series_out(series)


@series_router.patch("/{series_id}", response_model=EventSeriesOut)
def update_series(
    series_id: uuid.UUID,
    payload: EventSeriesUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventSeriesOut:
    series = _get_authorized_series_or_404(
        db,
        series_id=series_id,
        user_id=principal.user_id,
        permission_code="event.update",
        lock=True,
    )

    if payload.update_scope == "entire_series":
        fields = {}
        if payload.name is not None:
            fields["name"] = payload.name
        if payload.description is not None:
            fields["description"] = payload.description
        try:
            series = series_service.update_series_metadata(
                db,
                series=series,
                actor_user_id=principal.user_id,
                request_id=get_request_id(request),
                **fields,
            )
        except (EventSeriesDomainError, EventDomainError, ValueError) as exc:
            _raise_for_domain_error(exc)
        return _series_out(series)

    # this_and_following
    if payload.occurrence_id is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "occurrence_id_required",
            "occurrence_id is required for update_scope=this_and_following",
        )
    missing = [
        field_name
        for field_name in (
            "event_type",
            "series_start_at",
            "duration_minutes",
            "recurrence",
            "timezone",
        )
        if getattr(payload, field_name) is None
    ]
    if missing:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "missing_recurrence_fields",
            f"this_and_following requires: {', '.join(missing)}",
        )

    assert payload.recurrence is not None
    recurrence_rule, series_end_at = _build_recurrence(
        payload.recurrence, series_end_at=payload.series_end_at
    )

    try:
        successor, _rebound = series_service.create_successor_version(
            db,
            source_series_id=series_id,
            boundary_occurrence_id=payload.occurrence_id,
            name=payload.name or series.name,
            description=(
                payload.description if payload.description is not None else series.description
            ),
            event_type=payload.event_type,  # type: ignore[arg-type]
            series_start_at=payload.series_start_at,
            series_end_at=series_end_at,
            occurrence_limit=payload.occurrence_limit,
            duration_minutes=payload.duration_minutes,  # type: ignore[arg-type]
            recurrence_rule=recurrence_rule,
            timezone=payload.timezone,  # type: ignore[arg-type]
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except (
        StaleSeriesVersionError,
        BoundaryOccurrenceNotInCurrentVersionError,
        EventSeriesDomainError,
        EventDomainError,
    ) as exc:
        _raise_for_domain_error(exc)
    return _series_out(successor)


def _transition_series(
    series_id: uuid.UUID,
    new_status: str,
    request: Request,
    principal: CurrentPrincipal,
    db: Session,
) -> EventSeriesOut:
    permission_code = _SERIES_LIFECYCLE_PERMISSIONS[new_status]
    series = _get_authorized_series_or_404(
        db,
        series_id=series_id,
        user_id=principal.user_id,
        permission_code=permission_code,
        lock=True,
    )
    try:
        series = series_service.transition_series_status(
            db,
            series=series,
            new_status=new_status,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except EventSeriesDomainError as exc:
        _raise_for_domain_error(exc)
    return _series_out(series)


@series_router.post("/{series_id}/pause", response_model=EventSeriesOut)
def pause_series(
    series_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventSeriesOut:
    return _transition_series(series_id, "paused", request, principal, db)


@series_router.post("/{series_id}/resume", response_model=EventSeriesOut)
def resume_series(
    series_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventSeriesOut:
    return _transition_series(series_id, "active", request, principal, db)


@series_router.post("/{series_id}/cancel", response_model=EventSeriesOut)
def cancel_series(
    series_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventSeriesOut:
    return _transition_series(series_id, "cancelled", request, principal, db)


@series_router.post("/{series_id}/archive", response_model=EventSeriesOut)
def archive_series(
    series_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventSeriesOut:
    return _transition_series(series_id, "archived", request, principal, db)


@series_router.post("/{series_id}/exceptions", response_model=EventOccurrenceOut)
def create_series_exception(
    series_id: uuid.UUID,
    payload: EventSeriesExceptionRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventOccurrenceOut:
    # event.cancel for cancellation, event.update for reschedule/override —
    # mirrors app.api.v1.events._STATUS_TRANSITION_PERMISSIONS's split.
    permission_code = "event.cancel" if payload.exception_type == "cancelled" else "event.update"
    occurrence = _get_authorized_occurrence_or_404(
        db,
        occurrence_id=payload.occurrence_id,
        user_id=principal.user_id,
        permission_code=permission_code,
        lock=True,
    )
    if occurrence.series_id != series_id:
        raise APIError(
            status.HTTP_404_NOT_FOUND, _OCCURRENCE_NOT_FOUND_CODE, _OCCURRENCE_NOT_FOUND_MESSAGE
        )

    try:
        exception = series_service.set_occurrence_exception(
            db,
            occurrence=occurrence,
            exception_type=payload.exception_type,
            effective_start_at=payload.effective_start_at,
            effective_end_at=payload.effective_end_at,
            overrides=payload.overrides,
            cancellation_reason=payload.cancellation_reason,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except (EventSeriesDomainError, EventDomainError) as exc:
        _raise_for_domain_error(exc)
    return _occurrence_out(occurrence, exception)


@series_router.get(
    "/{series_id}/occurrences", response_model=CollectionResponse[EventOccurrenceOut]
)
def list_series_occurrences(
    series_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    from_: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = Query(default=None, alias="to"),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[EventOccurrenceOut]:
    series = _get_authorized_series_or_404(
        db, series_id=series_id, user_id=principal.user_id, permission_code="event.read"
    )

    # ADR-0015 §4: extend the materialized horizon on demand when the
    # caller asks for occurrences beyond what is currently materialized.
    if to is not None:
        ensure_materialized(db, series=series, until=to)
    else:
        ensure_materialized(db, series=series)

    stmt = select(EventOccurrence).where(EventOccurrence.series_id == series_id)
    if from_ is not None:
        stmt = stmt.where(EventOccurrence.starts_at >= from_)
    if to is not None:
        stmt = stmt.where(EventOccurrence.starts_at <= to)
    total = len(db.execute(stmt).scalars().all())
    stmt = (
        stmt.order_by(EventOccurrence.starts_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.execute(stmt).scalars().all()

    items = []
    for occurrence in rows:
        exception = _current_exception_for(db, occurrence.id)
        items.append(_occurrence_out(occurrence, exception))

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=items, pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages)
    )


# --- Occurrences --------------------------------------------------------


@occurrences_router.get("/{occurrence_id}", response_model=EventOccurrenceOut)
def get_occurrence(
    occurrence_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> EventOccurrenceOut:
    occurrence = _get_authorized_occurrence_or_404(
        db, occurrence_id=occurrence_id, user_id=principal.user_id, permission_code="event.read"
    )
    exception = _current_exception_for(db, occurrence.id)
    return _occurrence_out(occurrence, exception)


@occurrences_router.patch("/{occurrence_id}", response_model=EventOccurrenceOut)
def update_occurrence(
    occurrence_id: uuid.UUID,
    payload: EventOccurrenceUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventOccurrenceOut:
    occurrence = _get_authorized_occurrence_or_404(
        db,
        occurrence_id=occurrence_id,
        user_id=principal.user_id,
        permission_code="event.update",
        lock=True,
    )
    try:
        occurrence = series_service.transition_occurrence_status(
            db,
            occurrence=occurrence,
            new_status=payload.status,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except EventSeriesDomainError as exc:
        _raise_for_domain_error(exc)
    exception = _current_exception_for(db, occurrence.id)
    return _occurrence_out(occurrence, exception)


__all__ = ["series_router", "occurrences_router"]
