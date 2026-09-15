# ADR-0031 — Event Conflict Detection

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision owner:** Product Owner
- **Scope:** Event/EventOccurrence scheduling conflict detection

## 1. Decision

TourCRM treats a scheduling conflict as an overlap between two concrete scheduled Event/EventOccurrence intervals where the same explicitly assigned or targeted actor/domain resource would be required concurrently.

Canonical interval semantics are half-open `[start_at, end_at)`. Therefore `A.end_at == B.start_at` is not a conflict. Comparisons use canonical UTC instants.

Conflict detection operates on concrete scheduled records and their effective start/end values. Recurrence rules are never evaluated by a second conflict-specific recurrence engine.

## 2. Conflict domains

MVP conflict domains are:

1. **Instructor/staff/User** — two concrete events conflict when the same User has an applicable explicit event staffing/responsibility relationship in both overlapping events.
2. **Group** — two concrete events conflict when the same Group is explicitly targeted by both events.
3. **Participant/Person** — two concrete events conflict when the same Person has an applicable `EventParticipation` relationship in both events.

The following are outside MVP:

- room/location conflicts;
- equipment conflicts;
- generic resource conflicts.

Free-form location fields, equipment names, Club ID, ClubMembership, role names, and `created_by` do not establish a conflict domain.

## 3. Event and occurrence interoperability

Ordinary `Event` and materialized `EventOccurrence` are both concrete schedulable records for conflict purposes. An ordinary Event may conflict with an EventOccurrence, and two occurrences may conflict with each other, when their effective intervals and conflict-domain relationships overlap.

Recurring EventSeries itself is not compared directly against another series or Event. Conflict detection evaluates persisted occurrences within the requested time boundary.

Occurrence rescheduling/exceptions are evaluated using the occurrence's effective interval after the applicable exception/override.

Stable Event/EventOccurrence identities are preserved.

## 4. Lifecycle participation

Operational conflict detection considers only records that can represent an active scheduled commitment:

### Event

- `published` — participates;
- `in_progress` — participates;
- `draft` — does not participate in operational conflicts;
- `completed` — does not participate;
- `cancelled` — does not participate;
- `archived` — does not participate.

### EventOccurrence

- `scheduled` — participates;
- `in_progress` — participates;
- `completed` — does not participate;
- `cancelled` — does not participate.

Persisted historical records remain available for history and audit but do not block or constitute an operational scheduling conflict.

A draft may be inspected by a conflict-detection query where explicitly requested by the future API contract, but draft status alone never makes a conflict operational or blocks a mutation.

## 5. Participant semantics

`EventParticipation` is a conflict relationship only when the Person is actually associated with the concrete event occurrence under the existing participation model.

GroupMembership alone does not create a participant conflict.

The conflict detector must not infer participation from role, group membership, GuardianRelationship, or self-registration availability.

The exact participation statuses that represent an applicable participation relationship must follow the canonical EventParticipation lifecycle when the conflict service is implemented; this ADR does not invent a new participation state machine.

## 6. Conflict severity

MVP conflicts are **informational/non-blocking warnings**.

Conflict detection does not reject Event, EventSeries, or EventOccurrence writes merely because a conflict exists.

There is no Club-configurable conflict severity policy in MVP and no domain-specific hard/soft severity matrix.

Automatic rescheduling is not performed.

## 7. API boundary

MVP provides a conflict query API:

`GET /api/v1/events/conflicts`

The conflict detector is a reusable application/service-level capability and may be invoked by read APIs and future mutation flows, but the current MVP decision does not make conflict detection a hard mutation validator.

No new permission or scope is introduced. Conflict queries use the existing Event read permission and existing scope/object authorization model.

## 8. Authorization and information disclosure

A caller may receive only conflicts whose participating concrete objects are within the caller's existing Event visibility/object policy.

An inaccessible opposing object must not be exposed by ID, title, type, timing, count, pagination metadata, or another response property that reveals its existence through the conflict endpoint.

Authorization is applied before conflict result counting, pagination, and serialization.

The conflict detector must not use `club_id` as a substitute for object authorization.

Existing scope semantics remain authoritative:

- `all` — according to existing allowed Club/object policy;
- `own_events` / `assigned_events` — explicit event staffing/responsibility relationship;
- `own_groups` — applicable explicit Group targeting relationship and existing Group authorization;
- `self` — applicable participation relationship;
- `children` — applicable participation relationship plus existing GuardianRelationship/membership authorization;
- `none` — no results.

## 9. Query boundary and recurrence materialization

Conflict queries are bounded by required `[from,to)` timestamps and use canonical UTC instants.

Optional actor/group identifiers may narrow the already-authorized candidate set; they never expand authorization.

Recurring conflict detection uses persisted materialized `EventOccurrence` records. If the requested range exceeds the current materialization horizon, the existing recurrence materialization mechanism may extend materialization according to ADR-0015/ADR-0028. Conflict detection does not implement recurrence generation itself.

Materialization and conflict reads must remain concurrency-safe and idempotent according to the existing recurrence contract.

## 10. Conflict identity and reason

Each returned conflict is a derived, deterministic relationship between two concrete schedulable objects and one conflict domain. It is not a new persisted business entity in MVP.

A stable response identity must therefore be derived from the canonical unordered pair of object identities plus the conflict domain, rather than from a generated database row.

The API implementation must expose:

- first object identity and type;
- second object identity and type;
- conflict domain/reason code;
- effective overlapping interval;
- canonical API envelope and pagination metadata.

The pair ordering must be deterministic, and results must have deterministic ordering. Exact field names and common envelope formatting must follow `api-conventions.md` when the implementation contract is finalized.

## 11. No new resource model

Conflict detection does not create Room, Resource, EquipmentReservation, or generic Resource entities. If such a domain is introduced later, its conflict semantics require a separate specification/ADR.

## 12. Consequences

Positive:

- one conflict model works for ordinary and recurring calendar items;
- no duplicate recurrence engine;
- existing authorization remains the security boundary;
- conflicts can be surfaced without changing write semantics;
- future hard-block policies can reuse the same detector without redefining conflict identity.

Trade-offs:

- the MVP may allow an instructor, group, or participant to be scheduled in overlapping events;
- resource/location conflicts remain undetected until a canonical resource model exists;
- participant conflict applicability depends on the existing participation lifecycle and must not invent new statuses.

## 13. Related decisions

- ADR-0015 — Event Occurrence Materialization
- ADR-0018 — Event Lifecycle
- ADR-0020 — Event Authorization and Participation
- ADR-0028 — Event Recurrence Persistence and Versioning
- ADR-0029 — Event Occurrence Authorization Relationships
- ADR-0030 — Event Series Relationship Source
