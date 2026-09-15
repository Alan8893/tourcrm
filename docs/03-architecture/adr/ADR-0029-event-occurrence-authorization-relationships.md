# ADR-0029 — EventOccurrence authorization relationships

## Status
Accepted — Option A: materialize occurrence relationships.

## Decision

When an `EventOccurrence` is materialized, the relationships applicable to that occurrence are materialized as occurrence-level relationship records. These records are the authoritative relationship boundary for authorization of that concrete occurrence, not a display cache.

Materialized categories:

- **staff/responsibility** — identifies the User assignments used by `own_events`;
- **group targeting** — identifies the Groups used by `own_groups`;
- **participation** — identifies Persons used by `self` and, together with `GuardianRelationship`, `children`.

Each recurring occurrence relationship references the occurrence directly. No nullable `event_id` bridge is introduced.

### Historical stability

Once materialized, occurrence-level relationships remain attached to the occurrence unless a future canonical occurrence-level relationship mutation explicitly changes them. A Series version does not silently rewrite historical occurrence relationships.

For not-yet-materialized future occurrences, the governing Series version provides the source relationship definition. The implementation must make that source explicit and must not infer responsibility from `created_by` or another incidental field.

### This-and-following

Existing occurrence IDs remain stable when a successor Series version is created. Already-materialized future occurrences are rebound according to ADR-0028 and keep their occurrence relationship records. Relationship changes introduced by the new Series version propagate only according to explicit Series-to-occurrence rules implemented in the recurrence slice. Past occurrence relationships are never rewritten merely because a new Series version exists.

### Participation boundary

Materializing participation does not introduce self-registration policy. Only Persons already explicitly associated with the occurrence are represented. Registration requests, automatic registration, eligibility, cancellation and attendance remain deferred concerns.

### Authorization

Canonical scopes remain unchanged:

- `all` — authorized objects within the caller's allowed Club boundary;
- `own_events` — occurrence-level staff/responsibility relationship;
- `own_groups` — occurrence-level group target plus applicable active GroupInstructorAssignment;
- `self` — occurrence-level participation for the requester's Person;
- `children` — occurrence-level participation for a child plus active GuardianRelationship and the Club-membership requirements of ADR-0023;
- `none` — no access.

`assigned_events` remains an alias of `own_events`; `own_records` is not introduced. No new permission or scope is introduced.

### Cross-Club integrity

Materialized staff, group and participation relationships must satisfy the same authoritative Club ownership and membership checks as their canonical Event counterparts. `occurrence.club_id` is never a substitute for the relationship itself.

### Consistency and concurrency

Occurrence materialization and creation of its required relationship records are one transactional operation. A partially materialized occurrence must not be visible to authorization queries. Concurrent materializers must remain safe under PostgreSQL uniqueness/locking semantics required by ADR-0028.

## Rejected alternatives

### Option B — inherited canonical relationship owner
Rejected because it introduces a new inheritance-resolution layer and makes historical exceptions harder to reason about.

### Option C — bounded calendar authorization
Rejected because the intended internal calendar must support instructor, group, member and guardian visibility for recurring occurrences.

### Nullable `event_id` bridge
Rejected because ADR-0028 establishes `EventOccurrence` as the concrete recurring operational entity without requiring a second Event identity.

### `created_by` as `own_events`
Rejected because responsibility must remain an explicit relationship.

## Canonical references

- ADR-0013 — Scope Vocabulary;
- ADR-0020 — Event Authorization and Participation;
- ADR-0022 — Cross-Club Ownership Integrity;
- ADR-0023 — Event Relationships and GuardianRelationship Persistence;
- ADR-0028 — Event Recurrence Persistence and Series Versioning.

## Consequence for TH-0079

ODR-0001 is resolved. TH-0079 may now be completed as a deterministic internal-calendar specification gate. Its implementation Issue must include occurrence-level relationship persistence/materialization needed for calendar authorization and must not invent a separate permission/scope model.
