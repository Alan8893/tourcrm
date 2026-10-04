# ADR-0018: Event lifecycle

## Status
Accepted

## Context
The Event lifecycle was specified inconsistently: `business-rules.md` used `planned`, while the detailed Events module used `in_progress`. Event status is part of the domain and API contract and must have one canonical enum and transition graph.

## Decision
TourCRM uses the following canonical Event statuses:

- `draft`
- `published`
- `in_progress`
- `completed`
- `cancelled`
- `archived`

Allowed transitions:

- `draft -> published`
- `published -> in_progress`
- `published -> cancelled`
- `in_progress -> completed`
- `in_progress -> cancelled`
- `completed -> archived`
- `cancelled -> archived`

No other status transition is allowed by the baseline API contract. Cancellation requires a reason. Published, completed and cancelled events are retained; archiving does not delete history.

`planned` is not a separate Event status. A planned but not yet published event remains `draft`; publication is the boundary at which it becomes available to users.

Status values are lowercase canonical API/domain values. Documentation may render them uppercase in diagrams only if it is explicit that the canonical values are lowercase.

### Time-based lifecycle synchronization

The Event lifecycle is synchronized with the canonical `start_at` and `end_at` timestamps for the two forward operational transitions:

- `published -> in_progress` when `start_at` is reached;
- `in_progress -> completed` when `end_at` is reached.

This synchronization does not publish `draft` events automatically. A `cancelled` event does not become `completed` merely because its `end_at` has passed.

A manual transition to `completed` may occur before `end_at` when the event actually finishes earlier. If the scheduled transition moment is missed because the service is unavailable, the next lifecycle reconciliation must bring the Event to the applicable time-based state; the system must not require the service to be running at the exact timestamp.

The timestamps remain schedule data and the status remains the domain state. Time alone does not override an explicitly cancelled or archived Event.

## Consequences
The business-rules, Events module and Events API must use one lifecycle. Event implementation can enforce a deterministic transition graph without inventing additional states. Recurring occurrence materialization remains governed by ADR-0015.

## Traceability

- `docs/02-requirements/business-rules.md` §10.1
- `docs/04-modules/events-and-schedule.md` §4
- `docs/05-api/events-api.md` status transition contract
- `docs/03-architecture/adr/ADR-0015-event-occurrence-materialization.md`
