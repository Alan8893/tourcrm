"""Dependency-injection boundary for the future authorization layer.

The authentication mechanism is ODR-001 and is intentionally still open
(docs/03-architecture/adr/ADR-0008-open-decisions.md). This module exists so
future endpoints have one documented place to depend on for the current
caller's identity/authorization context, instead of each domain Issue
re-inventing that boundary. No endpoint uses this yet, and it performs no
authentication or authorization on its own.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentPrincipal:
    """Placeholder shape for the future authenticated identity context.

    Left intentionally minimal: the real fields (user id, person id, roles/
    permissions, club context, session metadata) depend on the ODR-001
    decision and the authorization ADR that follows it.
    """

    user_id: str


def get_current_principal() -> CurrentPrincipal | None:
    """Not implemented: authentication mechanism is not yet decided
    (ODR-001). Returns None rather than enforcing anything, so no endpoint
    should treat a non-None value as a security guarantee until this is
    replaced with real authentication.
    """
    return None
