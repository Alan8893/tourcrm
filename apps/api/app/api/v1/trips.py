"""Trip API — /api/v1/trips (Issue #245).

Canonical sources: docs/05-api/trips-and-tourist-profile-api.md §3/§4,
docs/05-api/endpoint-inventory.md §11, docs/04-modules/trips-and-
tourist-profile.md, and the Issue #245 PO/CTO decisions. Only the
operations defined so far: attach a Trip to an existing Event (optionally
with a TourismType), read Trips, edit the Trip's TourismType (Issue
#264), read recorded TripParticipants, and record the confirmed
`actual_participation` fact for an existing EventParticipation. No Trip
status endpoint (a Trip has no lifecycle of its own — use the Event
lifecycle endpoints), no participant add/remove (registration stays
EventParticipation's, ADR-0037), no correction endpoint.

A Trip is identified by its Event's id (`trips.event_id`).

Authorization reuses the Event infrastructure with the existing
`trip.read`/`trip.manage` permissions: `build_event_resource_context`
resolves `own_events`/`own_groups`/`self`/`children` for the Trip's
Event and `Authorizer` checks the permission's grants; the list uses
`event_visibility_filter` (app.trips.queries). Existence-hiding as in
the Event API: a missing Event, a missing Trip and an Event the caller
may not act on under the endpoint's permission all receive the same
404.
"""

import logging
import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.trips_schemas import (
    TripCreateRequest,
    TripOut,
    TripParticipantOut,
    TripParticipantRecordRequest,
    TripUpdateRequest,
)
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.events import Event
from app.db.session import get_db
from app.db.trips import Trip, TripParticipant
from app.events.authorization import build_event_resource_context
from app.trips import service as trips_service
from app.trips import tourism_types as tourism_type_service
from app.trips.queries import TRIP_READ_PERMISSION, list_trip_participants, list_trips_page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])

_TRIP_MANAGE_PERMISSION = "trip.manage"
_NOT_FOUND_DETAIL = "Trip not found"


def _get_authorized_event_or_404(
    db: Session,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> tuple[Event, ResourceContext]:
    stmt = select(Event).where(Event.id == event_id)
    if lock:
        stmt = stmt.with_for_update()
    event = db.execute(stmt).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    context = build_event_resource_context(db, event=event, user_id=user_id)
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        # Deliberately identical to "does not exist" — see module docstring.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return event, context


def _get_authorized_trip_event_or_404(
    db: Session,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> tuple[Event, Trip, ResourceContext]:
    event, context = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=user_id, permission_code=permission_code, lock=lock
    )
    trip = trips_service.get_trip(db, event.id)
    if trip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return event, trip, context


def _trip_out(trip: Trip) -> TripOut:
    return TripOut(
        event_id=trip.event_id,
        tourism_type_id=trip.tourism_type_id,
        created_at=trip.created_at,
        updated_at=trip.updated_at,
    )


def _raise_for_tourism_type_error(exc: tourism_type_service.TourismTypeError) -> NoReturn:
    if isinstance(exc, tourism_type_service.TourismTypeInactiveError):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "tourism_type_inactive", str(exc)
        ) from exc
    raise APIError(
        status.HTTP_422_UNPROCESSABLE_ENTITY, "tourism_type_not_found", str(exc)
    ) from exc


def _trip_participant_out(row: TripParticipant, person_id: uuid.UUID) -> TripParticipantOut:
    return TripParticipantOut(
        event_participation_id=row.event_participation_id,
        event_id=row.event_id,
        person_id=person_id,
        actual_participation=row.actual_participation,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _raise_for_trip_error(exc: trips_service.TripError) -> NoReturn:
    if isinstance(exc, trips_service.EventNotTripError):
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "event_not_trip", str(exc)) from exc
    if isinstance(exc, trips_service.TripEventLifecycleClosedError):
        raise APIError(status.HTTP_409_CONFLICT, "trip_event_lifecycle_closed", str(exc)) from exc
    if isinstance(exc, trips_service.TripAlreadyExistsError):
        raise APIError(status.HTTP_409_CONFLICT, "trip_already_exists", str(exc)) from exc
    if isinstance(exc, trips_service.TripEditingClosedError):
        raise APIError(status.HTTP_409_CONFLICT, "trip_editing_closed", str(exc)) from exc
    if isinstance(exc, trips_service.TripParticipationMissingError):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "participation_missing", str(exc)
        ) from exc
    if isinstance(exc, trips_service.ActualParticipationLifecycleClosedError):
        raise APIError(
            status.HTTP_409_CONFLICT, "actual_participation_lifecycle_closed", str(exc)
        ) from exc
    # TripParticipantHistoricallyClosedError
    raise APIError(
        status.HTTP_409_CONFLICT, "trip_participant_historically_closed", str(exc)
    ) from exc


