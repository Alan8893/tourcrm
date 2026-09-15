"""`/api/v1/me` — endpoints scoped to the authenticated principal only
(Issue #64 §7; Issue #88 / TH-0083 §3 for the Instructor Schedule).

`GET /me/children` never accepts any client-supplied guardian/person id
as a substitute for the authenticated principal (Issue #64 §13) — it
takes no path or query parameters that could name a different user; the
only identity input is the session-derived `CurrentPrincipal`.

`GET /me/instructor-schedule` (`docs/05-api/group-and-instructor-
schedule-api.md` §3): `me` means the authenticated **User** — never
Person, ClubMembership or RoleAssignment. See
`app.events.instructor_schedule`'s module docstring for the full
relationship-based (not scope-based) authorization contract.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.events_schemas import CalendarItemOut
from app.api.v1.guardian_relationships_schemas import ChildOut
from app.api.v1.schedule_params import parse_schedule_range
from app.db.identity import Person
from app.db.session import get_db
from app.events.calendar import CalendarItem
from app.events.instructor_schedule import list_instructor_schedule_items_page
from app.people.guardian_queries import list_children_for_guardian

router = APIRouter(prefix="/me", tags=["me"])


def _child_out(person: Person) -> ChildOut:
    full_name = " ".join(
        part for part in (person.last_name, person.first_name, person.middle_name) if part
    )
    return ChildOut(
        id=person.id,
        full_name=full_name,
        birth_date=person.birth_date,
        photo_file_id=person.photo_file_id,
    )


@router.get("/children", response_model=CollectionResponse[ChildOut])
def list_my_children(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[ChildOut]:
    rows = list_children_for_guardian(
        db, user_id=principal.user_id, permission_code="guardian_relationship.read"
    )
    total = len(rows)
    return CollectionResponse(
        items=[_child_out(person) for person in rows],
        pagination=Pagination(page=1, page_size=total or 1, total=total, pages=1 if total else 0),
    )


def _schedule_item_out(item: CalendarItem) -> CalendarItemOut:
    # Identical to app.api.v1.events._calendar_item_out / app.api.v1.groups.
    # _schedule_item_out — duplicated rather than imported, since those are
    # private to their own modules; all three endpoints reuse the same
    # CalendarItem/CalendarItemOut identity contract on purpose.
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


@router.get("/instructor-schedule", response_model=CollectionResponse[CalendarItemOut])
def get_my_instructor_schedule(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[CalendarItemOut]:
    from_at, to_at = parse_schedule_range(from_, to)

    items, total = list_instructor_schedule_items_page(
        db,
        user_id=principal.user_id,
        permission_code="event.read",
        from_at=from_at,
        to_at=to_at,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_schedule_item_out(item) for item in items],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )
