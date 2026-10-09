"""create telegram linking tables

Revision ID: 162f2e413172
Revises: 8c7a78e7178a
Create Date: 2026-10-09 01:07:22.171575

Issue #329 — Telegram user linking and long-polling persistence
(ADR-0047 §3.2/§4.2, docs/03-architecture/database-schema.md §18):

- `telegram_identities`: User <-> Telegram numeric user id, lifecycle
  `active`/`unlinked`/`replaced` (never deleted). Partial UNIQUE indexes
  over `status = 'active'`: one active identity per Telegram user id and
  one per User;
- `telegram_link_challenges`: one-time linking challenges — SHA-256
  `token_hash` only (UNIQUE), `pending`/`consumed`/`revoked` with
  CHECK-consistent timestamps, at most one `pending` challenge per User;
- `telegram_update_checkpoints`: the durable `getUpdates` offset per bot
  id (the bot's public numeric id, never its token). No raw update is
  stored.

It also amends ADR-0024 §4's closed audit action vocabulary (ADR-0047
§4.3) with `telegram_link_challenge.created`, `telegram_identity.linked`
and `telegram_identity.unlinked`. Alembic's autogenerate does not diff
CHECK constraint bodies, so that swap is hand-written, matching
7d2b4e9f1a63_add_trip_audit_actions.py.

Downgrade drops the three tables and restores the previous audit CHECK
constraint. Audit rows are append-only (ADR-0024 §3) and are never
deleted here: if rows with one of the Telegram actions exist, PostgreSQL
rejects the narrower constraint and the downgrade fails instead of
silently losing audit history.

No secret/credential column, no seed data, no permission or role grant.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '162f2e413172'
down_revision: Union[str, None] = '8c7a78e7178a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_ACTION_VALUES = (
    "'attendance.bulk_changed','attendance.changed','attendance.corrected',"
    "'attendance.created','document.created','document.downloaded',"
    "'document.exported','document.replaced','document.revoked',"
    "'document.updated','event.archived','event.created',"
    "'event.status_changed','event.updated',"
    "'event_occurrence.exception_changed','event_occurrence.exception_created',"
    "'event_occurrence.series_rebound','event_occurrence.status_changed',"
    "'event_occurrence_group_target.changed',"
    "'event_occurrence_group_target.created',"
    "'event_occurrence_group_target.ended',"
    "'event_occurrence_participant.changed',"
    "'event_occurrence_participant.created',"
    "'event_occurrence_participant.ended',"
    "'event_occurrence_staff_assignment.changed',"
    "'event_occurrence_staff_assignment.created',"
    "'event_occurrence_staff_assignment.ended',"
    "'event_participation.status_changed','event_series.created',"
    "'event_series.status_changed','event_series.updated',"
    "'event_series.version_created','event_series_group_target.changed',"
    "'event_series_group_target.created','event_series_group_target.ended',"
    "'event_series_participant.changed','event_series_participant.created',"
    "'event_series_participant.ended','event_series_staff_assignment.changed',"
    "'event_series_staff_assignment.created',"
    "'event_series_staff_assignment.ended','group.created','group.updated',"
    "'group_instructor_assignment.created','group_instructor_assignment.ended',"
    "'group_instructor_assignment.updated','group_membership.created',"
    "'group_membership.ended','group_membership.updated',"
    "'guardian_relationship.created','guardian_relationship.revoked',"
    "'guardian_relationship.updated','membership.created','membership.ended',"
    "'membership.import.applied','membership.status_changed',"
    "'membership.updated','password_reset_challenge.completed',"
    "'password_reset_challenge.created','person.archived','person.created',"
    "'person.updated','role_assignment.changed','role_assignment.created',"
    "'role_assignment.revoked','trip.created',"
    "'trip_participant.actual_participation_changed',"
    "'trip_participant.actual_participation_recorded','user.created',"
    "'user.locked','user.status_changed','user.unlocked'"
)
_NEW_ACTION_VALUES = (
    "'attendance.bulk_changed','attendance.changed','attendance.corrected',"
    "'attendance.created','document.created','document.downloaded',"
    "'document.exported','document.replaced','document.revoked',"
    "'document.updated','event.archived','event.created',"
    "'event.status_changed','event.updated',"
    "'event_occurrence.exception_changed','event_occurrence.exception_created',"
    "'event_occurrence.series_rebound','event_occurrence.status_changed',"
    "'event_occurrence_group_target.changed',"
    "'event_occurrence_group_target.created',"
    "'event_occurrence_group_target.ended',"
    "'event_occurrence_participant.changed',"
    "'event_occurrence_participant.created',"
    "'event_occurrence_participant.ended',"
    "'event_occurrence_staff_assignment.changed',"
    "'event_occurrence_staff_assignment.created',"
    "'event_occurrence_staff_assignment.ended',"
    "'event_participation.status_changed','event_series.created',"
    "'event_series.status_changed','event_series.updated',"
    "'event_series.version_created','event_series_group_target.changed',"
    "'event_series_group_target.created','event_series_group_target.ended',"
    "'event_series_participant.changed','event_series_participant.created',"
    "'event_series_participant.ended','event_series_staff_assignment.changed',"
    "'event_series_staff_assignment.created',"
    "'event_series_staff_assignment.ended','group.created','group.updated',"
    "'group_instructor_assignment.created','group_instructor_assignment.ended',"
    "'group_instructor_assignment.updated','group_membership.created',"
    "'group_membership.ended','group_membership.updated',"
    "'guardian_relationship.created','guardian_relationship.revoked',"
    "'guardian_relationship.updated','membership.created','membership.ended',"
    "'membership.import.applied','membership.status_changed',"
    "'membership.updated','password_reset_challenge.completed',"
    "'password_reset_challenge.created','person.archived','person.created',"
    "'person.updated','role_assignment.changed','role_assignment.created',"
    "'role_assignment.revoked','telegram_identity.linked',"
    "'telegram_identity.unlinked','telegram_link_challenge.created',"
    "'trip.created','trip_participant.actual_participation_changed',"
    "'trip_participant.actual_participation_recorded','user.created',"
    "'user.locked','user.status_changed','user.unlocked'"
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('telegram_update_checkpoints',
    sa.Column('bot_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('last_update_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('bot_id > 0', name='ck_telegram_update_checkpoints_bot_id_positive'),
    sa.CheckConstraint('last_update_id IS NULL OR last_update_id >= 0', name='ck_telegram_update_checkpoints_last_update_id_non_negative'),
    sa.PrimaryKeyConstraint('bot_id')
    )
    op.create_table('telegram_identities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'active') = (ended_at IS NULL)", name='ck_telegram_identities_ended_at_iff_ended'),
    sa.CheckConstraint("status IN ('active','replaced','unlinked')", name='ck_telegram_identities_status_valid'),
    sa.CheckConstraint('ended_at IS NULL OR ended_at >= linked_at', name='ck_telegram_identities_ended_after_linked'),
    sa.CheckConstraint('telegram_user_id > 0', name='ck_telegram_identities_telegram_user_id_positive'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_telegram_identities_telegram_user_id', 'telegram_identities', ['telegram_user_id'], unique=False)
    op.create_index('ix_telegram_identities_user_id', 'telegram_identities', ['user_id'], unique=False)
    op.create_index('uq_telegram_identities_active_telegram_user_id', 'telegram_identities', ['telegram_user_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index('uq_telegram_identities_active_user_id', 'telegram_identities', ['user_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_table('telegram_link_challenges',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('telegram_identity_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("(status = 'consumed') = (consumed_at IS NOT NULL)", name='ck_telegram_link_challenges_consumed_at_iff_consumed'),
    sa.CheckConstraint("(status = 'consumed') = (telegram_identity_id IS NOT NULL)", name='ck_telegram_link_challenges_identity_iff_consumed'),
    sa.CheckConstraint("(status = 'revoked') = (revoked_at IS NOT NULL)", name='ck_telegram_link_challenges_revoked_at_iff_revoked'),
    sa.CheckConstraint("status IN ('consumed','pending','revoked')", name='ck_telegram_link_challenges_status_valid'),
    sa.CheckConstraint('expires_at > created_at', name='ck_telegram_link_challenges_expires_after_created'),
    sa.ForeignKeyConstraint(['telegram_identity_id'], ['telegram_identities.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash', name='uq_telegram_link_challenges_token_hash')
    )
    op.create_index('ix_telegram_link_challenges_user_id_created_at', 'telegram_link_challenges', ['user_id', 'created_at'], unique=False)
    op.create_index('uq_telegram_link_challenges_pending_user_id', 'telegram_link_challenges', ['user_id'], unique=True, postgresql_where=sa.text("status = 'pending'"))
    # ### end Alembic commands ###
    op.drop_constraint('ck_audit_logs_action_valid', 'audit_logs', type_='check')
    op.create_check_constraint(
        'ck_audit_logs_action_valid', 'audit_logs', f'action IN ({_NEW_ACTION_VALUES})'
    )


def downgrade() -> None:
    op.drop_constraint('ck_audit_logs_action_valid', 'audit_logs', type_='check')
    op.create_check_constraint(
        'ck_audit_logs_action_valid', 'audit_logs', f'action IN ({_OLD_ACTION_VALUES})'
    )
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('uq_telegram_link_challenges_pending_user_id', table_name='telegram_link_challenges', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index('ix_telegram_link_challenges_user_id_created_at', table_name='telegram_link_challenges')
    op.drop_table('telegram_link_challenges')
    op.drop_index('uq_telegram_identities_active_user_id', table_name='telegram_identities', postgresql_where=sa.text("status = 'active'"))
    op.drop_index('uq_telegram_identities_active_telegram_user_id', table_name='telegram_identities', postgresql_where=sa.text("status = 'active'"))
    op.drop_index('ix_telegram_identities_user_id', table_name='telegram_identities')
    op.drop_index('ix_telegram_identities_telegram_user_id', table_name='telegram_identities')
    op.drop_table('telegram_identities')
    op.drop_table('telegram_update_checkpoints')
    # ### end Alembic commands ###
