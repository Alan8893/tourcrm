# ADR-0042 — Guardian Event Visibility Policy

- **Status:** Accepted
- **Date:** 2026-09-29
- **Decision owner:** Product Owner
- **Related:** `event.read(children)`, Guardian Home, TH-0172 / #213

## 1. Context

Guardian must be able to see events relevant to their children before a child is registered for a particular event.

The product purpose is not only to show already-confirmed participation. Guardian must be able to see the opportunities available to a child, discuss them with the child, and decide whether the child should participate.

Therefore visibility of a group event must not depend on `EventParticipation` for the child.

The existing authorization model already defines `event.read(children)`. This ADR defines the object relationship semantics of that scope for Guardian.

## 2. Decision

For a Guardian, `event.read(children)` authorizes an event when the event is relevant to at least one child for whom the Guardian has an active, valid GuardianRelationship.

A group-targeted event is relevant to a Guardian when at least one of the Guardian's children has an active membership in at least one target group of the event.

Direct event participation is an additional relevance path: if a child is directly registered/participating in an event, that event is relevant to the Guardian even when group membership is not the relationship that exposes it.

The authorization set for a Guardian with multiple children is the union of the authorized event sets for all of those children.

## 3. Group event visibility

The canonical relationship is:

```text
Guardian
  ↓ active GuardianRelationship
Child
  ↓ active GroupMembership
Group
  ↓ EventGroupTarget
Event
```

If the child is an active member of a group targeted by the event, the Guardian may read the event through `event.read(children)`.

The child does **not** need to be registered/participating in the specific event for the Guardian to see it.

This applies to group events such as trainings, lessons, hikes, competitions and other events targeted to the child's group, subject to the existing Event status/calendar visibility rules.

## 4. Direct child-event relationship

A Guardian may also read an event when the child has a direct EventParticipation relationship with that event.

This does not replace group-based visibility. Both relationship paths are valid inputs to the `children` scope.

## 5. Multiple children

For a Guardian with children A, B and C:

```text
allowed_events = events(A) ∪ events(B) ∪ events(C)
```

No active-child selector is introduced by this ADR.

The same rule applies to calendar projection and direct event access.

## 6. Visibility is not registration

Reading an event through `event.read(children)` does not imply that the child is registered for the event.

In particular:

- an event may be visible to a Guardian because it targets the child's group;
- the child may have no `EventParticipation` for that event;
- visibility must not create or imply participation.

This distinction is intentional: Guardian must be able to see opportunities available to the child before participation is decided.

## 7. Calendar and direct-event access

The same authorization policy applies to all read surfaces:

```text
canonical event authorization
        ↓
allowed event set
        ↓
calendar projection / event list / event details
```

Calendar filtering, date ranges and other query parameters may only narrow the already-authorized set. They must never expand it.

A Guardian opening a direct event URL receives the same authorization decision as a Guardian receiving that event through the calendar/list projection.

## 8. Non-examples

Guardian does not receive access merely because:

- the event belongs to a group in the same club;
- the Guardian knows the child's or event's ID;
- another child is participating in the event;
- the event is visible to another Guardian;
- the event is club-wide but has no applicable child relationship.

A club-wide event without a qualifying child relationship is not automatically included in `event.read(children)`.

## 9. Existing policy remains authoritative

This ADR does not introduce a new permission or scope.

It defines the object relationship semantics of the existing `event.read(children)` scope. Existing Event status visibility, club boundaries, occurrence authorization and other canonical Event policies continue to apply.

Frontend code must not infer Guardian → Child → Group → Event relationships itself. The backend remains the source of truth for authorization and returns only the authorized result set.
