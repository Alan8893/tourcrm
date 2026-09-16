# ADR-0034 — Person archiving deferred

## Status

Accepted — 2026-09-16

## Context

`Person` is the Club-neutral persistent identity record. The canonical persistence model does not contain `Person.status`, and the current People MVP does not define a complete archive lifecycle.

The previous People API contract contained `POST /api/v1/persons/{person_id}/archive`, but its semantics were explicitly left as GAP-7.

## Decision

For the current MVP, `Person` is **not archived**.

- No `Person.status` field is introduced.
- No `archived_at` or other soft-delete field is introduced.
- Physical deletion of Person is not part of the domain contract.
- The Person archive endpoint is deferred and is not an active MVP endpoint.
- Archive semantics must not be simulated by changing User, ClubMembership, GroupMembership or GuardianRelationship lifecycle state.
- Historical Person data remains available according to existing authorization rules.

## Technical debt

Person archiving remains an explicit future technical/product-debt item. Before implementation, a separate decision must define:

- persistence model;
- lifecycle and terminal/reversible behavior;
- interaction with User;
- interaction with ClubMembership and GroupMembership;
- interaction with GuardianRelationship;
- authorization and scope behavior;
- API contract and error semantics;
- audit events;
- frontend behavior and historical-data visibility.

No implementation agent may invent these semantics.

## Consequences

The People MVP can be completed without adding an artificial Person lifecycle field. Existing membership and relationship history remains intact. A future archive feature will require an explicit domain/API decision rather than retrofitting semantics into unrelated lifecycle entities.

## References

- TH-0096 — People Management — specification gate
- ADR-0005 — Identity / access-control separation
- ADR-0025 — People / Membership API decisions
- `docs/03-architecture/domain-model.md`
- `docs/03-architecture/data-model.md`
- `docs/05-api/people-api.md`
