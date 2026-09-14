"""add group lifecycle and invariant constraints

Revision ID: c63f4200dd82
Revises: 65aa6d48bf08
Create Date: 2026-09-14 15:00:00.000000

Issue #71 (implementing the Issue #69 specification gate,
docs/05-api/people-api.md §14-16):

- `groups.status`: closed CHECK vocabulary (`active`, `archived`).
- `group_memberships.membership_status`: closed CHECK vocabulary
  (`active`, `ended`).
- `group_memberships`: GiST exclusion constraint preventing a duplicate
  *active* row for the same `(group_id, club_membership_id)` pair with an
  overlapping `[valid_from, valid_to)` interval. Different Groups are
  never constrained against each other.
- `group_instructor_assignments`: GiST exclusion constraint preventing
  two `is_primary = true` rows for the same `group_id` with overlapping
  `[valid_from, valid_to)` intervals (touching boundaries are not an
  overlap, so sequential historical primaries remain allowed).

`btree_gist` is not (re-)created here — already created by the identity
foundation migration (80dd15675404), which this migration's whole chain
already depends on.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c63f4200dd82'
down_revision: Union[str, None] = '65aa6d48bf08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE groups ADD CONSTRAINT ck_groups_status_valid "
        "CHECK (status IN ('active', 'archived'))"
    )
    op.execute(
        "ALTER TABLE group_memberships ADD CONSTRAINT "
        "ck_group_memberships_membership_status_valid "
        "CHECK (membership_status IN ('active', 'ended'))"
    )
    op.execute(
        "ALTER TABLE group_memberships ADD CONSTRAINT "
        "ck_group_memberships_no_duplicate_active "
        "EXCLUDE USING gist ("
        "group_id WITH =, "
        "club_membership_id WITH =, "
        "tstzrange(valid_from, valid_to) WITH &&"
        ") WHERE (membership_status = 'active')"
    )
    op.execute(
        "ALTER TABLE group_instructor_assignments ADD CONSTRAINT "
        "ck_group_instructor_assignments_one_active_primary "
        "EXCLUDE USING gist ("
        "group_id WITH =, "
        "tstzrange(valid_from, valid_to) WITH &&"
        ") WHERE (is_primary = true)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE group_instructor_assignments DROP CONSTRAINT "
        "ck_group_instructor_assignments_one_active_primary"
    )
    op.execute(
        "ALTER TABLE group_memberships DROP CONSTRAINT "
        "ck_group_memberships_no_duplicate_active"
    )
    op.execute(
        "ALTER TABLE group_memberships DROP CONSTRAINT "
        "ck_group_memberships_membership_status_valid"
    )
    op.execute("ALTER TABLE groups DROP CONSTRAINT ck_groups_status_valid")
