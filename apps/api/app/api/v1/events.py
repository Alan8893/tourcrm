"""Event API — /api/v1/events (Issue #40).

Canonical sources: docs/05-api/events-api.md §4-9/§30/§31,
docs/02-requirements/roles-and-permissions.md §10-12,
docs/03-architecture/adr/ADR-0013 (scopes), ADR-0014 (response envelope),
ADR-0018 (lifecycle), ADR-0019 (field model), ADR-0022 (cross-Club
ownership), ADR-0023 (relationship persistence semantics).

Endpoints intentionally NOT implemented here (explicit Issue #40
non-goals): EventSeries/recurrence, EventOccurrence, iCalendar,
admin-driven participant management/status transitions, notifications.
Calendar projection (`/calendar`, Issue #82 / TH-0080), conflict
detection (`/conflicts`, Issue #91 / TH-0085), Attendance
(`/attendance...`, Issue #94 / TH-0087, ADR-0032) and participant
self-registration (`/participation`, TH-0108.2, ADR-0037 — supersedes
ADR-0020 §4's deferral for this one MVP policy) were added to this
router by their own later Issues.

Attendance's `{event_id}` path parameter resolves *only* against
`EventOccurrence.id` (ADR-0032 §1's canonical identity is
`(occurrence_id, person_id)`; see app.events.attendance module docstring
"Object resolution" for why ordinary, non-recurring `Event`s are not
supported — no canonical source defines a concrete-occurrence mapping
for them, and this implementation does not invent one).
`_get_authorized_attendance_target_or_404` below is the attendance-
specific counterpart of `_get_authorized_event_or_404`, applying the
same existence-hiding authorization gate to the resolved
`EventOccurrence`.

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
import re
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.documents_schemas import (
    DocumentPackageRequest,
    EventDocumentRequirementCheckListOut,
    EventDocumentRequirementCheckOut,
    EventDocumentRequirementCreateRequest,
    EventDocumentRequirementOut,
    EventDocumentRequirementUpdateRequest,
)
from app.api.v1.events_schemas import (
    AttendanceBulkMarkOut,
    AttendanceBulkMarkRequest,
    AttendanceCorrectionOut,
    AttendanceCorrectionRequest,
    AttendanceEntryOut,
    AttendanceListOut,
    AttendanceMarkOut,
    AttendanceMarkRequest,
    AttendancePersonOut,
    AttendanceSummaryOut,
    CalendarItemOut,
    ConflictObjectRefOut,
    ConflictOut,
    EventCreateRequest,
    EventOut,
    EventParticipationOut,
    EventStatusTransitionRequest,
    EventUpdateRequest,
)
from app.api.v1.schedule_params import parse_schedule_range
from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, Authorizer
from app.db.attendance import Attendance
from app.db.documents import EventDocumentRequirement
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation
from app.db.identity import Club, Person
from app.db.session import get_db
from app.documents.event_requirements import check_person_document_requirements
from app.documents.package import DocumentPackageIncompleteError, generate_event_document_package
from app.documents.requirement_management import (
    DuplicateEventDocumentRequirementError,
    create_event_document_requirement,
    delete_event_document_requirement,
    get_event_document_requirement,
    list_event_document_requirements,
    update_event_document_requirement,
)
from app.events import attendance
from app.events import crud as events_crud
from app.events import participation as event_participation
from app.events.authorization import build_event_resource_context
from app.events.calendar import (
    CALENDAR_STATUS_FILTER_VALUES,
    CalendarItem,
    list_calendar_items_page,
)
from app.events.conflicts import ConflictItem, list_conflicts_page
from app.events.lifecycle import (
    CancellationReasonRequiredError,
    EventDomainError,
    InvalidEventStatusTransitionError,
    InvalidTimeRangeError,
    validate_event_type,
    validate_time_range,
)
from app.events.queries import (
    DEFAULT_SORT,
    InvalidSortError,
    get_event_targeting,
    list_events_page,
)
from app.events.series_authorization import build_occurrence_resource_context
from app.events.service import (
    EventGroupNotFoundError,
    EventGroupTargetError,
    EventStaffAssignmentError,
    EventStaffAssignmentPrimaryConflictError,
    EventStaffUserNotFoundError,
)
from app.people.authorization import is_person_visible
from app.storage.file_storage import FileStorage
from app.storage.local import get_file_storage

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


def _event_out(db: Session, event: Event, *, viewer_user_id: uuid.UUID) -> EventOut:
    group_ids, instructor_ids = get_event_targeting(db, event_id=event.id)
    viewer_person_id = event_participation.person_id_for_user(db, viewer_user_id)
    my_registration_status = event_participation.get_viewer_registration_status(
        db, event_id=event.id, person_id=viewer_person_id
    )
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
        group_ids=group_ids,
        instructor_ids=instructor_ids,
        my_registration_status=my_registration_status,
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


def _raise_for_group_target_error(exc: EventGroupTargetError) -> NoReturn:
    """TH-0108 / ADR-0037 §1: dispatches app.events.service's
    EventGroupTarget validation failures (raised by
    create_event_with_targeting/update_event_with_targeting) onto the
    canonical 422 error envelope — never a bare 500."""
    if isinstance(exc, EventGroupNotFoundError):
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_group_id", str(exc)) from exc
    # EventGroupTargetClubMismatchError
    raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "group_club_mismatch", str(exc)) from exc


def _raise_for_staff_assignment_error(exc: EventStaffAssignmentError) -> NoReturn:
    """TH-0108 / ADR-0037 §2: dispatches app.events.service's
    EventStaffAssignment validation failures the same way — see
    `_raise_for_group_target_error`."""
    if isinstance(exc, EventStaffUserNotFoundError):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_instructor_id", str(exc)
        ) from exc
    if isinstance(exc, EventStaffAssignmentPrimaryConflictError):
        # Unreachable in practice today (this API never sets
        # is_primary=True), kept for completeness against the full
        # EventStaffAssignmentError hierarchy.
        raise APIError(status.HTTP_409_CONFLICT, "primary_conflict", str(exc)) from exc
    # EventStaffClubMembershipMissingError
    raise APIError(
        status.HTTP_422_UNPROCESSABLE_ENTITY, "instructor_club_membership_missing", str(exc)
    ) from exc


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
        items=[_event_out(db, event, viewer_user_id=principal.user_id) for event in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


def _calendar_item_out(item: CalendarItem) -> CalendarItemOut:
    return CalendarItemOut(
        id=item.id,
        kind=item.kind,  # type: ignore[arg-type]
        club_id=item.club_id,
        event_type=item.event_type,
        title=item.title,
        description=item.description,
        start_at=item.start_at,
        end_at=item.end_at,
        timezone=item.timezone,
        status=item.status,
        cancellation_reason=item.cancellation_reason,
        series_id=item.series_id,
        series_version=item.series_version,
    )


# events-api.md §16: a literal path, so it MUST be registered before
# "/{event_id}" below — Starlette matches routes in registration order,
# and "/{event_id}" would otherwise swallow "/calendar" as a (then
# UUID-unparseable) event_id, returning a generic 422 instead of ever
# reaching this handler.
@router.get("/calendar", response_model=CollectionResponse[CalendarItemOut])
def get_calendar(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user_id: uuid.UUID | None = Query(default=None),
    group_id: uuid.UUID | None = Query(default=None),
    event_type: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[CalendarItemOut]:
    if from_ is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "missing_from",
            "'from' query parameter is required",
        )
    if to is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "missing_to", "'to' query parameter is required"
        )
    if from_.tzinfo is None or to.tzinfo is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "naive_timestamp",
            "'from' and 'to' must be timezone-aware RFC 3339 timestamps",
        )
    try:
        validate_time_range(from_, to)
    except InvalidTimeRangeError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_range",
            "'from' must be strictly before 'to'",
        ) from exc
    if event_type is not None:
        try:
            validate_event_type(event_type)
        except EventDomainError as exc:
            raise APIError(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_event_type", str(exc)
            ) from exc
    if status_ is not None and status_ not in CALENDAR_STATUS_FILTER_VALUES:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_status",
            f"{status_!r} is not a calendar-visible status",
        )

    items, total = list_calendar_items_page(
        db,
        user_id=principal.user_id,
        permission_code="event.read",
        from_at=from_.astimezone(dt_timezone.utc),
        to_at=to.astimezone(dt_timezone.utc),
        page=page,
        page_size=page_size,
        user_id_filter=user_id,
        group_id_filter=group_id,
        event_type=event_type,
        status=status_,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_calendar_item_out(item) for item in items],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


def _conflict_out(item: ConflictItem) -> ConflictOut:
    return ConflictOut(
        id=item.id,
        first_object=ConflictObjectRefOut(
            object_type=item.first_object_type,  # type: ignore[arg-type]
            object_id=item.first_object_id,
            series_id=item.first_series_id,
            series_version=item.first_series_version,
        ),
        second_object=ConflictObjectRefOut(
            object_type=item.second_object_type,  # type: ignore[arg-type]
            object_id=item.second_object_id,
            series_id=item.second_series_id,
            series_version=item.second_series_version,
        ),
        domain=item.domain,  # type: ignore[arg-type]
        overlap_start_at=item.overlap_start_at,
        overlap_end_at=item.overlap_end_at,
    )


# events-api.md §28: also a literal path, so it MUST be registered before
# "/{event_id}" below — same routing-order reason as "/calendar" above.
@router.get("/conflicts", response_model=CollectionResponse[ConflictOut])
def get_conflicts(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user_id: uuid.UUID | None = Query(default=None),
    group_id: uuid.UUID | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[ConflictOut]:
    from_at, to_at = parse_schedule_range(from_, to)

    items, total = list_conflicts_page(
        db,
        user_id=principal.user_id,
        permission_code="event.read",
        from_at=from_at,
        to_at=to_at,
        page=page,
        page_size=page_size,
        user_id_filter=user_id,
        group_id_filter=group_id,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_conflict_out(item) for item in items],
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
    return _event_out(db, event, viewer_user_id=principal.user_id)


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
        event = events_crud.create_event_with_targeting(
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
            group_ids=payload.group_ids,
            instructor_user_ids=payload.instructor_ids,
        )
    except EventDomainError as exc:
        _raise_for_domain_error(exc)
    except EventGroupTargetError as exc:
        _raise_for_group_target_error(exc)
    except EventStaffAssignmentError as exc:
        _raise_for_staff_assignment_error(exc)
    logger.info("events.create.success event_id=%s user_id=%s", event.id, principal.user_id)
    return _event_out(db, event, viewer_user_id=principal.user_id)


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
    # group_ids/instructor_ids are targeting relationships, not
    # UPDATABLE_EVENT_FIELDS — extracted here so **fields below only ever
    # carries plain Event columns, exactly as update_event_with_targeting
    # (and the plain update_event it wraps) expects.
    group_ids = fields.pop("group_ids", None)
    instructor_ids = fields.pop("instructor_ids", None)
    for field_name in _NON_NULLABLE_UPDATE_FIELDS:
        if field_name in fields and fields[field_name] is None:
            raise APIError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_event_data",
                f"{field_name} cannot be null",
            )

    try:
        event = events_crud.update_event_with_targeting(
            db,
            event=event,
            updated_by=principal.user_id,
            group_ids=group_ids,
            instructor_user_ids=instructor_ids,
            **fields,
        )
    except EventDomainError as exc:
        _raise_for_domain_error(exc)
    except EventGroupTargetError as exc:
        _raise_for_group_target_error(exc)
    except EventStaffAssignmentError as exc:
        _raise_for_staff_assignment_error(exc)
    logger.info("events.update.success event_id=%s user_id=%s", event.id, principal.user_id)
    return _event_out(db, event, viewer_user_id=principal.user_id)


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
    return _event_out(db, event, viewer_user_id=principal.user_id)


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
    return _event_out(db, event, viewer_user_id=principal.user_id)


# --- Self-registration (TH-0108.2, ADR-0037) --------------------------------
#
# Deliberately does NOT use _get_authorized_event_or_404/Authorizer: per
# ADR-0037 §12, self-registration is a self-service operation gated by the
# participant's own identity, active ClubMembership, targeted GroupMembership
# and Event lifecycle — never by `event.read`/any generic Event permission
# or scope. See app.events.participation's module docstring for the full
# eligibility algorithm and rationale.


def _participation_out(row: EventParticipation) -> EventParticipationOut:
    return EventParticipationOut(
        id=row.id,
        event_id=row.event_id,
        person_id=row.person_id,
        registration_status=row.registration_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/{event_id}/participation", response_model=EventParticipationOut)
def register_for_event(
    event_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventParticipationOut:
    # ADR-0037 §13: the client MUST NOT submit a person_id — there is no
    # request body at all; the Person is resolved server-side from the
    # authenticated principal by app.events.participation.register_for_event.
    try:
        participation = event_participation.register_for_event(
            db,
            event_id=event_id,
            user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except event_participation.EventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL
        ) from exc
    except event_participation.EventNotPublishedError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "event_not_published", str(exc)) from exc
    except event_participation.NotEligibleForEventError as exc:
        raise APIError(status.HTTP_403_FORBIDDEN, "not_eligible_for_event", str(exc)) from exc
    logger.info(
        "events.participation.registered event_id=%s user_id=%s",
        event_id,
        principal.user_id,
    )
    return _participation_out(participation)


@router.delete("/{event_id}/participation", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_from_event(
    event_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    # Idempotent: no participation row at all, and a row already
    # `cancelled`, are both a silent no-op success — never a 404, matching
    # ADR-0037 §6's "the operation is idempotent" requirement (test M/L).
    event_participation.withdraw_from_event(
        db,
        event_id=event_id,
        user_id=principal.user_id,
        request_id=get_request_id(request),
    )
    logger.info(
        "events.participation.cancelled event_id=%s user_id=%s",
        event_id,
        principal.user_id,
    )


# --- Attendance (Issue #94 / TH-0087, ADR-0032) -----------------------------


def _get_authorized_attendance_target_or_404(
    db: Session, *, event_id: uuid.UUID, user_id: uuid.UUID, permission_code: str
) -> tuple[EventOccurrence, ResourceContext]:
    target = attendance.resolve_attendance_target(db, event_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    # ADR-0033 §5: the occurrence backing an ordinary Event is authorized
    # through *that Event's* own relationships (EventStaffAssignment/
    # EventGroupTarget/EventParticipation via build_event_resource_context)
    # — never the occurrence-level relationship tables, which are never
    # populated for a non-recurring occurrence (those are exclusively
    # materialized from a governing EventSeries, ADR-0029/ADR-0030). A
    # genuinely recurring occurrence keeps using
    # build_occurrence_resource_context exactly as before.
    if target.event_id is not None:
        event = db.get(Event, target.event_id)
        assert event is not None  # ADR-0033 §1: the FK guarantees this.
        context = build_event_resource_context(db, event=event, user_id=user_id)
    else:
        context = build_occurrence_resource_context(db, occurrence=target, user_id=user_id)

    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        # Deliberately the same detail/status as "does not exist" above —
        # see module docstring / _get_authorized_event_or_404.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return target, context


def _attendance_mark_out(row: Attendance) -> AttendanceMarkOut:
    return AttendanceMarkOut(
        person_id=row.person_id,
        status=row.status,  # type: ignore[arg-type]
        absence_reason=row.absence_reason,
        comment=row.comment,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _raise_for_normal_lifecycle_error(exc: attendance.AttendanceError) -> NoReturn:
    if isinstance(exc, attendance.AttendanceNormalWindowClosedError):
        raise APIError(
            status.HTTP_409_CONFLICT, "attendance_normal_window_closed", str(exc)
        ) from exc
    raise APIError(status.HTTP_409_CONFLICT, "attendance_lifecycle_closed", str(exc)) from exc


@router.get("/{event_id}/attendance", response_model=AttendanceListOut)
def get_attendance(
    event_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> AttendanceListOut:
    target, context = _get_authorized_attendance_target_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="attendance.read"
    )
    entries, summary, total = attendance.list_attendance(
        db,
        occurrence=target,
        club_id=target.club_id,
        resource_context=context,
        user_id=principal.user_id,
        permission_code="attendance.read",
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return AttendanceListOut(
        items=[
            AttendanceEntryOut(
                person=AttendancePersonOut(
                    id=entry.person_id,
                    first_name=entry.first_name,
                    last_name=entry.last_name,
                    middle_name=entry.middle_name,
                ),
                status=entry.status,  # type: ignore[arg-type]
                absence_reason=entry.absence_reason,
                comment=entry.comment,
            )
            for entry in entries
        ],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
        summary=AttendanceSummaryOut(
            total=summary.total,
            marked=summary.marked,
            present=summary.present,
            absent=summary.absent,
            unmarked=summary.unmarked,
        ),
    )


@router.put("/{event_id}/attendance/{person_id}", response_model=AttendanceMarkOut)
def mark_attendance(
    event_id: uuid.UUID,
    person_id: uuid.UUID,
    payload: AttendanceMarkRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> AttendanceMarkOut:
    target, _context = _get_authorized_attendance_target_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="attendance.update"
    )
    try:
        attendance.check_lifecycle_for_normal_change(target.status)
    except attendance.AttendanceError as exc:
        _raise_for_normal_lifecycle_error(exc)

    if not attendance.has_participation(db, occurrence=target, person_id=person_id):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "participation_missing",
            "Person has no participation for this object",
        )

    try:
        row, _created = attendance.upsert_attendance(
            db,
            occurrence=target,
            club_id=target.club_id,
            person_id=person_id,
            status=payload.status,
            absence_reason=payload.absence_reason,
            comment=payload.comment,
            actor_user_id=principal.user_id,
        )
    except attendance.AttendanceInvalidDataError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_attendance_data", str(exc)
        ) from exc
    return _attendance_mark_out(row)


@router.put("/{event_id}/attendance", response_model=AttendanceBulkMarkOut)
def bulk_mark_attendance(
    event_id: uuid.UUID,
    payload: AttendanceBulkMarkRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> AttendanceBulkMarkOut:
    target, _context = _get_authorized_attendance_target_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="attendance.update"
    )
    try:
        attendance.check_lifecycle_for_normal_change(target.status)
    except attendance.AttendanceError as exc:
        _raise_for_normal_lifecycle_error(exc)

    items = [
        attendance.AttendanceBulkItemInput(
            person_id=item.person_id,
            status=item.status,
            absence_reason=item.absence_reason,
            comment=item.comment,
        )
        for item in payload.items
    ]
    try:
        results = attendance.bulk_upsert_attendance(
            db,
            occurrence=target,
            club_id=target.club_id,
            items=items,
            actor_user_id=principal.user_id,
        )
    except attendance.AttendanceDuplicatePersonError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "duplicate_person_id", str(exc)
        ) from exc
    except attendance.AttendanceParticipationMissingError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "participation_missing", str(exc)
        ) from exc
    except attendance.AttendanceInvalidDataError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_attendance_data", str(exc)
        ) from exc
    return AttendanceBulkMarkOut(items=[_attendance_mark_out(row) for row, _created in results])


@router.post(
    "/{event_id}/attendance/{person_id}/corrections", response_model=AttendanceCorrectionOut
)
def correct_attendance(
    event_id: uuid.UUID,
    person_id: uuid.UUID,
    payload: AttendanceCorrectionRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> AttendanceCorrectionOut:
    target, _context = _get_authorized_attendance_target_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="attendance.update"
    )
    try:
        attendance.check_lifecycle_for_correction(target.status)
    except attendance.AttendanceUseNormalEndpointError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "use_normal_attendance_endpoint", str(exc)
        ) from exc
    except attendance.AttendanceLifecycleClosedError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "attendance_lifecycle_closed", str(exc)) from exc

    try:
        row, previous_status = attendance.correct_attendance(
            db,
            occurrence=target,
            club_id=target.club_id,
            person_id=person_id,
            status=payload.status,
            absence_reason=payload.absence_reason,
            comment=payload.comment,
            reason=payload.reason,
            actor_user_id=principal.user_id,
        )
    except attendance.AttendanceCorrectionReasonRequiredError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "correction_reason_required", str(exc)
        ) from exc
    except attendance.AttendanceNotFoundError as exc:
        raise APIError(status.HTTP_404_NOT_FOUND, "attendance_not_found", str(exc)) from exc
    except attendance.AttendanceParticipationMissingError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "participation_missing", str(exc)
        ) from exc
    except attendance.AttendanceInvalidDataError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_attendance_data", str(exc)
        ) from exc

    return AttendanceCorrectionOut(
        person_id=person_id,
        previous_status=previous_status,  # type: ignore[arg-type]
        new_status=row.status,  # type: ignore[arg-type]
        absence_reason=row.absence_reason,
        comment=row.comment,
        reason=payload.reason,
        actor_user_id=principal.user_id,
        corrected_at=row.updated_at,
    )


# --- Event document requirements check (TH-0117.4 / Issue #162, ADR-0040 §5) -
#
# Read-only: a computed result, never a persisted row (ADR-0040 §5). Two
# independent authorizations are required, per events-api.md §31's own
# rule ("An Event permission is necessary to see the Event itself, but
# not sufficient to see participant document content... derived from
# it"): `event.read` on the Event (via the same `_get_authorized_event_
# or_404` every other single-Event endpoint already uses) AND
# `document.read` on the Person (via `is_person_visible`, the same
# generic Person-scope engine app.api.v1.persons reuses for the
# Participant Document API — never `person.read`). Both checks
# existence-hide identically (404) on denial; there is no `document.
# export` requirement (this is a single-participant check, not an
# export/package).

_PERSON_NOT_FOUND_DETAIL = "Person not found"


def _get_authorized_person_for_document_check_or_404(
    db: Session, *, person_id: uuid.UUID, user_id: uuid.UUID
) -> Person:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_PERSON_NOT_FOUND_DETAIL)
    if not is_person_visible(
        db, person_id=person.id, user_id=user_id, permission_code="document.read"
    ):
        # Deliberately the same detail/status as "does not exist" above.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_PERSON_NOT_FOUND_DETAIL)
    return person


@router.get(
    "/{event_id}/document-requirements/{person_id}",
    response_model=EventDocumentRequirementCheckListOut,
)
def get_event_document_requirements_for_person(
    event_id: uuid.UUID,
    person_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> EventDocumentRequirementCheckListOut:
    """For every `EventDocumentRequirement` declared on `event_id`,
    compute `person_id`'s `valid`/`missing`/`expired` result (ADR-0040
    §5). Never returns binary content, `storage_key`, a filesystem path,
    `storage_backend`, or any other File/FileStorage internal — the
    response carries only `document_type`/`required`/`result`.
    """
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.read"
    )
    _get_authorized_person_for_document_check_or_404(
        db, person_id=person_id, user_id=principal.user_id
    )

    checks = check_person_document_requirements(db, event_id=event.id, person_id=person_id)
    return EventDocumentRequirementCheckListOut(
        event_id=event.id,
        person_id=person_id,
        requirements=[
            EventDocumentRequirementCheckOut(
                document_type=check.document_type, required=check.required, result=check.result
            )
            for check in checks
        ],
    )


# --- Event document requirements management (TH-0117.5 / Issue #164, -----
# events-api.md §31.1/§31.2) ------------------------------------------------
#
# Every operation below requires BOTH the Event object authorization
# (`event.read`/`event.manage`, via the same `_get_authorized_event_or_404`
# every other single-Event endpoint uses) AND the dedicated Document
# permission (`document.read`/`document.manage`) — neither is
# individually sufficient, and `person.read` grants nothing here.
# Unlike the per-participant check above, these four endpoints have no
# Person in their path to anchor a `is_person_visible` check against
# (they operate on the Event's own requirement declarations, not on any
# specific participant's documents) — `document.read`/`document.manage`
# is therefore checked as a plain, club-scoped permission grant via the
# generic `Authorizer`, the same shape already used elsewhere in this
# codebase for a flat, non-object-scoped permission (e.g. `account.
# manage`/`role.manage` in app.api.v1.persons). A denial on either half
# produces the same existence-hiding 404 as a nonexistent/inaccessible
# Event — never a 403 that would disclose which half failed.
#
# No `record_audit_event` call: events-api.md §31.4 is explicit that no
# dedicated audit action exists for this concept and none may be
# invented or repurposed from `document.*` here — mirroring the
# identical, already-shipped precedent in app.events.service (creating
# an EventStaffAssignment/EventGroupTarget, a sibling Event-relationship
# entity, has no audit call either).

_REQUIREMENT_NOT_FOUND_DETAIL = "Event document requirement not found"


def _require_document_permission_for_event_or_404(
    db: Session, *, event: Event, user_id: uuid.UUID, permission_code: str
) -> None:
    try:
        Authorizer(session=db, user_id=user_id, permission_code=permission_code).check(
            ResourceContext(club_id=event.club_id)
        )
    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL
        ) from exc


def _get_authorized_requirement_or_404(
    db: Session, *, event: Event, requirement_id: uuid.UUID
) -> EventDocumentRequirement:
    requirement = get_event_document_requirement(
        db, event_id=event.id, requirement_id=requirement_id
    )
    if requirement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_REQUIREMENT_NOT_FOUND_DETAIL
        )
    return requirement


def _requirement_out(requirement: EventDocumentRequirement) -> EventDocumentRequirementOut:
    return EventDocumentRequirementOut(
        id=requirement.id,
        event_id=requirement.event_id,
        document_type=requirement.document_type,
        required=requirement.required,
    )


@router.get(
    "/{event_id}/document-requirements",
    response_model=CollectionResponse[EventDocumentRequirementOut],
)
def list_event_document_requirements_endpoint(
    event_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[EventDocumentRequirementOut]:
    """`event.read` + `document.read` (events-api.md §31.2)."""
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.read"
    )
    _require_document_permission_for_event_or_404(
        db, event=event, user_id=principal.user_id, permission_code="document.read"
    )

    rows, total = list_event_document_requirements(
        db, event_id=event.id, page=page, page_size=page_size
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_requirement_out(row) for row in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post(
    "/{event_id}/document-requirements",
    status_code=status.HTTP_201_CREATED,
    response_model=EventDocumentRequirementOut,
)
def create_event_document_requirement_endpoint(
    event_id: uuid.UUID,
    payload: EventDocumentRequirementCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventDocumentRequirementOut:
    """`event.manage` + `document.manage` (events-api.md §31.2).
    `document_type` remains an open string (ADR-0040 §1) — no closed
    vocabulary/registry is enforced beyond length. `required` is
    persisted exactly as supplied, never inferred.
    """
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.manage"
    )
    _require_document_permission_for_event_or_404(
        db, event=event, user_id=principal.user_id, permission_code="document.manage"
    )

    try:
        requirement = create_event_document_requirement(
            db,
            event_id=event.id,
            document_type=payload.document_type,
            required=payload.required,
        )
    except DuplicateEventDocumentRequirementError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "duplicate_document_requirement", str(exc)
        ) from exc
    return _requirement_out(requirement)


@router.patch(
    "/{event_id}/document-requirements/{requirement_id}",
    response_model=EventDocumentRequirementOut,
)
def update_event_document_requirement_endpoint(
    event_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: EventDocumentRequirementUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EventDocumentRequirementOut:
    """`event.manage` + `document.manage` (events-api.md §31.2). Changes
    only `required` — `document_type` is immutable; changing it means
    DELETE the existing requirement and POST a new one.
    """
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.manage"
    )
    _require_document_permission_for_event_or_404(
        db, event=event, user_id=principal.user_id, permission_code="document.manage"
    )
    requirement = _get_authorized_requirement_or_404(
        db, event=event, requirement_id=requirement_id
    )

    requirement = update_event_document_requirement(
        db, requirement=requirement, required=payload.required
    )
    return _requirement_out(requirement)


@router.delete(
    "/{event_id}/document-requirements/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_event_document_requirement_endpoint(
    event_id: uuid.UUID,
    requirement_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    """`event.manage` + `document.manage` (events-api.md §31.2)."""
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.manage"
    )
    _require_document_permission_for_event_or_404(
        db, event=event, user_id=principal.user_id, permission_code="document.manage"
    )
    requirement = _get_authorized_requirement_or_404(
        db, event=event, requirement_id=requirement_id
    )

    delete_event_document_requirement(db, requirement=requirement)


# --- Event competition document package export (TH-0117.9 / Issue #172, --
# ADR-0040 §5/§6/§7, events-api.md §31.5) -----------------------------------
#
# The explicit Document export path: `event.read` (via the same
# `_get_authorized_event_or_404` every other single-Event endpoint uses)
# AND `document.export` (via the same flat, club-scoped
# `_require_document_permission_for_event_or_404` helper the requirement-
# management endpoints above already use) — neither alone is sufficient,
# and `document.read`/`person.read` grant nothing here. The participant
# set and requirement set are always resolved server-side by
# app.documents.package (EventParticipation + EventDocumentRequirement);
# no person_id/document_id/file_id is ever accepted from the client.

_UNSAFE_FILENAME_CHARS = re.compile(r'[\r\n\x00-\x1f\x7f"\\]')


def _package_content_disposition(event_title: str) -> str:
    """Mirrors `app.api.v1.persons._content_disposition`'s exact RFC 6266
    shape (duplicated rather than imported across router modules, per
    this codebase's own established convention) — applied to a fixed,
    event-derived name rather than a client-supplied one. Never contains
    the Event's internal UUID."""
    sanitized = _UNSAFE_FILENAME_CHARS.sub("", event_title).strip() or "event"
    base_name = f"competition-documents-{sanitized}.zip"
    ascii_fallback = base_name.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    encoded = quote(base_name, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@router.post("/{event_id}/document-package")
def export_event_document_package(
    event_id: uuid.UUID,
    request: Request,
    payload: DocumentPackageRequest | None = None,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
    _csrf: None = Depends(require_csrf_token),
) -> Response:
    """The synchronous competition document package export
    (events-api.md §31.5) — `event.read` + `document.export`. Only
    current, valid participant Documents are included; any `missing`/
    `expired` participant/requirement pair blocks the export with a 409
    unless `confirm_incomplete=True` is supplied. Never touches the
    filesystem directly — every binary is read through `FileStorage`
    (`app.documents.package`).
    """
    event = _get_authorized_event_or_404(
        db, event_id=event_id, user_id=principal.user_id, permission_code="event.read"
    )
    _require_document_permission_for_event_or_404(
        db, event=event, user_id=principal.user_id, permission_code="document.export"
    )

    confirm_incomplete = payload.confirm_incomplete if payload is not None else False

    try:
        archive_bytes = generate_event_document_package(
            db,
            storage,
            event=event,
            actor_user_id=principal.user_id,
            confirm_incomplete=confirm_incomplete,
            request_id=get_request_id(request),
        )
    except DocumentPackageIncompleteError as exc:
        incomplete = [
            {
                "participant_display_name": entry.display_name,
                "document_type": entry.document_type,
                "result": entry.result,
                "required": entry.required,
            }
            for entry in exc.entries
            if entry.result in ("missing", "expired")
        ]
        raise APIError(
            status.HTTP_409_CONFLICT,
            "document_package_incomplete",
            "The document package is incomplete: some participants are missing a "
            "valid required document. Pass confirm_incomplete=true to export anyway.",
            details={"incomplete": incomplete},
        ) from exc

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": _package_content_disposition(event.title)},
    )
