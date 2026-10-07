"""seed guardian guardian_relationship.read children grant

Revision ID: d8e3b5f02a47
Revises: c4f7a2e91b36
Create Date: 2026-10-07 17:00:00.000000

PO decision (docs/02-requirements/role-permission-scope-matrix.md §6,
docs/05-api/people-api.md `GET /api/v1/me/children`): the canonical system
`guardian` role receives the existing `guardian_relationship.read`
permission with the existing `children` scope — the prerequisite read
grant for the Guardian «Мои дети» projection. No permission code and no
scope value is created here — both already exist.

The grant itself carries no Club: a role's permission grant is Club-less
in this schema (`role_permissions`/`role_permission_scopes`); the Club
boundary lives on each `user_role_assignments` row. What `children`
means for `guardian_relationship.read` (the authenticated Guardian's own
active, interval-valid GuardianRelationship rows) is resolved at request
time by app.people.guardian_authorization; this migration only links the
role to the permission and its scope, exactly like c4f7a2e91b36.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their unique
constraints — re-running never duplicates a row. An already existing
(guardian, guardian_relationship.read) grant is reused, and only the
missing `children` scope is added to it.

## Rollback

`downgrade()` removes only the (guardian, guardian_relationship.read,
children) scope row and then that grant itself if — and only if — it no
longer carries any scope. `permissions`/`roles` rows are never touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8e3b5f02a47'
down_revision: Union[str, None] = 'c4f7a2e91b36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration (a migration must not import live
# application constants).
_ROLE_CODE = "guardian"
_PERMISSION_CODE = "guardian_relationship.read"
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
