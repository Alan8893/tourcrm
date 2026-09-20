"""Participant self-registration/withdrawal for a published Event
(TH-0108.2 / Issue #140-series), implementing ADR-0037 §1/§3/§5/§6/§7/§15.

Canonical sources: docs/03-architecture/adr/ADR-0037-event-targeting-
self-registration-participation.md (the accepted MVP policy — supersedes
ADR-0020 §4's deferral for this specific slice), ADR-0023 §4
(EventParticipation persistence: single current-state row per (event,
person), no `valid_from`/`valid_to`, uniqueness enforced by a DB
constraint), ADR-0022 (cross-Club ownership integrity, reused via
app.authorization.club_ownership).

This is a self-service operation, not a permission-gated one (ADR-0037
§12: "no new Event permission is introduced ... governed by the
participant's identity, active ClubMembership, targeted GroupMembership
and Event lifecycle"). It deliberately does not go through
app.authorization.service.Authorizer/ResourceContext or check
`event.read`/any Event permission at all — a Person's own eligibility to
register for their own Person is a narrower, independent question from
"can this User read/manage this Event via the generic scope engine",
mirroring how app.people.authorization already resolves `person.create`
outside that same generic engine for an analogous reason (no target
object yet / the check does not fit the shared Club-boundary shape).

Eligibility (ADR-0037 §3/§5), checked in this exact order:

1. The Event must exist and be `published` (ADR-0037 §7) — `draft`,
   `completed`, `cancelled`, `archived` are all rejected.
2. The requester's own Person (resolved from the authenticated User —
   never a client-supplied `person_id`, ADR-0037 §13) must have an
   active ClubMembership in the Event's Club (reuses
   app.authorization.club_ownership.user_has_active_club_membership, the
   same ADR-0022 building block app.events.service already uses for
   EventStaffAssignment/EventGroupTarget).
3. If the Event has one or more active target Groups (via
   app.events.queries.get_event_targeting, the same TH-0108.1 query the
   Event response itself uses), the Person must have an active
   GroupMembership in at least one of them. Zero target Groups means the
   Event is club-wide (ADR-0037 §1/§5): step 2 alone is then sufficient.

Registration is an idempotent create-or-update of the one
`(event_id, person_id)` EventParticipation row (upsert, mirroring
app.events.attendance.upsert_attendance's identical shape) — never a
second row (the existing `uq_event_participations_event_id_person_id`
constraint is the persistence invariant, unchanged). Withdrawal only
ever touches the authenticated participant's own row and never fails
when there is nothing to cancel (idempotent no-op).

Never creates: Attendance, GroupMembership, ClubMembership,
GuardianRelationship, Payment, Notification delivery, User, or
UserRoleAssignment. Never accepts or infers Attendance state, capacity,
waitlist position, or payment/financial obligation — all explicitly
deferred by ADR-0037 §8/§9/§10.
"""

import uuid
from typing import Any, Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.authorization.club_ownership import user_has_active_club_membership
from app.db.events import Event, EventParticipation
from app.db.groups import GroupMembership
from app.db.identity import ClubMembership, User
from app.events.queries import get_event_targeting

_PUBLISHED_STATUS = "published"
_ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"
_ACTIVE_GROUP_MEMBERSHIP_STATUS = "active"
REGISTERED_STATUS = "registered"
CANCELLED_STATUS = "cancelled"


class EventParticipationError(Exception):
    """Base class for this module's typed, expected failures."""


class EventNotFoundError(EventParticipationError):
    """No Event exists for the given id."""

    def __init__(self, *, event_id: uuid.UUID) -> None:
        super().__init__(f"Event {event_id} does not exist")
        self.event_id = event_id


class EventNotPublishedError(EventParticipationError):
    """ADR-0037 §7: self-registration is available only for a `published`
    Event.
    """

    def __init__(self, *, event_id: uuid.UUID, status: str) -> None:
        super().__init__(f"Event {event_id} is not open for registration (status={status})")
        self.event_id = event_id
        self.status = status


class NotEligibleForEventError(EventParticipationError):
    """ADR-0037 §3/§5: the Person lacks active ClubMembership in the
    Event's Club, or (for a targeted Event) active GroupMembership in
    any of its target Groups. Never raised because of role name alone.
    """

    def __init__(self, *, event_id: uuid.UUID, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} is not eligible to register for Event {event_id}")
        self.event_id = event_id
        self.person_id = person_id


def person_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    """Exported (no leading underscore) so app.api.v1.events can resolve
    the viewer's own Person once per request, for `EventOut.
    my_registration_status` — see `get_viewer_registration_status` below.
    """
    return session.execute(sa.select(User.person_id).where(User.id == user_id)).scalar_one()


def _active_interval(valid_from: Any, valid_to: Any) -> sa.ColumnElement[bool]:
    now = sa.func.now()
    return sa.and_(valid_from <= now, sa.or_(valid_to.is_(None), now < valid_to))


