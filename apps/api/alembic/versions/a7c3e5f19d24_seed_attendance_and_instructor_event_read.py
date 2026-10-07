"""seed attendance grants and instructor event.read

Revision ID: a7c3e5f19d24
Revises: d8e3b5f02a47
Create Date: 2026-10-07 20:00:00.000000

Issue #305 / ADR-0047 §3/§8, PO decision on the seed GAP found while
implementing it: docs/02-requirements/role-permission-scope-matrix.md §8
(and roles-and-permissions.md) already define

- `attendance.read`: instructor assigned/owned, member `self`,
  guardian `children`;
- `attendance.update`: instructor assigned/owned;

and the matrix defines `event.read` for instructor as `own_groups` /
`own_events`. None of these grants was ever seeded, so the canonical
lesson attendance workflow (instructor marking, Member/Guardian calendar
attendance indication) could not run for those roles. This migration only
synchronizes the seeded role grants with that already-approved policy. No
permission code and no scope value is created here — all of them already
exist. "assigned/owned" is expressed with the existing `own_groups` and
`own_events` scopes, exactly like the instructor `trip.*` grants of
3c1e9a7d5b20.

Deliberately NOT seeded here (separate authorization GAP, outside #305):
the matrix's other instructor Event grants (`event.create`,
`event.update`, `event.cancel`, `event.manage`).

What each scope means for these permissions is resolved at request time by
app.events.authorization / app.events.series_authorization /
app.events.attendance; this migration only links roles to permissions and
their scopes, exactly like 3c1e9a7d5b20/127da2741f20.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their unique
constraints (`uq_role_permissions_role_id_permission_id`,
`uq_role_permission_scopes_role_permission_id_scope_type`) — re-running
never duplicates a row. An already existing (role, permission) grant is
reused, and only the missing scopes are added to it.

## Rollback

`downgrade()` removes only the (role, permission, scope) rows listed below
and then each (role, permission) grant itself if — and only if — it no
longer carries any scope, so a grant that also held some other scope
before this migration keeps it. `permissions`/`roles` rows are never
touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e5f19d24'
down_revision: Union[str, None] = 'd8e3b5f02a47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration (a migration must not import live
# application constants): (role code, permission code, scope type).
_GRANT_SCOPES = (
    ("instructor", "attendance.read", "own_groups"),
    ("instructor", "attendance.read", "own_events"),
    ("instructor", "attendance.update", "own_groups"),
    ("instructor", "attendance.update", "own_events"),
    ("instructor", "event.read", "own_groups"),
    ("instructor", "event.read", "own_events"),
    ("member", "attendance.read", "self"),
    ("guardian", "attendance.read", "children"),
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