@router.get("", response_model=CollectionResponse[TripOut])
def list_trips(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_: str | None = Query(default=None, alias="status"),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[TripOut]:
    rows, total = list_trips_page(
        db, user_id=principal.user_id, page=page, page_size=page_size, status=status_
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_trip_out(trip) for trip in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TripOut)
def create_trip(
    payload: TripCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TripOut:
    event, _context = _get_authorized_event_or_404(
        db,
        event_id=payload.event_id,
        user_id=principal.user_id,
        permission_code=_TRIP_MANAGE_PERMISSION,
        lock=True,
    )
    try:
        trip = trips_service.create_trip(
            db,
            event=event,
            actor_user_id=principal.user_id,
            tourism_type_id=payload.tourism_type_id,
            request_id=get_request_id(request),
        )
    except trips_service.TripError as exc:
        _raise_for_trip_error(exc)
    except tourism_type_service.TourismTypeError as exc:
        _raise_for_tourism_type_error(exc)
    logger.info("trips.create.success event_id=%s user_id=%s", event.id, principal.user_id)
    return _trip_out(trip)


@router.get("/{event_id}", response_model=TripOut)
def get_trip(
    event_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> TripOut:
    _event, trip, _context = _get_authorized_trip_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code=TRIP_READ_PERMISSION
    )
    return _trip_out(trip)


@router.patch("/{event_id}", response_model=TripOut)
def update_trip(
    event_id: uuid.UUID,
    payload: TripUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TripOut:
    event, trip, _context = _get_authorized_trip_event_or_404(
        db,
        event_id=event_id,
        user_id=principal.user_id,
        permission_code=_TRIP_MANAGE_PERMISSION,
        lock=True,
    )
    if "tourism_type_id" in payload.model_fields_set:
        try:
            trip = trips_service.set_trip_tourism_type(
                db, event=event, trip=trip, tourism_type_id=payload.tourism_type_id
            )
        except trips_service.TripError as exc:
            _raise_for_trip_error(exc)
        except tourism_type_service.TourismTypeError as exc:
            _raise_for_tourism_type_error(exc)
    logger.info("trips.update.success event_id=%s user_id=%s", event_id, principal.user_id)
    return _trip_out(trip)


@router.get("/{event_id}/participants", response_model=CollectionResponse[TripParticipantOut])
def list_trip_participants_endpoint(
    event_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[TripParticipantOut]:
    event, _trip, context = _get_authorized_trip_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code=TRIP_READ_PERMISSION
    )
    rows, total = list_trip_participants(
        db,
        event=event,
        resource_context=context,
        user_id=principal.user_id,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_trip_participant_out(row, person_id) for row, person_id in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.put("/{event_id}/participants/{person_id}", response_model=TripParticipantOut)
def record_trip_participant(
    event_id: uuid.UUID,
    person_id: uuid.UUID,
    payload: TripParticipantRecordRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TripParticipantOut:
    event, _trip, _context = _get_authorized_trip_event_or_404(
        db,
        event_id=event_id,
        user_id=principal.user_id,
        permission_code=_TRIP_MANAGE_PERMISSION,
        lock=True,
    )
    try:
        row = trips_service.record_actual_participation(
            db,
            event=event,
            person_id=person_id,
            actual_participation=payload.actual_participation,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except trips_service.TripError as exc:
        _raise_for_trip_error(exc)
    logger.info(
        "trips.participant.recorded event_id=%s person_id=%s user_id=%s",
        event_id,
        person_id,
        principal.user_id,
    )
    return _trip_participant_out(row, person_id)
