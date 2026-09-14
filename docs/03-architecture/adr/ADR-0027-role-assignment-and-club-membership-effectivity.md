# ADR-0027: RoleAssignment and ClubMembership effectivity

## Status

Accepted.

## Context

ADR-0026 established that a club-scoped `RoleAssignment` may be created only when the target User has an active `ClubMembership` in the target Club. It also stated that ending that membership prevents the assignment from granting effective Club access.

During implementation of Issue #74, this latter rule was found to conflict with the existing TourCRM identity model and the shared authorization engine. `User` is the global authentication principal; `Person` and `ClubMembership` represent the person's membership relationship with a Club. Existing authorization behavior also permits club administration assignments to Users who are not themselves Club members. Making active `ClubMembership` a universal prerequisite for effective club-scoped RoleAssignment would therefore change already-shipped authorization semantics in Event, Person, Membership and Group domains.

The project requires one shared authorization mechanism and does not permit a parallel `role.manage`-specific authorization path.

## Decision

### 1. ClubMembership is a creation-time integrity prerequisite, not a universal authorization prerequisite

For a club-scoped `RoleAssignment`:

- the target User MUST have an active `ClubMembership` in the target Club at creation time;
- this rule protects cross-Club assignment integrity;
- the `RoleAssignment` remains an independent authorization fact owned by the User principal;
- ending the target User's `ClubMembership` does NOT automatically revoke, close, delete, or otherwise mutate the `RoleAssignment`;
- ending the target User's `ClubMembership` does NOT, by itself, make an otherwise temporally effective RoleAssignment ineffective;
- the RoleAssignment's own validity interval, User lifecycle, permission, scope and object-level authorization policies continue to determine effective access.

This explicitly separates **Club membership integrity at assignment creation** from **RoleAssignment effectivity**.

### 2. No retroactive global authorization change

The shared `applicable_assignments()` / `Authorizer` mechanism MUST NOT acquire a universal requirement that a User have an active ClubMembership merely because a RoleAssignment has a non-null `club_id`.

No existing Event, Person, Membership or Group authorization semantics are changed by this ADR.

If a future domain requires active ClubMembership as an additional access prerequisite, that requirement must be specified by that domain's canonical contract and implemented as an explicit object/domain policy rather than inferred globally from RoleAssignment storage.

### 3. RoleAssignment lifecycle remains independent

The lifecycle of `UserRoleAssignment` remains governed by its own temporal interval `[valid_from, valid_to)`.

- Revoke closes the RoleAssignment interval.
- Ending ClubMembership does not close the RoleAssignment interval.
- Re-joining the Club does not reopen a previously revoked RoleAssignment.
- A new RoleAssignment is required for a new authorization grant after revoke.

### 4. Cross-Club boundary remains mandatory

This ADR does not weaken ADR-0022 or ADR-0026's creation-time cross-Club integrity rule. A club-scoped RoleAssignment cannot be created for a User without an active ClubMembership in that Club.

The distinction is intentional: the membership check establishes that the assignment is attached to the correct Club at creation; it is not a permanent prerequisite for the User's later authorization checks.

## Consequences

- Club administrators and other staff may hold Club-scoped authorization without being forced to remain Club members.
- Ending a Person's ClubMembership does not unexpectedly remove administrative/staff authorization granted through an independent RoleAssignment.
- Existing shared authorization behavior remains stable across already implemented domains.
- RoleAssignment effectivity is deterministic from the assignment's own temporal state plus the normal authorization conditions.
- Domain-specific policies may still require active membership where that business rule is explicitly specified.

## Supersedes

This ADR supersedes **ADR-0026 §3 and the corresponding sentence in §5** only to the extent that they state that ending a target User's ClubMembership must make an otherwise effective club-scoped RoleAssignment ineffective.

All other decisions in ADR-0026 remain in force.

## Traceability

- Issue #74 — Implement RoleAssignment API
- ADR-0026 — RoleAssignment API and authorization semantics
- ADR-0022 — Cross-Club Ownership Integrity
- ADR-0017 — Identity and Authorization Documentation Canonicalization
