"""Event API — /api/v1/events (Issue #40).

Canonical sources: docs/05-api/events-api.md §4-9/§30/§31,
docs/02-requirements/roles-and-permissions.md §10-12,
docs/03-architecture/adr/ADR-0013 (scopes), ADR-0014 (response envelope),
ADR-0018 (lifecycle), ADR-0019 (field model), ADR-0022 (cross-Club
ownership), ADR-0023 (relationship persistence semantics).

Endpoints intentionally NOT implemented here (explicit Issue #40
non-goals): EventSeries/recurrence, EventOccurrence, calendar/iCalendar,
EventParticipation API, self-registration, participant status
transitions, attendance API, conflict detection, notifications.

Existence-hiding: for the single-Event endpoints (detail/update/status/
archive), an Event that does not exist and an Event that exists but the
caller is not authorized to act on (whether for lack of the permission
itself or a scope/object-relationship mismatch) receive an identical 404
response — mirroring the same "answered identically" precedent already
established by app.api.v1.auth.revoke_session for another owned
resource. The list endpoint instead silently excludes unauthorized rows
from `items`/`pagination.total` (never a 403) via
app.events.queries.list_events_page, since a collection is not a
specific-object operation. Only `event.create` (no object yet exists to
hide) uses the generic 403 AuthorizationDenied contract.
"""

import logging
import uuid
from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.events_schemas import (
    EventCreateRequest,
    EventOut,
    EventStatusTransitionRequest,
    EventUpdateRequest,
)
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.events import Event
from app.db.identity import Club
from app.db.session import get_db
from app.events import crud as events_crud
from app.events.authorization import build_event_resource_context
from app.events.lifecycle import (
    CancellationReasonRequiredError,
    EventDomainError,
    InvalidEventStatusTransitionError,
)
from app.events.queries import DEFAULT_SORT, InvalidSortError, list_events_page

logger = logging.getLogger("tourcrm.api")

router = APIRouter(prefix="/events", tags=["events"])

_NOT_FOUND_DETAIL = "Event not found"

# roles-and-permissions.md §12 / events-api.md §30: the general status
# endpoint's required permission depends only on the *target* status.
# `archived` is deliberately excluded — reachable only via the dedicated
# archive endpoint (`event.manage`).
_STATUS_TRANSITION_PERMISSIONS = {
    "published": "event.update",
    "in_progress": "event.update",
    "completed": "event.update",
    "cancelled": "event.cancel",
}


def _event_out(event: Event) -> EventOut:
    return EventOut(
        id=event.id,
        club_id=event.club_id,
        event_type=event.event_type,
        title=event.title,
        description=event.description,
        start_at=event.start_at,
        end_at=event.end_at,
        timezone=event.timezone,
        location_type=event.location_type,
        location_name=event.location_name,
        location_address=event.location_address,
        location_latitude=(
            float(event.location_latitude) if event.location_latitude is not None else None
        ),
        location_longitude=(
            float(event.location_longitude) if event.location_longitude is not None else None
        ),
        status=event.status,
        cancellation_reason=event.cancellation_reason,
        created_by=event.created_by,
        updated_by=event.updated_by,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def _get_authorized_event_or_404(
    db: Session,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> Event:
    stmt = select(Event).where(Event.id == event_id)
    if lock:
        stmt = stmt.with_for_update()
    event = db.execute(stmt).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    context = build_event_resource_context(db, event=event, user_id=user_id)
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        # Deliberately the same detail/status as "does not exist" above —
        # see module docstring.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return event


def _raise_for_domain_error(exc: EventDomainError) -> NoReturn:
    if isinstance(exc, InvalidEventStatusTransitionError):
        raise APIError(status.HTTP_409_CONFLICT, "invalid_status_transition", str(exc)) from exc
    if isinstance(exc, CancellationReasonRequiredError):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "cancellation_reason_required", str(exc)
        ) from exc
    # InvalidEventTypeError, InvalidEventStatusError, InvalidTimeRangeError,
    # InvalidTimezoneError, InconsistentCoordinatesError all land here.
    raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_event_data", str(exc)) from exc


@router.get("", response_model=CollectionResponse[EventOut])
def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=DEFAULT_SORT),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
    event_type: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    group_id: uuid.UUID | None = Query(default=None),
    participant_id: uuid.UUID | None = Query(default=None),
    instructor_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=255),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[EventOut]:
    try:
        rows, total = list_events_page(
            db,
            user_id=principal.user_id,
            permission_code="event.read",
            page=page,
            page_size=page_size,
            sort=sort,
            starts_from=from_,
            starts_to=to,
            event_type=event_type,
            status=status_,
            group_id=group_id,
            participant_id=participant_id,
            instructor_id=instructor_id,
            search=search,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_event_out(event) for event in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get("/{event_id}", response_model=EventOut)
def get_event(
    event_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> EventOut:
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.read"
    )
    return _event_out(event)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=EventOut)
