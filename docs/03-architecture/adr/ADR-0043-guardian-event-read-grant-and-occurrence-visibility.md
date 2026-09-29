# ADR-0043 — Guardian Event Read Grant and Occurrence Visibility

- **Status:** Accepted
- **Date:** 2026-09-29
- **Supersedes:** none
- **Related:** ADR-0042, ADR-0029, ADR-0030, `role-permission-scope-matrix.md`, `roles-and-permissions.md`, Issue #213

## 1. Decision

Guardian receives the canonical `event.read` permission with scope `children`.

This grant is required for the already accepted Guardian Events policy. The absence of the seeded grant is an implementation gap, not an intentional restriction of the Guardian role.

For a Guardian, an event is readable when at least one accessible child satisfies either of these paths:

1. the child has an active `GroupMembership` in a Group targeted by the Event; or
2. the child has a direct `EventParticipation` for the Event.

For a Guardian with multiple accessible children, the result is the **UNION** of the events reachable through any child.

Direct event access, event list access and calendar projection use the same authorization policy. Calendar/date/filter parameters may only narrow the already authorized event set; they must never expand it.

## 2. Recurring occurrences

The same semantic rule applies to recurring `EventOccurrence` resources.

For `event.read(children)`, an occurrence is readable when an accessible child satisfies either:

1. the child has an active GroupMembership in a Group targeted by the occurrence/series; or
2. the child has a direct occurrence/event participation relationship supported by the canonical occurrence model.

The group path is mandatory. A Guardian must not be required to wait for direct participation/registration in order to see a recurring group activity.

The authorization implementation must use the same relationship semantics for:

- `/events`;
- `/events/calendar`;
- `/events/{event_id}`;
- `/events/occurrences/{occurrence_id}`.

## 3. Visibility is not registration

Read access does not create, imply or modify `EventParticipation`.

A Guardian seeing a group event means only that the event is relevant information for the child's group. It does not mean that the child is registered for the event.

This distinction is intentional: a Guardian must be able to see proposed/available activities and discuss participation with the child. Event visibility must therefore not depend on the child already being registered for the specific event.

## 4. Relationship and lifecycle requirements

The authorization path must continue to require the existing relationship/lifecycle checks defined by ADR-0042, including:

- active GuardianRelationship;
- valid relationship interval;
- active ClubMembership context where required by the existing object policy;
- active GroupMembership for the group path;
- valid EventGroupTarget / occurrence group target interval.

A `club_id` match alone is never sufficient for `children` access.

## 5. Unresolved policy questions

The following are intentionally **not decided by this ADR** and must not be inferred by implementation:

- whether `EventParticipation.registration_status = cancelled` still counts as a direct child-event relationship;
- whether `/events` and `/events/{id}` should apply additional status visibility restrictions for `children` beyond the existing Event status policy;
- whether archived Groups should participate in the group-target authorization path;
- whether recurring materialization horizon needs a separate product rule for Guardian visibility.

These require a separate PO decision if implementation needs behavior different from the existing domain policy.

## 6. Implementation boundary

This ADR defines policy only. It does not prescribe a particular repository/service implementation.

The implementation must:

1. seed `guardian → event.read → children` in the canonical role/permission data;
2. extend occurrence authorization with the group-membership → occurrence-group-target path;
3. preserve fail-closed behavior when the grant or relationship is absent;
4. add regression coverage for group path, direct participation path, multi-child UNION, occurrence group path, and equivalence of list/calendar/detail authorization.

No new permission or scope is introduced.