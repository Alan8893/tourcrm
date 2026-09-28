"""permission-level scopes (AUTH-2A)

Revision ID: 20e1297d4e1a
Revises: 4b1f0c2d9a7e
Create Date: 2026-09-28 18:00:00.000000

AUTH-2A moves authorization scope from `UserRoleAssignment` to each
permission grant: `UserRoleAssignment -> RolePermission ->
RolePermissionScope`. PO decisions implemented here:

1. `role_permissions` gets a surrogate `id` primary key so a scope row can
   reference one grant; `(role_id, permission_id)` stays unique.
2. New `role_permission_scopes` (`role_permission_id`, `scope_type`),
   unique per pair, `scope_type` limited to ADR-0013's vocabulary.
3. Scopes are seeded only for grants that already exist (GAP-1: no new
   permission is granted to any role here):
   - every `admin` grant -> `all`;
   - `instructor` `user.directory.read` -> `all` (GAP-4; that permission's
     Club-boundary policy is unchanged).
   No other grant exists in a canonical installation (RolePermission has
   no write path besides migrations). Any other grant would be left
   without a scope and therefore grant nothing — never widened.
   The legacy `user_role_assignments.scope_type` is never copied into a
   permission scope.
4. GAP-8: every not-yet-ended assignment of the `admin` role whose legacy
   `scope_type` is not `all` is closed (`valid_to = now()`), because its
   grants now carry `all` and keeping it would widen that user's rights.
5. GAP-5: of several not-yet-ended assignments of the same role for the
   same user and Club (e.g. AUTH-2's `own_groups` + `self` pairs), the
   earliest (`valid_from`, then `created_at`, then `id`) is kept and the
   others are closed (`valid_to = now()`).
6. `uq_user_role_assignments_one_active_role`: one open-ended assignment
   per (user, role, Club), independent of the legacy `scope_type`.

Steps 4 and 5 never delete a row and never touch `valid_from`; a row
whose `valid_from` lies in the future is closed at its own `valid_from`
(an empty interval) so `ck_user_role_assignments_valid_to_after_
valid_from` holds. Each closed row gets one `role_assignment.revoked`
audit record with `actor_type = 'system'` and the same `details` shape
app.role_assignments.service.revoke_role_assignment writes
(`{"changes": {"valid_to": {"from": ..., "to": ...}}}`, UTC ISO 8601).
Step 4 runs before step 5 so an `admin` user who also holds a later `all`
assignment keeps that one.

The legacy `ck_user_role_assignments_no_overlapping_active` exclusion
constraint (which includes `scope_type`) is kept: closed legacy
duplicates overlap in their history, so a scope-less version of it could
not be created without rewriting that history.

## Rollback

`downgrade()` drops the index and `role_permission_scopes` and restores
the composite primary key. Assignments closed by steps 4/5 are not
reopened: they are ordinary revoked history with their own audit records,
and reopening them would recreate the rights widening/duplicates this
migration removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20e1297d4e1a'
down_revision: Union[str, None] = '4b1f0c2d9a7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NULL_SENTINEL_UUID = "00000000-0000-0000-0000-000000000000"

# Frozen as of this migration (a migration must not import live
# application constants).
_ADMIN_ROLE_CODE = "admin"
_ALL_SCOPE = "all"
_SCOPED_GRANTS = (
    # (role code, permission code or None for "every grant of the role")
    ("admin", None),
    ("instructor", "user.directory.read"),
)

# Closes the rows selected by `targets` (id, old_valid_to) and writes one
# system `role_assignment.revoked` audit record per closed row.
_CLOSE_AND_AUDIT = """
, closed AS (
    UPDATE user_role_assignments ura
    SET valid_to = GREATEST(now(), ura.valid_from), updated_at = now()
    FROM targets t
    WHERE ura.id = t.id
    RETURNING ura.id, ura.club_id, ura.valid_to, t.old_valid_to
)
INSERT INTO audit_logs (
    id, actor_type, actor_user_id, club_id, action, resource_type, resource_id, outcome, details
)
SELECT
    gen_random_uuid(), 'system', NULL, closed.club_id, 'role_assignment.revoked',
    'role_assignment', closed.id, 'success',
    jsonb_build_object(
        'changes', jsonb_build_object(
            'valid_to', jsonb_build_object(
                'from', to_jsonb(closed.old_valid_to), 'to', to_jsonb(closed.valid_to)
            )
        )
    )
