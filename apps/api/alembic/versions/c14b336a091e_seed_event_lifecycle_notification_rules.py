"""Seed the first business notification rules and Telegram templates.

Issue #336. Global Telegram policy remains authoritative and OFF by default.
"""
from alembic import op
import sqlalchemy as sa

revision = "c14b336a091e"
down_revision = "bef882c4e71c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    templates = sa.table(
        "notification_templates",
        sa.column("code", sa.String),
        sa.column("channel", sa.String),
        sa.column("locale", sa.String),
        sa.column("subject_template", sa.Text),
        sa.column("body_template", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    bind.execute(
        sa.insert(templates),
        [
            {
                "code": "event.cancelled.telegram.ru",
                "channel": "telegram",
                "locale": "ru",
                "subject_template": None,
                "body_template": "Мероприятие «{{event_title}}» отменено. Причина: {{cancellation_reason}}",
                "version": 1,
                "is_active": True,
            },
            {
                "code": "event.rescheduled.telegram.ru",
                "channel": "telegram",
                "locale": "ru",
                "subject_template": None,
                "body_template": "Изменилось время мероприятия «{{event_title}}». Новое время: {{new_event_datetime}}.",
                "version": 1,
                "is_active": True,
            },
        ],
    )

    rules = sa.table(
        "notification_rules",
        sa.column("club_id", sa.UUID),
        sa.column("event_type", sa.String),
        sa.column("channel", sa.String),
        sa.column("recipient_scope", sa.String),
        sa.column("is_enabled", sa.Boolean),
        sa.column("scheduling", sa.JSON),
    )
    bind.execute(
        sa.insert(rules),
        [
            {
                "club_id": None,
                "event_type": "event.cancelled",
                "channel": "telegram",
                "recipient_scope": "registered_participants",
                "is_enabled": True,
                "scheduling": None,
            },
            {
                "club_id": None,
                "event_type": "event.rescheduled",
                "channel": "telegram",
                "recipient_scope": "registered_participants",
                "is_enabled": True,
                "scheduling": None,
            },
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM notification_rules "
            "WHERE club_id IS NULL AND channel = 'telegram' "
            "AND recipient_scope = 'registered_participants' "
            "AND event_type IN ('event.cancelled', 'event.rescheduled')"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM notification_templates "
            "WHERE code IN ('event.cancelled.telegram.ru', 'event.rescheduled.telegram.ru')"
        )
    )
