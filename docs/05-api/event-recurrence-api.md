# TourCRM — Event Recurrence API Contract

## Scope

Implementation-facing contract for recurring Event Series, materialized Occurrences, and their authorization relationship snapshots. Canonical decisions come from ADR-0015, ADR-0018, ADR-0019, ADR-0020, ADR-0024, ADR-0028, ADR-0029 and ADR-0030.

## Endpoints

### Series

- `POST /api/v1/events/series`
- `GET /api/v1/events/series/{series_id}`
- `PATCH /api/v1/events/series/{series_id}`
- `POST /api/v1/events/series/{series_id}/pause`
- `POST /api/v1/events/series/{series_id}/resume`
- `POST /api/v1/events/series/{series_id}/cancel`
- `POST /api/v1/events/series/{series_id}/archive`
- `GET /api/v1/events/series/{series_id}/occurrences`

### Occurrences

- `GET /api/v1/events/occurrences/{occurrence_id}`
- `PATCH /api/v1/events/occurrences/{occurrence_id}`
- Occurrence reschedule/cancel/override operations use the series exception endpoint below; dedicated occurrence `/cancel` and `/reschedule` endpoints are not canonical.

### Exceptions

- `POST /api/v1/events/series/{series_id}/exceptions`

This is the canonical mutation endpoint for occurrence-level reschedule, cancellation and allow-listed property overrides.

### Series relationship definitions

The following logical resources are owned by an immutable `EventSeries` version:

- `SeriesStaffAssignment`;
- `SeriesGroupTarget`;
- `SeriesParticipant`.

The implementation must expose CRUD/close operations for these definitions through the existing Event relationship API surface, using direct Series-version ownership. Exact route names are implementation detail and must not create duplicate relationship semantics. The minimum contract is:

- create a definition for a specific Series version;
- list definitions for a Series version;
- change an active definition through the canonical relationship update semantics;
- end an active definition explicitly, closing `valid_to` at server UTC time;
- read definitions as part of the Series relationship projection.

No relationship definition may be attached to the logical root independently of a concrete Series version.

## Authorization

- Reads require `event.read` plus canonical scope/object policy.
- Series and occurrence updates require `event.update` or `event.manage` according to the operation.
- Series relationship mutations use the existing Event relationship permission mapping; no recurrence-specific permission exists.
- Club ownership and object-level authorization are mandatory.
- `own_events` resolves recurring occurrences through active occurrence staff/responsibility relationships.
- `own_groups` resolves recurring occurrences through active occurrence group targets plus applicable `GroupInstructorAssignment`.
- `self` resolves recurring occurrences through active occurrence participation for the current user's Person/Membership.
- `children` resolves recurring occurrences through active occurrence participation plus active `GuardianRelationship` and membership checks.
- `occurrence.club_id` alone never grants any non-`all` scope.

## Series creation

Request contains the canonical Event snapshot fields plus:

- `name`;
- `description`;
- `event_type`;
- `series_start_at`;
- `series_end_at` nullable;
- `occurrence_limit` nullable;
- `duration_minutes` — required positive integer duration of each occurrence;
- structured recurrence fields corresponding to the accepted RRULE vocabulary;
- `timezone`;
- applicable initial Series relationship definitions for group/instructor/participant targets.

The UI does not submit arbitrary raw RRULE. Backend validates the structured input and produces the canonical RRULE.

For each materialized occurrence:

`ends_at = starts_at + duration_minutes`

The duration is part of the Series version snapshot. A duration change affecting future occurrences is represented by the applicable Series update/versioning operation rather than by an undocumented default duration.

## Series relationship definitions

Relationship definitions belong directly to the concrete immutable Series version.

### Staff

`SeriesStaffAssignment` identifies a User responsible for the recurring Event and carries:

- `user_id`;
- `role_in_event`;
- `is_primary`;
- `valid_from`;
- `valid_to`.

Effectivity uses `[valid_from, valid_to)`; `valid_to = null` is open-ended. Existing Event staffing temporal and Club-boundary rules apply.

### Group target

`SeriesGroupTarget` identifies a Group targeted by the recurring Event and carries:

- `group_id`;
- `valid_from`;
- `valid_to`.

The Group must satisfy the same-Club invariant. Effectivity uses `[valid_from, valid_to)`.

### Participant

`SeriesParticipant` identifies a Person associated with the recurring Event and carries the accepted Event participation fields plus:

