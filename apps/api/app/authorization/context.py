"""Scope vocabulary and the explicit resource context authorization evaluates.

Canonical sources: docs/03-architecture/adr/ADR-0013-scope-canonicalization.md,
docs/02-requirements/roles-and-permissions.md §5, docs/05-api/auth-and-authorization.md §13.
"""

import uuid
from dataclasses import dataclass

from app.db.authorization import CANONICAL_SCOPE_TYPES

# ADR-0013: `assigned_events` is not a separate scope, only an alias of
# `own_events`. This is the one alias the ADR defines; anything else
# (including `own_records`, which ADR-0013 explicitly says is not a scope)
# is rejected by normalize_scope_type below.
_SCOPE_ALIASES = {"assigned_events": "own_events"}


class InvalidScopeError(ValueError):
    """Raised by normalize_scope_type for anything outside ADR-0013's vocabulary."""


def normalize_scope_type(value: str) -> str:
    """Normalize a scope value at an input boundary: resolve the one
    documented alias, then require exact membership in ADR-0013's
    vocabulary. Raises InvalidScopeError for anything else (`own_records`,
    typos, or an invented scope) — no new scope is ever introduced here.
    """
    resolved = _SCOPE_ALIASES.get(value, value)
    if resolved not in CANONICAL_SCOPE_TYPES:
        raise InvalidScopeError(f"{value!r} is not a canonical ADR-0013 scope")
    return resolved


@dataclass(frozen=True)
class ResourceContext:
    """Explicit, backend-asserted facts about one resource/operation.

    Every field here must be set by domain policy that has already
    resolved the real relationship from trusted server-side data (e.g. an
    active GuardianRelationship row, a Group responsible-instructor
    assignment) — never inferred from a client-supplied resource ID
    (docs/05-api/auth-and-authorization.md §6 / Issue #29 §6: IDOR and
    privilege escalation via a spoofed ID must not be possible).

    Fields default "false"/unset so an endpoint that only partially
    resolves context fails closed rather than accidentally granting a
    scope it never actually checked.
    """

    # The club the resource belongs to, if any. Required for a club-scoped
    # UserRoleAssignment to match (see app.authorization.service); unknown
    # (None) never matches a club-scoped assignment.
    club_id: uuid.UUID | None = None
    # `self`: the resource is the acting person's own data.
    is_self: bool = False
    # `children`: the resource belongs to a person connected to the actor
    # through an active, verified GuardianRelationship (future domain).
    is_child: bool = False
    # `own_groups`: the resource belongs to a group the actor is
    # responsible for (future Group domain).
    is_own_group: bool = False
    # `own_events`: the actor is explicitly assigned as
    # instructor/leader/authorized staff for the event (future Event domain).
    is_own_event: bool = False
