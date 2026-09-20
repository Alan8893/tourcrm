"""seed user.directory.read permission and grant it to admin and instructor

Revision ID: 95487f3b616b
Revises: a3f1c9e5d8b2
Create Date: 2026-09-20 00:00:00.000000

TH-0107 (Users directory API / Calendar instructor filter), PO decision:
a new, narrow canonical permission `user.directory.read` gates
`GET /api/v1/users`, deliberately independent of `person.read` — reusing
`person.read` would inherit its `own_groups` (`GroupInstructorAssignment`
-gated) scope for the `instructor` role, which cannot express "instructor
A sees instructor B, same Club, no shared Group," and ADR-0035 §11
explicitly rejects bare Club co-membership as sufficient grounds for
Person access, so that existing scope must not simply be widened instead.

Granted to both `admin` and `instructor` (PO decision — no other baseline
role receives it here). See `app.users.authorization` for how this
permission's own policy is resolved (club_id-boundary only, scope_type is
not consulted) — an instructor's already-existing `UserRoleAssignment`
(typically `own_groups`-scoped for other permissions, but still carrying
its own `club_id`) becomes sufficient for this permission once granted;
no additional `UserRoleAssignment` row is created here or anywhere else.

This migration seeds both the `permissions` row and the two
`role_permissions` links (mirroring 65aa6d48bf08's/f85df8d36f12's shape)
in one migration, since both steps are required together for
`user.directory.read` to actually grant anything and TH-0107 is the
single decision that introduces this code.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their respective
unique constraints (`permissions.code`, `role_permissions(role_id,
permission_id)`) — the same idempotent-seed shape already used by
e5ae1ad9e1e1/65aa6d48bf08/6a99a77234ba/f85df8d36f12.

## Rollback

`downgrade()` removes only the `(admin, user.directory.read)` and
`(instructor, user.directory.read)` rows from `role_permissions`. It
deliberately does not delete the `user.directory.read` row from
`permissions` — once a canonical permission code exists, it is stable
catalog data, matching the same precedent `f85df8d36f12` already set for
`person.create` (and the reasoning documented there in full).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert

# revision identifiers, used by Alembic.
revision: str = '95487f3b616b'
down_revision: Union[str, None] = 'a3f1c9e5d8b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CODE = "user.directory.read"
_GRANTED_ROLE_CODES = ("admin", "instructor")


def upgrade() -> None:
    import uuid

    bind = op.get_bind()

    permissions_table = sa.table(
        "permissions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code", sa.String),
    )
    bind.execute(
        pg_insert(permissions_table)
        .values([{"id": uuid.uuid4(), "code": _PERMISSION_CODE}])
        .on_conflict_do_nothing(index_elements=["code"])
    )

    for role_code in _GRANTED_ROLE_CODES:
        bind.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT (SELECT id FROM roles WHERE code = :role_code), p.id
                FROM permissions p
                WHERE p.code = :permission_code
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """
            ),
            {"role_code": role_code, "permission_code": _PERMISSION_CODE},
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
              AND r.code IN :role_codes
              AND p.code = :permission_code
            """
        ).bindparams(sa.bindparam("role_codes", expanding=True)),
        {"role_codes": list(_GRANTED_ROLE_CODES), "permission_code": _PERMISSION_CODE},
    )
