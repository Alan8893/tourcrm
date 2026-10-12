"""password reset code email delivery

Revision ID: b6655ba7d0c7
Revises: 700a5f703346
Create Date: 2026-10-12 12:00:00.000000

Issue #342 (auth-api.md §13/§14, authentication-persistence.md §5,
ADR-0045 §4): self-service password recovery delivers its one-time code by
email through the Notification Center and the outbox.

- `password_reset_challenges.delivery_secret_ciphertext` (nullable text):
  the AES-256-GCM encrypted copy of the raw challenge secret, kept only
  while its email delivery is pending. The key comes from the deployment
  secret AUTH_DELIVERY_ENCRYPTION_KEYS and is never stored in the
  database. Existing rows get NULL (nothing to deliver).
- CHECK `ck_password_reset_challenges_delivery_secret_only_while_open`: a
  consumed or revoked challenge never keeps an encrypted copy.
- the email template `auth_password_reset_code.email` (locale `ru`,
  `is_active = true`), fixed primary key, `ON CONFLICT DO NOTHING` — the
  same ownership pattern as a65b609f4d34. Its body carries the single
  placeholder `{{code}}`, filled only in worker memory at send time. No
  notification rule is seeded: this security email is not governed by
  notification rules or user preferences, only by the Global email switch.

Re-running `upgrade()` over an already upgraded schema changes nothing
(column/constraint creation is conditional, the template insert is
`ON CONFLICT DO NOTHING`).

## Rollback

`downgrade()` refuses (raises) while a Notification references the
template, so notification history is never deleted, and while a challenge
still holds an encrypted copy, so a pending delivery is never silently
dropped. Otherwise it removes the template row this migration created (by
its fixed id), the constraint and the column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6655ba7d0c7'
down_revision: Union[str, None] = '700a5f703346'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen as of this migration.
_TEMPLATE_ID = "7fef6d48-6e6b-4aa2-8daf-c5c5311d954d"
_TEMPLATE_CODE = "auth_password_reset_code.email"
_SUBJECT = "TourCRM: код для установки пароля"
_BODY = (
    "Здравствуйте!\n"
    "\n"
    "Для вашей учётной записи TourCRM запрошено восстановление пароля.\n"
    "\n"
    "Одноразовый код: {{code}}\n"
    "\n"
    "Откройте страницу входа TourCRM, выберите «У меня есть одноразовый код», "
    "введите этот код и задайте новый пароль. Код действует 1 час и может быть "
    "использован только один раз.\n"
    "\n"
    "Если вы не запрашивали восстановление пароля, просто проигнорируйте это "
    "письмо: ваш текущий пароль не изменится.\n"
)
_CONSTRAINT = "ck_password_reset_challenges_delivery_secret_only_while_open"


def upgrade() -> None:
    bind = op.get_bind()
    # Re-runnable (the seed-migration tests `stamp` an older revision and
    # upgrade over an already migrated schema): IF NOT EXISTS / catalog check.
    bind.execute(
        sa.text(
            "ALTER TABLE password_reset_challenges "
            "ADD COLUMN IF NOT EXISTS delivery_secret_ciphertext TEXT"
        )
    )
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": _CONSTRAINT}
    ).first()
    if exists is None:
        op.create_check_constraint(
            _CONSTRAINT,
            "password_reset_challenges",
            "delivery_secret_ciphertext IS NULL OR (consumed_at IS NULL AND revoked_at IS NULL)",
        )
    bind.execute(
        sa.text(
            "INSERT INTO notification_templates "
            "(id, code, channel, locale, subject_template, body_template, version, is_active) "
            "VALUES (CAST(:id AS uuid), :code, 'email', 'ru', :subject, :body, 1, true) "
            "ON CONFLICT DO NOTHING"
        ),
        {"id": _TEMPLATE_ID, "code": _TEMPLATE_CODE, "subject": _SUBJECT, "body": _BODY},
    )


def downgrade() -> None:
    bind = op.get_bind()
    referenced = bind.execute(
        sa.text("SELECT count(*) FROM notifications WHERE template_id = CAST(:id AS uuid)"),
        {"id": _TEMPLATE_ID},
    ).scalar_one()
    if referenced:
        raise RuntimeError(
            "notifications reference the password reset code email template; "
            "downgrade refused to keep their history"
        )
    pending = bind.execute(
        sa.text(
            "SELECT count(*) FROM password_reset_challenges "
            "WHERE delivery_secret_ciphertext IS NOT NULL"
        )
    ).scalar_one()
    if pending:
        raise RuntimeError(
            "password reset challenges still hold a pending delivery secret; "
            "downgrade refused"
        )
    bind.execute(
        sa.text("DELETE FROM notification_templates WHERE id = CAST(:id AS uuid)"),
        {"id": _TEMPLATE_ID},
    )
    op.drop_constraint(_CONSTRAINT, "password_reset_challenges", type_="check")
    op.drop_column("password_reset_challenges", "delivery_secret_ciphertext")
