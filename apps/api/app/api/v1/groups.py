"""Group / GroupMembership / GroupInstructorAssignment API —
/api/v1/groups, /api/v1/group-memberships, /api/v1/group-instructor-
assignments (Issue #71, implementing the Issue #69 specification gate).

Canonical sources: docs/05-api/people-api.md §14-16 (the full contract),
docs/05-api/endpoint-inventory.md §7 (endpoint list — `POST
/groups/{id}/members/bulk` is deliberately NOT implemented here, per
people-api.md §15.4's explicit deferral), ADR-0021 (persistence), ADR-0022
(cross-Club integrity), ADR-0013 (scopes), ADR-0014 (response envelope),
ADR-0024 (audit). No `/transfer` endpoint exists anywhere in this module
(people-api.md §15.3, closing GAP-GROUP-004).

Existence-hiding for every item-level endpoint, mirroring
app.api.v1.guardian_relationships/app.api.v1.events exactly: an object
that does not exist and one that exists but the caller is not authorized
to act on receive an identical 404 with the same machine-readable code
(`group_not_found`/`group_membership_not_found`/
`group_instructor_assignment_not_found`). `POST /groups` (no object yet
exists) uses the generic 403 AuthorizationDenied contract instead,
mirroring `POST /memberships`.

`GroupMembership`/`GroupInstructorAssignment` list/create endpoints are
nested under an already-authorized parent Group — see
app.groups.authorization's module docstring for why no separate
per-row visibility filter is needed for those two list endpoints.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.groups_schemas import (
    GroupCreateRequest,
    GroupInstructorAssignmentCreateRequest,
    GroupInstructorAssignmentOut,
    GroupMembershipCreateRequest,
    GroupMembershipOut,
    GroupMembershipUpdateRequest,
    GroupOut,
    GroupUpdateRequest,
)
from app.authorization.service import Authorizer
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership
from app.db.session import get_db
from app.groups import service as group_service
from app.groups.authorization import (
    build_group_create_context,
    build_group_instructor_assignment_resource_context,
    build_group_membership_resource_context,
    build_group_resource_context,
)
from app.groups.lifecycle import (
    InvalidGroupMembershipStatusTransitionError,
    InvalidGroupStatusTransitionError,
)
from app.groups.queries import (
    GROUP_DEFAULT_SORT,
    GROUP_INSTRUCTOR_ASSIGNMENT_DEFAULT_SORT,
    GROUP_MEMBERSHIP_DEFAULT_SORT,
    InvalidSortError,
    list_group_instructor_assignments_page,
    list_group_memberships_page,
    list_groups_page,
)

router = APIRouter(tags=["groups"])
groups_router = APIRouter(prefix="/groups", tags=["groups"])
group_memberships_router = APIRouter(prefix="/group-memberships", tags=["groups"])
group_instructor_assignments_router = APIRouter(
    prefix="/group-instructor-assignments", tags=["groups"]
)

_GROUP_NOT_FOUND_CODE = "group_not_found"
_GROUP_NOT_FOUND_MESSAGE = "Group not found"
_GROUP_MEMBERSHIP_NOT_FOUND_CODE = "group_membership_not_found"
_GROUP_MEMBERSHIP_NOT_FOUND_MESSAGE = "Group membership not found"
_GROUP_INSTRUCTOR_ASSIGNMENT_NOT_FOUND_CODE = "group_instructor_assignment_not_found"
_GROUP_INSTRUCTOR_ASSIGNMENT_NOT_FOUND_MESSAGE = "Group instructor assignment not found"


def _group_out(group: Group) -> GroupOut:
    return GroupOut(
        id=group.id,
        club_id=group.club_id,
        name=group.name,
        description=group.description,
        status=group.status,
        valid_from=group.valid_from,
        valid_to=group.valid_to,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _group_membership_out(membership: GroupMembership) -> GroupMembershipOut:
    return GroupMembershipOut(
        id=membership.id,
        group_id=membership.group_id,
        club_membership_id=membership.club_membership_id,
        valid_from=membership.valid_from,
        valid_to=membership.valid_to,
        membership_status=membership.membership_status,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _group_instructor_assignment_out(
    assignment: GroupInstructorAssignment,
) -> GroupInstructorAssignmentOut:
    return GroupInstructorAssignmentOut(
        id=assignment.id,
        group_id=assignment.group_id,
        user_id=assignment.user_id,
        role_in_group=assignment.role_in_group,
        is_primary=assignment.is_primary,
        valid_from=assignment.valid_from,
        valid_to=assignment.valid_to,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _get_authorized_group_or_404(
    db: Session,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> Group:
    stmt = select(Group).where(Group.id == group_id)
    if lock:
        stmt = stmt.with_for_update()
    group = db.execute(stmt).scalar_one_or_none()
    if group is None:
        raise APIError(status.HTTP_404_NOT_FOUND, _GROUP_NOT_FOUND_CODE, _GROUP_NOT_FOUND_MESSAGE)

    context = build_group_resource_context(db, group=group, requester_user_id=user_id)
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        # Deliberately the same status/code/message as "does not exist"
        # above — an existing-but-unauthorized Group must be
        # indistinguishable from a nonexistent one (IDOR protection).
        raise APIError(status.HTTP_404_NOT_FOUND, _GROUP_NOT_FOUND_CODE, _GROUP_NOT_FOUND_MESSAGE)
    return group


def _get_authorized_group_membership_or_404(
    db: Session,
    *,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> GroupMembership:
    stmt = select(GroupMembership).where(GroupMembership.id == membership_id)
    if lock:
        stmt = stmt.with_for_update()
    membership = db.execute(stmt).scalar_one_or_none()
    if membership is None:
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            _GROUP_MEMBERSHIP_NOT_FOUND_CODE,
            _GROUP_MEMBERSHIP_NOT_FOUND_MESSAGE,
        )

    context = build_group_membership_resource_context(
        db, membership=membership, requester_user_id=user_id
    )
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            _GROUP_MEMBERSHIP_NOT_FOUND_CODE,
            _GROUP_MEMBERSHIP_NOT_FOUND_MESSAGE,
        )
    return membership


def _get_authorized_group_instructor_assignment_or_404(
    db: Session,
    *,
    assignment_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> GroupInstructorAssignment:
    stmt = select(GroupInstructorAssignment).where(GroupInstructorAssignment.id == assignment_id)
    if lock:
        stmt = stmt.with_for_update()
    assignment = db.execute(stmt).scalar_one_or_none()
    if assignment is None:
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            _GROUP_INSTRUCTOR_ASSIGNMENT_NOT_FOUND_CODE,
            _GROUP_INSTRUCTOR_ASSIGNMENT_NOT_FOUND_MESSAGE,
        )

    context = build_group_instructor_assignment_resource_context(
        db, assignment=assignment, requester_user_id=user_id
    )
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            _GROUP_INSTRUCTOR_ASSIGNMENT_NOT_FOUND_CODE,
            _GROUP_INSTRUCTOR_ASSIGNMENT_NOT_FOUND_MESSAGE,
        )
    return assignment


# --- Group ------------------------------------------------------------


@groups_router.get("", response_model=CollectionResponse[GroupOut])
def list_groups(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=GROUP_DEFAULT_SORT),
    # people-api.md §14.1: closed active/archived vocabulary (app.db.groups.
    # CANONICAL_GROUP_STATUSES) — Literal, not `str`, so FastAPI/Pydantic's
    # own request-validation mechanism rejects any other value with the
    # existing generic `validation_error` code (HTTP 422) rather than a
    # filter that silently matches nothing.
    status_: Literal["active", "archived"] | None = Query(default=None, alias="status"),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[GroupOut]:
    try:
        rows, total = list_groups_page(
            db,
            user_id=principal.user_id,
            permission_code="group.read",
            page=page,
            page_size=page_size,
            sort=sort,
            status=status_,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_group_out(group) for group in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@groups_router.get("/{group_id}", response_model=GroupOut)
def get_group(
    group_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> GroupOut:
    group = _get_authorized_group_or_404(
        db, group_id=group_id, user_id=principal.user_id, permission_code="group.read"
    )
    return _group_out(group)


@groups_router.post("", status_code=status.HTTP_201_CREATED, response_model=GroupOut)
def create_group(
    payload: GroupCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupOut:
    # No Group exists yet: is_own_group stays unresolved (None), so only a
    # scope_type='all' assignment matching the target club can authorize
    # creation — same precedent as membership.create/event.create.
    authorizer = Authorizer(session=db, user_id=principal.user_id, permission_code="group.manage")
    authorizer.check(build_group_create_context(payload.club_id))

    group = group_service.create_group(
        db,
        club_id=payload.club_id,
        name=payload.name,
        description=payload.description,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
    )
    return _group_out(group)


@groups_router.patch("/{group_id}", response_model=GroupOut)
def update_group(
    group_id: uuid.UUID,
    payload: GroupUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupOut:
    group = _get_authorized_group_or_404(
        db,
        group_id=group_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )
    fields = payload.model_dump(exclude_unset=True)
    group = group_service.update_group(
        db,
        group=group,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
        **fields,
    )
    return _group_out(group)


@groups_router.post("/{group_id}/archive", response_model=GroupOut)
def archive_group(
    group_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupOut:
    group = _get_authorized_group_or_404(
        db,
        group_id=group_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )
    try:
        group = group_service.archive_group(
            db, group=group, actor_user_id=principal.user_id, request_id=get_request_id(request)
        )
    except InvalidGroupStatusTransitionError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "invalid_group_status_transition", str(exc)
        ) from exc
    return _group_out(group)


# --- GroupMembership ----------------------------------------------------


@groups_router.get("/{group_id}/members", response_model=CollectionResponse[GroupMembershipOut])
def list_group_members(
    group_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=GROUP_MEMBERSHIP_DEFAULT_SORT),
    # people-api.md §15.1: closed active/ended vocabulary (app.db.groups.
    # CANONICAL_GROUP_MEMBERSHIP_STATUSES) — see the identical rationale on
    # list_groups' `status_` parameter above.
    membership_status: Literal["active", "ended"] | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[GroupMembershipOut]:
    _get_authorized_group_or_404(
        db, group_id=group_id, user_id=principal.user_id, permission_code="group.read"
    )
    try:
        rows, total = list_group_memberships_page(
            db,
            group_id=group_id,
            page=page,
            page_size=page_size,
            sort=sort,
            membership_status=membership_status,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_group_membership_out(membership) for membership in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@groups_router.post(
    "/{group_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=GroupMembershipOut,
)
def create_group_member(
    group_id: uuid.UUID,
    payload: GroupMembershipCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupMembershipOut:
    group = _get_authorized_group_or_404(
        db,
        group_id=group_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )

    # people-api.md §15: the API accepts person_id; the persistence model
    # stores club_membership_id — resolve the Person's *active*
    # ClubMembership in the target Group's Club (ADR-0022 §4).
    club_membership = db.execute(
        select(ClubMembership).where(
            ClubMembership.person_id == payload.person_id,
            ClubMembership.club_id == group.club_id,
            ClubMembership.status == "active",
        )
    ).scalar_one_or_none()
    if club_membership is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "group_membership_club_mismatch",
            "Person has no active ClubMembership in the Group's Club",
        )

    try:
        membership = group_service.create_group_membership(
            db,
            group_id=group_id,
            club_membership_id=club_membership.id,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except group_service.GroupArchivedError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "group_archived", str(exc)) from exc
    except group_service.GroupMembershipClubMismatchError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "group_membership_club_mismatch", str(exc)
        ) from exc
    except group_service.DuplicateActiveGroupMembershipError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "duplicate_group_membership", str(exc)) from exc
    return _group_membership_out(membership)


@group_memberships_router.patch("/{membership_id}", response_model=GroupMembershipOut)
def update_group_membership(
    membership_id: uuid.UUID,
    payload: GroupMembershipUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupMembershipOut:
    membership = _get_authorized_group_membership_or_404(
        db,
        membership_id=membership_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )
    fields = payload.model_dump(exclude_unset=True)
    immutable_fields = set(fields) - {"valid_from"}
    if immutable_fields:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "group_membership_immutable_field",
            f"Fields not modifiable via PATCH: {sorted(immutable_fields)}",
        )

    membership = group_service.update_group_membership(
        db,
        membership=membership,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
        **{k: v for k, v in fields.items() if k == "valid_from"},
    )
    return _group_membership_out(membership)


@group_memberships_router.post("/{membership_id}/end", response_model=GroupMembershipOut)
def end_group_membership(
    membership_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupMembershipOut:
    membership = _get_authorized_group_membership_or_404(
        db,
        membership_id=membership_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )
    try:
        membership = group_service.end_group_membership(
            db,
            membership=membership,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except InvalidGroupMembershipStatusTransitionError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "invalid_group_membership_transition", str(exc)
        ) from exc
    return _group_membership_out(membership)


# --- GroupInstructorAssignment -------------------------------------------


@groups_router.get(
    "/{group_id}/instructors", response_model=CollectionResponse[GroupInstructorAssignmentOut]
)
def list_group_instructors(
    group_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=GROUP_INSTRUCTOR_ASSIGNMENT_DEFAULT_SORT),
    # people-api.md §16.1/§16.3: filter by activity — presence/absence of
    # `valid_to`. has_ended=false -> active/ongoing (valid_to IS NULL);
    # has_ended=true -> ended/historical (valid_to IS NOT NULL); omitted ->
    # no filter, all assignments.
    has_ended: bool | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[GroupInstructorAssignmentOut]:
    _get_authorized_group_or_404(
        db, group_id=group_id, user_id=principal.user_id, permission_code="group.read"
    )
    try:
        rows, total = list_group_instructor_assignments_page(
            db, group_id=group_id, page=page, page_size=page_size, sort=sort, has_ended=has_ended
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_group_instructor_assignment_out(assignment) for assignment in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@groups_router.post(
    "/{group_id}/instructors",
    status_code=status.HTTP_201_CREATED,
    response_model=GroupInstructorAssignmentOut,
)
def create_group_instructor(
    group_id: uuid.UUID,
    payload: GroupInstructorAssignmentCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupInstructorAssignmentOut:
    _get_authorized_group_or_404(
        db,
        group_id=group_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )
    try:
        assignment = group_service.create_group_instructor_assignment(
            db,
            group_id=group_id,
            user_id=payload.user_id,
            role_in_group=payload.role_in_group,
            is_primary=payload.is_primary,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except group_service.GroupArchivedError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "group_archived", str(exc)) from exc
    except group_service.InstructorClubMembershipMissingError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "instructor_club_membership_missing", str(exc)
        ) from exc
    except group_service.GroupInstructorPrimaryConflictError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "duplicate_primary_instructor", str(exc)) from exc
    return _group_instructor_assignment_out(assignment)


@group_instructor_assignments_router.post(
    "/{assignment_id}/end", response_model=GroupInstructorAssignmentOut
)
def end_group_instructor_assignment(
    assignment_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GroupInstructorAssignmentOut:
    assignment = _get_authorized_group_instructor_assignment_or_404(
        db,
        assignment_id=assignment_id,
        user_id=principal.user_id,
        permission_code="group.manage",
        lock=True,
    )
    try:
        assignment = group_service.end_group_instructor_assignment(
            db,
            assignment=assignment,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except group_service.InvalidGroupInstructorAssignmentTransitionError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "invalid_group_instructor_assignment_transition", str(exc)
        ) from exc
    return _group_instructor_assignment_out(assignment)


router.include_router(groups_router)
router.include_router(group_memberships_router)
router.include_router(group_instructor_assignments_router)
