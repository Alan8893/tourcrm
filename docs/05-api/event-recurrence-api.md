# TourCRM — Event Recurrence API Contract

## Scope

Implementation-facing contract for recurring Event Series and materialized Occurrences. Canonical decisions come from ADR-0015, ADR-0018, ADR-0019, ADR-0020, ADR-0024 and ADR-0028.

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

## Authorization

- Reads require `event.read` plus canonical scope/object policy.
- Series and occurrence updates require `event.update` or `event.manage` according to the operation.
- Series cancellation/archive use the canonical Event lifecycle permission mapping.
- No recurrence-specific permission exists.
- Club ownership and object-level authorization are mandatory.

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
- applicable group/instructor targets.

The UI does not submit arbitrary raw RRULE. Backend validates the structured input and produces the canonical RRULE.

For each materialized occurrence:

`ends_at = starts_at + duration_minutes`

The duration is part of the Series version snapshot. A duration change affecting future occurrences is represented by the applicable Series update/versioning operation rather than by an undocumented default duration.

## Series update

Every update that targets a recurring schedule must declare one of:

- `this_occurrence`;
- `this_and_following`;
- `entire_series`.

`this_occurrence` changes only the selected occurrence through an occurrence exception/override.

`this_and_following` additionally carries the selected future scheduled `occurrence_id` and the caller's source `series_version`. It creates a new EventSeries version beginning at that occurrence. The selected materialized occurrence keeps its stable ID and is rebound to the new version.

If the source version is stale, return `409 Conflict` and make no mutation.

Historical occurrences must not be rewritten.

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
- current exception state where applicable.

## Errors

At minimum:

- `400` invalid recurrence/field payload;
- `403` authorization failure;
- `404` inaccessible/nonexistent resource according to standard existence-hiding policy;
- `409` stale Series version, invalid successor boundary, lifecycle conflict or DB uniqueness/concurrency conflict.

No new `INSUFFICIENT_SCOPE` error is introduced.

## Audit

Required audit actions are the canonical codes in ADR-0024 as amended by ADR-0028.

## Side effects

Cancellation, reschedule and other obligation-changing mutations may enqueue notification events. Delivery is outside the database transaction.

## Idempotency

Materialization is DB-idempotent. Repeated materialization must not duplicate an occurrence. Mutation endpoint idempotency follows the existing API conventions; no new client idempotency contract is invented here.
