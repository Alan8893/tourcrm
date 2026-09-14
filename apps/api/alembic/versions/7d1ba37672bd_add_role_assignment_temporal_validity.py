"""add role assignment temporal validity

Revision ID: 7d1ba37672bd
Revises: c63f4200dd82
Create Date: 2026-09-14 18:30:00.000000

Issue #74 (implementing ADR-0026 §1, docs/02-requirements/roles-and-
permissions.md §19.1):

- `user_role_assignments.valid_from`/`valid_to`: the `[valid_from,
  valid_to)` temporal validity interval. `valid_to = NULL` is open-ended.
  Existing rows are backfilled from `created_at` (the row's actual
  original creation time is the most accurate available start of its
  validity — no historical data is discarded).
- Replaces `uq_user_role_assignments_no_duplicate` (Issue #19's
  unconditional uniqueness, which could never represent revoke +
  historical re-assignment) with a GiST exclusion constraint scoped to
  *overlapping* intervals for the same `(user_id, role_id, club_id,
  scope_type, scope_ref_id)` — sequential historical intervals and
  touching boundaries are allowed; only a genuine overlap conflicts.
  `club_id`/`scope_ref_id` are folded through `COALESCE` to a fixed
  sentinel UUID because PostgreSQL's `EXCLUDE USING gist` has no `NULLS
  NOT DISTINCT` clause (unlike `UNIQUE`, verified empirically against
  this project's own PostgreSQL 16) — see
  app.db.authorization._NULL_SENTINEL_UUID's own comment.

`btree_gist` is not (re-)created here — already created by the identity
foundation migration (80dd15675404), which this migration's whole chain
already depends on.

Downgrade note: restoring the old unconditional unique constraint will
fail if, by the time of a downgrade, genuine revoke + re-assignment
history already exists for the same 5-tuple (more than one row sharing
identical user/role/club/scope_type/scope_ref_id) — that data can only
be represented by the temporal model this migration introduces. This is
an inherent, expected limitation of downgrading past a genuinely
incompatible data-model change, not a defect in this migration; it
downgrades cleanly on a database that has not yet accumulated such
history (e.g. immediately after upgrading, as CI's schema-validation
checks this).
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7d1ba37672bd'
down_revision: Union[str, None] = 'c63f4200dd82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NULL_SENTINEL_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.add_column(
        'user_role_assignments',
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'user_role_assignments',
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: an existing row's own created_at is the most accurate
    # available start of its validity interval.
    op.execute(
        "UPDATE user_role_assignments SET valid_from = created_at WHERE valid_from IS NULL"
    )
    op.alter_column(
        'user_role_assignments',
        'valid_from',
        nullable=False,
        server_default=sa.text('now()'),
    )
    op.create_check_constraint(
        'ck_user_role_assignments_valid_to_after_valid_from',
        'user_role_assignments',
        'valid_to IS NULL OR valid_to >= valid_from',
    )

    op.drop_constraint(
        'uq_user_role_assignments_no_duplicate', 'user_role_assignments', type_='unique'
    )
    op.execute(
        "ALTER TABLE user_role_assignments ADD CONSTRAINT "
        "ck_user_role_assignments_no_overlapping_active "
        "EXCLUDE USING gist ("
        "user_id WITH =, "
        "role_id WITH =, "
        f"COALESCE(club_id, '{_NULL_SENTINEL_UUID}'::uuid) WITH =, "
        "scope_type WITH =, "
        f"COALESCE(scope_ref_id, '{_NULL_SENTINEL_UUID}'::uuid) WITH =, "
        "tstzrange(valid_from, valid_to) WITH &&"
        ")"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_role_assignments DROP CONSTRAINT "
        "ck_user_role_assignments_no_overlapping_active"
    )
    op.create_unique_constraint(
        'uq_user_role_assignments_no_duplicate',
        'user_role_assignments',
        ['user_id', 'role_id', 'club_id', 'scope_type', 'scope_ref_id'],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint(
        'ck_user_role_assignments_valid_to_after_valid_from',
        'user_role_assignments',
        type_='check',
    )
    op.drop_column('user_role_assignments', 'valid_to')
    op.drop_column('user_role_assignments', 'valid_from')
