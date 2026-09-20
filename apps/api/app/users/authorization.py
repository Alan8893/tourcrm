"""User directory authorization (TH-0107, PO decision recorded in
docs/05-api/users-api.md and docs/02-requirements/roles-and-permissions.md).

`GET /api/v1/users` is gated by its own, narrow permission —
`user.directory.read` — deliberately independent of `person.read`/
`event.read` and their `own_groups` (`GroupInstructorAssignment`-gated)
semantics. This module is the ONLY place that permission's policy is
resolved; nothing here touches `app.people.authorization` or
`app.events.authorization`.

Why a bespoke module instead of the generic `Authorizer`/`ResourceContext`
engine or `person_visibility_filter`: this permission has no meaningful
`own_groups`/`self`/`children`/`own_events` tier — "browsing the
instructor/user directory" has no per-Group or per-Person relationship to
narrow by, only a Club boundary. Re-audited before writing this: no
existing authorization primitive in this codebase already expresses "an
instructor may see other Users/Persons who merely share their Club" —
own_group_condition_for_person (app.people.authorization),
_own_group_condition (app.events.authorization), and group_visibility_filter
(app.groups.authorization) all *require* an active
`GroupInstructorAssignment`; `app.authorization.club_ownership.
user_has_active_club_membership` is a same-club check, but is used
exclusively as a write-side resource-ownership gate, never wired into any
read-side visibility decision. ADR-0035 §11 and roles-and-permissions.md
§11 both explicitly state that Club co-membership or holding the
`instructor` role alone is *not* sufficient grounds for Person access —
this permission is the one, explicitly PO-approved, narrowly-scoped
exception to that rule, valid only for this directory endpoint.

Two separate concerns, kept in two separate functions:
- `requester_has_directory_access`: does the requester hold this
  permission AT ALL right now? (the hard 403 gate — unlike `person.read`'s
  list-endpoint "silently empty" convention, a caller with zero
  `user.directory.read` assignments is denied outright, per PO decision.)
- `directory_reach_filter`: given that they do, which target Persons fall
  within their authorized Club reach? (a SQL predicate, applied inside the
  query — never fetch-then-filter-in-Python.)

Deliberately ignores each assignment's `scope_type` value entirely: only
`club_id` (global when NULL, otherwise that one Club) matters. A typical
`instructor` `UserRoleAssignment` already carries a `club_id` (usually with
`scope_type="own_groups"` for other permissions) — once the `instructor`
role is granted this permission via `RolePermission`, that same,
already-existing assignment is sufficient; no second/duplicate
`UserRoleAssignment` is created or required for this permission.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.authorization.service import applicable_assignments
from app.db.identity import ClubMembership, Person

PERMISSION_CODE = "user.directory.read"

_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"


def requester_has_directory_access(session: Session, *, user_id: uuid.UUID) -> bool:
    """True iff `user_id` holds at least one currently-effective
    `UserRoleAssignment` granting `user.directory.read` — the sole
    permission gate for `GET /users`. The endpoint raises
    `AuthorizationDenied` (-> HTTP 403) when this is False, rather than
    silently returning an empty page, because this permission exists
    specifically to grant or withhold the whole directory capability
    (PO decision), unlike `person.read`'s shared, scope-narrowed list.
    """
    return bool(applicable_assignments(session, user_id, PERMISSION_CODE))


def directory_reach_filter(session: Session, *, user_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """Build the predicate for a User-directory query (`.where(...)`
    referencing `Person.id`), true only for target Persons within the
    requester's authorized Club reach for `user.directory.read`.

    Each qualifying assignment contributes one clause: global reach
    (`club_id IS NULL`) or that one Club's *active* `ClubMembership`.
    Clauses are OR'd — a requester holding more than one assignment (e.g.
    scoped to different Clubs) is authorized for the union of their reach.
    Returns `sa.false()` when the requester holds no qualifying assignment
    at all (fail closed) — callers should have already rejected that case
    via `requester_has_directory_access` before reaching this point, but
    this function stays safe to call standalone regardless.
    """
    assignments = applicable_assignments(session, user_id, PERMISSION_CODE)
    if not assignments:
        return sa.false()

    clauses: list[sa.ColumnElement[bool]] = []
    for assignment in assignments:
        if assignment.club_id is None:
            clauses.append(sa.true())
        else:
            cm = aliased(ClubMembership)
            clauses.append(
                sa.exists(
                    sa.select(cm.id).where(
                        cm.person_id == Person.id,
                        cm.club_id == assignment.club_id,
                        cm.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
                    )
                )
            )
    return sa.or_(*clauses)


__all__ = ["PERMISSION_CODE", "requester_has_directory_access", "directory_reach_filter"]
