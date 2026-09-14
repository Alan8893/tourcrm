"""Group / GroupMembership / GroupInstructorAssignment list query
composition (Issue #71).

Canonical source: docs/05-api/people-api.md §14-16 (documented filters/
sort per endpoint). Applies deterministic scope-based authorization
directly inside the SQL query for the top-level Group list — never
fetch-then-filter-in-Python — mirroring app.people.queries exactly.
`GroupMembership`/`GroupInstructorAssignment` listing is nested under an
already-authorized parent Group (see app.groups.authorization's module
docstring) and therefore takes no `user_id`/`permission_code` here.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.groups.authorization import group_visibility_filter

_GROUP_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "name": Group.name.asc(),
    "-name": Group.name.desc(),
    "created_at": Group.created_at.asc(),
    "-created_at": Group.created_at.desc(),
}
GROUP_DEFAULT_SORT = "name"

_GROUP_MEMBERSHIP_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "valid_from": GroupMembership.valid_from.asc(),
    "-valid_from": GroupMembership.valid_from.desc(),
    "created_at": GroupMembership.created_at.asc(),
    "-created_at": GroupMembership.created_at.desc(),
}
GROUP_MEMBERSHIP_DEFAULT_SORT = "-valid_from"

_GROUP_INSTRUCTOR_ASSIGNMENT_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "valid_from": GroupInstructorAssignment.valid_from.asc(),
    "-valid_from": GroupInstructorAssignment.valid_from.desc(),
    "created_at": GroupInstructorAssignment.created_at.asc(),
    "-created_at": GroupInstructorAssignment.created_at.desc(),
}
GROUP_INSTRUCTOR_ASSIGNMENT_DEFAULT_SORT = "-valid_from"


class InvalidSortError(ValueError):
    """`sort` is not one of the whitelisted values (api-contract.md's
    "sorting (whitelist)" convention — never arbitrary/dynamic SQL).
    """


def list_groups_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = GROUP_DEFAULT_SORT,
    status: Optional[str] = None,
) -> tuple[list[Group], int]:
    if sort not in _GROUP_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        group_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if status is not None:
        conditions.append(Group.status == status)

    total = session.execute(
        sa.select(sa.func.count()).select_from(Group).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(Group)
            .where(*conditions)
            .order_by(_GROUP_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_group_memberships_page(
    session: Session,
    *,
    group_id: uuid.UUID,
    page: int,
    page_size: int,
    sort: str = GROUP_MEMBERSHIP_DEFAULT_SORT,
    membership_status: Optional[str] = None,
) -> tuple[list[GroupMembership], int]:
    if sort not in _GROUP_MEMBERSHIP_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [GroupMembership.group_id == group_id]
    if membership_status is not None:
        conditions.append(GroupMembership.membership_status == membership_status)

    total = session.execute(
        sa.select(sa.func.count()).select_from(GroupMembership).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(GroupMembership)
            .where(*conditions)
            .order_by(_GROUP_MEMBERSHIP_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_group_instructor_assignments_page(
    session: Session,
    *,
    group_id: uuid.UUID,
    page: int,
    page_size: int,
    sort: str = GROUP_INSTRUCTOR_ASSIGNMENT_DEFAULT_SORT,
    has_ended: Optional[bool] = None,
) -> tuple[list[GroupInstructorAssignment], int]:
    """people-api.md §16.1/§16.3: `GroupInstructorAssignment` has no status
    field — its "activity" filter is the documented presence/absence of
    `valid_to` (§16.1), not a point-in-time `now()` comparison (that
    formula is §16.2's `is_primary`-overlap invariant, a different,
    unrelated rule). `has_ended=False` -> `valid_to IS NULL`;
    `has_ended=True` -> `valid_to IS NOT NULL`; omitted -> no filter.
    """
    if sort not in _GROUP_INSTRUCTOR_ASSIGNMENT_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [GroupInstructorAssignment.group_id == group_id]
    if has_ended is True:
        conditions.append(GroupInstructorAssignment.valid_to.isnot(None))
    elif has_ended is False:
        conditions.append(GroupInstructorAssignment.valid_to.is_(None))

    total = session.execute(
        sa.select(sa.func.count()).select_from(GroupInstructorAssignment).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(GroupInstructorAssignment)
            .where(*conditions)
            .order_by(_GROUP_INSTRUCTOR_ASSIGNMENT_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


__all__ = [
    "InvalidSortError",
    "GROUP_DEFAULT_SORT",
    "GROUP_MEMBERSHIP_DEFAULT_SORT",
    "GROUP_INSTRUCTOR_ASSIGNMENT_DEFAULT_SORT",
    "list_groups_page",
    "list_group_memberships_page",
    "list_group_instructor_assignments_page",
]
