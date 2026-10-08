"""seed member person.read/person.update self grants

Revision ID: 3b1fd730bb0d
Revises: a7c3e5f19d24
Create Date: 2026-10-07 22:00:00.000000

Issue #312, PO approval 2026-10-07 (seed GAP found while preparing #311):
ADR-0035 §3 (AUTH-2C amendment) and
docs/02-requirements/role-permission-scope-matrix.md §3.3/§4/§4.1 already
define, for the canonical system `member` role,

- `person.read`: `self`;
- `person.update`: `self`, restricted to `first_name`, `last_name`,
  `middle_name`, `phone`, `address`, `photo` / avatar.

Neither grant was ever seeded, and AUTH-2A's permission-level scopes
(20e1297d4e1a) fail closed, so a seeded Member could neither read nor edit
their own Person (nor manage their own profile photo). This migration only
synchronizes the seeded role grants with that already-approved policy. No
permission code and no scope value is created here — both already exist
(`person.read`/`person.update`, ADR-0013's `self`).

What `self` means for a Person (User -> Person) and the restricted field
set of `person.update(self)` are resolved at request time by
app.people.authorization / app.api.v1.persons; this migration only links
the role to the permissions and their scope, exactly like
5e2c8a41d7b9/3c1e9a7d5b20.

Deliberately NOT seeded here (outside #312): the matrix's other Person
grants for `instructor` (`person.read(own_groups)`) and `guardian`
(`person.read(children)`/`person.update(children)`).

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their unique
constraints (`uq_role_permissions_role_id_permission_id`,
`uq_role_permission_scopes_role_permission_id_scope_type`) — re-running
never duplicates a row. An already existing (member, permission) grant is
reused, and only the missing `self` scope is added to it.

## Rollback

`downgrade()` removes only the (member, permission, self) rows listed
below and then each (member, permission) grant itself if — and only if —
it no longer carries any scope, so a grant that also held some other scope
before this migration keeps it. `permissions`/`roles` rows are never
touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3b1fd730bb0d'
down_revision: Union[str, None] = 'a7c3e5f19d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration (a migration must not import live
# application constants): (role code, permission code, scope type).
_GRANT_SCOPES = (
    ("member", "person.read", "self"),
    ("member", "person.update", "self"),
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
