"""drop guardian_relationship is_primary_contact (TH-0103)

ADR-0035 §8 (accepted 2026-09-17, later than ADR-0023) and TH-0103's own
task contract explicitly state there is no `primary`/`is_primary`/
`primary_guardian_id` concept for GuardianRelationship, including outside
the MVP — multiple active representatives are equal. ADR-0023 §3
originally defined `is_primary_contact` and a matching exclusion
constraint; `docs/05-api/people-api.md` §18 has already been reconciled
to ADR-0035 and states the field "is not part of the model". This
migration brings persistence into compliance by dropping the column and
its exclusion constraint. `docs/03-architecture/adr/ADR-0023-...md`,
`database-schema.md` and `domain-model.md` still list the removed field
and require a documentation-only follow-up outside this migration's
scope.

Revision ID: a3f1c9e5d8b2
Revises: f85df8d36f12
Create Date: 2026-09-17 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3f1c9e5d8b2'
down_revision: Union[str, None] = 'f85df8d36f12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE guardian_relationships DROP CONSTRAINT "
        "ck_guardian_relationships_no_overlapping_primary_contact"
    )
    op.drop_column('guardian_relationships', 'is_primary_contact')


def downgrade() -> None:
    op.add_column(
        'guardian_relationships',
        sa.Column(
            'is_primary_contact',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE guardian_relationships ADD CONSTRAINT "
        "ck_guardian_relationships_no_overlapping_primary_contact "
        "EXCLUDE USING gist ("
        "child_person_id WITH =, "
        "tstzrange(valid_from, valid_to) WITH &&"
        ") WHERE (is_primary_contact = true AND status = 'active')"
    )
