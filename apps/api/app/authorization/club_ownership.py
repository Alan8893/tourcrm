"""Shared ADR-0022 building block: does a User's Person hold an active
ClubMembership in a given Club?

This check is the reusable half of the ADR-0022 cross-Club ownership
mechanism (the other half — locking and comparing the specific
Club-owned resource's `club_id` — is necessarily specific to each
relationship/entity and stays in that entity's own service module, e.g.
app.groups.service, app.events.service). Extracted here so the
User -> Person -> active ClubMembership -> Club check is defined once
and reused by every relationship that needs it, per ADR-0022 §3's "one
shared ownership-validation mechanism rather than ad-hoc... checks".

Not part of app.authorization's scope/permission evaluation (context.py,
service.py) — this module answers a narrower question (Club membership
fact), not an authorization/scope decision.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.identity import ClubMembership, User

# identity.py's documented ClubMembership.status vocabulary (Issue #17):
# pending, active, suspended, inactive, archived. "active" is used here
# exactly as it is everywhere else in this codebase — no new value.
ACTIVE_CLUB_MEMBERSHIP_STATUS = "active"


def user_has_active_club_membership(
    session: Session, *, user_id: uuid.UUID, club_id: uuid.UUID
) -> bool:
    """True if `user_id`'s Person has an active ClubMembership in `club_id`.

    Reads with `SELECT ... FOR SHARE` (`with_for_update(read=True)`) so a
    concurrent change to that specific membership row cannot invalidate
    an already-passed check before the caller's transaction commits —
    see the callers' modules for the full transaction-boundary rationale
    (ADR-0022 §6).
    """
    person_id = session.execute(select(User.person_id).where(User.id == user_id)).scalar_one()
    row = session.execute(
        select(ClubMembership.id)
        .where(
            ClubMembership.person_id == person_id,
            ClubMembership.club_id == club_id,
            ClubMembership.status == ACTIVE_CLUB_MEMBERSHIP_STATUS,
        )
        .with_for_update(read=True)
        .limit(1)
    ).first()
    return row is not None


__all__ = ["ACTIVE_CLUB_MEMBERSHIP_STATUS", "user_has_active_club_membership"]
