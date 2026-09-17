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

Issue #62 originally left `children`/`own_events` unresolved for both
Person and ClubMembership (`own_events` still is: neither entity has an
Event relationship). TH-0102 (ADR-0035 §7.3) now requires `children` for
ClubMembership read: a Guardian may read their children's membership data
through the authorized `children`/GuardianRelationship path — resolved
below the same way app.people.guardian_authorization resolves it for
GuardianRelationship itself (an *active* GuardianRelationship from the
requester to the membership's Person). ADR-0035 §3.2 is explicit that a
Guardian gets no such access to Person through People management, so
Person's own `person_visibility_filter` deliberately still fails closed on
`children` — only ClubMembership's resolution changes here.

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

`is_system_admin_person_update_grant` (ADR-0035 §5, TH-0101) is a second,
narrower exception: `birth_date` may be changed only by the canonical
system `admin` role, not merely by *some* role holding an `all`-scope
`person.update` grant — see that function's own docstring for why a bare
scope check is not equivalent to "is admin" here.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authentication.bootstrap import ADMIN_ROLE_CODE
from app.authorization.context import ResourceContext
from app.authorization.service import applicable_assignments, club_boundary_matches, scope_matches
from app.db.groups import Group, GroupInstructorAssignment, GroupMembership
from app.db.identity import ClubMembership, GuardianRelationship, Person, User

_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"
_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _active_guardian_condition(*, guardian_person_id, child_person_id) -> sa.ColumnElement[bool]:
    """True if an *active* GuardianRelationship exists making
    `guardian_person_id` a guardian of `child_person_id` right now.

    Duplicated from app.people.guardian_authorization's identically-named
    helper rather than imported — matching that module's own documented
    convention of duplicating this specific small predicate per module
    instead of sharing it.
    """
    gr = aliased(GuardianRelationship)
    return sa.exists(
        sa.select(gr.id).where(
            gr.guardian_person_id == guardian_person_id,
            gr.child_person_id == child_person_id,
            gr.status == "active",
            _active_interval(gr.valid_from, gr.valid_to),
        )
    )


def own_group_condition_for_person(
    person_id, requester_user_id, *, club_id: uuid.UUID | None = None
) -> sa.ColumnElement[bool]:
    """True if `requester_user_id` has an active `GroupInstructorAssignment`
    for a Group where `person_id` has an active `ClubMembership` with an
    active `GroupMembership` in that Group — the exact chain Issue #62's
    accepted decisions require: `Person -> active ClubMembership ->
    active GroupMembership -> Group -> active GroupInstructorAssignment ->
    requesting User`. Co-membership in the same Group without a matching
    `GroupInstructorAssignment` row is never sufficient.

    `Group` is joined explicitly (not skipped as an implied hop between
    `GroupMembership` and `GroupInstructorAssignment`) so this predicate
    can itself enforce `Group.club_id == ClubMembership.club_id` — the
    authorization boundary must hold even against inconsistent/historical
    data, not rely solely on ADR-0022's write-time integrity checks.

    When `club_id` is given (a club-scoped assignment), the match is
    additionally restricted to that specific Club's ClubMembership —
    required because a club-scoped `own_groups` assignment must not
    reach across into a different Club.

    Public (no leading underscore) and exported so
    app.people.guardian_authorization can reuse this exact chain for
    GuardianRelationship's own `own_groups` resolution (TH-0103, ADR-0035
    §8.4) rather than re-deriving it — unlike this module's small
    single-line helpers (`_active_interval`, `_person_id_for_user`),
    which are duplicated per module by convention, this join is complex
    enough that duplicating it would risk the two copies silently
    diverging.
    """
    cm = aliased(ClubMembership)
    gm = aliased(GroupMembership)
    group = aliased(Group)
    gia = aliased(GroupInstructorAssignment)
    conditions = [
        cm.person_id == person_id,
        cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
        group.club_id == cm.club_id,
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
        .join(group, group.id == gm.group_id)
        .join(gia, gia.group_id == group.id)
        .where(*conditions)
    )


def _own_group_condition_for_membership(
    club_membership_id, requester_user_id
) -> sa.ColumnElement[bool]:
    """Same as `own_group_condition_for_person` but keyed directly by an
    existing `ClubMembership.id` — used when a `ClubMembership` row is
    already loaded (avoids an extra join back through `person_id`).

    Re-checks that the referenced ClubMembership is itself active and
    that the Group found via GroupMembership belongs to that same
    ClubMembership's Club — both required by Issue #62's accepted
    authorization chain, and enforced here directly rather than assumed
    from ADR-0022 write-time integrity alone.
    """
    cm = aliased(ClubMembership)
    gm = aliased(GroupMembership)
    group = aliased(Group)
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(gm.id)
        .join(cm, cm.id == gm.club_membership_id)
        .join(group, group.id == gm.group_id)
        .join(gia, gia.group_id == group.id)
        .where(
            cm.id == club_membership_id,
            cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
            group.club_id == cm.club_id,
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
            scope_predicate = own_group_condition_for_person(
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


def is_system_admin_person_update_grant(session: Session, user_id: uuid.UUID) -> bool:
    """ADR-0035 §5: only the canonical system `admin` role may change
    `birth_date`, including on the admin's own Person. This is a
    role-identity requirement, not merely a scope one: a bare `all`-scope
    `person.update` grant via *any* role (e.g. a custom, non-system role
    an installation happens to seed with that exact grant) is not
    "admin" in the ADR-0035 sense and must not pass this check, even
    though such a grant is otherwise sufficient for ordinary
    `person.update` access to every field except `birth_date`.

    Reuses the same identity `app.authentication.bootstrap` already uses
    to recognize the canonical administrator (`Role.code ==
    ADMIN_ROLE_CODE` *and* `Role.is_system`) — never a bare, ad hoc
    `Role.code == "admin"` string comparison invented in this module —
    and the same `applicable_assignments`/`club_boundary_matches`/
    `scope_matches` building blocks `app.authorization.service.can()`
    itself uses for its scope evaluation, evaluated against an empty
    `ResourceContext` (global reach only, matching `person.create`'s own
    precedent in this module): only a currently-effective, globally
    (`club_id IS NULL`) `all`-scope assignment through that one role
    satisfies it.
    """
    global_all_context = ResourceContext()
    assignments = applicable_assignments(session, user_id, "person.update")
    return any(
        assignment.role.code == ADMIN_ROLE_CODE
        and assignment.role.is_system
        and club_boundary_matches(assignment.club_id, global_all_context.club_id)
        and scope_matches(assignment.scope_type, global_all_context)
        for assignment in assignments
    )


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
    is_child = session.execute(
        sa.select(
            _active_guardian_condition(
                guardian_person_id=requester_person_id, child_person_id=membership.person_id
            )
        )
    ).scalar()
    return ResourceContext(
        club_id=membership.club_id,
        is_self=is_self,
        is_own_group=bool(is_own_group),
        is_child=bool(is_child),
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

    needs_person = any(a.scope_type in ("self", "children") for a in assignments)
    requester_person_id = _person_id_for_user(session, user_id) if needs_person else None

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
        elif assignment.scope_type == "children":
            # ADR-0035 §7.3: a Guardian may read their children's
            # membership data through the authorized `children`/
            # GuardianRelationship path — an *active* GuardianRelationship
            # from the requester to the membership's Person.
            scope_predicate = _active_guardian_condition(
                guardian_person_id=requester_person_id,
                child_person_id=ClubMembership.person_id,
            )
        elif assignment.scope_type == "none":
            scope_predicate = sa.false()
        else:
            continue
        clauses.append(sa.and_(club_boundary, scope_predicate))

    return sa.or_(*clauses) if clauses else sa.false()


__all__ = [
    "person_visibility_filter",
    "is_person_visible",
    "is_system_admin_person_update_grant",
    "build_membership_resource_context",
    "membership_visibility_filter",
    "own_group_condition_for_person",
]
