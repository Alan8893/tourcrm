"""Person/ClubMembership authorization scope resolution (Issue #62).

Canonical sources: docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/03-architecture/adr/ADR-0022-cross-club-ownership-integrity.md,
docs/02-requirements/roles-and-permissions.md, Issue #62 §11-13.

Mirrors app.events.authorization's shape: single-resource resolution
(`build_person_resource_context`/`build_membership_resource_context`) for
detail/update/status/archive endpoints, and list-level filtering
(`person_visibility_filter`/`membership_visibility_filter`) that applies
scope directly inside the SQL query — never fetch-then-filter-in-Python —
so an unauthorized row can never leak through pagination/totals/offsets.

Issue #62 only requires `all`/`own_groups`/`self` for Person and
ClubMembership (`children`/`own_events` are not applicable — Guardian
access is Issue #64's concern, and neither entity has an Event
relationship). Both stay at their ResourceContext tri-state default
(`None`, "never resolved") here, which is the correct fail-closed value
per app.authorization.context.ResourceContext's own contract.

`Person` has no `club_id` of its own (Club-neutral: a Person may have
`ClubMembership` rows in more than one Club). `ResourceContext.club_id`
is therefore always `None` for a Person resource — deliberately, not an
oversight. This has a real, documented consequence: `club_boundary_matches`
(app.authorization.service) treats a club-scoped assignment as never
matching a resource whose club is unresolved, so a *club-scoped* `all`/
`person.read`/`person.update` assignment can never see any Person through
this module; only a global (`club_id IS NULL`) assignment can. `own_groups`
is unaffected by this (see `_own_group_condition_for_person` below): the
Club consistency it needs is already guaranteed by the existing
Group/ClubMembership cross-Club invariants (ADR-0022) at the point those
rows were written, so no separate club match is needed here. This
asymmetry (Person has no usable club-scoped `all`, ClubMembership does)
is an accepted, explicitly flagged consequence of Person's Club-neutral
persistence model (ADR-0017) — see the Issue #62 implementation report.
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


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _own_group_condition_for_person(person_id, requester_user_id) -> sa.ColumnElement[bool]:
    """True if `requester_user_id` has an active `GroupInstructorAssignment`
    for a Group where `person_id` has an active `GroupMembership` (via its
    `ClubMembership`) — reusing the same relationship chain
    app.groups.service already establishes and enforces (ADR-0022), not a
    new one.
    """
    cm = aliased(ClubMembership)
    gm = aliased(GroupMembership)
    gia = aliased(GroupInstructorAssignment)
    return sa.exists(
        sa.select(cm.id)
        .join(gm, gm.club_membership_id == cm.id)
        .join(gia, gia.group_id == gm.group_id)
        .where(
            cm.person_id == person_id,
            gia.user_id == requester_user_id,
            gm.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
            _active_interval(gm.valid_from, gm.valid_to),
            _active_interval(gia.valid_from, gia.valid_to),
        )
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


def build_person_resource_context(
    session: Session, *, person_id: uuid.UUID, requester_user_id: uuid.UUID
) -> ResourceContext:
    """Resolve the ResourceContext for one Person against the acting user.

    `club_id` is always `None` (see module docstring). `is_child`/
    `is_own_event` stay unresolved (`None`) — not applicable to Person.
    """
    requester_person_id = _person_id_for_user(session, requester_user_id)
    is_self = person_id == requester_person_id
    is_own_group = session.execute(
        sa.select(_own_group_condition_for_person(person_id, requester_user_id))
    ).scalar()
    return ResourceContext(club_id=None, is_self=is_self, is_own_group=bool(is_own_group))


def person_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate for a Person list query (`.where(...)`
    referencing `Person.id`), true only for Persons the acting user is
    authorized to see under `permission_code`.
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    if not assignments:
        return sa.false()

    needs_self = any(a.scope_type == "self" for a in assignments)
    requester_person_id = _person_id_for_user(session, user_id) if needs_self else None

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        if assignment.scope_type == "all":
            # Person is Club-neutral: a club-scoped `all` assignment never
            # matches any Person (see module docstring) — only a global
            # assignment (club_id IS NULL) does.
            if assignment.club_id is not None:
                continue
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif assignment.scope_type == "own_groups":
            if assignment.club_id is not None:
                # Same Club-neutral reasoning: no club to boundary-match
                # against for a club-scoped assignment. own_groups already
                # carries its own Club consistency via the Group/
                # ClubMembership chain, but that does not by itself
                # satisfy an *assignment*-level club restriction, so a
                # club-scoped own_groups assignment is excluded here too,
                # matching the fail-closed default for an unresolved club.
                continue
            scope_predicate = _own_group_condition_for_person(Person.id, user_id)
        elif assignment.scope_type == "self":
            if assignment.club_id is not None:
                continue
            scope_predicate = Person.id == requester_person_id
        elif assignment.scope_type == "none":
            scope_predicate = sa.false()
        else:
            # children/own_events (or any future scope): not applicable to
            # Person in this Issue; fail closed rather than match.
            continue
        clauses.append(scope_predicate)

    return sa.or_(*clauses) if clauses else sa.false()


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
    "build_person_resource_context",
    "person_visibility_filter",
    "build_membership_resource_context",
    "membership_visibility_filter",
]
