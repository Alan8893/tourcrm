"""create notification settings tables

Revision ID: bef882c4e71c
Revises: 162f2e413172
Create Date: 2026-10-09 08:14:39.306396


Issue #333 — Administrator Notification Settings (ADR-0048 §2.6–§2.11):

- `notification_global_policy`: the installation-wide Global Admin Policy,
  one boolean per MVP channel; singleton (`id = 1`). No row = every
  channel OFF (fail closed). No club-level policy;
- `notification_email_settings` / `notification_telegram_settings`:
  non-secret SMTP and Telegram bot configuration; singletons;
- `integration_secrets`: the UI-managed secrets (SMTP password, Telegram
  bot token) as AES-256-GCM tokens only; the keys are deployment
  configuration and are never stored here;
- `notification_test_send_attempts`: persistent basis of the test-send
  rate limit (no address, chat id or message).

It also amends ADR-0024 §4's closed audit action vocabulary (ADR-0048
§2.11) with six `notification_*` actions; the CHECK swap is hand-written,
matching 162f2e413172. Downgrade drops the tables (the stored secrets are
lost and must be re-entered) and restores the previous audit CHECK; it
fails instead of deleting audit history if rows with the new actions exist.

No seed data, no permission or role grant.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bef882c4e71c'
down_revision: Union[str, None] = '162f2e413172'
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
    "'role_assignment.revoked','telegram_identity.linked',"
    "'telegram_identity.unlinked','telegram_link_challenge.created',"
    "'trip.created','trip_participant.actual_participation_changed',"
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
    "'membership.updated','notification_integration.updated',"
    "'notification_policy.updated','notification_rule.updated',"
    "'notification_secret.cleared','notification_secret.set',"
    "'notification_test_send.attempted','password_reset_challenge.completed',"
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
    op.create_table('integration_secrets',
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("ciphertext LIKE 'v1.%%'", name='ck_integration_secrets_ciphertext_versioned'),
    sa.CheckConstraint("name IN ('smtp_password','telegram_bot_token')", name='ck_integration_secrets_name_valid'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('notification_email_settings',
    sa.Column('id', sa.SmallInteger(), autoincrement=False, nullable=False),
    sa.Column('smtp_host', sa.String(length=255), nullable=True),
    sa.Column('smtp_port', sa.Integer(), nullable=True),
    sa.Column('smtp_security', sa.String(length=16), server_default='starttls', nullable=False),
    sa.Column('smtp_username', sa.String(length=255), nullable=True),
    sa.Column('sender_email', sa.String(length=320), nullable=True),
    sa.Column('sender_name', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("smtp_host IS NULL OR btrim(smtp_host) <> ''", name='ck_notification_email_settings_host_not_blank'),
    sa.CheckConstraint("smtp_security IN ('none','ssl','starttls')", name='ck_notification_email_settings_security_valid'),
    sa.CheckConstraint('id = 1', name='ck_notification_email_settings_singleton'),
    sa.CheckConstraint('smtp_port IS NULL OR smtp_port BETWEEN 1 AND 65535', name='ck_notification_email_settings_port_range'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('notification_global_policy',
    sa.Column('id', sa.SmallInteger(), autoincrement=False, nullable=False),
    sa.Column('email_enabled', sa.Boolean(), nullable=False),
    sa.Column('telegram_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.CheckConstraint('id = 1', name='ck_notification_global_policy_singleton'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('notification_telegram_settings',
    sa.Column('id', sa.SmallInteger(), autoincrement=False, nullable=False),
    sa.Column('bot_username', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("bot_username IS NULL OR btrim(bot_username) <> ''", name='ck_notification_telegram_settings_username_not_blank'),
    sa.CheckConstraint('id = 1', name='ck_notification_telegram_settings_singleton'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('notification_test_send_attempts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('destination_kind', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('outcome', sa.String(length=16), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.CheckConstraint("channel IN ('email','telegram')", name='ck_notification_test_send_attempts_channel_valid'),
    sa.CheckConstraint("destination_kind IN ('email_address','own_telegram_account','telegram_destination')", name='ck_notification_test_send_attempts_destination_kind_valid'),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('delivered','failed')", name='ck_notification_test_send_attempts_outcome_valid'),
    sa.CheckConstraint('(outcome IS NULL) = (completed_at IS NULL)', name='ck_notification_test_send_attempts_completed_iff_outcome'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notification_test_send_attempts_actor_created', 'notification_test_send_attempts', ['actor_user_id', 'created_at'], unique=False)
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
    op.drop_index('ix_notification_test_send_attempts_actor_created', table_name='notification_test_send_attempts')
    op.drop_table('notification_test_send_attempts')
    op.drop_table('notification_telegram_settings')
    op.drop_table('notification_global_policy')
    op.drop_table('notification_email_settings')
    op.drop_table('integration_secrets')
    # ### end Alembic commands ###
