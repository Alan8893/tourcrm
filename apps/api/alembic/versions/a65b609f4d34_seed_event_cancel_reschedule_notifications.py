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
publication additionally needs a configured route.

## Ownership and idempotency

Every row this migration creates has a fixed primary key, frozen below.
Each insert is `ON CONFLICT DO NOTHING`: when a template with the same
`code` or a rule with the same (club, event type, channel, recipient
scope) already exists, nothing is inserted and the existing row — its
body, `is_active`, `is_enabled` and every other value — is left exactly as
it was. Re-running `upgrade()` therefore never duplicates or overwrites a
row.

## Rollback

`downgrade()` deletes only rows carrying one of the fixed primary keys,
i.e. rows this migration created; a row that existed before `upgrade()`
has another key and is never touched. Before deleting anything it refuses
(raises) when a Notification references one of this migration's
templates, so notification history is never deleted. The refusal happens
before any delete and the downgrade runs in one transaction, so nothing is
removed partially.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a65b609f4d34'
down_revision: Union[str, None] = 'ddc70357f0cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration: (fixed id, code, body).
_TEMPLATES = (
    (
        "afb3fb04-3048-44b3-9b76-7fba5b1f66e4",
        "event_cancelled.telegram",
        "Событие **{{event_title}}** отменено. Дата: {{event_datetime}}. "
        "Причина: {{cancellation_reason}}. {{event_url}}",
    ),
    (
        "82a8f4ac-a206-4fda-ba3e-0f3080e08500",
        "event_rescheduled.telegram",
        "Событие **{{event_title}}** перенесено: {{old_event_datetime}} → "
        "{{new_event_datetime}}. {{event_url}}",
    ),
)
# Frozen as of this migration: (fixed id, event type, recipient scope).
_RULES = (
    ("18d65f23-d098-469b-b25d-0e60dfa50139", "event.cancelled", "registered_participants"),
    ("0a29c097-be54-4f11-aed7-23da2102065c", "event.cancelled", "telegram_destination"),
    ("f316ed2a-b73d-444d-a865-37f4c65bee94", "event.rescheduled", "registered_participants"),
    ("73237d1a-bf3f-41e3-8eca-fc346b6c606d", "event.rescheduled", "telegram_destination"),
)
_TEMPLATE_IDS = [template_id for template_id, _code, _body in _TEMPLATES]
_RULE_IDS = [rule_id for rule_id, _event_type, _scope in _RULES]


def upgrade() -> None:
    bind = op.get_bind()
    for template_id, code, body in _TEMPLATES:
        bind.execute(
            sa.text(
                "INSERT INTO notification_templates "
                "(id, code, channel, locale, body_template, version, is_active) "
                "VALUES (CAST(:id AS uuid), :code, 'telegram', 'ru', :body, 1, true) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": template_id, "code": code, "body": body},
        )
    for rule_id, event_type, recipient_scope in _RULES:
        bind.execute(
            sa.text(
                "INSERT INTO notification_rules "
                "(id, club_id, event_type, channel, recipient_scope, is_enabled) "
                "VALUES (CAST(:id AS uuid), NULL, :event_type, 'telegram', :scope, false) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": rule_id, "event_type": event_type, "scope": recipient_scope},
        )


def downgrade() -> None:
    bind = op.get_bind()
    referenced = bind.execute(
        sa.text(
            "SELECT count(*) FROM notifications "
            "WHERE template_id IN (SELECT CAST(t AS uuid) FROM unnest(CAST(:ids AS text[])) t)"
        ),
        {"ids": _TEMPLATE_IDS},
    ).scalar_one()
    if referenced:
        raise RuntimeError(
            "notifications reference the event cancellation/reschedule templates; "
            "downgrade refused to keep their history"
        )
    bind.execute(
        sa.text(
            "DELETE FROM notification_rules "
            "WHERE id IN (SELECT CAST(r AS uuid) FROM unnest(CAST(:ids AS text[])) r)"
        ),
        {"ids": _RULE_IDS},
    )
    bind.execute(
        sa.text(
            "DELETE FROM notification_templates "
            "WHERE id IN (SELECT CAST(t AS uuid) FROM unnest(CAST(:ids AS text[])) t)"
        ),
        {"ids": _TEMPLATE_IDS},
    )