- `valid_from`;
- `valid_to`.

Membership and Club integrity follow the accepted Event participation contract. This does not introduce self-registration or attendance behavior.

A relationship definition effective at the occurrence start instant is eligible for materialization. A definition outside its effectivity interval is not copied to that occurrence.

## Series update

Every update that targets a recurring schedule must declare one of:

- `this_occurrence`;
- `this_and_following`;
- `entire_series`.

`this_occurrence` changes only the selected occurrence through an occurrence exception/override.

`this_and_following` additionally carries the selected future scheduled `occurrence_id` and the caller's source `series_version`. It creates a new EventSeries version beginning at that occurrence. The selected materialized occurrence keeps its stable ID and is rebound to the new version.

The successor version receives an exact snapshot-copy of all predecessor Series relationship definitions before successor-specific relationship changes are applied. The successor then owns an independent definition set.

For already-materialized future occurrences, the version propagation operation updates non-protected occurrence relationship snapshots according to the successor definition. Explicit occurrence-level relationship changes are protected overrides and are not overwritten by later Series updates. Past occurrences are never rewritten.

If the source version is stale, return `409 Conflict` and make no mutation.

Historical occurrences must not be rewritten.

## Occurrence relationship overrides

Occurrence-level staff, group-target and participant overrides are allowed where supported by the existing Event relationship semantics. An explicit occurrence-level mutation establishes a protected override for the affected relationship.

The override remains authoritative over later Series relationship changes until explicitly changed or ended through the occurrence-level relationship operation.

If an occurrence relationship operation is not yet present in the existing Event API implementation, it is a separate implementation slice; this recurrence contract does not invent duplicate endpoints.

## Series lifecycle

Series lifecycle uses dedicated endpoints:

- `POST /api/v1/events/series/{series_id}/pause`;
- `POST /api/v1/events/series/{series_id}/resume`;
- `POST /api/v1/events/series/{series_id}/cancel`;
- `POST /api/v1/events/series/{series_id}/archive`.

Allowed lifecycle transitions are defined by ADR-0028:

`active ↔ paused`, `active → cancelled`, `cancelled → archived`.

Pausing stops creation of new occurrences but does not modify already materialized future occurrences. Cancellation permanently stops generation but does not automatically cancel or delete existing future occurrences.

## Occurrence exception

`POST /api/v1/events/series/{series_id}/exceptions` is the canonical endpoint for a concrete occurrence's reschedule, cancellation and allow-listed property override without changing the recurrence rule.

Reschedule keeps the occurrence ID and status `scheduled`. Cancellation requires a reason and makes the occurrence terminal.

The current exception state is represented by `EventOccurrenceException`; historical changes are represented by immutable Audit records.

A cancelled occurrence cannot be used as the boundary for a new Series version; the caller must select the next scheduled occurrence.

## Versioning response

Series responses expose enough information to identify the logical series and version chain:

- `id`;
- `root_series_id`;
- `version`;
- `supersedes_series_id` nullable;
- lifecycle/status;
- recurrence configuration;
- `duration_minutes`;
- event snapshot fields;
- timestamps.

No `is_current` field is part of the canonical model; current means terminal version in the chain.

## Occurrence response

Occurrence responses expose:

- `id`;
- `series_id`;
- `club_id`;
- snapshot Event fields;
- effective `starts_at` / `ends_at`;
- `timezone`;
- `status`;
- `cancellation_reason` nullable;
- current exception state where applicable;
- effective occurrence-level relationship projections needed by authorized clients.

## Errors

At minimum:

- `400` invalid recurrence/field payload;
- `403` authorization failure;
- `404` inaccessible/nonexistent resource according to standard existence-hiding policy;
- `409` stale Series version, invalid successor boundary, lifecycle conflict or DB uniqueness/concurrency conflict.

No new `INSUFFICIENT_SCOPE` error is introduced.

## Audit

Required audit actions are the canonical codes in ADR-0024 as amended by ADR-0028 and ADR-0030. Relationship source create/change/end and occurrence relationship override mutations are audited through the existing immutable audit infrastructure.

## Side effects

Cancellation, reschedule, relationship and other obligation-changing mutations may enqueue notification events. Delivery is outside the database transaction.

## Idempotency

Materialization is DB-idempotent. Repeated materialization must not duplicate an occurrence or its relationship snapshots. Mutation endpoint idempotency follows the existing API conventions; no new client idempotency contract is invented here.
