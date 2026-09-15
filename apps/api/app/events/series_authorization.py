"""EventSeries/EventOccurrence authorization scope resolution (Issue #79,
ADR-0028 §11).

ADR-0028 §11 is explicit: "Series and occurrence operations use the
canonical Event permissions and scopes. No separate recurrence permission
is introduced" — this module resolves those same permissions
(`event.read`/`event.update`/`event.manage`/etc.) and the same canonical
scope vocabulary (ADR-0013) against EventSeries/EventOccurrence resources,
reusing app.authorization.service's shared engine exactly as
app.events.authorization does for Event.

Structural consequence, not an invented rule: `own_events` and
`own_groups` are resolved (app.events.authorization) entirely through
`EventStaffAssignment.event_id`/`EventGroupTarget.event_id`, and `self`/
`children` through `EventParticipation.event_id` — all three keyed to the
base `Event` table. ADR-0028 §13 is explicit that EventOccurrence has no
`event_id` bridge to that table, and staffing/targeting/participation for
occurrences is out of this Issue's scope entirely. There is therefore no
relationship data from which `own_events`/`own_groups`/`self`/`children`
could ever be resolved for a Series/Occurrence resource today — so, like
`app.api.v1.events.create_event`'s already-established handling of a
not-yet-existing Event (`Authorizer.check(ResourceContext(club_id=...))`
with every other field left at its fail-closed `None` default), only a
`scope_type="all"` assignment (global or matching the resource's
`club_id`) can ever satisfy a Series/Occurrence permission check here.
This is the same existing tri-state ResourceContext design applied to a
resource type that genuinely has no other relationship to resolve, not a
new authorization mechanism.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authorization.context import ResourceContext
from app.authorization.service import applicable_assignments
from app.db.event_recurrence import EventOccurrence, EventSeries


def build_series_resource_context(*, series: EventSeries) -> ResourceContext:
    """See module docstring: only the Club boundary is resolvable for an
    EventSeries resource."""
    return ResourceContext(club_id=series.club_id)


def build_occurrence_resource_context(*, occurrence: EventOccurrence) -> ResourceContext:
    """See module docstring: only the Club boundary is resolvable for an
    EventOccurrence resource."""
    return ResourceContext(club_id=occurrence.club_id)


def _all_scope_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str, club_id_column
) -> sa.ColumnElement[bool]:
    """Shared by both list-visibility filters below: true only for rows
    whose `club_id` is matched by at least one of the acting user's
    applicable `scope_type="all"` assignments for `permission_code` (a
    global assignment, or one scoped to that same Club) — see module
    docstring for why `own_events`/`own_groups`/`self`/`children` can
    never contribute here.
    """
    assignments = applicable_assignments(session, user_id, permission_code)
    all_scope_club_ids = [a.club_id for a in assignments if a.scope_type == "all"]
    if not all_scope_club_ids:
        return sa.false()
    if any(club_id is None for club_id in all_scope_club_ids):
        return sa.true()
    return club_id_column.in_(all_scope_club_ids)


def series_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    return _all_scope_visibility_filter(
        session,
        user_id=user_id,
        permission_code=permission_code,
        club_id_column=EventSeries.club_id,
    )


def occurrence_visibility_filter(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> sa.ColumnElement[bool]:
    return _all_scope_visibility_filter(
        session,
        user_id=user_id,
        permission_code=permission_code,
        club_id_column=EventOccurrence.club_id,
    )


__all__ = [
    "build_series_resource_context",
    "build_occurrence_resource_context",
    "series_visibility_filter",
    "occurrence_visibility_filter",
]
