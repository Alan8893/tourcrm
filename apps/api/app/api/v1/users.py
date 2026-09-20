"""User directory API — /api/v1/users (TH-0107).

Canonical sources: docs/05-api/users-api.md, docs/05-api/api-conventions.md,
docs/02-requirements/roles-and-permissions.md §7.1, ADR-0035.

This is a safe, operational directory endpoint only — not the full admin
User Management API `docs/05-api/endpoint-inventory.md` §2 describes.
Authorization reuses the existing `person.read` permission and
`app.people.authorization.person_visibility_filter` verbatim (see
app.users.queries module docstring for why): there is no new permission,
scope, or ADR for this endpoint.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.users_schemas import UserDirectoryOut
from app.db.identity import User
from app.db.session import get_db
from app.users.queries import InvalidRoleError, InvalidUserStatusError, list_users_page

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> UserDirectoryOut:
    return UserDirectoryOut(
        id=user.id,
        person_id=user.person_id,
        first_name=user.person.first_name,
        last_name=user.person.last_name,
        middle_name=user.person.middle_name,
    )


@router.get("", response_model=CollectionResponse[UserDirectoryOut])
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    club_id: uuid.UUID | None = Query(default=None),
    role: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[UserDirectoryOut]:
    try:
        rows, total = list_users_page(
            db,
            user_id=principal.user_id,
            permission_code="person.read",
            page=page,
            page_size=page_size,
            search=search,
            club_id=club_id,
            role=role,
            status=status_,
        )
    except InvalidRoleError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role", f"Unsupported role value: {role}"
        ) from exc
    except InvalidUserStatusError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_status",
            f"Unsupported status value: {status_}",
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_user_out(user) for user in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )
