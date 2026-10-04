"""add trip audit actions

Revision ID: 7d2b4e9f1a63
Revises: 3c1e9a7d5b20
Create Date: 2026-10-04 16:30:00.000000

#247 (Trip Audit Trail) amends ADR-0024 §4's closed audit action
vocabulary with exactly three action codes for the tourism facts of #245:
`trip.created`, `trip_participant.actual_participation_recorded`,
`trip_participant.actual_participation_changed`. Alembic's autogenerate
does not diff CHECK constraint bodies, so this swap is hand-written,
matching the precedent in 4b1f0c2d9a7e_add_membership_import_applied_
audit_.py.

Downgrade restores the previous CHECK constraint. Audit rows are
append-only (ADR-0024 §3) and are never deleted here: if rows with one of
the three trip actions already exist, PostgreSQL rejects the narrower
constraint and the downgrade fails instead of silently losing audit
history.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7d2b4e9f1a63'
down_revision: Union[str, None] = '3c1e9a7d5b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_ACTION_VALUES = (
    "'attendance.bulk_changed','attendance.changed','attendance.corrected',"
    "'attendance.created','document.created','document.downloaded',"
    "'document.exported','document.replaced','document.revoked','document.updated',"
    "'event.archived','event.created','event.status_changed','event.updated',"
    "'event_occurrence.exception_changed','event_occurrence.exception_created',"
    "'event_occurrence.series_rebound','event_occurrence.status_changed',"
    "'event_occurrence_group_target.changed',"
    "'event_occurrence_group_target.created','event_occurrence_group_target.ended',"
    "'event_occurrence_participant.changed','event_occurrence_participant.created',"
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
    "'event_series_staff_assignment.created','event_series_staff_assignment.ended',"
    "'group.created','group.updated','group_instructor_assignment.created',"
    "'group_instructor_assignment.ended','group_instructor_assignment.updated',"
    "'group_membership.created','group_membership.ended',"
    "'group_membership.updated','guardian_relationship.created',"
    "'guardian_relationship.revoked','guardian_relationship.updated',"
    "'membership.created','membership.ended','membership.import.applied',"
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
    "'event.archived','event.created','event.status_changed','event.updated',"
    "'event_occurrence.exception_changed','event_occurrence.exception_created',"
    "'event_occurrence.series_rebound','event_occurrence.status_changed',"
    "'event_occurrence_group_target.changed',"
    "'event_occurrence_group_target.created','event_occurrence_group_target.ended',"
    "'event_occurrence_participant.changed','event_occurrence_participant.created',"
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
    "'event_series_staff_assignment.created','event_series_staff_assignment.ended',"
    "'group.created','group.updated','group_instructor_assignment.created',"
    "'group_instructor_assignment.ended','group_instructor_assignment.updated',"
    "'group_membership.created','group_membership.ended',"
    "'group_membership.updated','guardian_relationship.created',"
    "'guardian_relationship.revoked','guardian_relationship.updated',"
    "'membership.created','membership.ended','membership.import.applied',"
    "'membership.status_changed','membership.updated',"
    "'password_reset_challenge.completed','password_reset_challenge.created',"
    "'person.archived','person.created','person.updated','role_assignment.changed',"
    "'role_assignment.created','role_assignment.revoked','trip.created',"
    "'trip_participant.actual_participation_changed',"
    "'trip_participant.actual_participation_recorded','user.created','user.locked',"
    "'user.status_changed','user.unlocked'"
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
