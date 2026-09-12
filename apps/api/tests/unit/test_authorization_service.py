"""Pure-Python unit tests for the Issue #29 authorization engine's scope
logic — no database, no HTTP. `can()`/`Authorizer` (which query real
RolePermission/UserRoleAssignment rows) are covered in
tests/integration/test_authorization_service.py; HTTP enforcement in
tests/integration/test_authorization_enforcement.py.
"""

import uuid

import pytest

from app.authorization.context import InvalidScopeError, ResourceContext, normalize_scope_type
from app.authorization.service import club_boundary_matches, scope_matches

# --- normalize_scope_type ------------------------------------------------


def test_assigned_events_is_normalized_to_own_events() -> None:
    assert normalize_scope_type("assigned_events") == "own_events"


@pytest.mark.parametrize(
    "scope_type", ["all", "self", "children", "own_groups", "own_events", "none"]
)
def test_canonical_scopes_pass_through_unchanged(scope_type: str) -> None:
    assert normalize_scope_type(scope_type) == scope_type


def test_own_records_is_rejected() -> None:
    with pytest.raises(InvalidScopeError):
        normalize_scope_type("own_records")


@pytest.mark.parametrize("scope_type", ["superadmin", "own_club", "", "ALL"])
def test_unknown_scope_is_rejected(scope_type: str) -> None:
    with pytest.raises(InvalidScopeError):
        normalize_scope_type(scope_type)


# --- scope_matches --------------------------------------------------------
#
# ResourceContext's relationship fields (is_self/is_child/is_own_group/
# is_own_event) are tri-state, not bool: True (checked, holds), False
# (checked, does not hold) and None/unresolved (never checked at all) are
# three distinct states, and only True may match. Each scope below is
# tested against all three explicitly so "checked and absent" and "never
# checked" can never be confused with each other or with "confirmed".

_RELATIONSHIP_SCOPES = {
    "self": "is_self",
    "children": "is_child",
    "own_groups": "is_own_group",
    "own_events": "is_own_event",
}


def test_scope_all_always_matches() -> None:
    assert scope_matches("all", ResourceContext()) is True


def test_scope_none_never_matches_even_with_every_relationship_confirmed() -> None:
    context = ResourceContext(is_self=True, is_child=True, is_own_group=True, is_own_event=True)
    assert scope_matches("none", context) is False


@pytest.mark.parametrize("scope_type,field", _RELATIONSHIP_SCOPES.items())
def test_relationship_scope_allows_only_when_explicitly_confirmed_true(
    scope_type: str, field: str
) -> None:
    assert scope_matches(scope_type, ResourceContext(**{field: True})) is True


@pytest.mark.parametrize("scope_type,field", _RELATIONSHIP_SCOPES.items())
def test_relationship_scope_denies_when_explicitly_confirmed_false(
    scope_type: str, field: str
) -> None:
    assert scope_matches(scope_type, ResourceContext(**{field: False})) is False


@pytest.mark.parametrize("scope_type", _RELATIONSHIP_SCOPES.keys())
def test_relationship_scope_denies_when_unresolved(scope_type: str) -> None:
    # The field is left at its default (None/unresolved) — the endpoint
    # never checked this relationship at all. This must deny exactly like
    # an explicit False, never like an explicit True.
    assert scope_matches(scope_type, ResourceContext()) is False


@pytest.mark.parametrize("scope_type", _RELATIONSHIP_SCOPES.keys())
def test_relationship_scope_unresolved_and_false_deny_identically(scope_type: str) -> None:
    field = _RELATIONSHIP_SCOPES[scope_type]
    unresolved = scope_matches(scope_type, ResourceContext())
    confirmed_false = scope_matches(scope_type, ResourceContext(**{field: False}))
    assert unresolved is False
    assert confirmed_false is False


def test_unhandled_scope_type_raises() -> None:
    # Defensive: unreachable via the DB's own CHECK constraint, but this
    # module must not silently allow/deny a scope it doesn't recognize.
    with pytest.raises(ValueError):
        scope_matches("own_records", ResourceContext())


# --- Regression: unresolved context must never grant access ---------------


def test_endpoint_that_forgets_to_resolve_context_cannot_gain_access() -> None:
    """A future endpoint that builds a ResourceContext but forgets to set
    one of the relationship fields (e.g. only sets club_id) must still be
    denied for every relationship-scoped permission, not accidentally
    allowed because the unset field defaulted to something falsy-but-true-like.
    """
    partially_resolved = ResourceContext(club_id=uuid.uuid4())
    for scope_type in _RELATIONSHIP_SCOPES:
        assert scope_matches(scope_type, partially_resolved) is False


# --- club_boundary_matches -------------------------------------------------


def test_global_assignment_matches_any_club() -> None:
    assert club_boundary_matches(None, uuid.uuid4()) is True
    assert club_boundary_matches(None, None) is True


def test_club_scoped_assignment_matches_only_its_own_club() -> None:
    club_a = uuid.uuid4()
    club_b = uuid.uuid4()
    assert club_boundary_matches(club_a, club_a) is True
    assert club_boundary_matches(club_a, club_b) is False


def test_club_scoped_assignment_fails_closed_on_unknown_resource_club() -> None:
    # A resource whose club hasn't been resolved by domain policy must not
    # be treated as "same club" by default.
    assert club_boundary_matches(uuid.uuid4(), None) is False
