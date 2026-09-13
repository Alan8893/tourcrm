# ADR-0023: Event relationships and GuardianRelationship persistence

## Status
Accepted

## Context

ADR-0020 requires deterministic authorization for Event scopes `own_events`, `own_groups`, `self` and `children`. ADR-0021 defines Group responsibility but deliberately defers Event-to-Group targeting. The existing logical/module documentation also contains non-canonical descriptions of Event staff responsibility and GuardianRelationship.

Issue #47 resolves these persistence contracts before Event API authorization is implemented.

## Decision

### 1. Event responsibility — `EventStaffAssignment`

The canonical entity name is `EventStaffAssignment`.

Canonical fields:

- `id`
- `event_id`
- `user_id`
- `role_in_event`
- `is_primary`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

`user_id` is the authorization actor reference. `role_in_event` remains a string until a shared responsibility vocabulary is explicitly reconciled with `GroupInstructorAssignment.role_in_group`.

Multiple active staff assignments are allowed. At most one active assignment for an Event may have `is_primary = true`; that assignment is the primary Event responsibility. Assignments are historical and are closed through `valid_to` rather than deleted.

An assigned User must have an active `ClubMembership` in the Event's Club. This is enforced through the authoritative application/service ownership mechanism from ADR-0022.

`Event.created_by` is creation metadata only and must never substitute for Event responsibility or `own_events`.

### 2. Event-to-Group targeting — `EventGroupTarget`

The canonical entity name is `EventGroupTarget`.

Canonical fields:

- `id`
- `event_id`
- `group_id`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

An Event may target multiple Groups and a Group may be targeted by multiple Events. Targeting is historical: removing a target closes its validity period.

The invariant `Event.club_id == Group.club_id` is mandatory and is enforced at the authoritative application/service boundary according to ADR-0022.

`own_groups` Event visibility is resolved from active `EventGroupTarget` rows joined to Groups for which the requester has an active `GroupInstructorAssignment`.

Group targeting is audience selection only. It does not create `EventParticipation`, registration or attendance.

### 3. GuardianRelationship

`GuardianRelationship` remains a Club-neutral Person-to-Person relationship and has no `club_id`.

Canonical fields:

- `id`
- `guardian_person_id`
- `child_person_id`
- `relationship_type`
- `status`
- `is_primary_contact`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

Guardian and child must differ. Canonical status values are `active`, `inactive`, `revoked`. Duplicate active relationships for the same guardian, child and relationship type are forbidden; historical rows are preserved. At most one valid primary-contact relationship exists for a child at a time.

For Event access in Club X, guardian authorization requires an active valid GuardianRelationship, the child to have an active ClubMembership in Club X, and the guardian User's Person to have an active ClubMembership in Club X. A GuardianRelationship is not proof of authorization in another Club.

### 4. EventParticipation dependency

`EventParticipation` remains a separate persistence entity.

- `self` Event visibility requires an eligible EventParticipation relationship for the requesting Person;
- `children` Event visibility may derive from child EventParticipation or active child GroupMembership reached through active EventGroupTarget;
- group targeting does not create participation;
- participation does not imply attendance;
- self-registration and registration transition policy remain deferred under ADR-0020.

The persistence implementation must prevent duplicate participation for the same Event and Person.

### 5. Canonical authorization relationship sources

| Scope | Relationship source |
|---|---|
| `own_events` | active `EventStaffAssignment` for requester |
| `own_groups` | active `EventGroupTarget` + active `GroupInstructorAssignment` |
| `self` | requester Person's eligible `EventParticipation` |
| `children` | active `GuardianRelationship` + child-related participation or group targeting |
| `all` | club-level permission/object policy |
| `none` | no access |

Role membership alone never establishes these relationships.

## Consequences

- Event authorization no longer depends on `Event.created_by`.
- `own_groups`, `own_events`, `self` and `children` have explicit relationship sources.
- Event targeting remains separate from participation and attendance.
- Guardian relationships remain reusable across Clubs while Event authorization remains Club-bound.
- The persistence foundations can now be implemented as separate issues before Issue #40.

## Non-goals

No Event API, relationship persistence implementation, self-registration workflow, attendance implementation, new permissions/scopes or role grants are introduced by this ADR.

## Traceability

- Issue #47
- ADR-0013 — canonical scope vocabulary
- ADR-0017 — identity and authorization documentation canonicalization
- ADR-0020 — Event authorization and participation contract
- ADR-0021 — Group persistence model
- ADR-0022 — cross-Club ownership integrity
