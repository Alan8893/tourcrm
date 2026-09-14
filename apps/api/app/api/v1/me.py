"""`/api/v1/me` — endpoints scoped to the authenticated principal only
(Issue #64 §7).

`GET /me/children` never accepts any client-supplied guardian/person id
as a substitute for the authenticated principal (Issue #64 §13) — it
takes no path or query parameters that could name a different user; the
only identity input is the session-derived `CurrentPrincipal`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.guardian_relationships_schemas import ChildOut
from app.db.identity import Person
from app.db.session import get_db
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
        pagination=Pagination(
            page=1, page_size=total or 1, total=total, pages=1 if total else 0
        ),
    )
