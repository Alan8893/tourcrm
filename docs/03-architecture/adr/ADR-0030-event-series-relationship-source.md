# ADR-0030 — EventSeries Relationship Source Model

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision owner:** Product Owner
- **Scope:** recurring Event authorization relationships

## Context

ADR-0029 requires authorization of recurring `EventOccurrence` records through occurrence-level, FK-backed relationships. The recurrence model already defines immutable `EventSeries` versions and stable occurrence identity, but the Series-level source for staffing, group targeting, and participation was previously unspecified.

Without a canonical source model, recurring occurrences could not safely resolve `own_events`, `own_groups`, `self`, or `children`. `occurrence.club_id` is not an authorization relationship and must not be used as a substitute. A nullable `event_id` bridge is also prohibited.

## Decision

Each immutable `EventSeries` version owns its own relationship definitions:

- `SeriesStaffAssignment`
- `SeriesGroupTarget`
- `SeriesParticipant`

Each definition has a direct FK to `event_series.id` and belongs to exactly one Series version. There is no recurring-domain `event_id` bridge.

The definitions form the authoritative source for future occurrence materialization. When an occurrence is materialized, applicable definitions from its governing Series version are copied into direct occurrence-level relationship records in the same transaction as occurrence creation.

## Relationship semantics

### SeriesStaffAssignment

Logical fields:

- `id`
- `event_series_id`
- `user_id`
- `role_in_event`
- `is_primary`
- `valid_from`
- `valid_to`
- `created_at`
- `updated_at`

Effectivity uses `[valid_from, valid_to)`. `valid_to = NULL` means open-ended. The relationship is applicable to an occurrence only when effective at the occurrence start instant.

### SeriesGroupTarget

Logical fields:

- `id`
- `event_series_id`
- `group_id`
- `valid_from`
- `valid_to`
- `created_at`
- `updated_at`

The Group must belong to the same Club as the Series. Effectivity uses `[valid_from, valid_to)` and is evaluated at the occurrence start instant.

### SeriesParticipant

Logical fields:

- `id`
- `event_series_id`
- `person_id`
- accepted participation fields/lifecycle required by the existing Event participation model
- `valid_from`
- `valid_to`
- `created_at`
- `updated_at`

Participation remains occurrence-level operational data after materialization. This model does not introduce self-registration.

## Versioning and `this_and_following`

Creating a successor Series version creates a complete snapshot of the predecessor's relationship definitions. The successor is independent after creation and its relationship definitions may be changed without mutating the predecessor.

For `this_and_following`:

1. the selected future scheduled occurrence is the boundary;
2. the successor Series version receives a snapshot-copy of all predecessor relationship definitions;
3. existing future occurrences governed by the predecessor are rebound to the successor according to the established recurrence propagation rules;
4. occurrence identity remains stable;
5. occurrence relationship records are propagated in the same semantic manner as Series event-field snapshots;
6. an explicitly changed occurrence relationship is a protected occurrence-level override and is not overwritten by later Series changes;
7. past occurrences are never rewritten.

A cancelled future occurrence cannot be used as the boundary for a new Series version; the next scheduled occurrence must be selected.

## Materialization

Materialization is idempotent and concurrency-safe. The governing/current Series version is resolved under the existing Series materialization locking rules. The occurrence and all applicable authorization relationship snapshots are created atomically.

A partially materialized occurrence must not be visible to authorization queries.

Relationship effectivity is evaluated using the occurrence's scheduled start instant. A Series relationship that is not effective at that instant is not copied to the occurrence.

Future materialization reads only the governing Series version; it never reconstructs authorization relationships from `club_id`, `created_by`, or historical occurrence data.

## Occurrence-level overrides

Explicit occurrence-level staffing, group-target, and participant changes are allowed where supported by the existing Event API semantics. Such a mutation establishes a protected occurrence-level override for the affected relationship.

The override is independent of the Series definition and remains authoritative until explicitly changed or ended through the occurrence-level relationship operation. Later Series updates do not overwrite protected occurrence relationships.

If an occurrence-level relationship API is not yet implemented by the existing Event API, implementation is a separate API slice; this ADR does not invent duplicate endpoints solely for recurrence.

## Authorization

Canonical scopes remain unchanged:

- `own_events` → active occurrence staff/responsibility relationship for the authenticated User;
- `own_groups` → active occurrence Group target plus applicable active `GroupInstructorAssignment`;
- `self` → active occurrence participation for the current user's Person/Membership;
- `children` → active occurrence participation plus active `GuardianRelationship` and membership checks.

`assigned_events` remains an alias of `own_events`. `all`, `none`, and all other existing authorization semantics remain unchanged.

`occurrence.club_id` alone never grants a non-`all` scope. All object and Club-boundary checks remain mandatory and IDOR/existence-hiding behavior remains canonical.

## Audit

Source relationship mutations use stable business audit actions:

- `event_series_staff_assignment.created`
- `event_series_staff_assignment.changed`
- `event_series_staff_assignment.ended`
- `event_series_group_target.created`
- `event_series_group_target.changed`
- `event_series_group_target.ended`
- `event_series_participant.created`
- `event_series_participant.changed`
- `event_series_participant.ended`

Occurrence relationship overrides use:

- `event_occurrence_staff_assignment.created`
- `event_occurrence_staff_assignment.changed`
- `event_occurrence_staff_assignment.ended`
- `event_occurrence_group_target.created`
- `event_occurrence_group_target.changed`
- `event_occurrence_group_target.ended`
- `event_occurrence_participant.created`
- `event_occurrence_participant.changed`
- `event_occurrence_participant.ended`

These actions use the existing immutable audit infrastructure. No audit API is introduced.

## Rejected alternatives

- Inheriting relationships dynamically from a Series at authorization time: rejected because occurrence-level relationships are the canonical authorization boundary and historical behavior must remain stable.
- Treating `occurrence.club_id` as sufficient for non-`all` access: rejected as an IDOR/security violation.
- Adding nullable `event_id` to recurring occurrences: rejected because it creates a competing identity/authorization bridge.
- Introducing new authorization scopes: rejected; canonical vocabulary remains unchanged.

## Consequences

Positive:

- aligns relationship history with immutable Series version history;
- makes occurrence authorization deterministic;
- keeps historical occurrences stable;
- avoids inheritance graphs and competing identities;
- supports transactional materialization and concurrency safety.

Trade-off:

- relationship snapshots duplicate data across Series versions and occurrences. This is intentional for auditability, deterministic authorization, and bounded recurring materialization.
