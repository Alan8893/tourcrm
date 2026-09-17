"""Person API — /api/v1/persons (Issue #62, TH-0101 / Issue #116).

Canonical sources: docs/05-api/people-api.md §4-8 (as reconciled),
docs/02-requirements/roles-and-permissions.md, ADR-0013 (scopes),
ADR-0014 (response envelope), ADR-0017 (Person has no `status`),
ADR-0024/ADR-0025 (audit), ADR-0035 (People management authorization),
ADR-0034 (Person archiving deferred).

Endpoints intentionally NOT implemented here (Issue #62 non-goals):
Group/Role-assignment/Invitation/RegistrationRequest/Import API, and
`POST /persons/{person_id}/archive`. The last is formally deferred by
ADR-0034, not an open gap: `Person` has no `status`/`archived_at`/
soft-delete field, physical deletion is not part of the domain contract,
and archive semantics must not be simulated through User/ClubMembership/
GroupMembership/GuardianRelationship lifecycle changes. A future archive
decision must define persistence, lifecycle, authorization and API
contract separately (ADR-0034) — this module does not implement or
approximate any of that.

`GET/POST /persons/{person_id}/guardian-relationships` (Issue #64 §7)
live here rather than in app.api.v1.guardian_relationships, mirroring how
`GET /persons/{person_id}/memberships` lives here rather than in
app.api.v1.memberships — only the top-level `/guardian-relationships/{id}
...` paths live in that other module.

Existence-hiding for the single-Person endpoints (detail/update),
mirroring app.api.v1.events exactly: a Person that does not exist and a
Person that exists but the caller is not authorized to act on receive an
identical 404. The list endpoint instead silently excludes unauthorized
rows via app.people.queries.list_persons_page (never a 403). `POST
/persons` (no object yet exists to hide) uses the generic 403
AuthorizationDenied contract; `PATCH /persons/{person_id}` also uses it,
but only for the narrower `birth_date` admin-only field gate (ADR-0035
§5) — by that point existence/general-update access has already been
established via the 404 check above, so a 403 there discloses nothing an
authorized-for-other-fields caller didn't already know.
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
from app.api.v1.guardian_relationships import guardian_relationship_out
from app.api.v1.guardian_relationships_schemas import (
    GuardianRelationshipCreateRequest,
    GuardianRelationshipOut,
)
from app.api.v1.memberships_schemas import MembershipOut
from app.api.v1.persons_schemas import PersonCreateRequest, PersonOut, PersonUpdateRequest
from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, Authorizer
from app.db.identity import Person
from app.db.session import get_db
from app.people import guardian_service
from app.people import service as people_service
from app.people.authorization import is_person_visible, is_system_admin_person_update_grant
from app.people.guardian_authorization import build_guardian_relationship_create_context
from app.people.guardian_lifecycle import (
    DuplicateActiveGuardianRelationshipError,
    DuplicatePrimaryContactError,
    SelfLinkNotAllowedError,
)
from app.people.guardian_queries import (
    GUARDIAN_RELATIONSHIP_DEFAULT_SORT,
    list_guardian_relationships_for_child,
)
from app.people.guardian_queries import InvalidSortError as InvalidGuardianSortError
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
        phone=person.phone,
        email=person.email,
        address=person.address,
        photo_file_id=person.photo_file_id,
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
    # Person — see app.people.authorization module docstring. ADR-0035 §2
    # introduces `person.create` as its own canonical permission, distinct
    # from `person.update`; only `admin` holds it in the current MVP.
    authorizer = Authorizer(session=db, user_id=principal.user_id, permission_code="person.create")
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
        photo_file_id=payload.photo_file_id,
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
    if "birth_date" in fields:
        # ADR-0035 §5: only the canonical system admin role may change
        # birth_date, including on the admin's own Person — a bare
        # all-scope person.update grant via any role is not sufficient
        # (see app.people.authorization.is_system_admin_person_update_
        # grant's own docstring for why this cannot be expressed as a
        # plain Authorizer.check(ResourceContext()) scope check).
        if not is_system_admin_person_update_grant(db, principal.user_id):
            raise AuthorizationDenied("person.update")
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


@router.get(
    "/{person_id}/guardian-relationships",
    response_model=CollectionResponse[GuardianRelationshipOut],
)
def list_person_guardian_relationships(
    person_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=GUARDIAN_RELATIONSHIP_DEFAULT_SORT),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[GuardianRelationshipOut]:
    """Guardians of `person_id` — the child-side view (Issue #64 §7)."""
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    try:
        rows, total = list_guardian_relationships_for_child(
            db,
            person_id=person_id,
            user_id=principal.user_id,
            permission_code="guardian_relationship.read",
            page=page,
            page_size=page_size,
            sort=sort,
        )
    except InvalidGuardianSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[guardian_relationship_out(relationship) for relationship in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post(
    "/{person_id}/guardian-relationships",
    status_code=status.HTTP_201_CREATED,
    response_model=GuardianRelationshipOut,
)
def create_person_guardian_relationship(
    person_id: uuid.UUID,
    payload: GuardianRelationshipCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GuardianRelationshipOut:
    """Create a GuardianRelationship with `person_id` as the child side
    (Issue #64 §7-8)."""
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    if db.get(Person, payload.guardian_person_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_person_id",
            "guardian_person_id does not exist",
        )

    authorizer = Authorizer(
        session=db, user_id=principal.user_id, permission_code="guardian_relationship.manage"
    )
    context = build_guardian_relationship_create_context(
        db, child_person_id=person_id, requester_user_id=principal.user_id
    )
    authorizer.check(context)

    try:
        relationship = guardian_service.create_guardian_relationship(
            db,
            guardian_person_id=payload.guardian_person_id,
            child_person_id=person_id,
            relationship_type=payload.relationship_type,
            is_primary_contact=payload.is_primary_contact,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except SelfLinkNotAllowedError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "guardian_link_not_allowed", str(exc)
        ) from exc
    except (DuplicateActiveGuardianRelationshipError, DuplicatePrimaryContactError) as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "guardian_link_not_allowed", str(exc)
        ) from exc
    return guardian_relationship_out(relationship)
