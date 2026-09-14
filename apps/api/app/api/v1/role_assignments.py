"""RoleAssignment API — /api/v1/role-assignments (Issue #74, implementing
ADR-0026).

Canonical sources: docs/02-requirements/roles-and-permissions.md §19,
docs/03-architecture/adr/ADR-0026-role-assignment-api-decisions.md,
docs/05-api/endpoint-inventory.md §24 (endpoint list — `GET /roles`,
`GET /permissions`, `GET /audit-logs*` are deliberately NOT implemented
here; out of Issue #74's scope), ADR-0022 (cross-Club integrity),
ADR-0013 (scopes), ADR-0014 (response envelope), ADR-0024 (audit).

Existence-hiding for the revoke endpoint, mirroring
app.api.v1.groups/app.api.v1.guardian_relationships exactly: an
assignment that does not exist and one that exists but the caller's
`role.manage` does not cover receive an identical 404 with the same
machine-readable code (`role_assignment_not_found`). `POST
/role-assignments` (no object yet exists) uses the generic 403
AuthorizationDenied contract instead, mirroring `POST /memberships`/
`POST /groups`.

Validation order for `POST /role-assignments` follows Issue #74's
required checks (authentication; effective `role.manage` + target Club
boundary — one `Authorizer.check(ResourceContext(club_id=...))` call
satisfies both, exactly like `app.api.v1.memberships.create_membership`'s
identical precedent; target User exists; target Role exists; scope
combination valid; active ClubMembership for a club-scoped assignment;
temporal duplicate invariant; audit in the same transaction) — grouped
as simple existence/authorization checks in this router followed by
business-invariant validation in app.role_assignments.service, in that
service's own sensible order (structural scope-combination validity
before the relationship-dependent ClubMembership check), rather than
forcing an exact single linear sequence split across the router/service
boundary. This does not change externally observable behavior for any
well-formed request; only which single error is reported first for a
request that is simultaneously invalid in more than one way.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.role_assignments_schemas import RoleAssignmentCreateRequest, RoleAssignmentOut
from app.authorization.context import InvalidScopeError, ResourceContext, normalize_scope_type
from app.authorization.service import Authorizer
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import User
from app.db.session import get_db
from app.role_assignments import service as role_assignment_service
from app.role_assignments.authorization import build_role_assignment_resource_context
from app.role_assignments.lifecycle import (
    InvalidRoleAssignmentScopeError,
    InvalidRoleAssignmentTransitionError,
)
from app.role_assignments.queries import (
    ROLE_ASSIGNMENT_DEFAULT_SORT,
    InvalidSortError,
    list_role_assignments_page,
)

router = APIRouter(prefix="/role-assignments", tags=["role-assignments"])

_NOT_FOUND_CODE = "role_assignment_not_found"
_NOT_FOUND_MESSAGE = "Role assignment not found"


def _role_assignment_out(assignment: UserRoleAssignment) -> RoleAssignmentOut:
    return RoleAssignmentOut(
        id=assignment.id,
        user_id=assignment.user_id,
        role_id=assignment.role_id,
        club_id=assignment.club_id,
        scope_type=assignment.scope_type,
        scope_ref_id=assignment.scope_ref_id,
        valid_from=assignment.valid_from,
        valid_to=assignment.valid_to,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _get_authorized_role_assignment_or_404(
    db: Session,
    *,
    assignment_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> UserRoleAssignment:
    stmt = select(UserRoleAssignment).where(UserRoleAssignment.id == assignment_id)
    if lock:
        stmt = stmt.with_for_update()
    assignment = db.execute(stmt).scalar_one_or_none()
    if assignment is None:
        raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND_CODE, _NOT_FOUND_MESSAGE)

    context = build_role_assignment_resource_context(assignment)
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        # Deliberately the same status/code/message as "does not exist"
        # above — an existing-but-unauthorized assignment must be
        # indistinguishable from a nonexistent one (IDOR protection).
        raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND_CODE, _NOT_FOUND_MESSAGE)
    return assignment


@router.get("", response_model=CollectionResponse[RoleAssignmentOut])
def list_role_assignments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=ROLE_ASSIGNMENT_DEFAULT_SORT),
    user_id: uuid.UUID | None = Query(default=None),
    club_id: uuid.UUID | None = Query(default=None),
    role_id: uuid.UUID | None = Query(default=None),
    # Issue #74: lifecycle/effectivity filter — false selects currently-
    # effective rows (valid_to IS NULL), true selects historical/ended
    # rows (valid_to IS NOT NULL), omitted applies no filter. Same
    # `has_ended` convention already established for the one other
    # status-field-less temporal entity in this codebase
    # (GroupInstructorAssignment, app.api.v1.groups.list_group_instructors).
    has_ended: bool | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[RoleAssignmentOut]:
    try:
        rows, total = list_role_assignments_page(
            db,
            user_id=principal.user_id,
            permission_code="role.manage",
            page=page,
            page_size=page_size,
            sort=sort,
            filter_user_id=user_id,
            filter_club_id=club_id,
            filter_role_id=role_id,
            has_ended=has_ended,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_role_assignment_out(assignment) for assignment in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RoleAssignmentOut)
def create_role_assignment(
    payload: RoleAssignmentCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RoleAssignmentOut:
    # ADR-0013/app.authorization.context.normalize_scope_type: resolves
    # the `assigned_events` input alias to `own_events` and rejects
    # `own_records`/any non-canonical value — the exact existing
    # mechanism this module's own docstring documents as unused-until-now.
    try:
        scope_type = normalize_scope_type(payload.scope_type)
    except InvalidScopeError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role_assignment_scope", str(exc)
        ) from exc

    # Effective `role.manage` + target Club boundary in one call — no
    # RoleAssignment exists yet, so no relationship field is resolved;
    # only `scope_type='all'` (globally or for this specific club_id) can
    # authorize creation, exactly per ADR-0026 §5.
    authorizer = Authorizer(session=db, user_id=principal.user_id, permission_code="role.manage")
    authorizer.check(ResourceContext(club_id=payload.club_id))

    if db.get(User, payload.user_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_user_id", "user_id does not exist"
        )
    if db.get(Role, payload.role_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role_id", "role_id does not exist"
        )

    try:
        assignment = role_assignment_service.create_role_assignment(
            db,
            user_id=payload.user_id,
            role_id=payload.role_id,
            scope_type=scope_type,
            club_id=payload.club_id,
            scope_ref_id=payload.scope_ref_id,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except InvalidRoleAssignmentScopeError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role_assignment_scope", str(exc)
        ) from exc
    except role_assignment_service.RoleAssignmentClubMembershipMissingError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "role_assignment_club_membership_missing",
            str(exc),
        ) from exc
    except role_assignment_service.DuplicateRoleAssignmentError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "duplicate_role_assignment", str(exc)) from exc
    return _role_assignment_out(assignment)


@router.post("/{assignment_id}/revoke", response_model=RoleAssignmentOut)
def revoke_role_assignment(
    assignment_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RoleAssignmentOut:
    assignment = _get_authorized_role_assignment_or_404(
        db,
        assignment_id=assignment_id,
        user_id=principal.user_id,
        permission_code="role.manage",
        lock=True,
    )
    try:
        assignment = role_assignment_service.revoke_role_assignment(
            db,
            assignment=assignment,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except InvalidRoleAssignmentTransitionError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "invalid_role_assignment_transition", str(exc)
        ) from exc
    return _role_assignment_out(assignment)


__all__ = ["router"]
