# TourCRM — Recurrence Database Schema Addendum

## Status

Canonical addendum to `docs/03-architecture/database-schema.md` for the recurrence domain. Where the older recurrence section of `database-schema.md` conflicts with this document, this addendum and ADR-0028 are authoritative. Occurrence authorization relationship persistence is additionally governed by ADR-0029 and ADR-0030.

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

`EventOccurrence` is the concrete operational scheduled instance — either the materialized instance of a Series version, or (ADR-0033, amending this section) the one instance created together with an ordinary, non-recurring `Event` and kept in sync with it. Either way it is an operational entity with its own snapshot fields, never a bare bridge row that only exists to point at something else.

Fields:

- `id` — PK, UUID;
- `series_id` — FK to `event_series`, nullable — set only for the recurring case;
- `event_id` — FK to `events`, nullable — set only for the non-recurring case (ADR-0033); exactly one of `series_id`/`event_id` is set (CHECK);
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
- operational relationships attach to the occurrence;
- for the non-recurring case (ADR-0033): `UNIQUE(event_id)` — at most one occurrence per Event, ever; the occurrence's own snapshot fields (`name`/`description`/`event_type`/`starts_at`/`ends_at`/`timezone`) are kept in sync with the Event's own fields on every Event update, in the same transaction, never recreated; `status` is mapped from the Event's own status vocabulary (`published`/`in_progress`/`completed`/`cancelled` map onto the identically-named or `scheduled`-paired occurrence status; `draft`/`archived` leave the occurrence's status unchanged, since the occurrence vocabulary has no equivalent for either).

### Materialization idempotency key

The database must provide a uniqueness boundary that prevents duplicate materialization of the same logical occurrence. The implementation must use a stable recurrence identity derived from the Series version and the canonical recurrence position/slot. The exact physical key/index implementation is an implementation detail of the migration and must preserve idempotency under concurrent materializers. This key (`UNIQUE(series_id, recurrence_anchor_at)`) applies only to the recurring case; the non-recurring case's own uniqueness boundary is `UNIQUE(event_id)` instead (see above) — `recurrence_anchor_at` carries no idempotency meaning there (no RRULE position exists to protect against re-materializing).

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
           ├── EventOccurrenceException (0..1 current)
           ├── occurrence staff/responsibility relationships
           ├── occurrence group-target relationships
           └── occurrence participation relationships
