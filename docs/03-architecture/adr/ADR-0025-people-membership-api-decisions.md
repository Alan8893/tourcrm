# ADR-0025: People & Membership API — PO/architect decisions

## Status

Accepted.

## Context

The documentation-reconciliation pass for Issue #62 ("Foundation: People & Membership API") surfaced explicit GAPs that could not be resolved from existing canonical documents alone. The Product Owner/architect reviewed Issue #62 and explicitly accepted the decisions below. This ADR records decisions affecting persistence, permissions, API contracts and audit vocabulary before implementation proceeds.

## Decision

### 1. Audit vocabulary — `Person` actions (amends ADR-0024 §4)

`person.created`, `person.updated`, `person.archived` are added to the closed, canonical `AuditLog.action` vocabulary defined by ADR-0024 §4. ADR-0024's existing rules (transaction/fail-closed semantics, secret prohibition, append-only, no retention/TTL) are unchanged and apply to these three actions exactly as to every other canonical action.

### 2. GuardianRelationship permissions

`guardian_relationship.read` and `guardian_relationship.manage` are added to the canonical permission catalog (`docs/02-requirements/roles-and-permissions.md` §4), following the existing `<resource>.<action>` convention. `people-api.md`'s prior reference to a non-canonical `guardian.read` is corrected to use these two codes.

### 3. GuardianRelationship termination semantics

The `terminate` API action always sets `GuardianRelationship.status = revoked`. `inactive` remains a distinct, non-revoked historical status reached through other lifecycle events (for example, the relationship's `valid_to` naturally elapsing). No new status value is introduced; the three canonical values (`active`, `inactive`, `revoked`) from ADR-0023 §3 are unchanged.

### 4. GuardianRelationship URI

The canonical resource for addressing a `GuardianRelationship` by its own id is `/api/v1/guardian-relationships/{relationship_id}`. `/guardians` is not retained as an API alias or resource name for the relationship entity anywhere in the contract, including the nested per-person collection endpoint `/api/v1/persons/{person_id}/guardian-relationships`.

### 5. RegistrationRequest vs. ClubMembership "pending"

Self-registration approval is a separate workflow/entity (`RegistrationRequest`), not a `ClubMembership.status` transition. The conflicting `/memberships/pending` + `/memberships/{id}/approve|reject` model is removed from the current contract. `RegistrationRequest` persistence, API and approval workflow remain out of scope for the current People + Membership implementation slice and require a separate Issue once specified.

### 6. Role assignment URI

The canonical API resource for role assignment is the top-level `/api/v1/role-assignments` collection, not the nested `/api/v1/users/{user_id}/roles` shape. Role-assignment API implementation remains out of scope for the current People + Membership implementation slice.

### 7. Groups API sequencing

Group / GroupMembership / GroupInstructorAssignment API is implemented in a separate Issue after the People + Membership slices. The current implementation work is not expanded to include Group API.

### 8. Sensitive Person fields

`phone`, `email`, and `address` are not exposed through the baseline `Person` API response until a dedicated permission/scope policy for these fields is defined. This is an explicit accepted scope limitation for the initial implementation.

### 9. Duplicate detection

No heuristic duplicate-person detection is implemented for single `Person` creation in this MVP. The bulk-import heuristic is not ported to single-record creation. `DUPLICATE_PERSON` remains reserved for potential future use.

### 10. Concurrency control

No project-wide canonical optimistic-concurrency mechanism exists. The People + Membership implementation slice does not invent a new mechanism and accepts last-write-wins semantics for `PATCH` operations on `Person`/`ClubMembership`. Introducing a project-wide mechanism remains a separate future architectural decision.

### 11. Audit vocabulary — `ClubMembership` non-lifecycle updates (amends ADR-0024 §4)

`membership.updated` is added to the closed, canonical `AuditLog.action` vocabulary defined by ADR-0024 §4 for non-lifecycle changes to an existing `ClubMembership`, currently the `membership_type` attribute.

This action is distinct from:

- `membership.status_changed`, which records a membership status transition;
- `membership.ended`, which records the ending of a membership period when `left_at` is first set.

A single status transition that also ends a membership period may therefore emit both `membership.status_changed` and `membership.ended`. `membership.updated` is not used for lifecycle transitions.

The existing ADR-0024 transaction, fail-closed, secret-prohibition and append-only rules apply unchanged. Because the audit vocabulary is closed, this amendment is reflected in ADR-0024 §4 and in the implemented database CHECK constraint.

## Scope note

`GuardianRelationship` API (permission, URI, and termination semantics above) may be specified and implemented as a subsequent, separate slice; it does not block the `Person` + `ClubMembership` implementation.

## Consequences

- `docs/03-architecture/adr/ADR-0024-audit-infrastructure.md` §4 is amended by decisions 1 and 11 to include the three `person.*` actions and `membership.updated`.
- `docs/02-requirements/roles-and-permissions.md` gains the two GuardianRelationship permission codes and corresponding authorization-matrix row.
- People/API/domain documentation is kept aligned with the decisions above, including canonical `guardian-relationships` naming and the canonical User/person field vocabulary.
- Issue #62 reflects these resolutions; `Person`/`ClubMembership` are no longer blocked. `GuardianRelationship` API remains a follow-up slice.
- No new persistence table or field is required by this ADR. The audit action vocabulary is already represented by the existing `AuditLog.action` CHECK constraint and is updated as part of the implementation amendment.

## Traceability

- Issue #62 — Foundation: People & Membership API
- ADR-0024 — Canonical audit infrastructure (amended by decisions 1 and 11)
- ADR-0023 — Event relationships and GuardianRelationship persistence
- ADR-0017 — Identity and authorization documentation canonicalization
- ADR-0004 — API architecture
