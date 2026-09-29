"""seed guardian event.read children grant

Revision ID: 127da2741f20
Revises: 20e1297d4e1a
Create Date: 2026-09-29 12:00:00.000000

TH-0172 / Issue #213, implementing ADR-0043 §1/§6.1 (and
docs/02-requirements/role-permission-scope-matrix.md §7): the canonical
system `guardian` role receives the existing `event.read` permission with
the existing `children` scope. Its absence was an implementation gap, not
an intentional restriction (ADR-0043 §1). No permission code and no scope
value is created here — both already exist (`permissions.code =
'event.read'`, ADR-0013's `children`).

What `children` means for `event.read` (the Guardian → Child → Group /
Participation → Event relationship paths) is resolved at request time by
app.events.authorization / app.events.series_authorization; this
migration only links the role to the permission and its scope, exactly
like 95487f3b616b/20e1297d4e1a do for their own grants.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their unique
constraints (`uq_role_permissions_role_id_permission_id`,
`uq_role_permission_scopes_role_permission_id_scope_type`) — re-running
never duplicates a row. An already existing (guardian, event.read) grant
is reused, and only the missing `children` scope is added to it; no other
scope of that grant is touched.

## Rollback

`downgrade()` removes only the (guardian, event.read, children) scope row
and then the (guardian, event.read) grant itself if — and only if — it no
longer carries any scope, so a grant that also held some other scope
before this migration keeps it. `permissions`/`roles` rows are never
touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '127da2741f20'
down_revision: Union[str, None] = '20e1297d4e1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration (a migration must not import live
# application constants).
_ROLE_CODE = "guardian"
_PERMISSION_CODE = "event.read"
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
