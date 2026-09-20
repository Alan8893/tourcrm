# ADR-0037: Event targeting, self-registration and participation policy

## Status

Accepted

## Context

The Calendar MVP already supports Event creation and Calendar filtering, and the persistence foundation already contains three separate Event relationships:

- `EventGroupTarget` — which Groups an Event is intended for;
- `EventStaffAssignment` — which Users are responsible for the Event;
- `EventParticipation` — a Person's registration/participation in an Event.

The current Event API does not yet expose the complete creation/management workflow for these relationships, and ADR-0020 explicitly deferred self-registration because the registration policy was not deterministic enough.

The real club workflow requires this distinction:

1. A club creates an Event.
2. The Event is targeted to one or more Groups.
3. One or more instructors/responsible Users are assigned.
4. A participant decides whether to attend and registers independently.
5. Registration is not attendance.
6. For paid Events, registration and payment are separate facts.

The architecture must therefore enable Group-targeted Events and self-registration without conflating audience targeting, participation, attendance or finance.

## Decision

### 1. Event targeting is explicit

An Event may target one or more Groups through `EventGroupTarget`.

`EventGroupTarget` means:

> "This Event is intended for this Group."

It does not mean that every Group member is registered.

The Event and Group must belong to the same Club. Existing canonical Event/Group ownership and authorization rules remain authoritative.

A single Event may target multiple Groups.

### 2. Event responsibility is explicit

An Event may have one or more responsible Users through `EventStaffAssignment`.

The existing invariant remains:

- multiple active staff assignments are allowed;
- at most one active assignment is primary;
- assigned Users must have active ClubMembership in the Event's Club;
- `Event.created_by` is creation metadata only and never substitutes for Event responsibility.

For the Calendar MVP, the UI must allow selecting the Event's target Group(s) and responsible instructor(s) using the existing Group and User/Instructor directory capabilities.

### 3. Group targeting and participation remain separate

Targeting a Group MUST NOT automatically create `EventParticipation`.

A Person can belong to the target Group and still choose not to participate.

This distinction is mandatory:

```
EventGroupTarget
    = intended audience

EventParticipation
    = participant's registration decision

Attendance
    = fact of actual participation
```

No implementation may infer registration from GroupMembership.

### 4. Self-registration is a first-class Event workflow

An eligible participant may register themselves for an Event.

Self-registration creates or updates the participant's `EventParticipation` record with:

```
registration_status = registered
```

The operation is idempotent for the same Event/Person pair.

The existing unique constraint on `(event_id, person_id)` remains the persistence invariant.

Self-registration does not create Attendance.

### 5. MVP registration eligibility

For the first implementation slice, self-registration is allowed only for a Person who:

1. has an active ClubMembership in the Event's Club; and
2. belongs to at least one Group targeted by the Event.

This keeps the first workflow deterministic and prevents a generic Club-wide registration mechanism from being invented without a separate product decision.

An Event with no Group target is therefore not self-registerable in the MVP.

Public/club-wide registration for Events without a matching target Group is deferred.

### 6. Registration lifecycle

The canonical MVP participant actions are:

- register;
- withdraw/cancel own registration.

The persisted status values used by the implementation are:

- `registered`;
- `cancelled`.

Existing historical/reference values such as `invited`, `waitlisted`, `declined` and `removed` may remain documented where already required, but they are not introduced as additional MVP transitions by this ADR.

Registration status does not represent attendance.

### 7. Event lifecycle and registration availability

Self-registration is available only for Events whose lifecycle status is:

- `published`.

It is not available for:

- `draft`;
- `completed`;
- `cancelled`;
- `archived`.

The existing Event lifecycle from ADR-0018 remains unchanged.

### 8. Capacity and waitlist

The first implementation does not introduce capacity or waitlist semantics.

If capacity is required, it must be introduced through a separate product/architecture decision rather than inferred from the current `EventParticipation` model.

### 9. Paid Events

Payment is explicitly separate from registration.

The Event/Participation model must not encode payment by changing `registration_status`.

The intended future relationship is:

```
Event
  └── EventParticipation
        └── financial obligation / payment
```

For this implementation slice:

- no new financial entity is introduced;
- no payment gateway is introduced;
- registration does not imply payment;
- payment does not imply attendance.

