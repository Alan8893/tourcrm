# ADR-0015: Event occurrence materialization strategy

## Status
Accepted

## Context

Recurring events require stable occurrence identities because attendance, exceptions, cancellations and operational changes attach to a concrete occurrence. Fully dynamic recurrence expansion makes historical references and overrides unnecessarily fragile.

## Decision

TourCRM uses bounded materialization of `EventOccurrence` records.

1. A series is the canonical recurrence definition.
2. Occurrences are materialized for a configurable planning horizon.
3. The default planning horizon is 180 days forward.
4. The system may extend the horizon automatically when users request dates beyond the current materialized range.
5. Materialized occurrences receive stable IDs and are never recreated merely because the series is edited.
6. An occurrence that has operational history (attendance, participation, cancellation, manual override or audit-relevant mutation) must remain persisted.
7. Changes to a future occurrence are represented as occurrence-level exceptions/overrides and must not rewrite historical occurrences.
8. Changes to the series affect only eligible future occurrences that have no protected override/history.
9. Occurrences are generated idempotently; repeated materialization must not create duplicates.

## Consequences

Attendance and historical reporting always have stable occurrence references. The application avoids materializing an unbounded calendar while retaining deterministic behavior for the active planning window.

The exact job implementation and locking strategy are implementation details and must preserve these invariants.
