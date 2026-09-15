# ODR-0001 — EventOccurrence authorization relationships

## Status

Resolved — 2026-09-15 by ADR-0029 and ADR-0030 (PO accepted Option A).

## Resolution

Recurring `EventOccurrence` authorization relationships are materialized at occurrence level and are authoritative for authorization. ADR-0029 remains authoritative for occurrence-level authorization semantics.

ADR-0030 now defines the missing Series-level source model: each immutable `EventSeries` version owns `SeriesStaffAssignment`, `SeriesGroupTarget`, and `SeriesParticipant` definitions. Successor versions receive a snapshot-copy of predecessor definitions. Applicable definitions are materialized into direct occurrence-level relationship records atomically with occurrence creation.

The occurrence authorization boundary is therefore deterministic for `own_events`, `own_groups`, `self`, and `children` without dynamic inheritance, `club_id` authorization, or a nullable `event_id` bridge.

## References

- ADR-0030 — EventSeries Relationship Source Model
- ADR-0029 — EventOccurrence authorization relationships
- ADR-0028 — Event recurrence persistence and versioning
- ADR-0023 — Event relationships and GuardianRelationship persistence
- ADR-0020 — Event authorization and participation contract
