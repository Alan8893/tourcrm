"""Person/ClubMembership list query composition (Issue #62).

Canonical source: Issue #62 §10 (documented filters/sort). Applies
deterministic scope-based authorization directly inside the SQL query —
never fetch-then-filter-in-Python — mirroring app.events.queries exactly.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.identity import ClubMembership, Person
from app.people.authorization import membership_visibility_filter, person_visibility_filter

_PERSON_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "last_name": Person.last_name.asc(),
    "-last_name": Person.last_name.desc(),
    "created_at": Person.created_at.asc(),
    "-created_at": Person.created_at.desc(),
}
PERSON_DEFAULT_SORT = "last_name"

_MEMBERSHIP_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "joined_at": ClubMembership.joined_at.asc(),
    "-joined_at": ClubMembership.joined_at.desc(),
}
MEMBERSHIP_DEFAULT_SORT = "-joined_at"


class InvalidSortError(ValueError):
    """`sort` is not one of the whitelisted values (api-contract.md's
    "sorting (whitelist)" convention — never arbitrary/dynamic SQL).
    """


def list_persons_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = PERSON_DEFAULT_SORT,
    search: Optional[str] = None,
) -> tuple[list[Person], int]:
    if sort not in _PERSON_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        person_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if search:
        pattern = f"%{search}%"
        conditions.append(
            sa.or_(Person.last_name.ilike(pattern), Person.first_name.ilike(pattern))
        )

    total = session.execute(
        sa.select(sa.func.count()).select_from(Person).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(Person)
            .where(*conditions)
            .order_by(_PERSON_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_memberships_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = MEMBERSHIP_DEFAULT_SORT,
    status: Optional[str] = None,
    membership_type: Optional[str] = None,
    person_id: Optional[uuid.UUID] = None,
    club_id: Optional[uuid.UUID] = None,
    joined_after: Optional[datetime] = None,
    joined_before: Optional[datetime] = None,
    left_after: Optional[datetime] = None,
    left_before: Optional[datetime] = None,
) -> tuple[list[ClubMembership], int]:
    if sort not in _MEMBERSHIP_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        membership_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if status is not None:
        conditions.append(ClubMembership.status == status)
    if membership_type is not None:
        conditions.append(ClubMembership.membership_type == membership_type)
    if person_id is not None:
        conditions.append(ClubMembership.person_id == person_id)
    if club_id is not None:
        conditions.append(ClubMembership.club_id == club_id)
    if joined_after is not None:
        conditions.append(ClubMembership.joined_at >= joined_after)
    if joined_before is not None:
        conditions.append(ClubMembership.joined_at <= joined_before)
    if left_after is not None:
        conditions.append(ClubMembership.left_at >= left_after)
    if left_before is not None:
        conditions.append(ClubMembership.left_at <= left_before)

    total = session.execute(
        sa.select(sa.func.count()).select_from(ClubMembership).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(ClubMembership)
            .where(*conditions)
            .order_by(_MEMBERSHIP_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


__all__ = [
    "InvalidSortError",
    "PERSON_DEFAULT_SORT",
    "MEMBERSHIP_DEFAULT_SORT",
    "list_persons_page",
    "list_memberships_page",
]
