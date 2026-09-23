"""Closed vocabularies for the canonical audit contract (ADR-0024 §4).

Pure Python, no ORM/FastAPI import — mirrors app.events.vocabulary's role
for the Event domain: the persistence layer (app.db.audit) and the
service layer (app.audit.service) both import from here rather than
duplicating the same literal sets, so the CHECK constraint generated for
`audit_logs.action` and the service-level validation can never drift
apart.

ADR-0024 §4 is explicit that this action list is closed at this stage:
extending it requires a follow-up ADR update, not an application-level
addition. ADR-0025 §1 is one such amendment (the three `person.*`
actions below — the original ADR-0024 vocabulary had no action at all
for `Person` mutations, only `User`). Issue #62's accepted decisions add
a second: `membership.updated`, for non-lifecycle ClubMembership
attribute changes (currently only `membership_type`) — distinct from
`membership.status_changed` (a lifecycle status transition) and
`membership.ended` (a lifecycle transition that also sets `left_at`).

ADR-0037 (TH-0108.2) is a third such amendment: `event_participation.
status_changed`, covering every `EventParticipation.registration_status`
transition a participant's self-registration/withdrawal produces
(unregistered -> registered, registered -> cancelled, cancelled ->
registered) — one action code with `details={"from": ..., "to": ...}`,
mirroring `membership.status_changed`'s identical shape for a
multi-transition lifecycle column, rather than one action per direction.
"""

CANONICAL_ACTOR_TYPES: frozenset[str] = frozenset({"user", "system"})

CANONICAL_AUDIT_OUTCOMES: frozenset[str] = frozenset({"success", "failure"})

CANONICAL_AUDIT_ACTIONS: frozenset[str] = frozenset(
    {
        # Person (ADR-0025 §1 amendment)
        "person.created",
        "person.updated",
        "person.archived",
        # Identity
        "user.created",
        "user.status_changed",
        "user.locked",
        "user.unlocked",
        # Membership
        "membership.created",
        "membership.updated",
        "membership.status_changed",
        "membership.ended",
        # Participant import batch execution (TH-0118.3 amendment to
        # ADR-0024 §4): one batch-level record per applied ImportJob, with
        # the job as the audit resource. It never replaces the per-entity
        # `person.*`/`user.*`/`membership.*` records the import also writes.
        "membership.import.applied",
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
        # Event recurrence (ADR-0028 amendment to ADR-0024 §4)
        "event_series.created",
        "event_series.updated",
        "event_series.version_created",
        "event_series.status_changed",
        "event_occurrence.exception_created",
        "event_occurrence.exception_changed",
        "event_occurrence.status_changed",
        "event_occurrence.series_rebound",
        # EventSeries relationship sources / occurrence relationship
        # overrides (ADR-0030 amendment to ADR-0024 §4)
        "event_series_staff_assignment.created",
        "event_series_staff_assignment.changed",
        "event_series_staff_assignment.ended",
        "event_series_group_target.created",
        "event_series_group_target.changed",
        "event_series_group_target.ended",
        "event_series_participant.created",
        "event_series_participant.changed",
        "event_series_participant.ended",
        "event_occurrence_staff_assignment.created",
        "event_occurrence_staff_assignment.changed",
        "event_occurrence_staff_assignment.ended",
        "event_occurrence_group_target.created",
        "event_occurrence_group_target.changed",
        "event_occurrence_group_target.ended",
        "event_occurrence_participant.created",
        "event_occurrence_participant.changed",
        "event_occurrence_participant.ended",
        # Attendance (ADR-0032 §11 amendment to ADR-0024 §4)
        "attendance.created",
        "attendance.changed",
        "attendance.bulk_changed",
        "attendance.corrected",
        # Event self-registration (ADR-0037 amendment to ADR-0024 §4, TH-0108.2)
        "event_participation.status_changed",
        # Admin password reset / first-access setup (ADR-0038 amendment to
        # ADR-0024 §4, TH-0113): covers both self-service password-reset
        # requests (app.authentication.service.request_password_reset,
        # actor_type="system") and administrator-initiated ones (actor_
        # type="user", via app.authentication.account_provisioning) — the
        # same underlying PasswordResetChallenge mechanism, one action code
        # per lifecycle step, not one per initiator. Sessions revoked as a
        # consequence of a completed reset are recorded as a `details`
        # field on `password_reset_challenge.completed` rather than a
        # separate action, mirroring `event_participation.status_changed`'s
        # "one action, details carry the rest" shape.
        "password_reset_challenge.created",
        "password_reset_challenge.completed",
        # Participant Document domain (ADR-0040 §7 amendment to ADR-0024
        # §4, TH-0117.1): `document.replaced` is distinct from
        # `document.updated` — replacing a document's underlying file
        # creates a new version (ADR-0040 §4) and is `document.replaced`;
        # correcting non-file metadata on the current version in place is
        # `document.updated`. `document.downloaded`/`document.exported`
        # cover reading/exporting document content (including a sensitive
        # medical certificate) and are audit-required even when the
        # surrounding operation also touches non-sensitive documents. No
        # runtime caller emits any of these six actions yet (Issue #156 is
        # persistence-foundation only) — this amendment only makes the
        # vocabulary/schema ready for the API work that follows.
        "document.created",
        "document.updated",
        "document.replaced",
        "document.revoked",
        "document.downloaded",
        "document.exported",
    }
)
