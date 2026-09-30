"""News reader visibility and Administrator recognition (TH-0120 /
Issue #227; docs/04-ux/news.md §2/§7/§9).

The backend is the only authority on who sees a News item: every list and
detail read applies `news_visibility_filter` *inside* the SQL query (never
fetch-then-filter in Python), so an invisible row can never leak through
pagination totals, and a direct-ID read of an invisible News is answered
exactly like a nonexistent one (404).

Administrator (news.md §7/§9): recognized exactly as the existing
Administrator-only workflows recognize it — the canonical system `admin`
Role through a currently-effective assignment reaching the Club
(app.exports.authorization.is_club_administrator). No new permission is
introduced (news.md §9, Issue #227 "Permissions"). An Administrator sees
every News of the Club in every status.

Every other reader sees only `published` News of the Club, and only when
its audience matches (news.md §2):

- `club`: the reader holds a currently-effective assignment of one of
  the four canonical application roles (admin / instructor / member /
  guardian) reaching the Club;
- `groups`, resolved from the existing relationships only — never from a
  role alone and never from a parallel audience model:
  * Member: the reader's own Person has an active ClubMembership with an
    active, currently-valid GroupMembership in a selected Group;
  * Guardian: the reader's Person has an active, currently-valid
    GuardianRelationship to a child whose active ClubMembership has an
    active, currently-valid GroupMembership in a selected Group (the same
    "accessible child" condition as `GET /me/children`);
  * Instructor: the reader's User has a currently-valid
    GroupInstructorAssignment for a selected Group (ADR-0021 §3 — the only
    source of truth for Instructor <-> Group).

"Active"/"currently valid" uses the codebase-wide interval convention
`valid_from <= now() AND (valid_to IS NULL OR now() < valid_to)`;
ClubMembership uses `status = 'active'` alone (app.events.authorization's
documented precedent).
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.db.authorization import BASELINE_ROLE_CODES, Role, UserRoleAssignment
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, GuardianRelationship, User
from app.db.news import News, NewsGroupTarget
from app.exports.authorization import is_club_administrator
from app.news.vocabulary import AUDIENCE_CLUB, AUDIENCE_GROUPS, PUBLISHED

_ACTIVE = "active"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    # Duplicated per module by this codebase's convention (see
    # app.people.authorization's module docstring).
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def is_news_administrator(session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID) -> bool:
    return is_club_administrator(session, user_id=user_id, club_id=club_id)


def _holds_club_reader_role(session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID) -> bool:
    now = sa.func.now()
    stmt = (
        sa.select(UserRoleAssignment.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            UserRoleAssignment.user_id == user_id,
            Role.code.in_(BASELINE_ROLE_CODES),
            Role.is_system.is_(True),
            sa.or_(UserRoleAssignment.club_id.is_(None), UserRoleAssignment.club_id == club_id),
            UserRoleAssignment.valid_from <= now,
            sa.or_(UserRoleAssignment.valid_to.is_(None), now < UserRoleAssignment.valid_to),
        )
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def _member_of_group(person_id: Any, group_id: Any) -> sa.ColumnElement[bool]:
    """`person_id` has an active ClubMembership with an active, currently
    valid GroupMembership in `group_id` (and the Group belongs to that
    ClubMembership's Club — enforced here, not assumed)."""
    cm = aliased(ClubMembership)
    gm = aliased(GroupMembership)
    group = aliased(Group)
    return sa.exists(
        sa.select(gm.id)
        .join(cm, cm.id == gm.club_membership_id)
        .join(group, group.id == gm.group_id)
        .where(
            gm.group_id == group_id,
            cm.person_id == person_id,
            cm.status == _ACTIVE,
            group.club_id == cm.club_id,
            gm.membership_status == _ACTIVE,
            _active_interval(gm.valid_from, gm.valid_to),
        )
    )


def _guardian_child_in_group(guardian_person_id: Any, group_id: Any) -> sa.ColumnElement[bool]:
    gr = aliased(GuardianRelationship)
    return sa.exists(
        sa.select(gr.id).where(
            gr.guardian_person_id == guardian_person_id,
            gr.status == _ACTIVE,
            _active_interval(gr.valid_from, gr.valid_to),
            _member_of_group(gr.child_person_id, group_id),
        )
    )


def _instructor_of_group(user_id: Any, group_id: Any) -> sa.ColumnElement[bool]:
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(gia.id).where(
            gia.group_id == group_id,
            gia.user_id == user_id,
            _active_interval(gia.valid_from, gia.valid_to),
        )
    )


def news_visibility_filter(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> sa.ColumnElement[bool]:
    """Predicate (correlated against `News`) true only for News rows the
    acting user may read. See module docstring for the rules."""
    club_boundary = News.club_id == club_id
    if is_news_administrator(session, user_id=user_id, club_id=club_id):
        return club_boundary

    person_id = _person_id_for_user(session, user_id)
    target = aliased(NewsGroupTarget)
    group_audience = sa.and_(
        News.audience_type == AUDIENCE_GROUPS,
        sa.exists(
            sa.select(target.id).where(
                target.news_id == News.id,
                sa.or_(
                    _member_of_group(person_id, target.group_id),
                    _guardian_child_in_group(person_id, target.group_id),
                    _instructor_of_group(user_id, target.group_id),
                ),
            )
        ),
    )
    audience_clauses: list[sa.ColumnElement[bool]] = [group_audience]
    if _holds_club_reader_role(session, user_id=user_id, club_id=club_id):
        audience_clauses.append(News.audience_type == AUDIENCE_CLUB)

    return sa.and_(club_boundary, News.status == PUBLISHED, sa.or_(*audience_clauses))


__all__ = ["is_news_administrator", "news_visibility_filter"]
