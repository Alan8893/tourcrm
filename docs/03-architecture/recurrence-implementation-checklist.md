# Event recurrence implementation checklist

Implementation checklist derived from ADR-0028.

- [ ] event_series persistence and version chain
- [ ] event_occurrences persistence and deterministic materialization identity
- [ ] event_occurrence_exceptions persistence
- [ ] recurrence parser/validator for MVP RRULE vocabulary
- [ ] 180-day materialization service
- [ ] on-demand horizon extension
- [ ] idempotent concurrent materialization
- [ ] series version creation with stale-version 409 semantics
- [ ] boundary occurrence rebinding without ID replacement
- [ ] Series lifecycle
- [ ] Occurrence lifecycle
- [ ] recurrence API
- [ ] authorization/object ownership
- [ ] canonical audit actions
- [ ] integration/concurrency tests
- [ ] Alembic upgrade/downgrade verification
