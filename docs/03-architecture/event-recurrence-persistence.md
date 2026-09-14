# TourCRM — Event Recurrence Persistence Contract

This document is the implementation-facing persistence contract for recurring Events. It complements ADR-0028 and `database-schema-recurrence.md`.

## Canonical entities

- `EventSeries` — versioned recurrence definition.
- `EventOccurrence` — materialized operational occurrence.
- `EventOccurrenceException` — current exception/override for one occurrence.

## Required invariants

- Series versions form a single chain per `root_series_id`.
- `(root_series_id, version)` is unique.
- A predecessor can have at most one successor.
- Current version is the terminal version; no `is_current` column.
- Occurrence IDs are stable.
- Materialization is idempotent under concurrent execution.
- `EventOccurrenceException.occurrence_id` is unique.
- Reschedule never creates a replacement occurrence.
- Cancelled occurrences are terminal and cannot be version boundaries.
- Series version creation uses transactional locking and stale-version detection; stale updates return `409 Conflict`.
- `EventParticipation`, staffing and group targeting attach to the occurrence, not to a nullable Event bridge.

## Lifecycle

Series: `active ↔ paused`, `active → cancelled → archived`.

Occurrence: `scheduled → in_progress → completed` or `scheduled → cancelled`.

## Recurrence

RFC 5545-compatible RRULE. MVP: `FREQ`, `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`, `COUNT`, with `UNTIL` accepted only as input and normalized to `series_end_at`.

Timezone is mandatory IANA. `series_end_at` and `occurrence_limit` may both be set; first reached limit terminates generation.

## Exceptions

Current exception types: `rescheduled`, `cancelled`.

`overrides` is JSONB but is strictly allow-listed and validated by the same domain rules as normal Event updates. Audit is the immutable historical record.

## Materialization

Default horizon is 180 days forward. Horizon may extend on demand. Existing operational/history-bearing occurrences are retained. No automatic deletion/cancellation follows from pausing or cancelling a Series.

The exact PostgreSQL materialization idempotency key/index expression is an implementation detail, but it must provide deterministic DB-level uniqueness under concurrent workers.

## Audit

Canonical recurrence actions are defined by ADR-0024 as amended by ADR-0028:

- `event_series.created`
- `event_series.updated`
- `event_series.version_created`
- `event_series.status_changed`
- `event_occurrence.exception_created`
- `event_occurrence.exception_changed`
- `event_occurrence.status_changed`
- `event_occurrence.series_rebound`

All audit-required mutations follow the same transaction and fail-closed semantics as ADR-0024.
