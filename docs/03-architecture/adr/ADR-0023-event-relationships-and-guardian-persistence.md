# ADR-0023: Event relationships and GuardianRelationship persistence

## Status
Accepted

## Decision

Canonical relationship contracts for Event authorization are defined here. See Issue #47.

- Event responsibility: `EventStaffAssignment` (`id`, `event_id`, `user_id`, `role_in_event`, `is_primary`, `valid_from`, `valid_to`, timestamps). Multiple active staff assignments are allowed; at most one active assignment per Event is primary. Assigned User must have active ClubMembership in the Event Club. `Event.created_by` is never a substitute for `own_events`.
- Event-to-Group targeting: `EventGroupTarget` (`id`, `event_id`, `group_id`, `valid_from`, `valid_to`, timestamps). Many-to-many targeting is allowed, historical periods are retained, and `Event.club_id == Group.club_id` is mandatory. Targeting does not create participation or attendance.
- GuardianRelationship remains Person-to-Person and Club-neutral. Canonical status values are `active`, `inactive`, `revoked`; guardian and child must differ; duplicate active relationships of the same type are forbidden; at most one valid primary contact exists per child. Event access additionally requires the child and guardian to have active ClubMembership in the Event Club.
- EventParticipation remains separate. `self` uses eligible participation; `children` uses active GuardianRelationship plus child participation or active child GroupMembership reached through EventGroupTarget. Self-registration policy remains deferred.

Scope sources: `own_events` -> EventStaffAssignment; `own_groups` -> EventGroupTarget + GroupInstructorAssignment; `self` -> EventParticipation; `children` -> GuardianRelationship + child-related participation/group targeting; `all` -> club permission/object policy; `none` -> no access.

## Non-goals

No implementation, new permissions/scopes, role grants, self-registration or attendance are included.

## Traceability

Issue #47; ADR-0013; ADR-0017; ADR-0020; ADR-0021; ADR-0022.
