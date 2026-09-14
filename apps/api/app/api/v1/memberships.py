"""ClubMembership API — /api/v1/memberships (Issue #62).

Canonical sources: docs/05-api/people-api.md §9-13 (as reconciled),
docs/02-requirements/business-rules.md §4, ADR-0013 (scopes), ADR-0014
(response envelope), ADR-0024/ADR-0025 (audit).

Existence-hiding for the single-membership endpoints, mirroring
app.api.v1.events/app.api.v1.persons exactly: a membership that does not
exist and one that exists but the caller is not authorized to act on
receive an identical 404. `POST /memberships` (no object yet exists) uses
the generic 403 AuthorizationDenied contract instead.

`GET /memberships/{membership_id}/history` is deliberately NOT
implemented (Issue #62's accepted decisions explicitly remove it from
this API slice): no dedicated history/versioning persistence exists for
ClubMembership, and this Issue's non-goals exclude any audit-read API.
The canonical membership-period history is
`GET /api/v1/persons/{person_id}/memberships` (a list of distinct
ClubMembership rows/periods for that Person, across Clubs) — see
app.api.v1.persons.list_person_memberships. No alias for the removed
`/history` path is added.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
from app.api.v1.memberships_schemas import (
    MembershipCreateRequest,
    MembershipOut,
    MembershipStatusTransitionRequest,
    MembershipUpdateRequest,
)
from app.authorization.context import ResourceContext
from app.authorization.service import Authorizer
from app.db.identity import Club, ClubMembership, Person
from app.db.session import get_db
from app.people import service as people_service
from app.people.authorization import build_membership_resource_context
from app.people.lifecycle import (
    InvalidMembershipStatusTransitionError,
    MembershipDomainError,
    OverlappingActiveMembershipError,
)
from app.people.queries import (
    MEMBERSHIP_DEFAULT_SORT,
    InvalidSortError,
    list_memberships_page,
)

router = APIRouter(prefix="/memberships", tags=["memberships"])

_NOT_FOUND_DETAIL = "Membership not found"


def _membership_out(membership: ClubMembership) -> MembershipOut:
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


def _get_authorized_membership_or_404(
    db: Session,
    *,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> ClubMembership:
    stmt = select(ClubMembership).where(ClubMembership.id == membership_id)
    if lock:
        stmt = stmt.with_for_update()
    membership = db.execute(stmt).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    context = build_membership_resource_context(
        db, membership=membership, requester_user_id=user_id
    )
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return membership


@router.get("", response_model=CollectionResponse[MembershipOut])
def list_memberships(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=MEMBERSHIP_DEFAULT_SORT),
    status_: str | None = Query(default=None, alias="status"),
    membership_type: str | None = Query(default=None),
    person_id: uuid.UUID | None = Query(default=None),
    club_id: uuid.UUID | None = Query(default=None),
    joined_after: datetime | None = Query(default=None),
    joined_before: datetime | None = Query(default=None),
    left_after: datetime | None = Query(default=None),
    left_before: datetime | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[MembershipOut]:
    try:
        rows, total = list_memberships_page(
            db,
            user_id=principal.user_id,
            permission_code="membership.read",
            page=page,
            page_size=page_size,
            sort=sort,
            status=status_,
            membership_type=membership_type,
            person_id=person_id,
            club_id=club_id,
            joined_after=joined_after,
            joined_before=joined_before,
            left_after=left_after,
            left_before=left_before,
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


@router.get("/{membership_id}", response_model=MembershipOut)
def get_membership(
    membership_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> MembershipOut:
    membership = _get_authorized_membership_or_404(
        db,
        membership_id=membership_id,
        user_id=principal.user_id,
        permission_code="membership.read",
    )
    return _membership_out(membership)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MembershipOut)
def create_membership(
    payload: MembershipCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> MembershipOut:
    if db.get(Club, payload.club_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_club_id", "club_id does not exist"
        )
    if db.get(Person, payload.person_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_person_id", "person_id does not exist"
        )

    authorizer = Authorizer(
        session=db, user_id=principal.user_id, permission_code="membership.manage"
    )
    # No ClubMembership exists yet: is_self/is_own_group stay unresolved
    # (None), so only a scope_type='all' assignment matching the target
    # club can authorize creation — same precedent as event.create.
    authorizer.check(ResourceContext(club_id=payload.club_id))

    try:
        membership = people_service.create_membership(
            db,
            person_id=payload.person_id,
            club_id=payload.club_id,
            membership_type=payload.membership_type,
            status=payload.status,
            joined_at=payload.joined_at,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except OverlappingActiveMembershipError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "invalid_membership_transition", str(exc)
        ) from exc
    except MembershipDomainError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_membership_data", str(exc)
        ) from exc
    return _membership_out(membership)


@router.patch("/{membership_id}", response_model=MembershipOut)
def update_membership(
    membership_id: uuid.UUID,
    payload: MembershipUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> MembershipOut:
    membership = _get_authorized_membership_or_404(
        db,
        membership_id=membership_id,
        user_id=principal.user_id,
        permission_code="membership.manage",
        lock=True,
    )
    membership = people_service.update_membership_type(
        db,
        membership=membership,
        membership_type=payload.membership_type,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
    )
    return _membership_out(membership)


@router.post("/{membership_id}/status", response_model=MembershipOut)
def transition_membership_status(
    membership_id: uuid.UUID,
    payload: MembershipStatusTransitionRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> MembershipOut:
    membership = _get_authorized_membership_or_404(
        db,
        membership_id=membership_id,
        user_id=principal.user_id,
        permission_code="membership.manage",
        lock=True,
    )
    try:
        membership = people_service.transition_membership_status(
            db,
            membership=membership,
            new_status=payload.status,
            reason=payload.reason,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except InvalidMembershipStatusTransitionError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "invalid_membership_transition", str(exc)
        ) from exc
    except MembershipDomainError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_membership_data", str(exc)
        ) from exc
    return _membership_out(membership)
