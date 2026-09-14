"""seed guardian relationship permissions

Revision ID: 65aa6d48bf08
Revises: b99fd408c104
Create Date: 2026-09-14 07:12:53.809339

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = '65aa6d48bf08'
down_revision: Union[str, None] = 'b99fd408c104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-0025 §2 added guardian_relationship.read/guardian_relationship.manage
# to the canonical permission catalog (docs/02-requirements/roles-and-
# permissions.md §4) and docs/05-api/people-api.md §17 was reconciled to
# use them, but the e5ae1ad9e1e1 seed migration's _DOCUMENTED_PERMISSIONS
# tuple predates that ADR and was never extended — these two codes have
# never actually existed as `permissions` rows. Issue #64 §11 requires
# using exactly these two codes and no others; this migration only
# catches up the seed data to the already-accepted ADR-0025 decision, the
# same idempotent ON CONFLICT DO NOTHING shape as the original seed.
_GUARDIAN_RELATIONSHIP_PERMISSIONS = (
    "guardian_relationship.read",
    "guardian_relationship.manage",
)


def upgrade() -> None:
    permissions_table = sa.table(
        "permissions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code", sa.String),
    )
    bind = op.get_bind()
    bind.execute(
        pg_insert(permissions_table)
        .values([{"id": uuid.uuid4(), "code": code} for code in _GUARDIAN_RELATIONSHIP_PERMISSIONS])
        .on_conflict_do_nothing(index_elements=["code"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": list(_GUARDIAN_RELATIONSHIP_PERMISSIONS)},
    )
