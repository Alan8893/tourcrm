"""Person API — /api/v1/persons (Issue #62).

Canonical sources: docs/05-api/people-api.md §4-8 (as reconciled),
docs/02-requirements/roles-and-permissions.md, ADR-0013 (scopes),
ADR-0014 (response envelope), ADR-0017 (Person has no `status`),
ADR-0024/ADR-0025 (audit).

Endpoints intentionally NOT implemented here (Issue #62 non-goals):
GuardianRelationship (Issue #64), Group/Role-assignment/Invitation/
RegistrationRequest/Import API, and `POST /persons/{person_id}/archive`.
The last is a genuine, still-open gap (Issue #62 GAP-7): "archiving a
Person" has no defined effect anywhere in canonical docs, and `Person`
has no `status`/lifecycle field (ADR-0017) that could represent it —
adding one would be a new persistence field, which Issue #62 §6
prohibits without a separate decision. Implementing this endpoint would
require inventing either a new column or an unspecified business rule;
neither is done here — see the Issue #62 implementation report.

Existence-hiding for the single-Person endpoints (detail/update),
mirroring app.api.v1.events exactly: a Person that does not exist and a
Person that exists but the caller is not authorized to act on receive an
identical 404. The list endpoint instead silently excludes unauthorized
rows via app.people.queries.list_persons_page (never a 403). Only
`POST /persons` (no object yet exists to hide) uses the generic 403
AuthorizationDenied contract.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.memberships_schemas import MembershipOut
from app.api.v1.persons_schemas import PersonCreateRequest, PersonOut, PersonUpdateRequest
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.identity import Person
from app.db.session import get_db
from app.people import service as people_service
from app.people.authorization import is_person_visible
from app.people.queries import (
    MEMBERSHIP_DEFAULT_SORT,
    PERSON_DEFAULT_SORT,
    InvalidSortError,
    list_memberships_page,
    list_persons_page,
)

router = APIRouter(prefix="/persons", tags=["persons"])

_NOT_FOUND_DETAIL = "Person not found"


def _person_out(person: Person) -> PersonOut:
    return PersonOut(
        id=person.id,
        first_name=person.first_name,
        last_name=person.last_name,
        middle_name=person.middle_name,
        birth_date=person.birth_date,
        created_at=person.created_at,
        updated_at=person.updated_at,
    )


def _membership_out(membership) -> MembershipOut:
    return MembershipOut(
        id=membership.id,
        club_id=membership.club_id,
        person_id=membership.person_id,
        membership_type=membership.membership_type,
        status=membership.status,
        joined_at=membership.joined_at,
        left_at=membership.left_at,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _get_authorized_person_or_404(
    db: Session, *, person_id: uuid.UUID, user_id: uuid.UUID, permission_code: str
) -> Person:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    # Person authorization cannot use the generic Authorizer/ResourceContext
    # engine — see app.people.authorization module docstring for why.
    if not is_person_visible(
        db, person_id=person.id, user_id=user_id, permission_code=permission_code
    ):
        # Deliberately the same detail/status as "does not exist" above —
        # see module docstring.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return person


@router.get("", response_model=CollectionResponse[PersonOut])
def list_persons(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=PERSON_DEFAULT_SORT),
    search: str | None = Query(default=None, max_length=255),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[PersonOut]:
    try:
        rows, total = list_persons_page(
            db,
            user_id=principal.user_id,
            permission_code="person.read",
            page=page,
            page_size=page_size,
            sort=sort,
            search=search,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_person_out(person) for person in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get("/{person_id}", response_model=PersonOut)
def get_person(
    person_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> PersonOut:
    person = _get_authorized_person_or_404(
        db, person_id=person_id, user_id=principal.user_id, permission_code="person.read"
    )
    return _person_out(person)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PersonOut)
def create_person(
    payload: PersonCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonOut:
    # Person is Club-neutral (ADR-0017): no club_id to check ownership
    # against, so only a global `all`-scope assignment can create a
    # Person — see app.people.authorization module docstring.
    authorizer = Authorizer(session=db, user_id=principal.user_id, permission_code="person.update")
    authorizer.check(ResourceContext())

    person = people_service.create_person(
        db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        middle_name=payload.middle_name,
        birth_date=payload.birth_date,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
    )
    return _person_out(person)


@router.patch("/{person_id}", response_model=PersonOut)
def update_person(
    person_id: uuid.UUID,
    payload: PersonUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonOut:
    person = _get_authorized_person_or_404(
        db, person_id=person_id, user_id=principal.user_id, permission_code="person.update"
    )
    fields = payload.model_dump(exclude_unset=True)
    person = people_service.update_person(
        db,
        person=person,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
        **fields,
    )
    return _person_out(person)


@router.get("/{person_id}/memberships", response_model=CollectionResponse[MembershipOut])
def list_person_memberships(
    person_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=MEMBERSHIP_DEFAULT_SORT),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[MembershipOut]:
    """A Person's membership history across Clubs (Issue #62 §7).

    Existence of `person_id` is checked, but visibility of the returned
    memberships is governed entirely by `membership.read` + scope (never
    `person.read`) — this is a Membership listing, not a Person read.
    """
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    try:
        rows, total = list_memberships_page(
            db,
            user_id=principal.user_id,
            permission_code="membership.read",
            page=page,
            page_size=page_size,
            sort=sort,
            person_id=person_id,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_membership_out(membership) for membership in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )
