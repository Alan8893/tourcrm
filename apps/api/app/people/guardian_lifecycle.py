"""GuardianRelationship lifecycle validation (Issue #64).

Canonical sources: docs/03-architecture/adr/ADR-0023-event-relationships-
and-guardian-persistence.md §3, docs/03-architecture/adr/ADR-0025-people-
membership-api-decisions.md §3, Issue #64 §15 (as resolved by the PO's
explicit "read-time evaluation" instruction for GAP-C).

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.people.lifecycle's dependency direction exactly.

Canonical `status` values: `active`, `inactive`, `revoked` (ADR-0023 §3).
No `pending`/`verified`/`rejected`/`terminated` or any other value exists.

`terminate` always sets `status = revoked` — there is no alternative
outcome (ADR-0025 §3). `revoked` is therefore the only *stored* terminal
state this module enforces (`terminate` on an already-`revoked` row is
rejected).

`inactive` is reached through `valid_to` naturally elapsing, not through
any explicit action or write. Per the PO's explicit resolution of Issue
#64's GAP-C, this is evaluated at **read time** only: no background job,
scheduler, or write-time recompute exists or is introduced here. A row
whose stored `status` is still `active` but whose `valid_to` has already
elapsed is treated as `inactive` by `effective_status()` below — the
stored column itself is never rewritten to reflect this.
"""

import datetime as dt
from typing import Optional

CANONICAL_GUARDIAN_RELATIONSHIP_STATUSES: frozenset[str] = frozenset(
    {"active", "inactive", "revoked"}
)

# The only status a new GuardianRelationship may be created with (Issue
# #64 §8/§16): there is no `pending`/confirmation workflow for this
# entity (ADR-0023).
CREATABLE_GUARDIAN_RELATIONSHIP_STATUS = "active"


class GuardianRelationshipDomainError(Exception):
    """Base class for this module's typed, expected failures."""


class InvalidGuardianRelationshipStatusError(GuardianRelationshipDomainError):
    """`value` is not one of CANONICAL_GUARDIAN_RELATIONSHIP_STATUSES."""

    def __init__(self, value: str) -> None:
        super().__init__(f"{value!r} is not a canonical GuardianRelationship status")
        self.value = value


class SelfLinkNotAllowedError(GuardianRelationshipDomainError):
    """`guardian_person_id == child_person_id` — ADR-0023 §3 forbids a
    Person from being their own guardian; also DB-enforced by
    `ck_guardian_relationships_guardian_child_distinct` as a fail-safe.
    """


class AlreadyRevokedError(GuardianRelationshipDomainError):
    """`terminate` was called on a relationship whose stored `status` is
    already `revoked` — `revoked` is a terminal stored state."""


class DuplicateActiveGuardianRelationshipError(GuardianRelationshipDomainError):
    """database-schema.md §7: an active relationship already exists for
    this (guardian_person_id, child_person_id, relationship_type) with an
    overlapping validity interval — enforced by the
    `ck_guardian_relationships_no_overlapping_active` GiST exclusion
    constraint, caught and re-raised as this typed error by
    app.people.guardian_service."""


class DuplicatePrimaryContactError(GuardianRelationshipDomainError):
    """database-schema.md §7: an active, primary-contact relationship
    already exists for this child — enforced by the
    `ck_guardian_relationships_no_overlapping_primary_contact` GiST
    exclusion constraint."""


def validate_guardian_relationship_status(value: str) -> None:
    if value not in CANONICAL_GUARDIAN_RELATIONSHIP_STATUSES:
        raise InvalidGuardianRelationshipStatusError(value)


def validate_guardian_child_distinct(guardian_person_id, child_person_id) -> None:
    if guardian_person_id == child_person_id:
        raise SelfLinkNotAllowedError(
            "guardian_person_id and child_person_id must not be the same Person"
        )


def effective_status(
    *, status: str, valid_to: Optional[dt.datetime], now: Optional[dt.datetime] = None
) -> str:
    """The status this relationship should be *treated as* for
    authorization and read responses — read-time derivation only (see
    module docstring), never written back to the stored `status` column.

    `revoked` is always terminal and returned as-is. A stored `active`
    row whose `valid_to` has already elapsed is reported as `inactive`.
    Anything else (a stored `active` row still within its interval, or an
    already-stored `inactive` row) is returned unchanged.
    """
    if status == "revoked":
        return "revoked"
    if status == "active" and valid_to is not None:
        current_time = now if now is not None else dt.datetime.now(dt.timezone.utc)
        if valid_to <= current_time:
            return "inactive"
    return status


__all__ = [
    "CANONICAL_GUARDIAN_RELATIONSHIP_STATUSES",
    "CREATABLE_GUARDIAN_RELATIONSHIP_STATUS",
    "GuardianRelationshipDomainError",
    "InvalidGuardianRelationshipStatusError",
    "SelfLinkNotAllowedError",
    "AlreadyRevokedError",
    "DuplicateActiveGuardianRelationshipError",
    "DuplicatePrimaryContactError",
    "validate_guardian_relationship_status",
    "validate_guardian_child_distinct",
    "effective_status",
]
