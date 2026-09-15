# ODR-0002 — Group Schedule Visibility and Group Lifecycle

- **Status:** Open — blocks TH-0083 implementation readiness
- **Date:** 2026-09-15
- **Decision owner:** Product Owner
- **Scope:** `GET /api/v1/groups/{group_id}/schedule`

## Context

TH-0083 requires a deterministic authorization contract for the Group Schedule projection. Existing canonical sources establish that:

- `event.read` is the permission for Event read and calendar projections;
- `own_groups` is based on an explicit active `GroupInstructorAssignment`;
- `GroupMembership` is a separate membership fact and is not an Event authorization relationship;
- Event authorization for recurring occurrences uses direct occurrence-level GroupTarget relationships under ADR-0029;
- `children` access requires GuardianRelationship and the existing membership/object policy;
- `occurrence.club_id` alone never grants non-`all` access.

However, the canonical sources do not define whether a member's active GroupMembership, a guardian's active child membership, or another limited Group read relationship is sufficient to view the schedule of a Group, nor do they define whether an archived Group's schedule remains readable for historical events.

Inferring these rules from role names, UI assumptions, GroupMembership, or `club_id` would create a new authorization policy implicitly.

## Confirmed non-blocking contract

The endpoint is a contextual projection, not a new event identity model. It must:

- use `GET /api/v1/groups/{group_id}/schedule`;
- require `event.read`;
- use `[from, to)` timezone-aware RFC 3339 range semantics normalized to UTC;
- return only Event/EventOccurrence records explicitly targeted to the requested Group;
- never treat GroupMembership as an Event GroupTarget;
- use occurrence-level GroupTarget records for recurring occurrences;
- reuse canonical calendar response identity, effective occurrence times, status visibility, pagination envelope, deterministic `start_at ASC, id ASC` ordering, error envelope and authorization-before-pagination behavior;
- extend recurring materialization as required by the requested range, without computing RRULE in the endpoint;
- hide an unauthorized/non-existent Group consistently with existing object existence-hiding policy;
- never authorize from `Group.club_id` or `EventOccurrence.club_id` alone;
- exclude conflict detection, notifications, frontend behavior and new permissions/scopes.

## Blocking decisions required from Product Owner

### 1. Who may read a Group Schedule?

Choose and document the canonical object relationship for each access class:

- `all` — may read schedules of Groups within the caller's allowed Club boundary;
- `own_groups` — may read schedules only for Groups with an active `GroupInstructorAssignment` for the caller;
- `self` — whether an active GroupMembership for the caller's Person is sufficient to read that Group's schedule;
- `children` — whether an active GroupMembership for a related child is sufficient to read that Group's schedule;
- `own_events` — whether direct Event/EventOccurrence staff assignment alone permits the Group Schedule projection when the caller is not an instructor of the Group;
- `none` — no access.

### 2. Membership versus event targeting

If `self`/`children` access is granted through GroupMembership, explicitly confirm that the returned schedule still contains only events/occurrences whose authoritative relationship targets the requested Group. Membership must not expose unrelated events merely because the person belongs to the Group.

### 3. Archived Groups

Define whether an archived Group:

- remains readable for historical schedule queries;
- rejects schedule access entirely;
- or follows another explicit policy.

Also define whether future scheduled events targeting an archived Group remain visible if the Group itself is archived.

### 4. Ended membership

If `self`/`children` access is based on GroupMembership, define whether access is evaluated at request time or against membership effectivity at the event/occurrence start instant. The canonical GroupMembership interval is `[valid_from, valid_to)`.

## Rejected inference

Until the Product Owner decision is accepted, implementation must not:

- grant schedule access to every member of a Club;
- equate GroupMembership with EventGroupTarget;
- grant access because `created_by` matches the requester;
- use `club_id` as a substitute for an Event/Occurrence relationship;
- introduce a new `group.schedule.read` permission or a new scope;
- infer archived-group behavior from the Group lifecycle alone.

## Resolution

Once the Product Owner selects the policy, this ODR should be updated to **Resolved**, the canonical schedule API specification and authorization documentation should be synchronized, and only then should TH-0083 create its implementation Issue.
