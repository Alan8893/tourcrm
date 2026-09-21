"""Person/ClubMembership list query composition (Issue #62).

Canonical source: Issue #62 §10 (documented filters/sort). Applies
deterministic scope-based authorization directly inside the SQL query —
never fetch-then-filter-in-Python — mirroring app.events.queries exactly.

TH-0114 / people-api.md §4: the People list's single `search` field must
also match a Person's active system role (by canonical code or by ADR-0039
§3's human-readable label), never only their name — and a multi-word query
like "Иванов Инструктор" must match a Person satisfying every word (one
against the name, another against a role), not either field independently.
`search` is therefore split on whitespace into tokens, each token AND'ed
into `conditions` as its own OR-of-(name-fields, role-match) predicate —
this is a strict superset of the previous single-pattern-across-two-fields
behavior, so an existing single-word name search still matches exactly the
same rows as before (plus, now, `middle_name`).

TH-0116 / Issue #150: `club_id` is a *results* filter, independent of the
requester's own authorization reach (`person_visibility_filter`) — both
apply as separate, AND-ed conditions, mirroring exactly how
`app.users.queries.list_users_page`'s own `club_id` filter already works
for the User directory. It requires an *active* `ClubMembership` in that
Club and is used by the Group participant picker (`people-api.md` §4.1)
to search only people eligible to be added to a Group in that Club —
never fetch-all-then-filter-in-Python.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import ClubMembership, Person, User
from app.people.authorization import membership_visibility_filter, person_visibility_filter
from app.role_assignments.person_roles import role_codes_matching_search_term

_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"

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


def _persons_with_active_role_codes(role_codes: list[str]) -> sa.Select:
    now = sa.func.now()
    return (
        sa.select(User.person_id)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            Role.code.in_(role_codes),
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
    )


def _search_token_condition(token: str) -> sa.ColumnElement[bool]:
    """One `search` word: matches by name (last/first/middle) OR, if the
    word also identifies a canonical role (by code or ADR-0039 §3 label),
    by that role. See module docstring for why tokens are AND'ed together
    by the caller rather than combined here."""
    pattern = f"%{token}%"
    name_condition = sa.or_(
        Person.last_name.ilike(pattern),
        Person.first_name.ilike(pattern),
        Person.middle_name.ilike(pattern),
    )
    role_codes = role_codes_matching_search_term(token)
    if not role_codes:
        return name_condition
    return sa.or_(name_condition, Person.id.in_(_persons_with_active_role_codes(role_codes)))


def _has_active_club_membership_condition(club_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """True if `Person.id` has an *active* ClubMembership in `club_id`.

    Duplicated from app.users.queries's identically-named helper rather
    than imported — matching that module's own established convention of
    duplicating this small predicate per module (see also
    app.people.authorization's `_active_guardian_condition`).
    """
    cm = aliased(ClubMembership)
    return sa.exists(
        sa.select(cm.id).where(
            cm.person_id == Person.id,
            cm.club_id == club_id,
            cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )


def list_persons_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = PERSON_DEFAULT_SORT,
    search: Optional[str] = None,
    club_id: Optional[uuid.UUID] = None,
) -> tuple[list[Person], int]:
    if sort not in _PERSON_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        person_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if search:
        conditions.extend(_search_token_condition(token) for token in search.split())
    if club_id is not None:
        conditions.append(_has_active_club_membership_condition(club_id))

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
