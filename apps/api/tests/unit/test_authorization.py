"""Pure-Python/metadata checks for the Issue #19 authorization foundation —
no database required. Constraint-enforcement behavior is covered in
tests/integration/test_authorization.py.
"""

from sqlalchemy.dialects.postgresql import UUID

from app.db.authorization import (
    CANONICAL_SCOPE_TYPES,
    DOCUMENTED_PERMISSION_CODES,
    Permission,
    Role,
    RolePermission,
    UserRoleAssignment,
)


def test_canonical_scope_types_match_adr_0013() -> None:
    # ADR-0013: `assigned_events` is only an alias of `own_events`, and
    # `own_records` is not a canonical scope — neither may appear here.
    assert set(CANONICAL_SCOPE_TYPES) == {
        "all",
        "self",
        "children",
        "own_groups",
        "own_events",
        "none",
    }
    assert "assigned_events" not in CANONICAL_SCOPE_TYPES
    assert "own_records" not in CANONICAL_SCOPE_TYPES


def test_documented_permission_codes_have_no_invented_entries() -> None:
    # roles-and-permissions.md §4's exact catalog — Issue #19 forbids adding
    # permissions beyond what is documented there. `person.create` was
    # added by ADR-0035 §2 / TH-0101.
    assert set(DOCUMENTED_PERMISSION_CODES) == {
        "person.read",
        "person.create",
        "person.update",
        "membership.read",
        "membership.manage",
        "guardian_relationship.read",
        "guardian_relationship.manage",
        "group.read",
        "group.manage",
        "event.read",
        "event.create",
        "event.update",
        "event.cancel",
        "event.manage",
        "attendance.read",
        "attendance.update",
        "trip.read",
        "trip.manage",
        "achievement.read",
        "achievement.award",
        "knowledge.read",
        "knowledge.manage",
        "document.read",
        "document.manage",
        "consent.read",
        "consent.manage",
        "equipment.read",
        "equipment.manage",
        "finance.read",
        "finance.manage",
        "notification.read",
        "notification.manage",
        "audit.read",
        "settings.manage",
        "role.manage",
    }
    assert len(DOCUMENTED_PERMISSION_CODES) == len(set(DOCUMENTED_PERMISSION_CODES))


def test_id_columns_use_uuid_per_adr_0010() -> None:
    for model in (Role, Permission, UserRoleAssignment):
        assert isinstance(model.__table__.c.id.type, UUID)


def test_role_permission_has_no_surrogate_id_column() -> None:
    # database-schema.md §6.3: the primary key is the (role_id, permission_id)
    # pair itself, not a separate surrogate id (see PR discussion re:
    # data-model.md §2.1's general "every entity has an id" statement).
    columns = set(RolePermission.__table__.c.keys())
    assert columns == {"role_id", "permission_id"}
    assert [c.name for c in RolePermission.__table__.primary_key.columns] == [
        "role_id",
        "permission_id",
    ]


def test_foreign_key_columns_match_referenced_primary_key_type() -> None:
    # ADR-0010: "database foreign keys use the same ID type as their
    # referenced primary keys."
    for fk_column in (
        UserRoleAssignment.__table__.c.user_id,
        UserRoleAssignment.__table__.c.role_id,
        UserRoleAssignment.__table__.c.club_id,
        RolePermission.__table__.c.role_id,
        RolePermission.__table__.c.permission_id,
    ):
        assert isinstance(fk_column.type, UUID)
