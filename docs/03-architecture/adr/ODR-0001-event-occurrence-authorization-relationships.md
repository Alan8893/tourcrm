# ODR-0001 — EventOccurrence authorization relationships

## Status
Resolved — 2026-09-15 by ADR-0029 (Option A).

## Resolution

Recurring EventOccurrence authorization relationships are materialized at occurrence level. ADR-0029 is authoritative.

The general internal calendar specification gate is therefore unblocked. No implementation may introduce an alternative inheritance model, nullable `event_id` bridge, or new permission/scope.

## References

- ADR-0029 — EventOccurrence authorization relationships
- ADR-0028 — Event recurrence persistence and versioning
- ADR-0023 — Event relationships and GuardianRelationship persistence
- ADR-0020 — Event authorization and participation contract
