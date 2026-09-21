"""seed account.manage permission and grant it to admin

Revision ID: 75b7574b6e44
Revises: 2cd38e8e090d
Create Date: 2026-09-21 06:09:16.554537

TH-0113 (Admin password reset / first-access setup), implementing the
accepted PO decision in ADR-0038 (Account Provisioning and Password
Lifecycle): a new, narrow canonical permission `account.manage` gates
administrative User account/credential operations (creating a User for
an existing Person, issuing a first-access/administrative password-reset
challenge) — deliberately independent of `role.manage`, `settings.manage`
and `person.update` (see app.db.authorization.DOCUMENTED_PERMISSION_
CODES's own comment for the full rationale).

Granted only to `admin` in this slice (PO decision — no other baseline
role receives it here; a fuller role/permission matrix is explicitly a
separate, later task).

Mirrors 95487f3b616b's/f85df8d36f12's identical shape: seeds the
`permissions` row and the one `role_permissions` link together, since
both are required for `account.manage` to actually grant anything and
TH-0113 is the single decision that introduces this code.

## Idempotency

Both inserts use `ON CONFLICT ... DO NOTHING` against their respective
unique constraints (`permissions.code`, `role_permissions(role_id,
permission_id)`) — the same idempotent-seed shape already used by
e5ae1ad9e1e1/65aa6d48bf08/6a99a77234ba/f85df8d36f12/95487f3b616b.

## Rollback

`downgrade()` removes only the `(admin, account.manage)` row from
`role_permissions`. It deliberately does not delete the `account.manage`
row from `permissions` — once a canonical permission code exists, it is
stable catalog data, matching the same precedent `f85df8d36f12`/
`95487f3b616b` already set.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert

# revision identifiers, used by Alembic.
revision: str = '75b7574b6e44'
down_revision: Union[str, None] = '2cd38e8e090d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CODE = "account.manage"
_GRANTED_ROLE_CODES = ("admin",)


def upgrade() -> None:
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
