# ADR-0020: Event authorization and participation contract

## Status
Accepted

## Context

Before implementation of the Event API and participation layer, the Event API
specification contained permission names that were not present in the canonical
permission catalog, and the scope/participation rules were not deterministic
enough for implementation.

Conflicting Event permission references included `event.archive`,
`event.participant.read`, `event.participant.manage`, `attendance.correct` and
`event.schedule.manage`. The canonical catalog already defines
`event.read`, `event.create`, `event.update`, `event.cancel`, `event.manage`,
`attendance.read` and `attendance.update`.

The participation model also distinguishes target audience, registration and
attendance, but the first implementation slice did not yet have a complete
self-registration policy (registration windows, age/group restrictions and
status transitions).

## Decision

### 1. Canonical permissions

No new Event/attendance permissions are introduced by this decision.

The following API operations map to existing canonical permissions:

| Operation | Canonical permission |
|---|---|
| Event read, participant read, calendar projection | `event.read` |
| Event create | `event.create` |
| Event update, recurrence/occurrence scheduling management | `event.update` / `event.manage` according to operation |
| Event cancel | `event.cancel` |
| Event archive | `event.manage` |
| Participant management | `event.manage` |
| Attendance read | `attendance.read` |
| Attendance update | `attendance.update` |
| Attendance correction after the normal attendance window | `attendance.update` plus mandatory correction reason and audit |

No implementation may invent a replacement permission or infer a role grant
that is not present in the canonical permission catalog.

### 2. Canonical scope semantics for Event access

The existing ADR-0013 vocabulary remains canonical:

- `all` — all eligible Event objects of the club, subject to permission,
  feature and object-policy checks;
- `own_groups` — Events targeted to groups for which the requester has an
  applicable responsible/ownership relationship;
- `own_events` — Events for which the requester is explicitly assigned or
  responsible;
- `self` — only the requester's own participation/registration or explicitly
  self-visible Event data;
- `children` — only Event data related to persons connected to the requester
  through an active GuardianRelationship and otherwise allowed by policy;
- `none` — no access.

`assigned_events` remains an alias of `own_events` as defined by ADR-0013.
`own_records` is not a scope.

Scope is evaluated together with object relationship and permission. A role
name alone never grants unrestricted Event visibility.

### 3. First-slice Event visibility and management

- `event.read` with `all` may read all eligible club Events.
- `event.read` with `own_groups` may read Events targeted to the requester's
  responsible groups.
- `event.read` with `own_events` may read Events explicitly assigned to the
  requester.
- `event.read` with `self` may read Events where the requester has an allowed
  self relationship, such as own participation/registration.
- `event.read` with `children` may read only Events related to the requester's
  children through active GuardianRelationship; it does not expose the club's
  complete Event catalog.
- Event mutation requires the corresponding canonical permission and an
  applicable object scope. Instructor role membership alone is insufficient.
- Event archive uses `event.manage`; it does not introduce `event.archive`.
- Participant management uses `event.manage`; it does not introduce separate
  participant permissions.
- Schedule/recurrence management uses the canonical Event update/manage
  permissions according to the mutation; it does not introduce
  `event.schedule.manage`.

### 4. Participation model

`EventParticipation` remains separate from group targeting. Being in a target
group does not automatically create a registration/participation record, and
registration does not imply attendance.

The first implementation slice does **not** implement self-registration
because the required registration policy is not yet deterministic.

The following registration statuses remain documented reference values:

- `invited`;
- `registered`;
- `waitlisted`;
- `declined`;
- `removed`.

Their complete transition graph, registration windows, age restrictions,
group restrictions, capacity/waitlist rules and cancellation deadline remain
out of scope until a separate business-policy decision is accepted.

### 5. Guardian access

Guardian Event visibility is relationship-based. An active
`GuardianRelationship` is required, and the resulting access is limited to
Event data relevant to the related child. Guardian access must never be
implemented as unrestricted `all` access merely because the requester has the
`guardian` role.

### 6. Attendance correction

Correction after the normal attendance window is still an
`attendance.update` operation. It additionally requires a non-empty reason
and an audit record containing the actor, previous value, new value, reason and
change time. A separate `attendance.correct` permission is not introduced.

## Consequences

- The Event API and module documentation use only the canonical permission
  catalog.
- Authorization can be implemented without inventing permissions or role
  grants.
- Event list/detail/calendar/participant access has deterministic first-slice
  scope semantics.
- Self-registration endpoints remain documented but blocked from implementation
  until the missing registration policy is resolved.
- A future decision may extend participation policy without changing the
  authorization vocabulary established here.

## Traceability

- `docs/02-requirements/roles-and-permissions.md`
- `docs/02-requirements/business-rules.md`
- `docs/04-modules/events-and-schedule.md`
- `docs/05-api/events-api.md`
- `docs/03-architecture/adr/ADR-0013-canonical-scope-vocabulary.md`
- `docs/03-architecture/adr/ADR-0017-identity-and-authorization-documentation-canonicalization.md`
