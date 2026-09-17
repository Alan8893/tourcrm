"""GuardianRelationship API — /api/v1/guardian-relationships (Issue #64).

Canonical sources: docs/05-api/people-api.md §17-19 (as reconciled),
ADR-0023 (persistence), ADR-0025 §2-3 (permissions/terminate semantics),
ADR-0013 (scopes), ADR-0014 (response envelope), ADR-0024 (audit).

`GET/POST /persons/{person_id}/guardian-relationships` live in
app.api.v1.persons (nested under the Persons router), mirroring how
`GET /persons/{person_id}/memberships` lives there rather than here —
this router only owns the top-level `/guardian-relationships/{id}...`
paths, matching Issue #62's precedent split between app.api.v1.persons
and app.api.v1.memberships.

Existence-hiding for PATCH/terminate, mirroring every other single-
resource endpoint in this codebase: a relationship that does not exist
and one that exists but the caller is not authorized to act on receive
an identical 404 — same status, same body, same machine-readable `code`
(`guardian_relationship_not_found`, per Issue #64 §17's canonical
`GUARDIAN_RELATIONSHIP_NOT_FOUND`, lowercased to match this codebase's
actual `APIError` code convention — see e.g. `invalid_membership_transition`
in app.api.v1.memberships, not the uppercase spelling docs use). Raised
via `APIError` (never a plain `HTTPException`) so the response carries
this specific code instead of the generic `not_found` the shared
`http_exception_handler` would otherwise produce.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.v1.guardian_relationships_schemas import (
    GuardianRelationshipOut,
    GuardianRelationshipUpdateRequest,
)
from app.authorization.service import Authorizer
from app.db.identity import GuardianRelationship
from app.db.session import get_db
from app.people import guardian_service
from app.people.guardian_authorization import build_guardian_relationship_resource_context
from app.people.guardian_lifecycle import (
    AlreadyRevokedError,
    DuplicateActiveGuardianRelationshipError,
    GuardianRelationshipDomainError,
    effective_status,
)

router = APIRouter(prefix="/guardian-relationships", tags=["guardian-relationships"])

_NOT_FOUND_MESSAGE = "Guardian relationship not found"
_NOT_FOUND_CODE = "guardian_relationship_not_found"


def guardian_relationship_out(relationship: GuardianRelationship) -> GuardianRelationshipOut:
    """Shared response builder, reused by app.api.v1.persons' nested list
    endpoint so the two never disagree on shape or on the read-time
    `status` derivation.
    """
    return GuardianRelationshipOut(
        id=relationship.id,
        guardian_person_id=relationship.guardian_person_id,
        child_person_id=relationship.child_person_id,
        relationship_type=relationship.relationship_type,
        status=effective_status(status=relationship.status, valid_to=relationship.valid_to),
        valid_from=relationship.valid_from,
        valid_to=relationship.valid_to,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
    )


def get_authorized_guardian_relationship_or_404(
    db: Session,
    *,
    relationship_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    lock: bool = False,
) -> GuardianRelationship:
    stmt = select(GuardianRelationship).where(GuardianRelationship.id == relationship_id)
    if lock:
        stmt = stmt.with_for_update()
    relationship = db.execute(stmt).scalar_one_or_none()
    if relationship is None:
        raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND_CODE, _NOT_FOUND_MESSAGE)

    context = build_guardian_relationship_resource_context(
        db, relationship=relationship, requester_user_id=user_id
    )
    authorizer = Authorizer(session=db, user_id=user_id, permission_code=permission_code)
    if not authorizer.is_allowed(context):
        # Deliberately the same status/code/message as "does not exist"
        # above — an existing-but-unauthorized relationship must be
        # indistinguishable from a nonexistent one (IDOR protection).
        raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND_CODE, _NOT_FOUND_MESSAGE)
    return relationship


@router.patch("/{relationship_id}", response_model=GuardianRelationshipOut)
def update_guardian_relationship(
    relationship_id: uuid.UUID,
    payload: GuardianRelationshipUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GuardianRelationshipOut:
    relationship = get_authorized_guardian_relationship_or_404(
        db,
        relationship_id=relationship_id,
        user_id=principal.user_id,
        permission_code="guardian_relationship.manage",
        lock=True,
    )
    fields = payload.model_dump(exclude_unset=True)
    try:
        relationship = guardian_service.update_guardian_relationship(
            db,
            relationship=relationship,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
            **fields,
        )
    except DuplicateActiveGuardianRelationshipError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "guardian_link_not_allowed", str(exc)
        ) from exc
    return guardian_relationship_out(relationship)


@router.post("/{relationship_id}/terminate", response_model=GuardianRelationshipOut)
def terminate_guardian_relationship(
    relationship_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GuardianRelationshipOut:
    relationship = get_authorized_guardian_relationship_or_404(
        db,
        relationship_id=relationship_id,
        user_id=principal.user_id,
        permission_code="guardian_relationship.manage",
        lock=True,
    )
    try:
        relationship = guardian_service.terminate_guardian_relationship(
            db,
            relationship=relationship,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except AlreadyRevokedError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "guardian_link_not_allowed", str(exc)
        ) from exc
    except GuardianRelationshipDomainError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "guardian_link_not_allowed", str(exc)
        ) from exc
    return guardian_relationship_out(relationship)
