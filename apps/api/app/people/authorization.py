"""Person/ClubMembership authorization scope resolution (Issue #62).

Canonical sources: docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/03-architecture/adr/ADR-0022-cross-club-ownership-integrity.md,
docs/02-requirements/roles-and-permissions.md, Issue #62 §11-13.

Mirrors app.events.authorization's shape for ClubMembership: single-resource
resolution (`build_membership_resource_context`) for detail/update/status
endpoints, and list-level filtering (`membership_visibility_filter`) that
applies scope directly inside the SQL query — never fetch-then-filter-in-
Python — so an unauthorized row can never leak through pagination/totals/
offsets. Person authorization does not use this ResourceContext-based
shape at all — see below.

Issue #62 only requires `all`/`own_groups`/`self` for Person and
ClubMembership (`children`/`own_events` are not applicable — Guardian
access is Issue #64's concern, and neither entity has an Event
relationship). Both stay at their ResourceContext tri-state default
(`None`, "never resolved") here, which is the correct fail-closed value
per app.authorization.context.ResourceContext's own contract.

`Person` has no `club_id` of its own (Club-neutral: a Person may have
`ClubMembership` rows in more than one Club). Because of this, a single
scalar `ResourceContext.club_id` cannot represent "the" Club of a Person,
and the generic `app.authorization.service.can()`/`club_boundary_matches`
engine (which compares one assignment club against one resource club)
cannot correctly decide a *club-scoped* assignment for Person: per Issue
#62's accepted decisions, a club-scoped `all` assignment must match a
Person who has a `ClubMembership` in that specific Club, and a
club-scoped `own_groups` assignment must match only within that Club —
neither is expressible as a single `club_id` on `ResourceContext`.

For this reason, Person authorization (both the list filter and the
single-resource check) is resolved entirely in this module by iterating
`applicable_assignments` directly and building a per-assignment SQL
predicate (`person_visibility_filter`), the same shape already used for
list-query filtering — never via `Authorizer.check(ResourceContext(...))`
with a single shared context. `is_person_visible` reuses the exact same
predicate for the single-resource case, so the two can never disagree.
`POST /persons` (create) is the one exception: no Person row exists yet
to check a relationship against, so it keeps using the generic
`Authorizer.check(ResourceContext())` — which correctly requires a global
(`club_id IS NULL`) `all` assignment, matching Issue #62's explicit
"Creating a Person has no Club relationship yet and therefore requires
global `all`" decision.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_assignments
from app.db.groups import GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, Person, User

_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"
_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _own_group_condition_for_person(
    person_id, requester_user_id, *, club_id: uuid.UUID | None = None
) -> sa.ColumnElement[bool]:
    """True if `requester_user_id` has an active `GroupInstructorAssignment`
    for a Group where `person_id` has an active `ClubMembership` with an
    active `GroupMembership` in that Group — the exact chain Issue #62's
    accepted decisions require: `Person -> active ClubMembership ->
    active GroupMembership -> Group -> active GroupInstructorAssignment ->
    requesting User`. Co-membership in the same Group without a matching
    `GroupInstructorAssignment` row is never sufficient.

    When `club_id` is given (a club-scoped assignment), the match is
    additionally restricted to that specific Club's ClubMembership —
    required because a club-scoped `own_groups` assignment must not
    reach across into a different Club.
    """
    cm = aliased(ClubMembership)
    gm = aliased(GroupMembership)
    gia = aliased(GroupInstructorAssignment)
    conditions = [
        cm.person_id == person_id,
        cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        gia.user_id == requester_user_id,
        gm.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
        _active_interval(gm.valid_from, gm.valid_to),
        _active_interval(gia.valid_from, gia.valid_to),
    ]
    if club_id is not None:
        conditions.append(cm.club_id == club_id)
    return sa.exists(
        sa.select(cm.id)
        .join(gm, gm.club_membership_id == cm.id)
        .join(gia, gia.group_id == gm.group_id)
        .where(*conditions)
    )


def _own_group_condition_for_membership(
    club_membership_id, requester_user_id
) -> sa.ColumnElement[bool]:
    """Same as `_own_group_condition_for_person` but keyed directly by an
    existing `ClubMembership.id` — used when a `ClubMembership` row is
    already loaded (avoids an extra join back through `person_id`).
    """
    gm = aliased(GroupMembership)
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(gm.id)
        .join(gia, gia.group_id == gm.group_id)
        .where(
            gm.club_membership_id == club_membership_id,
            gia.user_id == requester_user_id,
            gm.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(gm.valid_from, gm.valid_to),
            _active_interval(gia.valid_from, gia.valid_to),
        )
    )


def person_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate for a Person query (`.where(...)` referencing
    `Person.id`), true only for Persons the acting user is authorized to
    see under `permission_code`. Used both for the list endpoint and (via
    `is_person_visible`) for a single-Person check — see module docstring
    for why Person authorization cannot go through the generic
    `Authorizer`/`ResourceContext` engine.
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    if not assignments:
        return sa.false()

    needs_self = any(a.scope_type == "self" for a in assignments)
    requester_person_id = _person_id_for_user(session, user_id) if needs_self else None

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        if assignment.scope_type == "all":
            if assignment.club_id is None:
                scope_predicate: sa.ColumnElement[bool] = sa.true()
            else:
                # Issue #62 accepted decision: a club-scoped `all`
                # assignment may access a Person only when that Person
                # has a ClubMembership in that specific Club.
                cm = aliased(ClubMembership)
                scope_predicate = sa.exists(
                    sa.select(cm.id).where(
                        cm.person_id == Person.id, cm.club_id == assignment.club_id
                    )
                )
        elif assignment.scope_type == "own_groups":
            scope_predicate = _own_group_condition_for_person(
                Person.id, user_id, club_id=assignment.club_id
            )
        elif assignment.scope_type == "self":
            # Issue #62 accepted decision: `self` is identity-level and
            # independent of Club — the assignment's own club_id (if any)
            # is not a boundary here.
            scope_predicate = Person.id == requester_person_id
        elif assignment.scope_type == "none":
            scope_predicate = sa.false()
        else:
            # children/own_events (or any future scope): not applicable to
            # Person in this Issue; fail closed rather than match.
            continue
        clauses.append(scope_predicate)

    return sa.or_(*clauses) if clauses else sa.false()


def is_person_visible(
    session: Session, *, person_id: uuid.UUID, user_id: uuid.UUID, permission_code: str
) -> bool:
    """Single-Person authorization check, built from the exact same
    per-assignment predicate as `person_visibility_filter` so the list and
    detail endpoints can never disagree.
    """
    predicate = person_visibility_filter(session, user_id=user_id, permission_code=permission_code)
    stmt = sa.select(sa.exists(sa.select(Person.id).where(Person.id == person_id, predicate)))
    return bool(session.execute(stmt).scalar())


def build_membership_resource_context(
    session: Session, *, membership: ClubMembership, requester_user_id: uuid.UUID
) -> ResourceContext:
    """Resolve the ResourceContext for one already-loaded ClubMembership.

    Unlike Person, `ClubMembership.club_id` is a real column, so club-scoped
    assignments work normally here (no Club-neutral caveat).
    """
    requester_person_id = _person_id_for_user(session, requester_user_id)
    is_self = membership.person_id == requester_person_id
    is_own_group = session.execute(
        sa.select(_own_group_condition_for_membership(membership.id, requester_user_id))
    ).scalar()
    return ResourceContext(
        club_id=membership.club_id, is_self=is_self, is_own_group=bool(is_own_group)
    )


def membership_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate for a ClubMembership list query (`.where(...)`
    referencing `ClubMembership.id`/`ClubMembership.club_id`).
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    if not assignments:
        return sa.false()

    needs_self = any(a.scope_type == "self" for a in assignments)
    requester_person_id = _person_id_for_user(session, user_id) if needs_self else None

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        club_boundary: sa.ColumnElement[bool] = (
            sa.true()
            if assignment.club_id is None
            else ClubMembership.club_id == assignment.club_id
        )
        if assignment.scope_type == "all":
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif assignment.scope_type == "own_groups":
            scope_predicate = _own_group_condition_for_membership(ClubMembership.id, user_id)
        elif assignment.scope_type == "self":
            scope_predicate = ClubMembership.person_id == requester_person_id
        elif assignment.scope_type == "none":
            scope_predicate = sa.false()
        else:
            continue
        clauses.append(sa.and_(club_boundary, scope_predicate))

    return sa.or_(*clauses) if clauses else sa.false()


__all__ = [
    "person_visibility_filter",
    "is_person_visible",
    "build_membership_resource_context",
    "membership_visibility_filter",
]
