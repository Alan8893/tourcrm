# TourCRM — ADR-0028 — Event Recurrence Persistence and Series Versioning

- Status: Accepted
- Date: 2026-09-14

## Context

TourCRM needs deterministic recurring Events with materialized `EventOccurrence` records. The recurrence model must preserve operational history, keep occurrence identifiers stable, support exceptions, and allow a "this and following" change without rewriting the past.

Existing canonical decisions:

- ADR-0015 — Event Occurrence Materialization;
- ADR-0018 — Event Lifecycle;
- ADR-0019 — Event Field Model;
- ADR-0024 — Audit Infrastructure.

These decisions require an explicit physical persistence model and concurrency semantics before implementation.

## Decision

### 1. Logical model

A recurring schedule is represented by a logical `EventSeries` and one or more immutable versions of that logical series.

### 2. EventSeries version chain

Each physical `EventSeries` version contains:

- `id` — UUID primary key;
- `root_series_id` — UUID identifying the logical series;
- `version` — positive integer, monotonically increasing within `root_series_id`;
- `supersedes_series_id` — nullable FK to the immediately preceding version;
- `club_id`;
- recurrence and event snapshot fields;
- lifecycle/status fields;
- audit timestamps and actor fields where applicable.

For version 1: `root_series_id = id`, `version = 1`, `supersedes_series_id = NULL`.

For later versions, `root_series_id` is inherited from the root, `version = previous_version + 1`, and `supersedes_series_id = previous_version.id`.

The current version is the terminal version in the chain: it is not superseded by another version. No stored `is_current` flag is used.

A database uniqueness constraint must prevent two versions with the same `(root_series_id, version)`. A database-level integrity mechanism must prevent more than one successor from being created for the same predecessor.

### 3. Version boundaries

A "this and following" change starts a new EventSeries version at the selected future `scheduled` occurrence.

- The previous version remains stored for history.
- Past occurrences remain associated with the historical version and are immutable.
- The selected occurrence keeps its existing `id` and is rebound to the new version if already materialized.
- Following occurrences belong to the new version.
- No new occurrence is created solely because of the rebinding.
- A cancelled occurrence cannot be selected as the boundary; the caller must select the next scheduled occurrence.

Version ranges must be non-overlapping in effective scheduling responsibility. The implementation must reject a boundary that would create an invalid or ambiguous version chain.

### 4. EventOccurrence

Each occurrence contains:

- `id` — stable UUID primary key;
- `series_id` — FK to the EventSeries version that currently governs it;
- `club_id`;
- snapshot fields required for operational use;
- effective start/end timestamps;
- timezone;
- lifecycle status;
- cancellation reason where applicable;
- audit timestamps and actor fields where applicable.

A uniqueness rule must make materialization idempotent for a series version and its canonical recurrence occurrence key. The exact generated recurrence key is an implementation detail, but it must be deterministic and persisted or derivable without relying on wall-clock execution time.

### 5. EventOccurrenceException

An occurrence may have at most one current exception record.

Fields:

- `id`;
- `occurrence_id` — unique FK;
- `exception_type` — `rescheduled | cancelled`;
- `original_start_at`;
- `effective_start_at` nullable;
- `effective_end_at` nullable;
- `overrides` JSONB;
- `cancellation_reason` nullable;
- `created_by`;
- `created_at`;
- `updated_at`.

`overrides` contains only an explicitly allow-listed subset of Event fields. The backend validates names, types and domain rules exactly as for ordinary Event updates. JSONB must not become an escape hatch around domain validation.

`rescheduled` keeps the occurrence status `scheduled`; rescheduling is not a lifecycle transition.

`cancelled` requires a cancellation reason and makes the occurrence terminal according to ADR-0018 occurrence lifecycle.

Audit records preserve the immutable history of exception changes; the exception row represents the current exception state.

### 6. Series lifecycle

The EventSeries lifecycle is:

```text
active ↔ paused
active → cancelled → archived
```

- `paused` stops creation of new occurrences and does not modify already materialized future occurrences;
- `cancelled` permanently stops generation and does not automatically cancel existing future occurrences;
- `archived` is terminal;
- no automatic deletion or cancellation of existing occurrences occurs because a series is paused or cancelled.

### 7. Occurrence lifecycle

The EventOccurrence lifecycle is:

```text
scheduled → in_progress → completed
scheduled → cancelled
```

No other transitions are allowed. `completed` and `cancelled` are terminal.

### 8. Recurrence rule

The recurrence rule is RFC 5545-compatible.

MVP-supported components:

- `FREQ`: `DAILY | WEEKLY | MONTHLY | YEARLY`;
- `INTERVAL`;
- `BYDAY`;
- `BYMONTHDAY`;
- `BYMONTH`;
- `COUNT`;
- `UNTIL` at input only.

