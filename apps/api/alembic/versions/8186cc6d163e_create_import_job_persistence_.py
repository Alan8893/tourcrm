"""create import job persistence foundation

Revision ID: 8186cc6d163e
Revises: c70bcac43032
Create Date: 2026-09-23 18:00:00.000000

TH-0118.1 / Issue #185, implementing the participant import contract in
docs/05-api/people-api.md §22 and docs/04-modules/people-and-membership.md
§11.1/§11.2:

- `import_jobs`: one Club-bound participant import job — `club_id`,
  `created_by_user_id`, `source_file_id` (FK to the existing `files`
  table; the binary itself stays in FileStorage, never inline),
  `source_format` restricted to `csv`/`xlsx`, `status` restricted to the
  ten canonical lifecycle values, nullable non-negative aggregate record
  counters (NULL = not computed yet), timestamps.
- `import_job_errors`: validation/application error storage foundation,
  each row bound to exactly one `import_job_id`, with an optional
  1-based `row_number`, optional `field`, a machine-readable `code` and a
  human-readable `message`.

Seeds the canonical `membership.import` permission (roles-and-permissions.md
§4) and grants it only to `admin` (roles-and-permissions.md role matrix:
"Participant import job management — admin, `membership.import` + `all`").

The closed audit action vocabulary (ADR-0024 §4) is deliberately NOT
touched: creating an ImportJob is not audit-required (people-api.md §22
"Import security and audit"); the apply-stage action code is a separate,
future ADR amendment.

## Idempotency

The permission-seed inserts use `ON CONFLICT ... DO NOTHING`, matching
the established idempotent-seed shape (e5ae1ad9e1e1/65aa6d48bf08/
6a99a77234ba/f85df8d36f12/75b7574b6e44/95487f3b616b/c70bcac43032).

## Rollback

`downgrade()` removes only the `(admin, membership.import)`
`role_permissions` row and drops the two new tables (in FK-dependency
order). It deliberately does not delete the `membership.import` row from
`permissions` — once a canonical permission code exists, it is stable
catalog data, matching the f85df8d36f12/75b7574b6e44/c70bcac43032
precedent.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = '8186cc6d163e'
down_revision: Union[str, None] = 'c70bcac43032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CODE = "membership.import"
_ADMIN_ROLE_CODE = "admin"

_RECORD_COUNTERS = (
    "total_records",
    "valid_records",
    "invalid_records",
    "created_records",
    "updated_records",
    "skipped_records",
)


def upgrade() -> None:
    op.create_table('import_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('club_id', sa.UUID(), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('source_file_id', sa.UUID(), nullable=False),
    sa.Column('source_format', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    *(sa.Column(counter, sa.Integer(), nullable=True) for counter in _RECORD_COUNTERS),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint(
        "status IN ('applying','approved','cancelled','completed','failed','parsing',"
        "'partially_completed','preview_ready','uploaded','validating')",
        name='ck_import_jobs_status_valid',
    ),
    sa.CheckConstraint("source_format IN ('csv','xlsx')", name='ck_import_jobs_source_format_valid'),
    *(
        sa.CheckConstraint(
            f"{counter} IS NULL OR {counter} >= 0",
            name=f"ck_import_jobs_{counter}_non_negative",
        )
        for counter in _RECORD_COUNTERS
    ),
    sa.ForeignKeyConstraint(['club_id'], ['clubs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_file_id'], ['files.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_import_jobs_club_id', 'import_jobs', ['club_id'], unique=False)
    op.create_index('ix_import_jobs_created_by_user_id', 'import_jobs', ['created_by_user_id'], unique=False)
    op.create_index('ix_import_jobs_source_file_id', 'import_jobs', ['source_file_id'], unique=False)
    op.create_table('import_job_errors',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('import_job_id', sa.UUID(), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=True),
    sa.Column('field', sa.String(length=255), nullable=True),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("row_number IS NULL OR row_number >= 1", name='ck_import_job_errors_row_number_positive'),
    sa.ForeignKeyConstraint(['import_job_id'], ['import_jobs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_import_job_errors_import_job_id', 'import_job_errors', ['import_job_id'], unique=False)

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

    op.drop_index('ix_import_job_errors_import_job_id', table_name='import_job_errors')
    op.drop_table('import_job_errors')
    op.drop_index('ix_import_jobs_source_file_id', table_name='import_jobs')
    op.drop_index('ix_import_jobs_created_by_user_id', table_name='import_jobs')
    op.drop_index('ix_import_jobs_club_id', table_name='import_jobs')
    op.drop_table('import_jobs')