FROM closed
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Surrogate id on role_permissions.
    op.add_column("role_permissions", sa.Column("id", sa.UUID(), nullable=True))
    bind.execute(sa.text("UPDATE role_permissions SET id = gen_random_uuid()"))
    op.alter_column("role_permissions", "id", nullable=False)
    op.drop_constraint("role_permissions_pkey", "role_permissions", type_="primary")
    op.create_primary_key("role_permissions_pkey", "role_permissions", ["id"])
    op.create_unique_constraint(
        "uq_role_permissions_role_id_permission_id",
        "role_permissions",
        ["role_id", "permission_id"],
    )

    # 2. role_permission_scopes.
    op.create_table(
        "role_permission_scopes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("role_permission_id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('all','self','children','own_groups','own_events','none')",
            name="ck_role_permission_scopes_scope_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["role_permission_id"], ["role_permissions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_permission_id",
            "scope_type",
            name="uq_role_permission_scopes_role_permission_id_scope_type",
        ),
    )

    # 3. Scopes for existing grants only.
    for role_code, permission_code in _SCOPED_GRANTS:
        bind.execute(
            sa.text(
                """
                INSERT INTO role_permission_scopes (id, role_permission_id, scope_type)
                SELECT gen_random_uuid(), rp.id, :scope_type
                FROM role_permissions rp
                JOIN roles r ON r.id = rp.role_id
                JOIN permissions p ON p.id = rp.permission_id
                WHERE r.code = :role_code
                  AND (CAST(:permission_code AS varchar) IS NULL OR p.code = :permission_code)
                ON CONFLICT (role_permission_id, scope_type) DO NOTHING
                """
            ),
            {"scope_type": _ALL_SCOPE, "role_code": role_code, "permission_code": permission_code},
        )

    # Audit `details` timestamps in UTC, like the service's isoformat().
    bind.execute(sa.text("SET LOCAL TIME ZONE 'UTC'"))

    # 4. GAP-8: legacy admin assignments with a non-`all` scope.
    bind.execute(
        sa.text(
            """
            WITH targets AS (
                SELECT ura.id, ura.valid_to AS old_valid_to
                FROM user_role_assignments ura
                JOIN roles r ON r.id = ura.role_id
                WHERE r.code = :admin_role_code
                  AND ura.scope_type <> :all_scope
                  AND (ura.valid_to IS NULL OR ura.valid_to > now())
                FOR UPDATE OF ura
            )
            """
            + _CLOSE_AND_AUDIT
        ),
        {"admin_role_code": _ADMIN_ROLE_CODE, "all_scope": _ALL_SCOPE},
    )

    # 5. GAP-5: keep the earliest not-yet-ended assignment of a role per
    # (user, Club); close the others.
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    ura.id,
                    ura.valid_to AS old_valid_to,
                    row_number() OVER (
                        PARTITION BY ura.user_id, ura.role_id,
                                     COALESCE(ura.club_id, CAST(:sentinel AS uuid))
                        ORDER BY ura.valid_from, ura.created_at, ura.id
                    ) AS position
                FROM user_role_assignments ura
                WHERE ura.valid_to IS NULL OR ura.valid_to > now()
            ),
            targets AS (
                SELECT id, old_valid_to FROM ranked WHERE position > 1
            )
            """
            + _CLOSE_AND_AUDIT
        ),
        {"sentinel": _NULL_SENTINEL_UUID},
    )

    # 6. One open-ended assignment per (user, role, Club).
    op.create_index(
        "uq_user_role_assignments_one_active_role",
        "user_role_assignments",
        [
            "user_id",
            "role_id",
            sa.text(f"COALESCE(club_id, '{_NULL_SENTINEL_UUID}'::uuid)"),
        ],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_role_assignments_one_active_role",
        table_name="user_role_assignments",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_table("role_permission_scopes")
    op.drop_constraint(
        "uq_role_permissions_role_id_permission_id", "role_permissions", type_="unique"
    )
    op.drop_constraint("role_permissions_pkey", "role_permissions", type_="primary")
    op.create_primary_key("role_permissions_pkey", "role_permissions", ["role_id", "permission_id"])
    op.drop_column("role_permissions", "id")

