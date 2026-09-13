# ADR-0022: Cross-Club ownership integrity

## Status
Accepted

## Context

The Group persistence foundation uses independent foreign keys:

- `Group.club_id -> Club`;
- `GroupMembership.group_id -> Group`;
- `GroupMembership.club_membership_id -> ClubMembership`;
- `GroupInstructorAssignment.group_id -> Group`;
- `GroupInstructorAssignment.user_id -> User`.

Individual foreign keys provide referential integrity but do not prove that related objects belong to the same Club. PR #43 demonstrated that cross-Club GroupMembership and instructor assignments can therefore be persisted unless an ownership invariant is enforced separately.

`User` is intentionally not Club-owned. A User represents an account for a Person and may have relationships with more than one Club. Adding a synthetic `User.club_id` would conflict with the identity model.

A database trigger/composite-FK solution would require additional physical coupling or redundant Club columns and would introduce a new mechanism not used by the current identity model.

## Decision

### 1. Canonical ownership invariant

Every relationship between Club-owned Group data and another domain object must resolve to the same Club before the relationship is accepted.

For the Group foundation:

- `GroupMembership` is valid only when `Group.club_id == ClubMembership.club_id`;
- `GroupInstructorAssignment` is valid only when the assigned `User` has an active Club relationship for `Group.club_id` through the existing identity/membership model.

A User's relationship with one Club does not authorize assignment to a Group belonging to another Club.

### 2. User and multiple Clubs

`User` remains Club-neutral.

A User may be associated with multiple Clubs. Club context is resolved from the explicit relationship being operated on:

```text
User -> Person -> ClubMembership -> Club
```

For `GroupInstructorAssignment`, the target Group supplies the required Club context. The assignment is valid only if the User's Person has an active `ClubMembership` in that Club.

A membership in Club A does not satisfy an assignment to a Group in Club B.

The same User may have valid instructor assignments in multiple Clubs when the corresponding Club relationships exist.

### 3. Enforcement boundary

Cross-Club ownership is an **application/service-layer invariant**, enforced by one shared ownership-validation mechanism rather than by ad-hoc endpoint checks.

The service layer is authoritative for accepting writes to these relationships. API handlers must not duplicate or weaken the invariant; they must call the canonical service/domain operation.

The database remains responsible for ordinary referential integrity, interval constraints, uniqueness/exclusion constraints and deletion protection. The database is not required to infer Club ownership across independently keyed relationships.

No database trigger or redundant `club_id` column is introduced by this ADR.

### 4. GroupMembership rule

Before creating or changing a `GroupMembership`, the canonical service operation must verify:

```text
Group.club_id == ClubMembership.club_id
```

The operation must reject a mismatch and must not persist the relationship.

The existing `club_membership_id` remains the canonical ownership path. No `person_id` or duplicate Club field is added to `GroupMembership`.

### 5. GroupInstructorAssignment rule

Before creating or changing a `GroupInstructorAssignment`, the canonical service operation must verify that:

```text
User -> Person -> active ClubMembership -> Group.club_id
```

exists.

The global `instructor` role is not sufficient by itself. A role assignment may grant permission to perform the operation, but it does not replace the explicit Club relationship required by this ownership invariant.

The assignment continues to store `user_id`, not `person_id` and not `club_id`.

### 6. Transaction boundary

The ownership check and relationship write must execute within the same database transaction. The implementation must avoid a check-then-write sequence that can silently bypass the invariant under concurrent membership changes.

Where concurrency can invalidate the checked relationship, the service implementation must use appropriate transactional locking/isolation or an equivalent revalidation strategy. The exact SQLAlchemy/PostgreSQL technique is implementation detail unless a later ADR requires otherwise.

### 7. Future Event/Group relationships

Any future Event-to-Group relationship must apply the same invariant:

```text
Event.club_id == Group.club_id
```

The relationship must be accepted only through the same canonical ownership-validation mechanism. `Event.created_by` must not be used as a substitute.

### 8. Scope

This ADR defines the ownership invariant and enforcement architecture. It does not implement Group API, Event-to-Group targeting, Event API, new permissions, new scopes, or role grants.

Issue #41 must add the canonical service-level enforcement when its persistence foundation is made usable by write paths. If the current persistence-only implementation has no service layer, the database models may remain structurally independent until the shared service operation is introduced; detection-only tests must not be treated as satisfying this invariant for production writes.

## Consequences

- Club ownership is explicit and deterministic without changing the identity model.
- A User can legitimately participate in multiple Clubs without a synthetic primary Club.
- Cross-Club Group relationships are rejected at one shared application boundary.
- Database-level referential integrity remains simple and consistent with the existing architecture.
- Future Event-to-Group authorization can rely on the same Club invariant.
- Persistence-only tests may demonstrate the database's structural limitation, but production write services must enforce the invariant before accepting a relationship.

## Reconciliation requirements

The following canonical documents must describe this invariant consistently:

- `docs/03-architecture/domain-model.md`;
- `docs/03-architecture/data-model.md`;
- `docs/03-architecture/database-schema.md`;
- `docs/04-modules/people-and-membership.md`;
- `docs/05-api/auth-and-authorization.md`;
- `docs/03-architecture/adr/ADR-0021-group-persistence-model.md`.

## Traceability

- Issue #44 — Define and enforce cross-Club ownership integrity for Group relationships
- Issue #41 — Group persistence foundation
- ADR-0017 — Identity and authorization documentation canonicalization
- ADR-0021 — Group persistence model
