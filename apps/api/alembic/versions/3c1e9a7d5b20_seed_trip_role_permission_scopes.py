"""seed trip role permission scopes

Revision ID: 3c1e9a7d5b20
Revises: f6527395ba00
Create Date: 2026-10-04 15:30:00.000000

Issue #245 (TH-012x), PO/CTO decision on Trip authorization: the existing
`trip.read`/`trip.manage` permissions are granted to the baseline roles
with existing scopes — no permission code and no scope value is created
here:

| Permission    | admin | instructor              | member | guardian   |
|---------------|-------|-------------------------|--------|------------|
| `trip.read`   | `all` | `own_groups`+`own_events` | `self` | `children` |
| `trip.manage` | `all` | `own_groups`+`own_events` | —      | —          |

The two `admin` grants already exist with `all` (6a99a77234ba +
20e1297d4e1a) and are not touched. What each scope means for a Trip is
resolved at request time by the Event authorization infrastructure
(app.events.authorization) against the Trip's Event; this migration only
links roles to permissions and their scopes, exactly like 127da2741f20.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their unique
constraints (`uq_role_permissions_role_id_permission_id`,
`uq_role_permission_scopes_role_permission_id_scope_type`) — re-running
never duplicates a row; an existing grant is reused and only missing
scopes are added.

## Rollback

`downgrade()` removes only the scope rows listed above and then each of
those grants if — and only if — it no longer carries any scope, so a grant
that also held another scope keeps it. `permissions`/`roles` rows are
never touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3c1e9a7d5b20'
down_revision: Union[str, None] = 'f6527395ba00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration (a migration must not import live
# application constants): (role code, permission code, scope type).
_GRANT_SCOPES = (
    ("instructor", "trip.read", "own_groups"),
    ("instructor", "trip.read", "own_events"),
    ("instructor", "trip.manage", "own_groups"),
    ("instructor", "trip.manage", "own_events"),
    ("member", "trip.read", "self"),
    ("guardian", "trip.read", "children"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for role_code, permission_code, scope_type in _GRANT_SCOPES:
        params = {"role_code": role_code, "permission_code": permission_code}
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
            {**params, "scope_type": scope_type},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for role_code, permission_code, scope_type in _GRANT_SCOPES:
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
            {"role_code": role_code, "permission_code": permission_code, "scope_type": scope_type},
        )
    for role_code, permission_code in sorted({(r, p) for r, p, _ in _GRANT_SCOPES}):
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
            {"role_code": role_code, "permission_code": permission_code},
        )