def create_event(
    payload: EventCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventOut:
    if db.get(Club, payload.club_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_club_id", "club_id does not exist"
        )

    authorizer = Authorizer(session=db, user_id=principal.user_id, permission_code="event.create")
    # No Event exists yet: is_self/is_child/is_own_group/is_own_event are
    # structurally unresolvable and stay at their fail-closed `None`
    # default, so only a `scope_type='all'` assignment (matching the
    # target club) can ever satisfy event.create — a consequence of the
    # existing tri-state ResourceContext design, not a new rule.
    authorizer.check(ResourceContext(club_id=payload.club_id))

    try:
        event = events_crud.create_event(
            db,
            club_id=payload.club_id,
            event_type=payload.event_type,
            title=payload.title,
            description=payload.description,
            start_at=payload.start_at,
            end_at=payload.end_at,
            timezone=payload.timezone,
            location_type=payload.location_type,
            location_name=payload.location_name,
            location_address=payload.location_address,
            location_latitude=payload.location_latitude,
            location_longitude=payload.location_longitude,
            created_by=principal.user_id,
        )
    except EventDomainError as exc:
        _raise_for_domain_error(exc)
    logger.info("events.create.success event_id=%s user_id=%s", event.id, principal.user_id)
    return _event_out(event)


_NON_NULLABLE_UPDATE_FIELDS = ("event_type", "title", "start_at", "end_at", "timezone")


@router.patch("/{event_id}", response_model=EventOut)
def update_event(
    event_id: uuid.UUID,
    payload: EventUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventOut:
    event = _get_authorized_event_or_404(
        db,
        event_id=event_id,
        user_id=principal.user_id,
        permission_code="event.update",
        lock=True,
    )

    fields = payload.model_dump(exclude_unset=True)
    for field_name in _NON_NULLABLE_UPDATE_FIELDS:
        if field_name in fields and fields[field_name] is None:
            raise APIError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_event_data",
                f"{field_name} cannot be null",
            )

    try:
        event = events_crud.update_event(
            db, event=event, updated_by=principal.user_id, **fields
        )
    except EventDomainError as exc:
        _raise_for_domain_error(exc)
    logger.info("events.update.success event_id=%s user_id=%s", event.id, principal.user_id)
    return _event_out(event)


@router.post("/{event_id}/status", response_model=EventOut)
def transition_event_status(
    event_id: uuid.UUID,
    payload: EventStatusTransitionRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventOut:
    if payload.status == events_crud.ARCHIVED_STATUS:
        raise APIError(
            status.HTTP_400_BAD_REQUEST,
            "use_archive_endpoint",
            "Archiving must use POST /events/{event_id}/archive",
        )
    permission_code = _STATUS_TRANSITION_PERMISSIONS.get(payload.status, "event.update")

    event = _get_authorized_event_or_404(
        db,
        event_id=event_id,
        user_id=principal.user_id,
        permission_code=permission_code,
        lock=True,
    )

    try:
        event = events_crud.transition_event_status(
            db,
            event=event,
            new_status=payload.status,
            cancellation_reason=payload.cancellation_reason,
            updated_by=principal.user_id,
        )
    except EventDomainError as exc:
        _raise_for_domain_error(exc)
    logger.info(
        "events.status.success event_id=%s status=%s user_id=%s",
        event.id,
        event.status,
        principal.user_id,
    )
    return _event_out(event)


@router.post("/{event_id}/archive", response_model=EventOut)
def archive_event(
    event_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventOut:
    event = _get_authorized_event_or_404(
        db,
        event_id=event_id,
        user_id=principal.user_id,
        permission_code="event.manage",
        lock=True,
    )

    try:
        event = events_crud.archive_event(db, event=event, updated_by=principal.user_id)
    except EventDomainError as exc:
        _raise_for_domain_error(exc)
    logger.info("events.archive.success event_id=%s user_id=%s", event.id, principal.user_id)
    return _event_out(event)
