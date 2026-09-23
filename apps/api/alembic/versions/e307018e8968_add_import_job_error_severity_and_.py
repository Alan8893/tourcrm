"""add import job error severity and matched person

Revision ID: e307018e8968
Revises: 8186cc6d163e
Create Date: 2026-09-24 10:00:00.000000

TH-0118.2 (participant import preview), per the PO decision recorded in
docs/05-api/people-api.md §22 "Preview": the preview is the existing
`import_job_errors` rows plus the job's counters — no separate preview
entity — so `import_job_errors` is minimally extended with:

- `severity` — `error` (row invalid / job failed) or `warning` (row stays
  valid, e.g. `duplicate_exact`), CHECK-restricted to those two values.
  Rows that already exist were all errors (TH-0118.1 wrote no warnings),
  so they are backfilled as `error` through a temporary server default
  that is dropped again: every new row must state its severity explicitly.
- `matched_person_id` — nullable FK to `persons` (RESTRICT), set only for
  a `duplicate_exact` warning against an existing Person.

## Rollback

`downgrade()` drops the two columns and the CHECK constraint. Warning rows
are dropped with them rather than silently turned into errors, so the
downgrade deletes `warning` rows first.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e307018e8968'
down_revision: Union[str, None] = '8186cc6d163e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'import_job_errors',
        sa.Column('severity', sa.String(length=16), server_default='error', nullable=False),
    )
    op.alter_column('import_job_errors', 'severity', server_default=None)
    op.add_column('import_job_errors', sa.Column('matched_person_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'import_job_errors_matched_person_id_fkey',
        'import_job_errors',
        'persons',
        ['matched_person_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    op.create_check_constraint(
        'ck_import_job_errors_severity_valid',
        'import_job_errors',
        "severity IN ('error','warning')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM import_job_errors WHERE severity = 'warning'")
    op.drop_constraint('ck_import_job_errors_severity_valid', 'import_job_errors', type_='check')
    op.drop_constraint(
        'import_job_errors_matched_person_id_fkey', 'import_job_errors', type_='foreignkey'
    )
    op.drop_column('import_job_errors', 'matched_person_id')
    op.drop_column('import_job_errors', 'severity')
