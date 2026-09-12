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


def test_scope_all_always_matches() -> None:
    assert scope_matches("all", ResourceContext()) is True


def test_scope_none_never_matches() -> None:
    assert scope_matches("none", ResourceContext(is_self=True, is_child=True)) is False


def test_scope_self_requires_is_self() -> None:
    assert scope_matches("self", ResourceContext(is_self=True)) is True
    assert scope_matches("self", ResourceContext(is_self=False)) is False
    assert scope_matches("self", ResourceContext()) is False  # fails closed by default


def test_scope_children_requires_is_child() -> None:
    assert scope_matches("children", ResourceContext(is_child=True)) is True
    assert scope_matches("children", ResourceContext()) is False


def test_scope_own_groups_requires_is_own_group() -> None:
    assert scope_matches("own_groups", ResourceContext(is_own_group=True)) is True
    assert scope_matches("own_groups", ResourceContext()) is False


def test_scope_own_events_requires_is_own_event() -> None:
    assert scope_matches("own_events", ResourceContext(is_own_event=True)) is True
    assert scope_matches("own_events", ResourceContext()) is False


def test_unhandled_scope_type_raises() -> None:
    # Defensive: unreachable via the DB's own CHECK constraint, but this
    # module must not silently allow/deny a scope it doesn't recognize.
    with pytest.raises(ValueError):
        scope_matches("own_records", ResourceContext())


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
