# ODR-0001 — EventOccurrence authorization relationships

## Status
Resolved — ADR-0029 accepted Option A (materialize occurrence relationships).

## Resolution

Recurring `EventOccurrence` authorization relationships are materialized at occurrence level. Staff/responsibility, group targeting and explicit participation relationships are attached directly to the concrete occurrence and are the authoritative relationship boundary for calendar authorization.

No nullable `event_id` bridge is introduced. No new permission or scope is introduced.

## Consequence

TH-0079 is no longer blocked by the relationship-model ambiguity. The recurrence schema/module/API documentation and the internal calendar specification must use ADR-0029 as the canonical relationship strategy.

## References

- ADR-0013 — Scope Vocabulary;
- ADR-0020 — Event Authorization and Participation;
- ADR-0022 — Cross-Club Ownership Integrity;
- ADR-0023 — Event Relationships and GuardianRelationship Persistence;
- ADR-0028 — Event Recurrence Persistence and Series Versioning;
- ADR-0029 — EventOccurrence authorization relationships.
