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

`user_id` is used because Event authorization is evaluated against the authenticated User principal. The Person behind the User is available through the identity model.

`role_in_event` remains a string. The initial persistence contract does not close the vocabulary; the value must describe the person's responsibility/staff role in the Event. Vocabulary reconciliation with `GroupInstructorAssignment.role_in_group` remains a follow-up if the product requires a shared dictionary.

Multiple active staff assignments are allowed. At most one active assignment for an Event may have `is_primary = true`. The primary assignment is the canonical Event responsibility used for `own_events` when responsibility is required. Non-primary assignments still represent explicit Event responsibility/staff membership and may be used by operations whose policy permits assigned staff access.

Assignments are historical. Closing `valid_to` preserves the relationship history and does not delete it.

An Event staff assignment is valid only when the assigned User's Person has an active `ClubMembership` in the Event's Club. Cross-Club enforcement uses the shared application/service ownership mechanism from ADR-0022.

`Event.created_by` is not an Event responsibility substitute and must never be used to implement `own_events`.

### 2. Event-to-Group targeting: `EventGroupTarget`

The canonical entity name is `EventGroupTarget`.

It represents target audience only. It does not create participation, registration or attendance.

Canonical fields:

- `id`
- `event_id`
- `group_id`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

An Event may target multiple Groups and a Group may be targeted by multiple Events.

Targeting is historical: removing a target closes the relationship period instead of deleting the historical row.

The relationship is valid only when `Event.club_id == Group.club_id`. Enforcement follows ADR-0022 at the authoritative application/service boundary, in the same transaction as the write.

`own_groups` Event visibility is determined from active `EventGroupTarget` rows joined to Groups for which the requester has an active `GroupInstructorAssignment`.

Targeting a Group does not automatically create an `EventParticipation` row.

### 3. GuardianRelationship

`GuardianRelationship` remains a Person-to-Person relationship and does not receive a `club_id` column.

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

The relationship is invalid when guardian and child are the same Person.

Canonical relationship status values are:

- `active`
- `inactive`
- `revoked`

`valid_from`/`valid_to` determine temporal validity. An `active` relationship is authorization-eligible only when its validity interval contains the authorization time.

Duplicate active relationships for the same guardian, child and relationship type are not allowed. Historical inactive/revoked rows are preserved.

At most one valid primary-contact relationship may exist for a child at a time. Primary-contact status is independent of the relationship type.

Because the relationship itself is Club-neutral, Club authorization is resolved from the persons' current Club memberships. Guardian access to an Event in Club X requires:

1. an active GuardianRelationship from the requester to the child;
2. the child to have an active ClubMembership in Club X;
3. the guardian User's Person to have an active ClubMembership in Club X when the product's club membership policy requires authenticated club participation.

A GuardianRelationship in one Club must not be treated as proof of membership or authorization in another Club.

### 4. EventParticipation dependency boundary

`EventParticipation` is a separate persistence entity and remains outside the relationship foundation implementation defined by this ADR.

The authorization dependency is deterministic:

- `self` Event visibility requires an active/eligible `EventParticipation` relationship for the requesting Person, unless a later explicit self-visible Event policy adds another relationship;
- `children` Event visibility is allowed when the Event is related to an authorized child through either EventParticipation or active Group membership represented by an active `EventGroupTarget` to that child's Group;
- group targeting alone does not create participation;
- participation does not imply attendance;
- registration status transition rules and self-registration policy remain deferred as stated by ADR-0020.

The persistence implementation of `EventParticipation` must therefore preserve at least the documented `event_id`, `person_id`, registration state and timestamps, with a uniqueness constraint preventing duplicate participation for the same Event and Person.

### 5. Authorization consequences

The canonical relationship sources are:

| Scope | Relationship source |
|---|---|
| `own_events` | active `EventStaffAssignment` for requester, with applicable responsibility policy |
| `own_groups` | active `EventGroupTarget` + active `GroupInstructorAssignment` for requester |
| `self` | requester Person's eligible `EventParticipation` |
| `children` | active `GuardianRelationship` + child-related EventParticipation or active Group membership through EventGroupTarget |
| `all` | club-level permission and object policy; no relationship required |
| `none` | no access |

Role names alone never establish these relationships.

## Consequences

- Event API authorization has explicit persistence sources for `own_events`, `own_groups` and `children`.
- Event responsibility no longer depends on `created_by`.
- Group targeting remains separate from participation and attendance.
- Guardian relationships remain reusable across Clubs while authorization remains Club-bound through active memberships and Event relationships.
- Event API implementation can proceed after the persistence foundations covered by this ADR are implemented.

## Non-goals

This ADR does not implement:

- Event API;
- EventStaffAssignment persistence;
- EventGroupTarget persistence;
- GuardianRelationship persistence;
- EventParticipation persistence;
- self-registration workflow;
- registration transition policy;
- attendance;
- new permissions or scopes;
- role grants.

## Traceability

- Issue #47
- ADR-0013 — canonical scope vocabulary
- ADR-0017 — identity and authorization documentation canonicalization
- ADR-0020 — Event authorization and participation contract
- ADR-0021 — Group persistence model
- ADR-0022 — cross-Club ownership integrity
