# ADR-0013: Canonical scope vocabulary

## Status
Accepted

## Context

The documentation used `own_events`, `assigned_events` and `own_records` in addition to the core scopes. This creates ambiguity for the authorization engine.

## Decision

The canonical authorization scope vocabulary is:

- `all` — all resources within the authorized club/system boundary;
- `self` — resources belonging to the authenticated person's own identity;
- `children` — resources belonging to persons connected through an active verified guardian relationship;
- `own_groups` — resources belonging to groups for which the actor has an authorized relationship;
- `own_events` — resources for events where the actor is explicitly assigned as instructor/leader/authorized staff;
- `none` — no resource-level access.

`assigned_events` is not a separate scope. It is normalized to `own_events`.

`own_records` is not a separate scope. Resource-specific ownership is expressed by `self`, `children`, or a domain-specific authorization predicate; a new generic scope must not be introduced without an ADR.

## Rules

1. Permissions and scopes are separate concepts.
2. Scope evaluation is performed by the backend authorization layer.
3. A module may define additional resource predicates, but must not invent a new global scope name without updating this ADR.
4. Frontend visibility never grants authorization.
5. Scope resolution is deny-by-default.

## Consequences

All API and domain specifications must use this vocabulary. Existing references to `assigned_events` must be treated as aliases of `own_events`; references to `own_records` must be rewritten using the applicable canonical scope/predicate.