```

A logical Series is represented by the `root_series_id` chain:

```text
v1 → v2 → v3
```

where each arrow is represented by `supersedes_series_id` on the newer version.

## 5. Series-level relationship source

Per ADR-0030, every immutable `EventSeries` version owns its own relationship-definition snapshot. These source records are version-owned and have direct FK to `event_series.id`.

### 5.1 `SeriesStaffAssignment`

Logical fields:

- `id` — PK, UUID;
- `event_series_id` — FK to `event_series`;
- `user_id` — FK to `users`;
- `role_in_event` — required;
- `is_primary` — required boolean;
- `valid_from` — required, timezone-aware timestamp;
- `valid_to` — nullable, timezone-aware timestamp;
- `created_at`;
- `updated_at`.

Invariants:

- `[valid_from, valid_to)` semantics; `valid_to = NULL` is open-ended;
- the User relationship is subject to the same Club-boundary/authorization integrity rules as Event staffing;
- only definitions effective at an occurrence's start instant are materialized to that occurrence;
- duplicate concurrent definitions for the same Series/User/role relationship are prohibited according to the existing staffing temporal rules;
- primary-assignment temporal invariants must preserve the existing Event staffing semantics rather than introduce a weaker unconditional uniqueness rule.

### 5.2 `SeriesGroupTarget`

Logical fields:

- `id` — PK, UUID;
- `event_series_id` — FK to `event_series`;
- `group_id` — FK to `groups`;
- `valid_from` — required, timezone-aware timestamp;
- `valid_to` — nullable, timezone-aware timestamp;
- `created_at`;
- `updated_at`.

Invariants:

- `[valid_from, valid_to)` semantics; `valid_to = NULL` is open-ended;
- the Group must belong to the same Club as the Series;
- only definitions effective at an occurrence's start instant are materialized;
- historical definitions remain immutable except through explicit lifecycle/relationship operations.

### 5.3 `SeriesParticipant`

Logical fields:

- `id` — PK, UUID;
- `event_series_id` — FK to `event_series`;
- `person_id` — FK to `people`;
- accepted participation fields/lifecycle required by the existing Event participation persistence boundary;
- `valid_from` — required, timezone-aware timestamp;
- `valid_to` — nullable, timezone-aware timestamp;
- `created_at`;
- `updated_at`.

Invariants:

- `[valid_from, valid_to)` semantics; `valid_to = NULL` is open-ended;
- Person/membership and Club integrity follows the accepted Event participation model;
- only definitions effective at an occurrence's start instant are materialized;
- materialization does not introduce self-registration or attendance semantics.

### 5.4 Version snapshot semantics

When a successor Series version is created for `this_and_following`, all predecessor relationship definitions are snapshot-copied into the successor before any successor-specific relationship change is applied. The successor then owns an independent definition set.

Existing future materialized occurrences are handled by the same explicit propagation operation that handles future Event snapshot fields:

- occurrence IDs remain stable;
- occurrence relationships are propagated from the successor source where they are not protected by an explicit occurrence-level override;
- protected occurrence relationships remain authoritative;
- past occurrence relationships are never rewritten.

## 6. Occurrence authorization relationships

Per ADR-0029, recurring occurrence authorization relationships are materialized directly against `EventOccurrence` and are authoritative for authorization of the concrete occurrence.

The implementation must provide occurrence-level persistence equivalent in semantics to the canonical Event relationships:

- **staff/responsibility:** occurrence ↔ User, preserving `role_in_event`, `is_primary`, `valid_from`, `valid_to` semantics required for `own_events`;
- **group targeting:** occurrence ↔ Group, preserving `valid_from`/`valid_to` semantics and the ADR-0022 same-Club invariant required for `own_groups`;
- **participation:** occurrence ↔ Person, preserving the accepted participation persistence boundary and required Club/membership validation for `self` and `children`.

These are occurrence-level relationship records, not fields copied into the occurrence JSON/snapshot. Their exact table names are an implementation detail, but each relationship must have a stable identity and direct FK to `event_occurrences`.

When an occurrence is materialized, applicable definitions from its governing Series version are materialized into occurrence-level relationship records in the same transaction as occurrence creation. A partially materialized occurrence must not be visible to authorization queries.

For already-materialized future occurrences, a Series-version propagation operation updates only non-protected relationship records. An explicit occurrence-level relationship mutation establishes a protected override for the affected relationship. Past occurrence relationships remain historically stable.

Materializing participation does not decide self-registration or attendance policy. Only explicitly associated participants are represented until those separate policies are specified.

Existing non-recurrence Event persistence remains governed by ADR-0019/ADR-0023. Recurring occurrences must not introduce a competing nullable `event_id` identity bridge.

## 7. Transaction and concurrency requirements

Series version creation must run in one database transaction:

1. lock the relevant current Series/root state;
2. verify the caller's base version is still current;
3. validate the new version boundary and recurrence definition;
4. create the successor version;
5. snapshot-copy all relationship definitions into the successor;
6. rebind selected already-materialized scheduled occurrences when required;
7. propagate non-protected occurrence relationship snapshots where required;
8. commit atomically.

If the base version is no longer current, the operation fails with `409 Conflict` and creates no successor.

Materialization must rely on database-level uniqueness/idempotency so concurrent workers cannot create duplicate occurrences or duplicate occurrence relationships.

## 8. Deferred implementation details

The following remain implementation details:

- exact PostgreSQL index/exclusion expression for the materialization identity;
- exact PostgreSQL constraints/indexes for the relationship tables;
- exact RRULE parser library;
- exact physical table names for occurrence relationship records;
- exact HTTP route naming for relationship CRUD/close operations, which must reconcile with the existing Event API rather than create duplicate semantics;
- notification delivery implementation;
- calendar/iCalendar projection storage;
- attendance persistence.

These must follow the accepted ADRs and the implementation Issue generated from the completed specification gate.
