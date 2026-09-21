"""create document/file persistence foundation

Revision ID: c70bcac43032
Revises: 9a1c2f5e7b3d
Create Date: 2026-09-21 15:30:00.000000

TH-0117.1 / Issue #156, implementing the accepted architecture baseline in
ADR-0040 (Document Domain and File Storage): creates the persistence
foundation for the participant Document/File domain —

- `files` (ADR-0040 §1; database-schema.md §11): immutable physical file
  artifact metadata. No binary content column — content is stored outside
  PostgreSQL (ADR-0006/ADR-0011/ADR-0040 §3), unimplemented in this task.
- `documents` (ADR-0040 §1/§2/§4; database-schema.md §15.1): the versioned
  participant document, explicitly owned by `person_id` (never a
  polymorphic `subject_type`/`subject_id` pair). `status` is restricted to
  exactly `active`/`expired`/`revoked` — `missing` is never a stored value,
  and no `revoked -> expired` mapping is introduced.
- `event_document_requirements` (ADR-0040 §5; database-schema.md §15.2): an
  Event's declared requirement for a document type, unique per
  `(event_id, document_type)`. Does not couple `Document` to `Event`
  directly.

This migration also amends the closed audit action vocabulary (ADR-0040
§7 amendment to ADR-0024 §4) with `document.created`/`document.updated`/
`document.replaced`/`document.revoked`/`document.downloaded`/
`document.exported`, and seeds the new `document.export` permission
(`document.read`/`document.manage` already exist — seeded by
e5ae1ad9e1e1 — and were already granted to `admin` by 6a99a77234ba).

No FileStorage adapter, upload/download endpoint, Document/
EventDocumentRequirement API, validity/check engine, or export/package
generation is implemented here (Issue #156 §7) — persistence only.

`Person.photo_file_id` is deliberately left as-is (no FK added): ADR-0040
itself states that adding this FK is a separate future implementation
task, not part of this ADR baseline — this migration does not touch the
`persons` table.

## Seed permission

Mirrors 75b7574b6e44's/f85df8d36f12's identical shape: seeds the
`permissions` row for `document.export` and grants it only to `admin`
(the existing, already-established "admin receives every canonical
permission" bootstrap invariant from 6a99a77234ba/TH-0092 — not a new
role/scope decision; `instructor`/`member`/`guardian` receive nothing
here, matching Issue #156's explicit instruction not to invent role/scope
grants beyond what an existing canonical bootstrap mechanism already
requires).

## Idempotency

The permission-seed inserts use `ON CONFLICT ... DO NOTHING`, matching
the established idempotent-seed shape (e5ae1ad9e1e1/65aa6d48bf08/
6a99a77234ba/f85df8d36f12/75b7574b6e44/95487f3b616b). Alembic's
autogenerate does not diff CHECK constraint bodies, so the audit-action
CHECK swap is hand-written, matching 668662ab95e0/0bada762f164's own
precedent.

## Rollback

`downgrade()` drops the three new tables (in FK-dependency order:
`event_document_requirements`, `documents`, `files`), restores the prior
audit-action CHECK constraint, and removes only the `(admin,
document.export)` `role_permissions` row — it deliberately does not
delete the `document.export` row from `permissions` itself, matching the
same precedent `f85df8d36f12`/`75b7574b6e44` already established ("once a
canonical permission code exists, it is stable catalog data").
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = 'c70bcac43032'
down_revision: Union[str, None] = '9a1c2f5e7b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_CODE = "document.export"
_ADMIN_ROLE_CODE = "admin"

_OLD_ACTION_VALUES = (
    "'attendance.bulk_changed','attendance.changed','attendance.corrected',"
    "'attendance.created','event.archived','event.created','event.status_changed',"
    "'event.updated','event_occurrence.exception_changed',"
    "'event_occurrence.exception_created','event_occurrence.series_rebound',"
    "'event_occurrence.status_changed','event_occurrence_group_target.changed',"
    "'event_occurrence_group_target.created','event_occurrence_group_target.ended',"
    "'event_occurrence_participant.changed','event_occurrence_participant.created',"
    "'event_occurrence_participant.ended',"
    "'event_occurrence_staff_assignment.changed',"
    "'event_occurrence_staff_assignment.created',"
    "'event_occurrence_staff_assignment.ended','event_participation.status_changed',"
    "'event_series.created','event_series.status_changed','event_series.updated',"
    "'event_series.version_created','event_series_group_target.changed',"
    "'event_series_group_target.created','event_series_group_target.ended',"
    "'event_series_participant.changed','event_series_participant.created',"
    "'event_series_participant.ended','event_series_staff_assignment.changed',"
    "'event_series_staff_assignment.created','event_series_staff_assignment.ended',"
    "'group.created','group.updated','group_instructor_assignment.created',"
    "'group_instructor_assignment.ended','group_instructor_assignment.updated',"
    "'group_membership.created','group_membership.ended','group_membership.updated',"
    "'guardian_relationship.created','guardian_relationship.revoked',"
    "'guardian_relationship.updated','membership.created','membership.ended',"
    "'membership.status_changed','membership.updated',"
    "'password_reset_challenge.completed','password_reset_challenge.created',"
    "'person.archived','person.created','person.updated','role_assignment.changed',"
    "'role_assignment.created','role_assignment.revoked','user.created',"
    "'user.locked','user.status_changed','user.unlocked'"
)
_NEW_ACTION_VALUES = (
    "'attendance.bulk_changed','attendance.changed','attendance.corrected',"
    "'attendance.created','document.created','document.downloaded',"
    "'document.exported','document.replaced','document.revoked','document.updated',"
    "'event.archived','event.created','event.status_changed',"
    "'event.updated','event_occurrence.exception_changed',"
    "'event_occurrence.exception_created','event_occurrence.series_rebound',"
    "'event_occurrence.status_changed','event_occurrence_group_target.changed',"
    "'event_occurrence_group_target.created','event_occurrence_group_target.ended',"
    "'event_occurrence_participant.changed','event_occurrence_participant.created',"
    "'event_occurrence_participant.ended',"
    "'event_occurrence_staff_assignment.changed',"
    "'event_occurrence_staff_assignment.created',"
    "'event_occurrence_staff_assignment.ended','event_participation.status_changed',"
    "'event_series.created','event_series.status_changed','event_series.updated',"
    "'event_series.version_created','event_series_group_target.changed',"
    "'event_series_group_target.created','event_series_group_target.ended',"
    "'event_series_participant.changed','event_series_participant.created',"
    "'event_series_participant.ended','event_series_staff_assignment.changed',"
    "'event_series_staff_assignment.created','event_series_staff_assignment.ended',"
    "'group.created','group.updated','group_instructor_assignment.created',"
    "'group_instructor_assignment.ended','group_instructor_assignment.updated',"
    "'group_membership.created','group_membership.ended','group_membership.updated',"
    "'guardian_relationship.created','guardian_relationship.revoked',"
    "'guardian_relationship.updated','membership.created','membership.ended',"
    "'membership.status_changed','membership.updated',"
    "'password_reset_challenge.completed','password_reset_challenge.created',"
    "'person.archived','person.created','person.updated','role_assignment.changed',"
    "'role_assignment.created','role_assignment.revoked','user.created',"
    "'user.locked','user.status_changed','user.unlocked'"
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('original_name', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=255), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('checksum', sa.String(length=128), nullable=False),
    sa.Column('storage_backend', sa.String(length=32), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key')
    )
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('person_id', sa.UUID(), nullable=False),
    sa.Column('document_group_id', sa.UUID(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('document_type', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('file_id', sa.UUID(), nullable=False),
    sa.Column('uploaded_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("issued_at IS NULL OR expires_at IS NULL OR expires_at >= issued_at", name='ck_documents_expires_at_after_issued_at'),
    sa.CheckConstraint("status IN ('active','expired','revoked')", name='ck_documents_status_valid'),
    sa.CheckConstraint("version_number >= 1", name='ck_documents_version_number_positive'),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_group_id', 'version_number', name='uq_documents_group_id_version_number')
    )
    op.create_index('ix_documents_document_group_id', 'documents', ['document_group_id'], unique=False)
    op.create_index('ix_documents_file_id', 'documents', ['file_id'], unique=False)
    op.create_index('ix_documents_person_id', 'documents', ['person_id'], unique=False)
    op.create_table('event_document_requirements',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('document_type', sa.String(length=64), nullable=False),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'document_type', name='uq_event_document_requirements_event_id_type')
    )
    op.create_index('ix_event_document_requirements_event_id', 'event_document_requirements', ['event_id'], unique=False)
    # ### end Alembic commands ###

    op.drop_constraint('ck_audit_logs_action_valid', 'audit_logs', type_='check')
    op.create_check_constraint(
        'ck_audit_logs_action_valid', 'audit_logs', f'action IN ({_NEW_ACTION_VALUES})'
    )

    bind = op.get_bind()
    permissions_table = sa.table(
        "permissions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code", sa.String),
    )
    bind.execute(
        pg_insert(permissions_table)
        .values([{"id": uuid.uuid4(), "code": _PERMISSION_CODE}])
        .on_conflict_do_nothing(index_elements=["code"])
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT (SELECT id FROM roles WHERE code = :role_code), p.id
            FROM permissions p
            WHERE p.code = :permission_code
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ),
        {"role_code": _ADMIN_ROLE_CODE, "permission_code": _PERMISSION_CODE},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            USING roles r, permissions p
            WHERE role_permissions.role_id = r.id
              AND role_permissions.permission_id = p.id
              AND r.code = :role_code
              AND p.code = :permission_code
            """
        ),
        {"role_code": _ADMIN_ROLE_CODE, "permission_code": _PERMISSION_CODE},
    )

    op.drop_constraint('ck_audit_logs_action_valid', 'audit_logs', type_='check')
    op.create_check_constraint(
        'ck_audit_logs_action_valid', 'audit_logs', f'action IN ({_OLD_ACTION_VALUES})'
    )

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_event_document_requirements_event_id', table_name='event_document_requirements')
    op.drop_table('event_document_requirements')
    op.drop_index('ix_documents_person_id', table_name='documents')
    op.drop_index('ix_documents_file_id', table_name='documents')
    op.drop_index('ix_documents_document_group_id', table_name='documents')
    op.drop_table('documents')
    op.drop_table('files')
    # ### end Alembic commands ###