def _person_has_active_group_membership(
    session: Session,
    *,
    person_id: uuid.UUID,
    event_club_id: uuid.UUID,
    group_ids: Sequence[uuid.UUID],
) -> bool:
    """True if `person_id` has an active GroupMembership, backed by an
    active ClubMembership in `event_club_id`, in at least one of
    `group_ids` — the same chain
    app.events.authorization._child_condition's `child_via_group_target`
    resolves for Guardian access, duplicated here per this codebase's own
    convention of duplicating small predicates across modules rather than
    importing across the authorization/domain-service boundary.
    """
    if not group_ids:
        return False
    return (
        session.execute(
            sa.select(sa.literal(True)).where(
                sa.exists(
                    sa.select(GroupMembership.id)
                    .join(ClubMembership, ClubMembership.id == GroupMembership.club_membership_id)
                    .where(
                        ClubMembership.person_id == person_id,
                        ClubMembership.club_id == event_club_id,
                        ClubMembership.status == _ACTIVE_CLUB_MEMBERSHIP_STATUS,
                        GroupMembership.group_id.in_(group_ids),
                        GroupMembership.membership_status == _ACTIVE_GROUP_MEMBERSHIP_STATUS,
                        _active_interval(GroupMembership.valid_from, GroupMembership.valid_to),
                    )
                )
            )
        ).scalar_one_or_none()
        is not None
    )


def register_for_event(
    session: Session,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> EventParticipation:
    """ADR-0037 §1/§3/§4/§5/§6/§7: self-register the authenticated User's
    own Person for `event_id`. Idempotent — a second call while already
    `registered` is a safe no-op; a call while `cancelled` restores the
    same row to `registered` rather than creating a duplicate.

    Raises EventNotFoundError, EventNotPublishedError, or
    NotEligibleForEventError, persisting nothing, when any precondition
    fails — eligibility is always re-checked, even on an idempotent
    repeat call, so a since-cancelled/completed Event cannot be
    (re-)registered for just because a `registered` row already exists.
    """
    event = session.execute(
        sa.select(Event).where(Event.id == event_id).with_for_update(read=True)
    ).scalar_one_or_none()
    if event is None:
        raise EventNotFoundError(event_id=event_id)

    person_id = person_id_for_user(session, user_id)

    if event.status != _PUBLISHED_STATUS:
        raise EventNotPublishedError(event_id=event.id, status=event.status)

    if not user_has_active_club_membership(session, user_id=user_id, club_id=event.club_id):
        raise NotEligibleForEventError(event_id=event.id, person_id=person_id)

    group_ids, _instructor_ids = get_event_targeting(session, event_id=event.id)
    if group_ids and not _person_has_active_group_membership(
        session, person_id=person_id, event_club_id=event.club_id, group_ids=group_ids
    ):
        raise NotEligibleForEventError(event_id=event.id, person_id=person_id)

    existing = session.execute(
        sa.select(EventParticipation)
        .where(EventParticipation.event_id == event_id, EventParticipation.person_id == person_id)
        .with_for_update()
    ).scalar_one_or_none()

    previous_status = existing.registration_status if existing is not None else None
    if existing is not None and previous_status == REGISTERED_STATUS:
        # Already registered: idempotent no-op, no audit-worthy change.
        return existing

    try:
        if existing is None:
            row = EventParticipation(
                event_id=event_id, person_id=person_id, registration_status=REGISTERED_STATUS
            )
            session.add(row)
        else:
            row = existing
            row.registration_status = REGISTERED_STATUS
        session.flush()
        record_audit_event(
            session,
            action="event_participation.status_changed",
            actor_type="user",
            actor_user_id=user_id,
            club_id=event.club_id,
            resource_type="event_participation",
            resource_id=row.id,
            outcome="success",
            request_id=request_id,
            details={
                "changes": {
                    "registration_status": {"from": previous_status, "to": REGISTERED_STATUS}
                }
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return row


def withdraw_from_event(
    session: Session,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> Optional[EventParticipation]:
    """ADR-0037 §6: cancel the authenticated User's own registration for
    `event_id`. Idempotent — returns `None` (nothing to cancel) when no
    participation row exists at all, and returns the row unchanged
    without a write when it is already `cancelled`. Only ever touches
    the caller's own row: `person_id` is resolved from `user_id`, never
    accepted as input.
    """
    person_id = person_id_for_user(session, user_id)

    existing = session.execute(
        sa.select(EventParticipation)
        .where(EventParticipation.event_id == event_id, EventParticipation.person_id == person_id)
        .with_for_update()
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.registration_status == CANCELLED_STATUS:
        return existing

    previous_status = existing.registration_status
    try:
        existing.registration_status = CANCELLED_STATUS
        session.flush()
        record_audit_event(
            session,
            action="event_participation.status_changed",
            actor_type="user",
            actor_user_id=user_id,
            resource_type="event_participation",
            resource_id=existing.id,
            outcome="success",
            request_id=request_id,
            details={
                "changes": {
                    "registration_status": {"from": previous_status, "to": CANCELLED_STATUS}
                }
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return existing


def get_viewer_registration_status(
    session: Session, *, event_id: uuid.UUID, person_id: uuid.UUID
) -> Optional[str]:
    """The viewer's own current `registration_status` for `event_id`
    (`"registered"`, `"cancelled"`, or `None` if they have never
    registered) — a purely informational read for `EventOut.
    my_registration_status`, so the frontend can render "Вы записаны" /
    "Записаться" without any client-side authorization/eligibility
    logic of its own (ADR-0037 §12's self-service model is otherwise
    silent on a GET; this piggybacks on the existing Event response
    rather than adding a new endpoint).
    """
    return session.execute(
        sa.select(EventParticipation.registration_status).where(
            EventParticipation.event_id == event_id, EventParticipation.person_id == person_id
        )
    ).scalar_one_or_none()


__all__ = [
    "REGISTERED_STATUS",
    "CANCELLED_STATUS",
    "EventParticipationError",
    "EventNotFoundError",
    "EventNotPublishedError",
    "NotEligibleForEventError",
    "person_id_for_user",
    "register_for_event",
    "withdraw_from_event",
    "get_viewer_registration_status",
]
