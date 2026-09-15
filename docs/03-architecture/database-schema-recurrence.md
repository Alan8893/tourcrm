# TourCRM — Recurrence Database Schema Addendum

## Status

Canonical addendum to `docs/03-architecture/database-schema.md` for the recurrence domain. Where the older recurrence section of `database-schema.md` conflicts with this document, this addendum and ADR-0028 are authoritative.

## 1. `event_series`

`EventSeries` is a versioned recurrence definition. One logical recurring schedule consists of an ordered chain of versions.

Fields:

- `id` — PK, UUID;
- `root_series_id` — FK to the first version in the logical series;
- `supersedes_series_id` — nullable self-FK to the immediately preceding version;
- `version` — positive integer;
- `club_id` — FK to `clubs`;
- `name` — required;
- `description` — nullable;
- `event_type` — required;
- `series_start_at` — required, timezone-aware timestamp;
- `series_end_at` — nullable, timezone-aware timestamp;
- `occurrence_limit` — nullable positive integer;
- `duration_minutes` — required positive integer; duration of each occurrence in this Series version;
- `recurrence_rule` — required canonical RRULE representation;
- `timezone` — required IANA timezone;
- `status` — required;
- `created_by` — FK to `users`;
- `updated_by` — FK to `users`;
- `created_at`;
- `updated_at`.

Constraints and invariants:

- `root_series_id` identifies the logical series across all versions;
- `version` is unique within `root_series_id`;
- `supersedes_series_id` points only to the immediately preceding version of the same root series;
- the first version has `version = 1` and `supersedes_series_id IS NULL`;
- every later version has exactly one `supersedes_series_id`;
- there is at most one successor for any series version;
- there is no stored `is_current` flag;
- the current version is the terminal version in the chain;
- version creation is serialized transactionally and must reject a stale base version with `409 Conflict` at the API boundary;
- historical versions are retained;
- `active ↔ paused`, `active → cancelled`, `cancelled → archived`;
- `paused` stops creation of new occurrences but does not alter already materialized future occurrences;
- `cancelled` permanently stops generation but does not automatically cancel or delete existing future occurrences;
- `archived` is terminal.

`UNTIL` is not stored as a second canonical termination field in `recurrence_rule`; an incoming RRULE `UNTIL` is normalized into `series_end_at`.

`duration_minutes` is part of the Series version snapshot. A duration change affecting future occurrences is represented by the applicable Series update/versioning operation; no undocumented default duration exists.

## 2. `event_occurrences`

`EventOccurrence` is the concrete materialized scheduled instance of a Series version. It is an operational entity and is not represented by a separate required `Event` row.

Fields:

- `id` — PK, UUID;
- `series_id` — FK to `event_series`;
- `club_id` — FK to `clubs`;
- `name` — snapshot;
- `description` — snapshot, nullable;
- `event_type` — snapshot;
- `starts_at` — required, timezone-aware timestamp;
- `ends_at` — required, timezone-aware timestamp;
- `timezone` — required IANA timezone;
- `status` — required;
- `cancellation_reason` — nullable;
- `created_by` — FK to `users`;
- `updated_by` — FK to `users`;
- `created_at`;
- `updated_at`.

Constraints and invariants:

- `ends_at > starts_at`;
- for a normally materialized occurrence, `ends_at = starts_at + duration_minutes` from its governing Series version;
- lifecycle is exactly `scheduled → in_progress → completed` or `scheduled → cancelled`;
- `completed` and `cancelled` are terminal;
- cancellation requires a reason;
- reschedule is not a status;
- occurrence ID is stable for its entire lifetime;
- an occurrence may be rebound from one Series version to the next only for the accepted `this and following` boundary operation; it is never recreated merely because a Series version changes;
- cancelled occurrences cannot be used as the boundary for a new Series version;
- operational relationships attach to the occurrence.

### Materialization idempotency key

The database must provide a uniqueness boundary that prevents duplicate materialization of the same logical occurrence. The implementation must use a stable recurrence identity derived from the Series version and the canonical recurrence position/slot. The exact physical key/index implementation is an implementation detail of the migration and must preserve idempotency under concurrent materializers.

## 3. `event_occurrence_exceptions`

Stores the current exception/override state for one occurrence. Historical actions are preserved by the immutable Audit log rather than by accumulating multiple current exception rows.

Fields:

- `id` — PK, UUID;
- `occurrence_id` — FK to `event_occurrences`, UNIQUE;
- `exception_type` — `rescheduled | cancelled`;
- `original_start_at` — required, timezone-aware timestamp;
- `effective_start_at` — nullable, timezone-aware timestamp;
- `effective_end_at` — nullable, timezone-aware timestamp;
- `cancellation_reason` — nullable;
- `overrides` — JSONB, nullable/empty when no field override is required;
- `created_by` — FK to `users`;
- `created_at`;
- `updated_at`.

Constraints and invariants:

- one current exception per occurrence;
- `rescheduled` preserves the original occurrence identity and stores the effective schedule;
- `cancelled` requires `cancellation_reason`;
- arbitrary JSON keys are not accepted by the application layer;
- JSONB values are validated against the canonical Event field/type rules;
- JSONB cannot bypass ordinary domain validation;
- an exception never deletes the occurrence;
- a reschedule never creates a replacement occurrence.

## 4. Relationships

```text
Club
 └── EventSeries (version 1..N)
      └── EventOccurrence (materialized)
           └── EventOccurrenceException (0..1 current)
```

A logical Series is represented by the `root_series_id` chain:

```text
v1 → v2 → v3
```

where each arrow is represented by `supersedes_series_id` on the newer version.

## 5. Participation and operational ownership

`EventParticipation`, staffing, group targeting and future attendance are occurrence-level operational relationships. They must not depend on a nullable `event_id` bridge for the recurrence model.

Existing non-recurrence Event persistence remains governed by ADR-0019/ADR-0023. The recurrence implementation must not silently introduce a second competing identity for the same occurrence.

## 6. Transaction and concurrency requirements

Series version creation must run in one database transaction:

1. lock the relevant current Series/root state;
2. verify the caller's base version is still current;
3. validate the new version boundary and recurrence definition;
4. create the successor version;
5. rebind the selected already-materialized scheduled occurrence when required;
6. commit atomically.

If the base version is no longer current, the operation fails with `409 Conflict` and creates no successor.

Materialization must rely on database-level uniqueness/idempotency so concurrent workers cannot create duplicate occurrences.

## 7. Deferred implementation details

The following are deliberately not invented by this addendum:

- exact PostgreSQL index/exclusion expression for the materialization identity;
- exact RRULE parser library;
- notification delivery implementation;
- calendar/iCalendar projection storage;
- attendance persistence.

These must follow the accepted ADRs and the implementation Issue generated from the completed specification gate.
