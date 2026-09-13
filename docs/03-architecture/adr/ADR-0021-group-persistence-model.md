# ADR-0021: Group persistence model

## Status
Accepted

## Context

The canonical Group documentation described `Group`, `GroupMembership` and group instructor responsibility, but the persistence contract was inconsistent across the domain model, logical data model, database schema and People & Membership module.

The main conflict concerned `GroupMembership`: some documents referenced `person_id`, while the database schema referenced `club_membership_id`. The documents also disagreed on the status field name and introduced `is_primary` / `assigned_by` without a single canonical basis.

`GroupInstructorAssignment` was identified as necessary for deterministic `own_groups` authorization, but its persistence fields were not specified.

Group status values were also not defined as a canonical lifecycle vocabulary.

## Decision

### 1. Group

`Group` remains a standalone domain entity owned by a `Club`.

Canonical persistence fields are:

- `id`;
- `club_id`;
- `name`;
- `description`;
- `status`;
- `valid_from`;
- `valid_to`;
- `created_at`;
- `updated_at`.

`Group.status` remains a string value for now. No canonical enum/check constraint is introduced by this ADR. Group lifecycle transitions require a separate business-policy decision.

### 2. GroupMembership

`GroupMembership` is an historical association between a `ClubMembership` and a `Group`.

Canonical persistence fields are:

- `id`;
- `group_id`;
- `club_membership_id`;
- `valid_from`;
- `valid_to`;
- `membership_status`;
- `created_at`;
- `updated_at`.

`person_id` is **not** stored in `group_memberships`.

The person is resolved through `club_membership_id -> ClubMembership.person_id`. This makes the club boundary explicit because both `Group` and `ClubMembership` belong to the same `Club`.

`membership_status` is the canonical field name. The generic name `status` is not used for this table.

`is_primary` is not part of the first persistence model. Whether a person may belong to multiple groups simultaneously and whether a primary group concept is needed are separate business-policy questions.

`assigned_by` is not a GroupMembership domain field. Assignment actor/audit information is handled through the applicable audit/created-by infrastructure rather than a second domain actor field.

Historical membership records are preserved. Closing one interval does not delete it.

### 3. GroupInstructorAssignment

Explicit instructor responsibility for a group is persisted as a separate domain association. Global `instructor` role membership is not sufficient to establish responsibility for a group.

Canonical persistence fields are:

- `id`;
- `group_id`;
- `user_id`;
- `role_in_group`;
- `is_primary`;
- `valid_from`;
- `valid_to`;
- `created_at`;
- `updated_at`.

`user_id` is used because authorization responsibility belongs to an authenticated `User` principal.

`role_in_group` is intentionally not assigned a closed enum by this ADR. The concrete allowed vocabulary must be reconciled with the Event responsibility model before implementation of overlapping responsibility concepts.

`is_primary` distinguishes the primary responsible instructor from other explicit group assignments.

Assignment history is preserved through `valid_from` / `valid_to`; ending an assignment does not delete the historical record.

### 4. Group ownership and authorization

A Group belongs to exactly one Club. Group-related objects must not cross Club boundaries.

The `own_groups` authorization scope is based on explicit active `GroupInstructorAssignment` relationships. It must not be inferred solely from a user's global or club-level instructor role.

This ADR does not define Group API authorization grants or role grants; those remain governed by the canonical permission catalog and existing authorization ADRs.

### 5. Event targeting is separate

This ADR deliberately does **not** define the Event-to-Group targeting association.

`own_groups` Event authorization requires both:

```text
User
  -> GroupInstructorAssignment
  -> Group
  <- EventGroupTarget
  <- Event
```

The Event-to-Group relationship is a separate domain dependency and must be specified before the complete Event authorization implementation can be accepted.

It must not be approximated by `Event.created_by` or another unrelated field.

### 6. Scope of the Group persistence foundation

The first implementation of this ADR is limited to persistence foundations for:

- `Group`;
- `GroupMembership`;
- `GroupInstructorAssignment`.

It does not include:

- Group API endpoints;
- Event-to-Group targeting;
- Event API;
- EventParticipation;
- GuardianRelationship;
- self-registration;
- attendance;
- notification/calendar integrations;
- new permissions, scopes or role grants;
- frontend work.

## Consequences

- The physical GroupMembership model has one unambiguous club-aware ownership path.
- `own_groups` can be implemented from explicit group responsibility rather than role inference once Event targeting exists.
- Group membership history is preserved without introducing an unsupported primary-group concept.
- Group instructor responsibility is explicit and historical.
- Group lifecycle vocabulary remains intentionally open until product/business rules are defined.
- Event authorization remains blocked until the separate Event-to-Group targeting contract exists, together with the other documented Event dependencies.

## Traceability

- `docs/04-modules/people-and-membership.md` §8
- `docs/03-architecture/domain-model.md` §9
- `docs/03-architecture/data-model.md` §6
- `docs/03-architecture/database-schema.md` §8
- `docs/05-api/people-api.md` §§14–16, 24
- `docs/02-requirements/roles-and-permissions.md`
- `docs/03-architecture/adr/ADR-0013-canonical-scope-vocabulary.md`
- `docs/03-architecture/adr/ADR-0020-event-authorization-and-participation-contract.md`
