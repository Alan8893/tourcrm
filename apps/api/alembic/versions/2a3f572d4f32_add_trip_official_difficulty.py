"""add trip official difficulty

Revision ID: 2a3f572d4f32
Revises: c4f8a2e61d93
Create Date: 2026-10-05 11:36:19.566025

Issue #268 (Tourism Facts v2 — Official Difficulty Foundation), canonical
source docs/04-modules/trips-and-tourist-profile.md §4 (Issue #257):

- adds the nullable `trips.official_difficulty_mode`/`_value`/`_source`
  columns: one structured classification per Trip (0..1 by construction —
  it lives on the Trip row). Existing Trip rows keep all three `NULL`
  (no Difficulty);
- `ck_trips_official_difficulty_combination` allows only the approved
  combinations: no Difficulty; `NONE` without value (source optional);
  `DEGREE` I–III with source; `CATEGORY` I–VI with source; `WEEKEND`
  without value, with source;
- `ck_trips_official_difficulty_source_not_blank` rejects a blank source.

No seed rows, no catalog table, no permission: Official Difficulty reuses
`trip.read`/`trip.manage`.

Downgrade drops the constraints and the columns (meant for an
installation that has not yet relied on Official Difficulty).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a3f572d4f32'
down_revision: Union[str, None] = 'c4f8a2e61d93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMBINATIONS_SQL = (
    # COALESCE: a CHECK whose expression is NULL passes, and `value IN
    # (...)`/`mode = ...` are NULL for a NULL column — never let that
    # unknown slip through as "allowed".
    "COALESCE("
    "(official_difficulty_mode IS NULL"
    " AND official_difficulty_value IS NULL AND official_difficulty_source IS NULL)"
    " OR (official_difficulty_mode = 'NONE' AND official_difficulty_value IS NULL)"
    " OR (official_difficulty_mode = 'DEGREE'"
    " AND official_difficulty_value IN ('I', 'II', 'III')"
    " AND official_difficulty_source IS NOT NULL)"
    " OR (official_difficulty_mode = 'CATEGORY'"
    " AND official_difficulty_value IN ('I', 'II', 'III', 'IV', 'V', 'VI')"
    " AND official_difficulty_source IS NOT NULL)"
    " OR (official_difficulty_mode = 'WEEKEND' AND official_difficulty_value IS NULL"
    " AND official_difficulty_source IS NOT NULL),"
    " false)"
)


def upgrade() -> None:
    op.add_column('trips', sa.Column('official_difficulty_mode', sa.String(length=16), nullable=True))
    op.add_column('trips', sa.Column('official_difficulty_value', sa.String(length=8), nullable=True))
    op.add_column('trips', sa.Column('official_difficulty_source', sa.String(length=500), nullable=True))
    op.create_check_constraint(
        'ck_trips_official_difficulty_combination', 'trips', _COMBINATIONS_SQL
    )
    op.create_check_constraint(
        'ck_trips_official_difficulty_source_not_blank',
        'trips',
        'official_difficulty_source IS NULL OR length(btrim(official_difficulty_source)) > 0',
    )


def downgrade() -> None:
    op.drop_constraint('ck_trips_official_difficulty_source_not_blank', 'trips', type_='check')
    op.drop_constraint('ck_trips_official_difficulty_combination', 'trips', type_='check')
    op.drop_column('trips', 'official_difficulty_source')
    op.drop_column('trips', 'official_difficulty_value')
    op.drop_column('trips', 'official_difficulty_mode')
