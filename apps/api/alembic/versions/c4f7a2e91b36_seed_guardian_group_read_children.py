"""seed guardian group.read children grant

Revision ID: c4f7a2e91b36
Revises: 9b4d6e2f8a10
Create Date: 2026-10-07 13:00:00.000000

ADR-0046 (Guardian Child-to-Group Context, Issue #301) and
docs/02-requirements/role-permission-scope-matrix.md §6 (`group.read`,
guardian: `children`): the canonical system `guardian` role receives the
existing `group.read` permission with the existing `children` scope. No
permission code and no scope value is created here — both already exist
(`permissions.code = 'group.read'`, ADR-0013's `children`).

What `children` means for a Group (User -> guardian Person -> active,
interval-valid GuardianRelationship -> child -> active ClubMembership ->
active GroupMembership -> active Group) is resolved at request time by
app.groups.authorization; this migration only links the role to the
permission and its scope, exactly like 5e2c8a41d7b9/127da2741f20.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their unique
constraints (`uq_role_permissions_role_id_permission_id`,
`uq_role_permission_scopes_role_permission_id_scope_type`) — re-running
never duplicates a row. An already existing (guardian, group.read) grant
is reused, and only the missing `children` scope is added to it; no other
scope of that grant is touched.

## Rollback

`downgrade()` removes only the (guardian, group.read, children) scope row
and then the (guardian, group.read) grant itself if — and only if — it no
longer carries any scope, so a grant that also held some other scope
before this migration keeps it. `permissions`/`roles` rows are never
touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4f7a2e91b36'
down_revision: Union[str, None] = '9b4d6e2f8a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration (a migration must not import live
# application constants).
_ROLE_CODE = "guardian"
_PERMISSION_CODE = "group.read"
_SCOPE_TYPE = "children"


def upgrade() -> None:
    bind = op.get_bind()
    params = {"role_code": _ROLE_CODE, "permission_code": _PERMISSION_CODE}

    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (id, role_id, permission_id)
            SELECT gen_random_uuid(), r.id, p.id
            FROM roles r, permissions p
            WHERE r.code = :role_code AND p.code = :permission_code
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ),
        params,
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO role_permission_scopes (id, role_permission_id, scope_type)
            SELECT gen_random_uuid(), rp.id, :scope_type
            FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE r.code = :role_code AND p.code = :permission_code
            ON CONFLICT (role_permission_id, scope_type) DO NOTHING
            """
        ),
        {**params, "scope_type": _SCOPE_TYPE},
    )


def downgrade() -> None:
    bind = op.get_bind()
    params = {"role_code": _ROLE_CODE, "permission_code": _PERMISSION_CODE}

    bind.execute(
        sa.text(
            """
            DELETE FROM role_permission_scopes rps
            USING role_permissions rp, roles r, permissions p
            WHERE rps.role_permission_id = rp.id
              AND rp.role_id = r.id
              AND rp.permission_id = p.id
              AND r.code = :role_code
              AND p.code = :permission_code
              AND rps.scope_type = :scope_type
            """
        ),
        {**params, "scope_type": _SCOPE_TYPE},
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions rp
            USING roles r, permissions p
            WHERE rp.role_id = r.id
              AND rp.permission_id = p.id
              AND r.code = :role_code
              AND p.code = :permission_code
              AND NOT EXISTS (
                  SELECT 1 FROM role_permission_scopes rps
                  WHERE rps.role_permission_id = rp.id
              )
            """
        ),
        params,
    )
