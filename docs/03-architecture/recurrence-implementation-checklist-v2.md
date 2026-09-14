# Event recurrence implementation checklist

Derived from ADR-0028.

- [ ] event_series persistence/version chain
- [ ] event_occurrences persistence/idempotency identity
- [ ] event_occurrence_exceptions persistence
- [ ] RRULE parser/validator
- [ ] materialization horizon and on-demand extension
- [ ] concurrent/idempotent materialization
- [ ] series version creation + stale 409
- [ ] boundary occurrence rebinding
- [ ] Series lifecycle
- [ ] Occurrence lifecycle
- [ ] recurrence API
- [ ] authorization and Club ownership
- [ ] audit actions
- [ ] integration/concurrency tests
- [ ] Alembic upgrade/downgrade verification
