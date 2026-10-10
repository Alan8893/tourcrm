"""seed event cancellation and reschedule notification rules and templates

Revision ID: a65b609f4d34
Revises: ddc70357f0cb
Create Date: 2026-10-10 10:00:00.000000

Issue #336 PR-1 (ADR-0049 §2.10, PO decision 13, template decision of
2026-10-09): seeds the Telegram templates and the installation-wide rules
of `event.cancelled` and `event.rescheduled`.

- templates `event_cancelled.telegram` / `event_rescheduled.telegram`
  (catalog §4 text, locale `ru`, `is_active = true`);
- rules, all `is_enabled = false` and `club_id` NULL: the personal rule
  (`recipient_scope = registered_participants`) and the group/topic route
  rule (`recipient_scope = telegram_destination`) of each event type.
  Telegram only — no Email rule.

An administrator enables a rule in Settings → Notifications after
verifying the slice; nothing is delivered before that, and route
publication additionally needs a configured route. Inserts are
`ON CONFLICT DO NOTHING`, so an existing row is never overwritten.

Downgrade deletes the four rules and the two templates. It refuses
(raises, changing nothing) instead of deleting history when a
Notification already references one of the templates.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a65b609f4d34'
down_revision: Union[str, None] = 'ddc70357f0cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATES = (
    (
        "event_cancelled.telegram",
        "Событие **{{event_title}}** отменено. Дата: {{event_datetime}}. "
        "Причина: {{cancellation_reason}}. {{event_url}}",
    ),
    (
        "event_rescheduled.telegram",
        "Событие **{{event_title}}** перенесено: {{old_event_datetime}} → "
        "{{new_event_datetime}}. {{event_url}}",
    ),
)
_RULES = (
    ("event.cancelled", "registered_participants"),
    ("event.cancelled", "telegram_destination"),
    ("event.rescheduled", "registered_participants"),
    ("event.rescheduled", "telegram_destination"),
)
_TEMPLATE_CODES = tuple(code for code, _body in _TEMPLATES)
_EVENT_TYPES = ("event.cancelled", "event.rescheduled")


def upgrade() -> None:
    for code, body in _TEMPLATES:
        op.get_bind().execute(
            sa.text(
                "INSERT INTO notification_templates "
                "(id, code, channel, locale, body_template, version, is_active) "
                "VALUES (gen_random_uuid(), :code, 'telegram', 'ru', :body, 1, true) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "body": body},
        )
    for event_type, recipient_scope in _RULES:
        op.get_bind().execute(
            sa.text(
                "INSERT INTO notification_rules "
                "(id, club_id, event_type, channel, recipient_scope, is_enabled) "
                "VALUES (gen_random_uuid(), NULL, :event_type, 'telegram', :scope, false) "
                "ON CONFLICT ON CONSTRAINT uq_notification_rules_club_event_channel_scope "
                "DO NOTHING"
            ),
            {"event_type": event_type, "scope": recipient_scope},
        )


def downgrade() -> None:
    referenced = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM notifications n "
            "JOIN notification_templates t ON t.id = n.template_id "
            "WHERE t.code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list(_TEMPLATE_CODES)},
    ).scalar_one()
    if referenced:
        raise RuntimeError(
            "notifications reference the event cancellation/reschedule templates; "
            "downgrade refused to keep their history"
        )
    op.get_bind().execute(
        sa.text(
            "DELETE FROM notification_rules WHERE club_id IS NULL "
            "AND channel = 'telegram' AND event_type IN :event_types "
            "AND recipient_scope IN ('registered_participants', 'telegram_destination')"
        ).bindparams(sa.bindparam("event_types", expanding=True)),
        {"event_types": list(_EVENT_TYPES)},
    )
    op.get_bind().execute(
        sa.text("DELETE FROM notification_templates WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": list(_TEMPLATE_CODES)},
    )
