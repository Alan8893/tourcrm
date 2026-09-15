# ADR-0032 — Event Attendance

**Status:** Accepted

**Date:** 2026-09-15

## Context

TourCRM needs a canonical attendance model for concrete event occurrences before backend implementation. Attendance must remain compatible with EventOccurrence-based recurrence, EventParticipation, existing authorization, and immutable business audit.

## Decision

### 1. Attendance identity

Attendance is a concrete record for exactly one `EventOccurrence` and one `Person`.

Canonical identity:

`(occurrence_id, person_id)`

PostgreSQL must enforce uniqueness for this pair.

Attendance is allowed only when the person has an `EventParticipation` for that occurrence. Attendance does not create participation and does not exist independently of participation.

Historical Attendance is preserved when participation is ended, removed, or otherwise changes state.

For ordinary non-recurring Events, attendance is attached to the concrete event occurrence used by the existing Event API model. There is no direct Event-level Attendance entity. Recurring events use the concrete `EventOccurrence`.

### 2. Status vocabulary

MVP Attendance has exactly two statuses:

- `present`
- `absent`

`late`, `excused`, `unknown`, `pending` and similar states are not introduced.

Absence semantics are represented separately by `absence_reason`.

### 3. Absence reasons

MVP uses a closed canonical vocabulary:

- `sick`
- `family_reason`
- `injury`
- `education`
- `work`
- `other`

`absence_reason` is nullable.

An `absent` Attendance may omit the reason. A `present` Attendance must have no absence reason.

Reasons are not a CRUD-managed entity in MVP and are not club-configurable.

### 4. Comment

Attendance contains nullable `comment`.

`comment` is allowed only for `absent` Attendance. It does not replace `absence_reason`.

`other` may be accompanied by a comment.

For `present`, both `absence_reason` and `comment` are `NULL`.

### 5. Lifecycle and editability

Attendance may be created or normally changed while the concrete EventOccurrence is:

- `scheduled`;
- `in_progress`.

For `completed`, normal PUT changes are closed. Corrections use the existing correction endpoint and require a mandatory correction reason plus audit.

For `cancelled`, Attendance cannot be created or normally changed.

Attendance is not deleted when an event is completed, cancelled, archived, or when participation changes.

`cancelled` EventOccurrences do not receive Attendance records.

### 6. Participant dependency

Only a person with EventParticipation for the concrete occurrence may receive Attendance.

Ending/removing/cancelling participation does not delete historical Attendance.

GroupMembership alone does not qualify a person for Attendance.

EventStaffAssignment does not qualify a User for Attendance; instructors/staff are not Attendance subjects in MVP.

### 7. Bulk semantics

`PUT /api/v1/events/{event_id}/attendance` is a partial bulk upsert, not full-list replacement.

Each supplied item is independently an idempotent upsert within one logical transaction:

- missing Attendance → create;
- existing Attendance → change;
- omitted people → unchanged;
- no Attendance records are deleted by bulk.

Duplicate `person_id` values in one request are invalid input and must not produce ambiguous writes.

The bulk operation is atomic: validation or authorization failure causes the logical operation to fail without partial Attendance persistence.

Bulk applies the same lifecycle, participant, status, reason and authorization rules as single-record update.

### 8. Authorization

Attendance uses existing permissions only:

- `attendance.read`
- `attendance.update`

No `attendance.correct` permission is introduced.

Authorization is occurrence/object based and follows the existing canonical scope model:

- `all` — within the caller's allowed club/object boundary;
- `own_events` / `assigned_events` — applicable explicit event staffing/responsibility;
- `own_groups` — applicable explicit group targeting and existing group authorization;
- `self` — the caller's own Person participation;
- `children` — applicable EventParticipation plus active GuardianRelationship/membership authorization;
- `none` — no access.

Authorization is evaluated before list/count/summary/pagination/serialization. Inaccessible opposing objects must not leak through existence, identifiers, counts, timing or pagination metadata.

Occurrence-level authorization remains authoritative for recurring events per ADR-0029.

Cross-club IDOR protection follows existing object/scope authorization; `occurrence.club_id` alone is never sufficient for non-`all` access.

### 9. API response

`GET /api/v1/events/{event_id}/attendance` returns the full EventParticipation-backed participant set visible to the requester, including participants without an Attendance record.

For each visible participant:

- `person` identity/projection;
- `status`: `present | absent | null`;
- `absence_reason`: nullable;
- `comment`: nullable.

`status = null` means Attendance has not yet been marked. It does not mean `absent`.

The response includes derived summary values:

- `total` — visible eligible participants;
- `marked` — participants with Attendance;
- `present` — present records;
- `absent` — absent records;
- `unmarked` — `total - marked`.

Summary is derived, not persisted.

Use the standard API v1 envelope, pagination and deterministic ordering conventions. Attendance-specific response casing follows the existing API conventions.

The existing correction contract exposes previous status, new status, mandatory reason, actor and timestamp as already defined by the Events API.

### 10. Person attendance history

A dedicated `GET /api/v1/persons/{person_id}/attendance` history endpoint is deferred from MVP. No new endpoint, permission or scope is introduced by this ADR.

### 11. Audit

Attendance business audit uses these action codes:

- `attendance.created`
- `attendance.changed`
- `attendance.bulk_changed`
- `attendance.corrected`

A normal upsert that creates the first record produces `attendance.created`; a normal update produces `attendance.changed`.

A bulk command produces one `attendance.bulk_changed` business audit event for the logical operation, with safe details sufficient to identify the affected records/result without secrets or sensitive technical metadata.

A correction produces `attendance.corrected` and requires the correction reason.

Attendance mutation and its audit write occur in the same transaction. Audit failure rolls back the Attendance mutation according to ADR-0024 fail-closed semantics.

### 12. Concurrency

MVP uses last-write-wins for concurrent Attendance changes.

No optimistic locking, ETag/If-Match or Attendance version field is introduced.

DB uniqueness on `(occurrence_id, person_id)` and transactional upsert provide consistency. Audit records each successful logical mutation.

### 13. Non-goals

This ADR does not introduce:

- biometric/check-in systems;
- GPS attendance;
- automatic presence detection;
- self-registration;
- attendance notifications;
- new permissions or scopes;
- club-configurable absence reason CRUD;
- staff/instructor attendance;
- persisted attendance summaries;
- Person attendance history API;
- automatic correction or rescheduling.

## Consequences

Attendance has a stable occurrence-level identity, remains historically durable, and can be queried as a complete participant journal. The model is compatible with recurring EventOccurrence authorization and avoids treating missing Attendance as absence.

## Traceability

Specification gate: TH-0086 / Issue #93.
Implementation task: TH-0087.
