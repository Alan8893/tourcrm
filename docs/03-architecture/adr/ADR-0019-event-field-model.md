# ADR-0019: Event canonical field model

## Status
Accepted

## Context
Before implementing the Event persistence foundation (Issue #36), the Event
field list was specified inconsistently across canonical documents:

- `docs/03-architecture/data-model.md` §7, `docs/03-architecture/domain-model.md`
  §10 and `docs/03-architecture/database-schema.md` §9 (`events`) described a
  single `location` field.
- `docs/04-modules/events-and-schedule.md` §5 described a decomposed location
  model instead: `location_type`, `location_name`, `location_address`,
  `location_latitude`, `location_longitude`. No document defined `location`
  as a composite/JSON value made of these five fields, so this was a genuine
  structural conflict, not a wording difference.
- `docs/03-architecture/data-model.md` §7 and `docs/03-architecture/database-schema.md`
  §9 listed `updated_by` as an Event field; `docs/03-architecture/domain-model.md`
  §10 and `docs/04-modules/events-and-schedule.md` §5 omitted it.

Event status/transitions are governed separately by ADR-0018, which this ADR
does not change or extend.

## Decision
The canonical Event field list is:

- `id` — primary key, UUID (ADR-0010).
- `club_id` — FK to `Club`, required.
- `event_type` — required; value from the documented event type catalog
  (`docs/04-modules/events-and-schedule.md` §3).
- `title` — required.
- `description` — nullable.
- `start_at` — required.
- `end_at` — required.
- `timezone` — required; explicit IANA timezone identifier.
- `location_type` — part of the location model (see below).
- `location_name` — part of the location model (see below).
- `location_address` — nullable (`events-and-schedule.md` §5: "координаты и
  адрес опциональны").
- `location_latitude` — nullable (same source); must be consistent with
  `location_longitude` when coordinates are supplied.
- `location_longitude` — nullable (same source); must be consistent with
  `location_latitude` when coordinates are supplied.
- `status` — required; canonical lifecycle values and transitions per
  ADR-0018.
- `cancellation_reason` — nullable; required only when `status = cancelled`
  (ADR-0018).
- `created_by` — FK to `User`, nullable per the general cross-cutting-columns
  convention (`database-schema.md` §4).
- `updated_by` — FK to `User`, nullable per the same convention.
- `created_at` — required.
- `updated_at` — required.

### Location model
There is no separate `location` field. `location_type`, `location_name`,
`location_address`, `location_latitude` and `location_longitude` are the
sole canonical representation of an Event's location. No canonical document
may reintroduce a single `location` field as an alternative model alongside
this one.

Canonical documentation does not otherwise specify whether `location_type`
and `location_name` are individually required or nullable; this ADR leaves
that unspecified rather than inventing a rule, and it is not decided by this
ADR.

### `updated_by`
`updated_by` is a canonical Event field. Every canonical document that lists
Event's fields must include it, consistent with `data-model.md` §7 and
`database-schema.md` §9.

### Relations
`club_id` references `Club` (`clubs.id`); this matches `data-model.md` §7's
`Club 1:N Event` relationship. `created_by` and `updated_by` reference the
`User` identity entity (`users.id`), consistent with the actor-references-User
pattern already used elsewhere in the canonical data model (for example
`AuditLog.actor_user_id`, `data-model.md` §19) and with ADR-0010's identifier
strategy.

## Consequences
- `docs/03-architecture/data-model.md`, `docs/03-architecture/domain-model.md`,
  `docs/03-architecture/database-schema.md` and
  `docs/04-modules/events-and-schedule.md` all describe the same Event field
  list following this ADR.
- Any future canonical document describing Event's fields must use this list;
  a single `location` field must not reappear.
- This ADR does not change Event status/lifecycle, which remains governed
  exclusively by ADR-0018.
- This ADR does not define new business rules, constraints, or validation
  beyond restating what is already unambiguous in the referenced documents.

## Traceability

- `docs/03-architecture/data-model.md` §7
- `docs/03-architecture/domain-model.md` §10
- `docs/03-architecture/database-schema.md` §9
- `docs/04-modules/events-and-schedule.md` §5
- `docs/03-architecture/adr/ADR-0010-primary-key-strategy.md`
- `docs/03-architecture/adr/ADR-0018-event-lifecycle.md` (status/transitions only, unaffected by this ADR)
