# ODR-0003 — Event Conflict Semantics

- **Status:** Resolved — 2026-09-15 by ADR-0031
- **Scope:** Event scheduling conflict detection

## Resolution

The Product Owner accepted the following MVP conflict policy, now authoritative in **ADR-0031 — Event Conflict Detection**:

- conflict domains: instructor/staff User, Group, and participant Person;
- room/location/equipment/generic resources are outside MVP;
- conflict means overlap of effective concrete intervals using canonical half-open `[start_at, end_at)` semantics and UTC instants;
- ordinary Event and EventOccurrence may conflict with each other; recurring conflict detection evaluates persisted materialized occurrences rather than RRULE directly;
- Event operational conflicts include `published` and `in_progress`; draft/completed/cancelled/archived do not participate;
- EventOccurrence operational conflicts include `scheduled` and `in_progress`; completed/cancelled do not participate;
- participant conflicts are based on explicit EventParticipation, not GroupMembership or role;
- conflicts are informational/non-blocking warnings in MVP;
- no Club-configurable severity policy in MVP;
- `GET /api/v1/events/conflicts` is the canonical query boundary;
- conflict detection is a reusable service capability but does not hard-block Event/Series/Occurrence mutations in MVP;
- existing Event authorization/object policy remains the security boundary; inaccessible opposing objects must not leak through IDs, titles, timing, counts, pagination, or other metadata;
- query is bounded by `[from,to)` and may use narrowing actor/group identifiers;
- recurrence materialization uses the existing ADR-0015/ADR-0028 mechanism and does not introduce a second recurrence engine;
- no new permission or scope is introduced;
- no automatic rescheduling is performed.

See ADR-0031 for the complete canonical decision and consequences.
