"""seed canonical admin role permissions

Revision ID: 6a99a77234ba
Revises: 91a40113d17b
Create Date: 2026-09-16 10:07:36.393545

TH-0093 / Issue #106, implementing the accepted PO decision in TH-0092 /
Issue #105 (Variant A): the canonical system `admin` role
(`Role.code = 'admin'`, `Role.is_system = true`) receives a
`RolePermission` grant for every permission currently in the canonical
catalog (docs/02-requirements/roles-and-permissions.md §4, mirrored in
`app.db.authorization.DOCUMENTED_PERMISSION_CODES`).

`instructor`/`member`/`guardian` grants are explicitly out of scope for
this task (TH-0092) and are not touched here.

Both `roles` and `permissions` are already fully seeded by the time this
migration runs (e5ae1ad9e1e1 seeded the baseline roles and the first 32
permission codes; 65aa6d48bf08 added the 2 `guardian_relationship.*`
codes) — this migration only *links* the existing `admin` Role row to
each existing Permission row by code; it creates no Role or Permission
rows itself.

## Idempotency / partial-state convergence

`INSERT ... SELECT ... ON CONFLICT (role_id, permission_id) DO NOTHING`
against `role_permissions`' own composite primary key: re-running this
migration (or re-running the equivalent statement by hand) only ever
inserts pairs that are still missing — an existing valid `(admin,
permission)` pair is left untouched, and no duplicate row can ever be
created. This is the same idempotent-seed shape e5ae1ad9e1e1 and
65aa6d48bf08 already use for `roles`/`permissions`, applied here to
`role_permissions` instead.

The `INSERT` only ever targets rows where `roles.code = 'admin'` and
`permissions.code` is one of this migration's own fixed list — it can
never insert a pair for any other role, and never touches a
`role_permissions` row for an unrelated (role, permission) combination.

## Rollback

`role_permissions` has no application-level write path anywhere in this
codebase (ADR-0026 §4: "The current slice introduces no ... RolePermission
CRUD") and no other migration in this chain writes to it — this
migration is the only mechanism that has ever populated the table. It is
therefore safe for `downgrade()` to unconditionally remove exactly the
(admin, canonical-permission) pairs this migration's own fixed list
defines: no other code path could have created one of those exact pairs
independently of this migration having run. This mirrors the same
reasoning 65aa6d48bf08's own downgrade already relies on for its
`permissions` rows ("these two codes have never actually existed... this
migration only catches up the seed").

`downgrade()` only deletes from `role_permissions`, scoped to
`roles.code = 'admin'` and this migration's own permission-code list — it
never touches `roles` or `permissions` rows, and never touches a
`role_permissions` row for any other role or any permission code outside
this fixed list (so a genuinely unrelated/custom grant — e.g. for
`instructor`, or an admin grant for a future, not-yet-canonical
permission — survives downgrade unchanged).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a99a77234ba'
down_revision: Union[str, None] = '91a40113d17b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN_ROLE_CODE = "admin"

# Frozen as of this migration (mirrors e5ae1ad9e1e1's own
# `_DOCUMENTED_PERMISSIONS`/65aa6d48bf08's own `_GUARDIAN_RELATIONSHIP_
# PERMISSIONS`: a migration is a historical record and must not import a
# live, evolving application constant — `app.db.authorization.
# DOCUMENTED_PERMISSION_CODES` is the same 34 codes as of this writing;
# a *future* addition to that catalog requires its own explicit seed/
# migration decision per TH-0092, not an edit to this file).
_ADMIN_GRANT_PERMISSION_CODES = (
    "person.read",
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
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT (SELECT id FROM roles WHERE code = :role_code), p.id
            FROM permissions p
            WHERE p.code IN :permission_codes
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ).bindparams(sa.bindparam("permission_codes", expanding=True)),
        {"role_code": _ADMIN_ROLE_CODE, "permission_codes": list(_ADMIN_GRANT_PERMISSION_CODES)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            USING roles r, permissions p
            WHERE role_permissions.role_id = r.id
              AND role_permissions.permission_id = p.id
              AND r.code = :role_code
              AND p.code IN :permission_codes
            """
        ).bindparams(sa.bindparam("permission_codes", expanding=True)),
        {"role_code": _ADMIN_ROLE_CODE, "permission_codes": list(_ADMIN_GRANT_PERMISSION_CODES)},
    )
