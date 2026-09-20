"""User directory list query composition (TH-0107).

Backs `GET /api/v1/users` — a safe, operational directory used by callers
(initially the Calendar instructor filter) to pick a User by name, never a
full admin User Management API. See docs/05-api/users-api.md for the full
contract.

Authorization deliberately reuses `app.people.authorization.
person_visibility_filter` verbatim rather than inventing a parallel model:
`User` is 1:1 with `Person` (`User.person_id`, unique), and the directory
only ever displays Person-derived names, so Person's existing `person.read`
scope policy (all/own_groups/self/none) is the correct policy to reuse.
This also means an authenticated caller with no applicable `person.read`
assignment gets an empty page (never a 403) — the exact same convention
`app.people.queries.list_persons_page` already established for `GET
/persons`, not a new one invented here.

`club_id`/`role`/`status` are *results filters*, independent of the
requester's own authorization reach (`person_visibility_filter`) — both
apply as separate, AND-ed conditions. In particular the `club_id` filter
requires an *active* ClubMembership (task requirement), which is stricter
than `person_visibility_filter`'s own club-scoped `all` predicate (an
authorization-reach check that intentionally does not look at membership
status) — the two never conflict because they answer different questions.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.db.authorization import BASELINE_ROLE_CODES, Role, UserRoleAssignment
from app.db.identity import ClubMembership, Person, User
from app.people.authorization import person_visibility_filter

# Same 6-value vocabulary as the `ck_users_status_valid` CHECK constraint
# (app.db.identity.User) — whitelisted here rather than trusted from the
# client, matching api-conventions.md §10's "whitelist sortable/filterable
# fields" rule.
VALID_USER_STATUSES = ("pending", "active", "locked", "suspended", "disabled", "archived")

_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"


class InvalidRoleError(ValueError):
    """`role` is not one of the canonical baseline role codes."""


class InvalidUserStatusError(ValueError):
    """`status` is not one of `VALID_USER_STATUSES`."""


def _active_interval(valid_from, valid_to) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _has_effective_role_condition(role_code: str) -> sa.ColumnElement[bool]:
    """True if `User.id` currently holds an effective UserRoleAssignment
    for `role_code` — independent of scope/club (this is a *result*
    filter describing what the target user currently is, not an
    authorization check on the requester).
    """
    ura = aliased(UserRoleAssignment)
    role = aliased(Role)
    return sa.exists(
        sa.select(ura.id)
        .join(role, role.id == ura.role_id)
        .where(
            ura.user_id == User.id,
            role.code == role_code,
            _active_interval(ura.valid_from, ura.valid_to),
        )
    )


def _has_active_club_membership_condition(club_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """True if `Person.id` has an *active* ClubMembership in `club_id`.

    Deliberately does not walk through GroupMembership/
    GroupInstructorAssignment: club-level directory inclusion is a
    ClubMembership fact, not a Group-responsibility one (task §7 — do not
    conflate the two).
    """
    cm = aliased(ClubMembership)
    return sa.exists(
        sa.select(cm.id).where(
            cm.person_id == Person.id,
            cm.club_id == club_id,
            cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
    )


def list_users_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    club_id: Optional[uuid.UUID] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[User], int]:
    if role is not None and role not in BASELINE_ROLE_CODES:
        raise InvalidRoleError(role)
    if status is not None and status not in VALID_USER_STATUSES:
        raise InvalidUserStatusError(status)

    conditions: list[sa.ColumnElement[bool]] = [
        person_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    ]
    if search:
        pattern = f"%{search}%"
        conditions.append(
            sa.or_(
                Person.last_name.ilike(pattern),
                Person.first_name.ilike(pattern),
                Person.middle_name.ilike(pattern),
            )
        )
    if club_id is not None:
        conditions.append(_has_active_club_membership_condition(club_id))
    if role is not None:
        conditions.append(_has_effective_role_condition(role))
    if status is not None:
        conditions.append(User.status == status)

    total = session.execute(
        sa.select(sa.func.count())
        .select_from(User)
        .join(Person, Person.id == User.person_id)
        .where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(User)
            .join(Person, Person.id == User.person_id)
            .where(*conditions)
            .order_by(Person.last_name.asc(), Person.first_name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


__all__ = [
    "InvalidRoleError",
    "InvalidUserStatusError",
    "VALID_USER_STATUSES",
    "list_users_page",
]
