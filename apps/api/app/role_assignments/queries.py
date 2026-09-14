"""`GET /api/v1/role-assignments` list query composition (Issue #74).

Canonical source: docs/03-architecture/adr/ADR-0026-role-assignment-api-
decisions.md §5 (administrative visibility boundary), Issue #74 (filters:
`user_id`, `club_id`, `role_id`, lifecycle/effectivity state). Applies
deterministic scope-based authorization directly inside the SQL query —
never fetch-then-filter-in-Python — mirroring app.groups.queries/
app.people.queries exactly.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.authorization import UserRoleAssignment
from app.role_assignments.authorization import role_assignment_visibility_filter

_ROLE_ASSIGNMENT_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "created_at": UserRoleAssignment.created_at.asc(),
    "-created_at": UserRoleAssignment.created_at.desc(),
    "valid_from": UserRoleAssignment.valid_from.asc(),
    "-valid_from": UserRoleAssignment.valid_from.desc(),
}
ROLE_ASSIGNMENT_DEFAULT_SORT = "-created_at"


class InvalidSortError(ValueError):
    """`sort` is not one of the whitelisted values (api-contract.md's
    "sorting (whitelist)" convention — never arbitrary/dynamic SQL).
    """


def list_role_assignments_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = ROLE_ASSIGNMENT_DEFAULT_SORT,
    filter_user_id: Optional[uuid.UUID] = None,
    filter_club_id: Optional[uuid.UUID] = None,
    filter_role_id: Optional[uuid.UUID] = None,
    has_ended: Optional[bool] = None,
) -> tuple[list[UserRoleAssignment], int]:
    """`user_id`/`permission_code` identify the *caller* (for the
    authorization-scoping visibility filter); `filter_user_id`/
    `filter_club_id`/`filter_role_id` are the caller-supplied query
    filters (Issue #74: "at minimum ... target user_id, club_id ...,
    role_id ..."). `has_ended` is the lifecycle/effectivity filter —
    `False` selects currently-effective rows (`valid_to IS NULL`),
    `True` selects historical/ended rows (`valid_to IS NOT NULL`),
    omitted applies no filter — matching the identical convention
    established for GroupInstructorAssignment
    (app.groups.queries.list_group_instructor_assignments_page), the one
    other entity in this codebase with no separate status field.
    """
    if sort not in _ROLE_ASSIGNMENT_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        role_assignment_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if filter_user_id is not None:
        conditions.append(UserRoleAssignment.user_id == filter_user_id)
    if filter_club_id is not None:
        conditions.append(UserRoleAssignment.club_id == filter_club_id)
    if filter_role_id is not None:
        conditions.append(UserRoleAssignment.role_id == filter_role_id)
    if has_ended is True:
        conditions.append(UserRoleAssignment.valid_to.isnot(None))
    elif has_ended is False:
        conditions.append(UserRoleAssignment.valid_to.is_(None))

    total = session.execute(
        sa.select(sa.func.count()).select_from(UserRoleAssignment).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(UserRoleAssignment)
            .where(*conditions)
            .order_by(_ROLE_ASSIGNMENT_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


__all__ = [
    "InvalidSortError",
    "ROLE_ASSIGNMENT_DEFAULT_SORT",
    "list_role_assignments_page",
]
