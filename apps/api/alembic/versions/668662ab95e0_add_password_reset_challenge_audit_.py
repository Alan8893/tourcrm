"""add password reset challenge audit actions

Revision ID: 668662ab95e0
Revises: 75b7574b6e44
Create Date: 2026-09-21 06:15:00.000000

ADR-0038 §8 amends ADR-0024 §4's closed audit action vocabulary with two
new action codes for TH-0113 (admin password reset / first-access
setup): `password_reset_challenge.created` and `password_reset_challenge.
completed`. Alembic's autogenerate does not diff CHECK constraint
bodies, so this swap is hand-written, matching the precedent in
2cd38e8e090d_add_event_participation_status_changed_.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '668662ab95e0'
down_revision: Union[str, None] = '75b7574b6e44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    "'membership.status_changed','membership.updated','person.archived',"
    "'person.created','person.updated','role_assignment.changed',"
    "'role_assignment.created','role_assignment.revoked','user.created',"
    "'user.locked','user.status_changed','user.unlocked'"
)
_NEW_ACTION_VALUES = (
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


def upgrade() -> None:
    op.drop_constraint('ck_audit_logs_action_valid', 'audit_logs', type_='check')
    op.create_check_constraint(
        'ck_audit_logs_action_valid', 'audit_logs', f'action IN ({_NEW_ACTION_VALUES})'
    )


def downgrade() -> None:
    op.drop_constraint('ck_audit_logs_action_valid', 'audit_logs', type_='check')
    op.create_check_constraint(
        'ck_audit_logs_action_valid', 'audit_logs', f'action IN ({_OLD_ACTION_VALUES})'
    )