A future finance decision will define the canonical financial object and payment lifecycle.

### 10. Attendance remains separate

A registered Person is not automatically marked attended.

Attendance continues to be recorded through the existing occurrence-based attendance model.

The lifecycle is therefore:

```
targeted → eligible → registered → attended
                     └──────→ cancelled
```

with the important distinction that "registered" and "attended" are independent facts.

### 11. Guardian registration

This ADR establishes participant self-registration for the participant's own Person.

Guardian/parent registration of a child is NOT introduced by this decision.

It remains a separate policy decision because it requires explicit rules for:

- which GuardianRelationship types qualify;
- whether Guardian permission is sufficient;
- whether child consent is required;
- financial responsibility;
- cancellation.

Existing Guardian Event visibility rules remain unchanged.

### 12. Authorization

No new Event permission is introduced.

Existing canonical permissions remain authoritative:

- `event.read`;
- `event.create`;
- `event.update`;
- `event.cancel`;
- `event.manage`;
- `attendance.read`;
- `attendance.update`.

Self-registration is a self-service operation governed by the participant's identity, active ClubMembership, targeted GroupMembership and Event lifecycle.

Event management by administrators/instructors continues to require the existing Event permissions and applicable scopes.

### 13. API shape

The implementation must expose canonical operations for:

#### Event creation/targeting

Event creation/management must support:

- target Group(s);
- responsible instructor/User(s).

The implementation may use dedicated relationship endpoints if that better matches the existing service architecture, but it must not create a second parallel relationship model.

#### Self-registration

The canonical API must provide an authenticated self-service operation equivalent to:

```
POST /api/v1/events/{event_id}/participation
```

The exact request/response envelope must follow existing API conventions.

The server derives the Person from the authenticated User. The client MUST NOT submit an arbitrary `person_id` for self-registration.

#### Withdrawal

The canonical API must provide an authenticated self-service operation equivalent to:

```
DELETE /api/v1/events/{event_id}/participation
```

or the repository's canonical mutation convention if an explicit status transition endpoint is required.

The operation affects only the authenticated participant's own registration.

### 14. Calendar behavior

Calendar filtering by Group and Instructor must operate on the explicit Event relationships:

- Group filter → `EventGroupTarget`;
- Instructor filter → `EventStaffAssignment`.

Creating an Event without those relationships must not be presented in the UI as a fully targeted group schedule.

The Calendar projection itself remains unchanged by this ADR.

### 15. Data integrity

The following invariants are mandatory:

- Event and Group belong to the same Club.
- Event staff User has active ClubMembership in Event Club.
- No duplicate Event/Person participation row.
- Self-registration cannot cross Club boundaries.
- Self-registration cannot target another Person.
- Group membership does not automatically create participation.
- Registration does not automatically create attendance.
- Payment state does not replace registration state.

## Consequences

### Positive

- Club schedule can represent which Group an Event is actually for.
- Instructors can be explicitly assigned.
- Participants can make their own registration decision.
- Registration, attendance and finance remain separate domains.
- Existing persistence foundations are reused rather than duplicated.
- The model supports future paid trips without coupling EventParticipation to finance.

### Deferred

The following remain outside this MVP decision:

- public/club-wide Event registration;
- Guardian registration for children;
- participant capacity;
- waitlists;
- registration deadlines;
- automatic invitations;
- notifications;
- payment processing and financial obligations;
- approval/moderation workflow for registrations.

## Reconciliation with previous ADRs

This ADR supersedes the self-registration deferral in ADR-0020 §4 for the specific MVP policy defined here.

It does NOT replace:

- ADR-0018 — Event lifecycle;
- ADR-0020 — Event authorization vocabulary and general Event access;
- ADR-0023 — Event relationship persistence;
- ADR-0033 — EventOccurrence operational model.

The existing `EventGroupTarget`, `EventStaffAssignment` and `EventParticipation` entities remain canonical.

## Traceability

- ADR-0018 — Event lifecycle
- ADR-0020 — Event authorization and participation contract
- ADR-0023 — Event relationships and GuardianRelationship persistence
- ADR-0033 — EventOccurrence as operational instance
- docs/02-requirements/business-rules.md §10–14
- docs/05-api/events-api.md
