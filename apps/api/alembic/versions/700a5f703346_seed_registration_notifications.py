"""seed registration notification rules and templates

Revision ID: 700a5f703346
Revises: a65b609f4d34
Create Date: 2026-10-11 10:00:00.000000

Issue #336 PR-2 (ADR-0049 §2.10, PO decision 13, template decision of
2026-10-09, PO decisions of 2026-10-11): seeds the Telegram templates and
the installation-wide personal rules of `registration.created` and
`registration.cancelled`.

- templates `registration_created.telegram` /
  `registration_cancelled.telegram` (catalog §4 text, locale `ru`,
  `is_active = true`); the withdrawal message carries no date/time;
- rules, all `is_enabled = false` and `club_id` NULL, Telegram only, with
  `recipient_scope = registrant`. No group/topic route rule: registration
  notifications are personal only (catalog `group_routing = false`). No
  Email rule.

An administrator enables a rule in Settings → Notifications after
verifying the slice; nothing is delivered before that.

## Ownership and idempotency

Same pattern as a65b609f4d34. Every row this migration creates has a fixed
primary key, frozen below. Each insert is `ON CONFLICT DO NOTHING`: an
existing template with the same `code` or rule with the same (club, event
type, channel, recipient scope) is left exactly as it was, so re-running
`upgrade()` never duplicates or overwrites a row.

## Rollback

`downgrade()` deletes only rows carrying one of the fixed primary keys,
i.e. rows this migration created. Before deleting anything it refuses
(raises) when a Notification references one of this migration's
templates, so notification history is never deleted and nothing is
removed partially (the downgrade runs in one transaction).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '700a5f703346'
down_revision: Union[str, None] = 'a65b609f4d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration: (fixed id, code, body).
_TEMPLATES = (
    (
        "6ce95ca6-150d-4f9b-9c74-8a0f8abc5fd7",
        "registration_created.telegram",
        "Вы зарегистрированы на **{{event_title}}**. Дата: {{event_datetime}}. {{event_url}}",
    ),
    (
        "47d50261-b5a7-43e8-be8c-a870220d69bb",
        "registration_cancelled.telegram",
        "Ваша регистрация на **{{event_title}}** отменена. {{event_url}}",
    ),
)
# Frozen as of this migration: (fixed id, event type, recipient scope).
_RULES = (
    ("c1f92c00-53b5-417f-ba2a-2b1f8f943408", "registration.created", "registrant"),
    ("33d73e97-1f6a-41f0-abac-13a398e2c6ff", "registration.cancelled", "registrant"),
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
            "notifications reference the registration templates; "
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
