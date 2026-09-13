# ADR-0023: Event relationships and GuardianRelationship persistence

## Status
Accepted

## Context

ADR-0020 requires deterministic authorization for Event scopes `own_events`, `own_groups`, `self` and `children`. ADR-0021 defines Group responsibility but deliberately defers Event-to-Group targeting. The logical model also contains only non-canonical descriptions for Event staff responsibility and GuardianRelationship.

Issue #47 resolves these persistence contracts before Event API authorization is implemented.

## Decision

### 1. Event responsibility: `EventStaffAssignment`

The canonical entity name is `EventStaffAssignment`.

It represents an explicit responsibility/staff relationship between an authenticated `User` and an `Event`.

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

`user_id` is used because Event authorization is evaluated against the authenticated User principal. `role_in_event` remains a string until a shared responsibility vocabulary is explicitly reconciled. Multiple active staff assignments are allowed; at most one active assignment per Event may be primary. Assignments are historical. An assigned User must have an active ClubMembership in the Event's Club. `Event.created_by` is never a substitute for Event responsibility or `own_events`.

### 2. Event-to-Group targeting: `EventGroupTarget`

The canonical entity name is `EventGroupTarget`.

Canonical fields:

- `id`
- `event_id`
- `group_id`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

An Event may target multiple Groups and a Group may be targeted by multiple Events. Targeting is historical. `Event.club_id` must equal `Group.club_id`; enforcement uses the authoritative service-layer mechanism from ADR-0022. Active `own_groups` visibility is resolved through active EventGroupTarget plus active GroupInstructorAssignment. Targeting never creates EventParticipation or attendance.

### 3. GuardianRelationship

`GuardianRelationship` remains a Person-to-Person relationship and has no `club_id`.

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

Guardian and child must differ. Canonical status values are `active`, `inactive`, `revoked`. Duplicate active relationships for the same guardian, child and relationship type are forbidden; historical rows are retained. At most one valid primary-contact relationship exists for a child at a time.

For Event access in Club X, guardian authorization requires an active valid GuardianRelationship, the child to have an active ClubMembership in Club X, and the guardian User's Person to have an active ClubMembership in Club X. A GuardianRelationship is not proof of authorization in another Club.

### 4. EventParticipation dependency

EventParticipation remains a separate persistence entity. `self` visibility requires an eligible participation relationship for the requester. `children` visibility may derive from child EventParticipation or active child GroupMembership reached through active EventGroupTarget. Group targeting does not create participation, and participation does not imply attendance. Self-registration and registration transition policy remain deferred under ADR-0020.

### 5. Scope relationship sources

| Scope | Canonical source |
|---|---|
| `own_events` | active EventStaffAssignment for requester |
| `own_groups` | active EventGroupTarget + active GroupInstructorAssignment |
| `self` | requester Person's eligible EventParticipation |
| `children` | active GuardianRelationship + child-related participation or group targeting |
| `all` | club-level permission/object policy |
| `none` | no access |

Role names alone never establish these relationships.

## Consequences

Event authorization now has explicit persistence sources for `own_events`, `own_groups` and `children`. Event responsibility is separate from creation metadata. Group targeting remains separate from participation and attendance. Guardian relationships remain reusable across Clubs while Event authorization remains Club-bound.

## Non-goals

No Event API, persistence implementation, self-registration, attendance, new permissions/scopes or role grants are implemented by this ADR.

## Traceability

- Issue #47
- ADR-0013 — canonical scope vocabulary
- ADR-0017 — identity and authorization documentation canonicalization
- ADR-0020 — Event authorization and participation contract
- ADR-0021 — Group persistence model
- ADR-0022 — cross-Club ownership integrity
