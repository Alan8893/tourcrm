"""add notification destinations, personal preferences and render context

Revision ID: ddc70357f0cb
Revises: bef882c4e71c
Create Date: 2026-10-09 22:00:00.000000

Issue #336 PR-0 — ADR-0049 (business-event notification integration):

- `communication_preferences.destination_type` (NOT NULL, default
  `user`, CHECK `IN ('user')`): a per-event preference governs only the
  personal destination; the UNIQUE constraint becomes
  (user_id, channel, destination_type, notification_type). Existing rows
  are personal preferences and receive `user`;
- `communication_channel_preferences`: the personal master switch per
  (user_id, channel, destination_type), stored apart from the per-event
  preferences so toggling it never rewrites them. No row means OFF;
- `notifications.render_context` (JSONB object, default `{}`): the
  template's variable snapshot written in the business transaction;
- `notifications.recipient_user_id` becomes nullable and
  `notifications.recipient_destination_id` (FK `telegram_destinations`,
  RESTRICT) is added; CHECK: exactly one of the two is set. A
  group/topic publication is addressed to its route, not to a User;
- `telegram_destinations.notification_scope -> 'event_types'`, when
  present, must be a JSON array.

No rule, template, destination or preference row is created: every
business rule is seeded disabled by its own slice (ADR-0049 §2.9).

Downgrade reverses every step. It refuses (raises, changing nothing)
instead of deleting history when a Notification addressed to a route
exists; the personal master switches are dropped with their table and
the render contexts with their column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ddc70357f0cb'
down_revision: Union[str, None] = 'bef882c4e71c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- communication_preferences: destination dimension -----------------
    op.add_column(
        "communication_preferences",
        sa.Column(
            "destination_type", sa.String(length=32), server_default="user", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_communication_preferences_destination_type_valid",
        "communication_preferences",
        "destination_type IN ('user')",
    )
    op.drop_constraint(
        "uq_communication_preferences_user_channel_type",
        "communication_preferences",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_communication_preferences_user_channel_destination_type",
        "communication_preferences",
        ["user_id", "channel", "destination_type", "notification_type"],
    )

    # --- personal master switch -------------------------------------------
    op.create_table(
        "communication_channel_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column(
            "destination_type", sa.String(length=32), server_default="user", nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('email','telegram')",
            name="ck_communication_channel_preferences_channel_valid",
        ),
        sa.CheckConstraint(
            "destination_type IN ('user')",
            name="ck_communication_channel_preferences_destination_type_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "channel",
            "destination_type",
            name="uq_communication_channel_preferences_user_channel_destination",
        ),
    )

    # --- notifications: render context and route recipients ---------------
    op.add_column(
        "notifications",
        sa.Column(
            "render_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_notifications_render_context_is_object",
        "notifications",
        "jsonb_typeof(render_context) = 'object'",
    )
    op.add_column(
        "notifications", sa.Column("recipient_destination_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "notifications_recipient_destination_id_fkey",
        "notifications",
        "telegram_destinations",
        ["recipient_destination_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("notifications", "recipient_user_id", existing_type=sa.UUID(), nullable=True)
    op.create_check_constraint(
        "ck_notifications_exactly_one_recipient",
        "notifications",
        "(recipient_user_id IS NULL) <> (recipient_destination_id IS NULL)",
    )
    op.create_index(
        "ix_notifications_recipient_destination_id_created_at",
        "notifications",
        ["recipient_destination_id", "created_at"],
        unique=False,
    )

    # --- telegram_destinations: event_types shape --------------------------
    op.create_check_constraint(
        "ck_telegram_destinations_event_types_is_array",
        "telegram_destinations",
        "NOT (notification_scope ? 'event_types') "
        "OR jsonb_typeof(notification_scope -> 'event_types') = 'array'",
    )


def downgrade() -> None:
    # Publication history is never deleted to make the narrower schema fit.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM notifications WHERE recipient_user_id IS NULL) THEN
                RAISE EXCEPTION
                    'notifications addressed to a telegram destination exist; '
                    'downgrade refused to keep their history';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        "ck_telegram_destinations_event_types_is_array", "telegram_destinations", type_="check"
    )
    op.drop_index("ix_notifications_recipient_destination_id_created_at", table_name="notifications")
    op.drop_constraint("ck_notifications_exactly_one_recipient", "notifications", type_="check")
    op.alter_column("notifications", "recipient_user_id", existing_type=sa.UUID(), nullable=False)
    op.drop_constraint(
        "notifications_recipient_destination_id_fkey", "notifications", type_="foreignkey"
    )
    op.drop_column("notifications", "recipient_destination_id")
    op.drop_constraint("ck_notifications_render_context_is_object", "notifications", type_="check")
    op.drop_column("notifications", "render_context")

    op.drop_table("communication_channel_preferences")

    op.drop_constraint(
        "uq_communication_preferences_user_channel_destination_type",
        "communication_preferences",
        type_="unique",
    )
    # Every row is `user` (the only allowed value), so the narrower UNIQUE
    # constraint holds for the existing data.
    op.create_unique_constraint(
        "uq_communication_preferences_user_channel_type",
        "communication_preferences",
        ["user_id", "channel", "notification_type"],
    )
    op.drop_constraint(
        "ck_communication_preferences_destination_type_valid",
        "communication_preferences",
        type_="check",
    )
    op.drop_column("communication_preferences", "destination_type")
