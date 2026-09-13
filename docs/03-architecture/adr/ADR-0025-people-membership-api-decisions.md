# ADR-0025: People & Membership API — PO/architect decisions

## Status

Accepted.

## Context

The documentation-reconciliation pass for Issue #62 ("Foundation: People & Membership API") surfaced ten explicit GAPs that could not be resolved from existing canonical documents alone: a missing audit action vocabulary for `Person`, a missing permission for `GuardianRelationship`, an unresolved `GuardianRelationship` termination semantics/resource-naming ambiguity, a three-way inconsistency around self-registration ("pending" `ClubMembership` vs. a separate `RegistrationRequest` entity), a Role-assignment URI conflict, an incomplete Group API contract, an unresolved sensitive-Person-field policy, an unspecified duplicate-detection heuristic, and an unchosen concurrency-control mechanism.

The Product Owner/architect reviewed Issue #62 and explicitly accepted the decisions below (recorded here verbatim as an ADR per this repository's convention that PO decisions affecting persistence, permissions, or API contracts are captured in an ADR before implementation proceeds).

## Decision

### 1. Audit vocabulary — `Person` actions (amends ADR-0024 §4)

`person.created`, `person.updated`, `person.archived` are added to the closed, canonical `AuditLog.action` vocabulary defined by ADR-0024 §4. ADR-0024's existing rules (transaction/fail-closed semantics, secret prohibition, append-only, no retention/TTL) are unchanged and apply to these three actions exactly as to every other canonical action. No other action is added; ADR-0024 §4 remains closed beyond this explicit amendment.

### 2. GuardianRelationship permissions

`guardian_relationship.read` and `guardian_relationship.manage` are added to the canonical permission catalog (`docs/02-requirements/roles-and-permissions.md` §4), following the existing `<resource>.<action>` convention (e.g. `membership.read`/`membership.manage`). `people-api.md`'s prior reference to a non-canonical `guardian.read` is corrected to use these two codes.

### 3. GuardianRelationship termination semantics

The `terminate` API action always sets `GuardianRelationship.status = revoked`. `inactive` remains a distinct, non-revoked historical status reached through other lifecycle events (for example, the relationship's `valid_to` naturally elapsing) — it is never the result of the explicit `terminate` action. No new `status` value is introduced; the three canonical values (`active`, `inactive`, `revoked`) from ADR-0023 §3 are unchanged.

### 4. GuardianRelationship URI

The canonical resource for addressing a `GuardianRelationship` by its own id is `/api/v1/guardian-relationships/{relationship_id}`. `/guardians` is **not** retained as an API alias or resource name for the relationship entity anywhere in the contract — including the nested per-person collection endpoint, which is likewise named `guardian-relationships` (e.g. `/api/v1/persons/{person_id}/guardian-relationships`). This resolves the duplication previously present in `docs/05-api/endpoint-inventory.md` §5 (which exposed both `/guardians/*` and `/guardian-relationships/*` for the same entity) and the mismatch with `docs/05-api/people-api.md` (which used only `/guardians/*`).

### 5. RegistrationRequest vs. ClubMembership "pending"

Self-registration approval is a separate workflow/entity (`RegistrationRequest`, per `docs/04-modules/people-and-membership.md` §10), not a `ClubMembership.status` transition. `people-api.md`'s prior `/memberships/pending` + `/memberships/{id}/approve|reject` endpoints described a conflicting model and are removed from the current contract. `RegistrationRequest`'s own persistence model, API, and approval workflow remain out of scope for the current People + Membership implementation slice and require a separate Issue once specified.

### 6. Role assignment URI

The canonical API resource for role assignment is the top-level `/api/v1/role-assignments` collection (matching `docs/05-api/endpoint-inventory.md` §24), not the nested `/api/v1/users/{user_id}/roles` shape previously described in `people-api.md` §23. Role-assignment API implementation remains out of scope for the current People + Membership implementation slice.

### 7. Groups API sequencing

Group / GroupMembership / GroupInstructorAssignment API is implemented in a separate Issue after the People + Membership (Person + ClubMembership + GuardianRelationship) slices. The current implementation work is not expanded to include Group API.

### 8. Sensitive Person fields

`phone`, `email`, and `address` are not exposed through the baseline `Person` API response until a dedicated permission/scope policy for these fields is defined. This is an explicit, accepted scope limitation for the initial implementation, not a temporary oversight to be silently widened later.

### 9. Duplicate detection

No heuristic duplicate-person detection is implemented for single `Person` creation in this MVP. The bulk-import heuristic (`docs/04-modules/people-and-membership.md` §11.3) is not ported to single-record creation. `DUPLICATE_PERSON` remains a reserved error code for potential future use, not a requirement of the current slice.

### 10. Concurrency control

No project-wide canonical optimistic-concurrency mechanism exists (`docs/05-api/api-contract.md` §19/`docs/05-api/api-conventions.md` §17 leave the specific mechanism, e.g. version column vs. ETag/`If-Match`, to be "selected per affected domain"; no other implemented domain in this codebase — Events, Groups — has adopted one). Per this ADR, the People + Membership implementation slice does not invent a new mechanism. It accepts last-write-wins semantics for `PATCH` operations on `Person`/`ClubMembership` for this slice, and the implementation must document this explicitly (not silently omit concurrency handling without comment). Introducing a project-wide mechanism remains a separate, future architectural decision.

## Scope note

`GuardianRelationship` API (permission, URI, and termination semantics above) may be specified and implemented as a subsequent, separate slice; it does not block the `Person` + `ClubMembership` implementation.

## Consequences

- `docs/03-architecture/adr/ADR-0024-audit-infrastructure.md` §4 is amended (not silently rewritten — see the amendment note added there) to include the three `person.*` actions.
- `docs/02-requirements/roles-and-permissions.md` gains two permission codes and a corresponding authorization-matrix row.
- `docs/05-api/people-api.md`, `docs/05-api/endpoint-inventory.md`, `docs/03-architecture/domain-model.md`, and `docs/03-architecture/database-schema.md` are updated to reflect decisions 3-6 above.
- Repository-wide consistency sweep for decision 4 (guardian resource naming) also corrected `docs/03-architecture/application-architecture.md`'s example domain list and the resource-naming lists in `docs/05-api/api-contract.md` and `docs/05-api/api-conventions.md` (all previously showed `/guardians`, one also showed `/members` instead of `/persons` — same class of drift as the `/people` vs `/persons` fix in the earlier reconciliation PR), plus the informal `permission guardian` reference in `docs/05-api/auth-and-authorization.md` §14 (now names the two concrete codes) and non-canonical `blocked`/`date_of_birth` wording found in `docs/05-api/auth-api.md`, `docs/05-api/auth-and-authorization.md`, and `docs/04-modules/people-and-membership.md` (corrected to the ADR-0017-canonical `User.status` vocabulary and the canonical `birth_date` field name respectively).
- Issue #62 is updated to reflect these resolutions; `Person`/`ClubMembership` are no longer blocked. `GuardianRelationship` API is moved to a separate follow-up Issue per the scope note above.
- No persistence schema change is required by this ADR (the `AuditLog`/`RolePermission`/`GuardianRelationship` tables already support the field values named here; only the closed vocabularies/catalogs they validate against are extended).

## Traceability

- Issue #62 — Foundation: People & Membership API (documentation-reconciliation draft)
- ADR-0024 — Canonical audit infrastructure (amended by decision 1)
- ADR-0023 — Event relationships and GuardianRelationship persistence (status vocabulary unchanged by decision 3)
- ADR-0017 — Identity and authorization documentation canonicalization
- ADR-0004 — API architecture
