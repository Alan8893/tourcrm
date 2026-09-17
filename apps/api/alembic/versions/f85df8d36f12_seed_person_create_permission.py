"""seed person.create permission and grant it to admin

Revision ID: f85df8d36f12
Revises: 6a99a77234ba
Create Date: 2026-09-17 00:00:00.000000

TH-0101 / Issue #116, implementing ADR-0035 §2: a new canonical
permission `person.create` is introduced and, in the current MVP, granted
only to the system `admin` role (`Role.code = 'admin'`, `Role.is_system =
true`). `instructor`/`member`/`guardian` receive no grant here, matching
ADR-0035 §2's explicit statement that they do not receive `person.create`.

This migration seeds both the `permissions` row (mirroring 65aa6d48bf08's
shape for `guardian_relationship.*`) and the `role_permissions` link for
`admin` (mirroring 6a99a77234ba's shape) in one migration, since both
steps are required together for `person.create` to actually grant
anything and TH-0101 is the single decision that introduces this code.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their respective
unique constraints (`permissions.code`, `role_permissions(role_id,
permission_id)`) — the same idempotent-seed shape already used by
e5ae1ad9e1e1/65aa6d48bf08/6a99a77234ba.

## Rollback

`downgrade()` removes only the `(admin, person.create)` row from
`role_permissions`. It deliberately does **not** delete the
`person.create` row from `permissions`: once a canonical permission code
exists, it is stable catalog data (the same status 65aa6d48bf08's own two
codes have going forward) — `tests/integration/test_role_permission_seed.py`
exercises downgrading to the revision immediately before 6a99a77234ba and
asserts the full current `DOCUMENTED_PERMISSION_CODES` catalog is still
present in `permissions` after that downgrade/upgrade cycle; deleting the
permission row here would falsify that invariant for every migration
introduced after 6a99a77234ba. Removing the `role_permissions` link is
sufficient to undo what this migration actually decided (the admin
grant); it never touches any other role's grants or any other permission.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert

# revision identifiers, used by Alembic.
revision: str = 'f85df8d36f12'
down_revision: Union[str, None] = '6a99a77234ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CODE = "person.create"
_ADMIN_ROLE_CODE = "admin"


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
        {"role_code": _ADMIN_ROLE_CODE, "permission_code": _PERMISSION_CODE},
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
              AND p.code = :permission_code
            """
        ),
        {"role_code": _ADMIN_ROLE_CODE, "permission_code": _PERMISSION_CODE},
    )
