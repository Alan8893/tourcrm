"""Closed vocabularies for the canonical audit contract (ADR-0024 §4).

Pure Python, no ORM/FastAPI import — mirrors app.events.vocabulary's role
for the Event domain: the persistence layer (app.db.audit) and the
service layer (app.audit.service) both import from here rather than
duplicating the same literal sets, so the CHECK constraint generated for
`audit_logs.action` and the service-level validation can never drift
apart.

ADR-0024 §4 is explicit that this action list is closed at this stage:
extending it requires a follow-up ADR update, not an application-level
addition.
"""

CANONICAL_ACTOR_TYPES: frozenset[str] = frozenset({"user", "system"})

CANONICAL_AUDIT_OUTCOMES: frozenset[str] = frozenset({"success", "failure"})

CANONICAL_AUDIT_ACTIONS: frozenset[str] = frozenset(
    {
        # Identity
        "user.created",
        "user.status_changed",
        "user.locked",
        "user.unlocked",
        # Membership
        "membership.created",
        "membership.status_changed",
        "membership.ended",
        # Roles
        "role_assignment.created",
        "role_assignment.changed",
        "role_assignment.revoked",
        # Groups
        "group.created",
        "group.updated",
        "group_membership.created",
        "group_membership.updated",
        "group_membership.ended",
        "group_instructor_assignment.created",
        "group_instructor_assignment.updated",
        "group_instructor_assignment.ended",
        # Guardians
        "guardian_relationship.created",
        "guardian_relationship.updated",
        "guardian_relationship.revoked",
        # Events
        "event.created",
        "event.updated",
        "event.status_changed",
        "event.archived",
    }
)