The canonical persisted model does not store `UNTIL` inside the RRULE. An input `UNTIL` is normalized to `series_end`.

`series_end` and `occurrence_limit` may both be set. The first reached limit terminates generation. If neither is set, the series is open-ended subject to lifecycle state and materialization horizon.

Timezone is mandatory and must be an IANA timezone identifier.

The UI uses structured recurrence controls. Arbitrary raw RRULE entry is not an MVP UI capability. The backend generates and validates the canonical RRULE representation.

### 9. Materialization

Materialization follows ADR-0015:

- default horizon: 180 days forward;
- horizon may extend when a requested period exceeds the materialized range;
- materialization is idempotent;
- repeated execution creates no duplicate occurrences;
- existing occurrences are never deleted automatically;
- occurrences with operational history are retained;
- occurrence IDs remain stable across series changes.

Materialization must be safe under concurrent execution using PostgreSQL uniqueness/locking semantics, not only an application process lock.

### 10. Concurrency

Creating a successor EventSeries version is a transactional operation.

The implementation must lock the relevant current/predecessor series state before checking and creating the successor. The operation must also verify that the caller's source version is still current.

If another transaction has already created a successor, the stale operation fails with `409 Conflict`. The system does not silently auto-rebase or create a parallel successor.

### 11. Authorization

Series and occurrence operations use the canonical Event permissions and scopes. No separate recurrence permission is introduced.

- read → `event.read`;
- series/occurrence update and scheduling changes → `event.update` or `event.manage` according to the operation;
- cancellation/archive → canonical Event lifecycle permissions.

Object-level authorization and Club ownership checks remain mandatory.

### 12. Audit

The recurrence domain extends the canonical audit vocabulary with these stable action codes:

| Action code | Meaning |
|---|---|
| `event_series.created` | Initial EventSeries version created. |
| `event_series.updated` | Current Series version's mutable non-versioning data changed where the operation does not create a successor version. |
| `event_series.version_created` | New EventSeries version created by a "this and following" change. |
| `event_series.status_changed` | EventSeries lifecycle transition (`active/paused/cancelled/archived`). |
| `event_occurrence.exception_created` | First exception created for an occurrence. |
| `event_occurrence.exception_changed` | Existing current exception changed. |
| `event_occurrence.status_changed` | EventOccurrence lifecycle transition. |
| `event_occurrence.series_rebound` | Already-materialized occurrence rebound to a new EventSeries version at a version boundary. |

These actions are business audit events and follow ADR-0024: mutation and audit are committed atomically; audit is append-only; no secrets or security-sensitive request data are stored.

A single user operation may legitimately produce more than one audit record when it changes multiple business resources. For example, a "this and following" change may emit `event_series.version_created` and `event_occurrence.series_rebound` for the materialized boundary occurrence.

### 13. Canonical physical persistence

The canonical physical recurrence model is defined by `docs/03-architecture/database-schema-recurrence.md`.

`event_occurrences` do not require a nullable bridge to a separate `events` row. Occurrence snapshots contain the operational Event fields needed by the occurrence domain. Existing non-recurring Event persistence remains governed by ADR-0019/ADR-0023.

`event_occurrence_exceptions` has a unique `occurrence_id` and stores the current exception state. Historical exception actions are represented by the immutable audit stream.

### 14. Implementation boundary

The following remain implementation details and must not become undocumented business rules:

- exact PostgreSQL index/exclusion expression for materialization identity;
- exact recurrence parser library;
- exact locking statement and transaction isolation level, provided the required conflict semantics are preserved;
- notification delivery implementation;
- calendar/iCalendar projection storage;
- attendance persistence.

## Consequences

### Positive

- Operational history is preserved.
- Occurrence IDs remain stable.
- "this and following" has deterministic semantics.
- Series edits cannot silently rewrite past events.
- Materialization is retry-safe and concurrency-safe.
- Current state and immutable audit history remain separate concerns.

### Negative

- The data model contains explicit series versions rather than mutating one recurrence row in place.
- Version creation and materialization require transactional concurrency handling.
- The API must expose or internally carry source-version information for conflict detection.
- Recurrence audit vocabulary grows beyond the existing single-Event vocabulary.

## Rejected alternatives

### Mutable single EventSeries row

Rejected because it cannot represent historical recurrence definitions safely once future occurrences have already been materialized.

### `is_current` on EventSeries

Rejected because current state is derivable from the version chain and a stored flag introduces synchronization risk.

### Automatic rebase of concurrent edits

Rejected because the system cannot safely infer the user's intended merge semantics.

### Full copy of occurrence history on every version

Rejected because occurrence identity and operational history must remain stable rather than being duplicated.
