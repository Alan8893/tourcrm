"""GuardianRelationship authorization scope resolution (Issue #64).

Canonical sources: docs/03-architecture/adr/ADR-0023-event-relationships-
and-guardian-persistence.md §3/§5, docs/03-architecture/adr/ADR-0025-
people-membership-api-decisions.md §2/§3, Issue #64 §12-13.

`GuardianRelationship` is Club-neutral (no `club_id`, ADR-0023 §3) — the
same structural situation as `Person` (see app.people.authorization's
module docstring). Unlike Person, Issue #64 does not define any
club-scoped-`all`-via-membership override for this entity (Issue #62's
analogous override for Person was an explicit, separate PO decision that
Issue #64 never restates for GuardianRelationship). This module therefore
does NOT reinvent that override: `ResourceContext.club_id` stays `None`
always, so per the existing generic `app.authorization.service.
club_boundary_matches` rule, a club-scoped assignment of any scope_type
never matches a GuardianRelationship — only a global (`club_id IS NULL`)
assignment can. This is the plain, existing fail-closed default already
documented for an unresolved-club resource, applied here without any
GuardianRelationship-specific carve-out.

Two canonical scopes apply, resolved via ADR-0023 §5's relationship
sources:

- `self`: the requester's own Person is the `child_person_id` of the
  relationship in question (`self` = "this relationship is about me, the
  child") — used by `GET /persons/{person_id}/guardian-relationships`
  (`person_id == self`) and by the create endpoint.
- `children`: the requester holds an *active* GuardianRelationship to the
  same `child_person_id` (`children` = "I am an active guardian of this
  child") — resolved via `_active_guardian_condition` below, reused
  unchanged for `/me/children` (Issue #64 §12: "self also applies to
  /me/children").

`own_groups`/`own_events` are not applicable (no Group/Event relationship
exists for this entity) and fail closed, matching Person's treatment of
inapplicable scopes.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_assignments
from app.db.identity import GuardianRelationship, Person, User


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    # Duplicated from app.people.authorization/app.events.authorization
    # rather than imported: matches this codebase's existing convention
    # of duplicating this specific small helper per module rather than
    # sharing it (see app.people.authorization's own module docstring).
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _active_guardian_condition(*, guardian_person_id, child_person_id) -> sa.ColumnElement[bool]:
    """True if an *active* GuardianRelationship exists making
    `guardian_person_id` a guardian of `child_person_id` right now.

    "Active" here means the same read-time-derived condition as
    app.people.guardian_lifecycle.effective_status: stored
    `status = 'active'` AND the current time falls inside
    `[valid_from, valid_to)` — reusing the same "active interval" pattern
    already used for ClubMembership/GroupMembership/
    GroupInstructorAssignment rather than inventing a second one.
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


def build_guardian_relationship_resource_context(
    session: Session, *, relationship: GuardianRelationship, requester_user_id: uuid.UUID
) -> ResourceContext:
    """Resolve the ResourceContext for one already-loaded
    GuardianRelationship against the acting user. `club_id` stays `None`
    (see module docstring).
    """
    requester_person_id = _person_id_for_user(session, requester_user_id)
    is_self = relationship.child_person_id == requester_person_id
    is_child = session.execute(
        sa.select(
            _active_guardian_condition(
                guardian_person_id=requester_person_id,
                child_person_id=relationship.child_person_id,
            )
        )
    ).scalar()
    return ResourceContext(club_id=None, is_self=is_self, is_child=bool(is_child))


def build_guardian_relationship_create_context(
    session: Session, *, child_person_id: uuid.UUID, requester_user_id: uuid.UUID
) -> ResourceContext:
    """Resolve the ResourceContext used to authorize *creating* a new
    GuardianRelationship for `child_person_id` — no relationship row
    exists yet, so `is_self`/`is_child` are computed directly against the
    prospective child rather than a loaded row. `self` matches when the
    requester themselves is `child_person_id`; `children` matches when
    the requester already holds an active GuardianRelationship to that
    same child (e.g. a parent adding a co-guardian) — the same relationship
    sources `build_guardian_relationship_resource_context` uses, applied
    consistently rather than inventing separate create-time semantics.
    """
    requester_person_id = _person_id_for_user(session, requester_user_id)
    is_self = child_person_id == requester_person_id
    is_child = session.execute(
        sa.select(
            _active_guardian_condition(
                guardian_person_id=requester_person_id, child_person_id=child_person_id
            )
        )
    ).scalar()
    return ResourceContext(club_id=None, is_self=is_self, is_child=bool(is_child))


def guardian_relationship_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Build the predicate for a GuardianRelationship list query
    (`.where(...)` referencing `GuardianRelationship.guardian_person_id`/
    `.child_person_id`), true only for rows the acting user is authorized
    to see under `permission_code`. Used by
    `GET /persons/{person_id}/guardian-relationships`.
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    if not assignments:
        return sa.false()

    needs_person = any(a.scope_type in ("self", "children") for a in assignments)
    requester_person_id = _person_id_for_user(session, user_id) if needs_person else None

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        if assignment.club_id is not None:
            # GuardianRelationship is Club-neutral: no club-scoped
            # override is defined for it (see module docstring) — a
            # club-scoped assignment never matches, of any scope_type.
            continue
        if assignment.scope_type == "all":
            scope_predicate: sa.ColumnElement[bool] = sa.true()
        elif assignment.scope_type == "self":
            scope_predicate = GuardianRelationship.child_person_id == requester_person_id
        elif assignment.scope_type == "children":
            scope_predicate = _active_guardian_condition(
                guardian_person_id=requester_person_id,
                child_person_id=GuardianRelationship.child_person_id,
            )
        elif assignment.scope_type == "none":
            scope_predicate = sa.false()
        else:
            # own_groups/own_events: not applicable to GuardianRelationship.
            continue
        clauses.append(scope_predicate)

    return sa.or_(*clauses) if clauses else sa.false()


def children_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    """Predicate for `GET /me/children` (`.where(...)` referencing
    `Person.id`): true only for Persons the authenticated user has an
    *active* GuardianRelationship to, gated by the same
    `guardian_relationship.read` permission + scope as the other guardian
    endpoints (Issue #64 §11 lists `/me/children` among the read
    endpoints). `self`/`children`/`all` (global only) all resolve to the
    same underlying relationship source here — "my own children" — since
    this endpoint is inherently about the requester's own guardian
    relationships; a club-scoped assignment never matches, same as
    `guardian_relationship_visibility_filter`.
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    if not assignments:
        return sa.false()

    requester_person_id = _person_id_for_user(session, user_id)

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        if assignment.club_id is not None:
            continue
        if assignment.scope_type == "none":
            continue
        if assignment.scope_type not in ("all", "self", "children"):
            continue
        clauses.append(
            _active_guardian_condition(
                guardian_person_id=requester_person_id, child_person_id=Person.id
            )
        )

    return sa.or_(*clauses) if clauses else sa.false()


__all__ = [
    "build_guardian_relationship_resource_context",
    "build_guardian_relationship_create_context",
    "guardian_relationship_visibility_filter",
    "children_visibility_filter",
]
