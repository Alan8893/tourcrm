# ODR-0003 — Event Conflict Semantics

- **Status:** Open — blocks TH-0084 implementation readiness
- **Created:** 2026-09-15
- **Scope:** Event scheduling conflict detection

## Context

`docs/05-api/events-api.md` already reserves `GET /api/v1/events/conflicts` and states that conflict detection should consider overlapping times, instructor assignment, room/location/resource where modeled, and participant constraints only when explicitly enabled by policy. It also permits conflict warnings to be non-blocking if business rules allow.

The current canonical domain does not provide enough deterministic policy to implement this endpoint or mutation-time conflict validation without guessing.

The conflict model must preserve the existing Event/EventOccurrence identity, half-open interval semantics, authorization model, and recurrence materialization model.

## Evidence

The business rules define Event lifecycle and explicit instructor responsibility, but do not define a conflict graph or whether overlapping responsibilities are prohibited. They also state that Event location uses canonical location fields, while no canonical resource/equipment conflict model is defined.

The Events API reserves the conflicts endpoint and gives only a high-level list of possible conflict inputs; it does not define response, authorization, mutation, lifecycle, or hard/soft validation semantics.

## Blocking PO decisions

### 1. Conflict domains

Choose which domains are conflicts in MVP:

- same instructor/User;
- same Group;
- same participant/Person;
- same physical/virtual location;
- equipment/resource;
- other explicitly modelled resource.

Do not infer a resource conflict from free-form location fields or equipment names without an accepted resource identity model.

### 2. Time overlap

Confirm that conflict means overlap of effective intervals using canonical half-open semantics:

`[start_at, end_at)`

Therefore touching boundaries do not conflict:

`A.end_at == B.start_at` → no conflict.

Comparison is against canonical UTC instants.

### 3. Mixed Event / EventOccurrence

Define whether an ordinary Event and a materialized EventOccurrence can conflict with each other when their effective intervals and conflict relationships overlap.

The expected technical model is to compare concrete scheduled records, never an RRULE directly.

### 4. Lifecycle participation

Define which statuses participate in conflict detection. At minimum decide treatment of:

- `draft`;
- `published` / `scheduled`;
- `in_progress`;
- `completed`;
- `cancelled`;
- `archived`.

Do not assume cancelled or archived objects are conflicts merely because records remain persisted.

### 5. Participant conflicts

Decide whether `EventParticipation` creates a conflict domain in MVP. If yes, define which participation states count and whether capacity/waitlist states participate.

This must not be coupled to the currently blocked self-registration policy unless explicitly decided.

### 6. API versus mutation validation

Choose MVP boundary:

- conflict detection API only;
- conflict detection service used by API and writes;
- validation during Event/Series/Occurrence mutations;
- or another explicitly justified boundary.

Do not make conflict detection a hard write blocker unless explicitly accepted.

### 7. Warning versus blocking

Choose whether conflicts are:

- informational/non-blocking warnings;
- hard validation errors;
- configurable by Club;
- domain-specific (for example instructor conflicts hard, group conflicts warning).

If blocking, define the canonical error code and stale/concurrent mutation semantics.

### 8. Authorization

For conflict queries decide whether results are restricted to conflicts among objects already visible to the caller, and how inaccessible opposing objects are handled without leaking existence.

`club_id`, role name, `created_by`, or shared membership must not become an authorization shortcut.

Authorization must precede count/list/pagination wherever result cardinality could reveal unauthorized objects.

### 9. Query boundary

Define whether `GET /api/v1/events/conflicts` is bounded by required `[from,to)` and optional narrowing actor/resource/group identifiers, and whether it may trigger recurrence materialization extension.

## Non-blocking implementation assumptions

Unless a PO decision changes them, the following existing canonical rules remain authoritative:

- concrete conflict comparison uses effective scheduled start/end times;
- interval semantics are `[start_at, end_at)`;
- timestamps are compared as UTC instants;
- recurring conflicts operate on persisted `EventOccurrence` records;
- stable Event/EventOccurrence IDs are preserved;
- no second recurrence engine;
- no new permission/scope is introduced merely for conflicts;
- existing Event authorization and IDOR/existence-hiding remain authoritative;
- conflict detection does not introduce automatic rescheduling.

## Non-goals

- frontend conflict UI;
- navigation/visual work;
- PR #75;
- notifications;
- automatic rescheduling;
- new resource/equipment domain;
- attendance/self-registration implementation.

## Resolution rule

TH-0084 remains a specification gate until the blocking decisions above are explicitly accepted by the Product Owner and reflected in the canonical ADR/API contract. No implementation Issue should be created before that point.
