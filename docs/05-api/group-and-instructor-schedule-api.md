# TourCRM — Group Schedule and Instructor Schedule API

## Status

**Specification gate — Group Schedule has an open authorization ODR. Instructor Schedule is contract-ready subject to the shared calendar contract. No application implementation is implied by this document until TH-0083 is closed.**

Canonical references:

- `docs/05-api/events-api.md` §16, §26, §27;
- `docs/05-api/api-conventions.md`;
- `docs/05-api/auth-and-authorization.md`;
- ADR-0013 — canonical scope vocabulary;
- ADR-0021 — Group persistence model;
- ADR-0022 — cross-Club ownership integrity;
- ADR-0026/0027 — RoleAssignment and effectivity;
- ADR-0029 — occurrence-level authorization relationships;
- ADR-0030 — EventSeries relationship source;
- ODR-0002 — Group Schedule Visibility and Group Lifecycle.

## 1. Shared projection contract

Both endpoints are contextual projections over the canonical Event/EventOccurrence model. They do not introduce a schedule-specific identity or duplicate recurrence engine.

### Range

`from` and `to` are mandatory timezone-aware RFC 3339 timestamps. The requested interval is `[from, to)`. The server normalizes both boundaries to canonical UTC instants before querying.

### Identity

Each item represents the underlying `Event` or materialized `EventOccurrence` and uses its stable opaque public ID. Recurring occurrences are not assigned a second calendar/schedule identity.

The response identifies the item kind (`event` or `occurrence`) and exposes series identity/version for recurring occurrences where already part of the public calendar contract.

### Effective times

For `Event`, use `start_at`/`end_at`.

For `EventOccurrence`, use its current effective `starts_at`/`ends_at` after the canonical occurrence exception/override rules. The schedule endpoint does not recompute RRULE or derive an independent recurrence timeline.

### Status visibility

Use the same visibility as the canonical internal calendar:

- Event: `published`, `in_progress`, `completed`, `cancelled`;
- EventOccurrence: canonical occurrence statuses (`scheduled`, `in_progress`, `completed`, `cancelled`).

`draft` and `archived` ordinary Events are excluded. Historical completed/cancelled items are included when they fall inside the explicitly requested range.

### Pagination and ordering

Use the standard API v1 pagination envelope. Results are ordered deterministically by `start_at ASC, id ASC`. Authorization and object filtering happen before total counting and pagination.

No arbitrary client-selected sort is supported.

### Errors

Use the canonical API error envelope and existing existence-hiding behavior. An unauthorized Group or object must not be distinguishable from a non-existent object when the canonical object policy requires existence hiding.

No new permission or scope is introduced by either endpoint.

### Recurrence

Recurring results use persisted `EventOccurrence` rows. If the requested `to` exceeds the materialized planning horizon, backend materialization may extend the horizon under ADR-0015/ADR-0028. Materialization is idempotent and concurrency-safe.

## 2. Group Schedule

### Endpoint

`GET /api/v1/groups/{group_id}/schedule`

### Purpose

Return the schedule of events and recurring occurrences whose authoritative event relationship targets the requested Group.

The Group path parameter is the contextual selector and must never be converted into an implicit Event authorization grant.

### Relationship qualification

Only explicit Group targeting qualifies an item:

- ordinary `Event` → `EventGroupTarget` for the requested `group_id`;
- recurring `EventOccurrence` → direct occurrence-level GroupTarget under ADR-0029;
- future recurring materialization → Series-level GroupTarget from the governing EventSeries version under ADR-0030, copied atomically to the occurrence.

`GroupMembership` is not an EventGroupTarget and must not by itself make an unrelated Event visible.

### Authorization

The endpoint requires `event.read` plus the canonical scope/object relationship. The exact `self`, `children`, `own_events`, archived-Group and membership-effectivity policy is blocked by ODR-0002 and must not be guessed by implementation.

`own_groups` remains the explicit active `GroupInstructorAssignment` relationship defined by ADR-0021. `occurrence.club_id`/`Group.club_id` alone is not sufficient for a non-`all` Event authorization decision.

### Filters

No filter may broaden access. If additional narrowing filters are introduced later, they must follow the same whitelist/narrowing-only rules as the canonical calendar projection.

### Group lifecycle

Archived-Group schedule behavior is not yet canonical. See ODR-0002. The implementation must not invent a restore-like or historical-read rule.

## 3. Instructor Schedule

### Endpoint

`GET /api/v1/me/instructor-schedule`

### Meaning of `me`

`me` identifies the authenticated **User**. It does not mean Person, ClubMembership or RoleAssignment.

The endpoint returns events related to the authenticated User's instructor responsibilities. A role name alone does not create a schedule relationship.

### Qualification relationship

An item is included when the authenticated User has at least one applicable relationship:

1. direct event staffing/responsibility (`own_events`); or
2. responsibility for a target Group (`own_groups`) through an active `GroupInstructorAssignment` and the event's authoritative GroupTarget.

For recurring occurrences, qualification uses direct occurrence-level staff and GroupTarget relationships under ADR-0029, with Series-level sources materialized according to ADR-0030.

A GroupInstructorAssignment is not copied into every Event as an EventStaffAssignment; it remains a Group-level responsibility relationship. The schedule projection resolves the event through the explicit GroupTarget + applicable GroupInstructorAssignment path.

### Effectivity

Staffing and GroupInstructorAssignment relationships use `[valid_from, valid_to)`, with `valid_to = NULL` open-ended. Applicability is evaluated at the Event/Occurrence scheduled start instant for relationship-based visibility.

Historical assignments do not retroactively authorize unrelated historical events unless the relationship was effective for the event/occurrence under the canonical object policy. Ending an assignment stops future applicability; it does not delete historical records.

### Scope behavior

The instructor schedule is relationship-based and must not become a club-wide calendar merely because the authenticated User holds an `all` RoleAssignment. `all` may authorize the ordinary calendar, but this contextual endpoint is still limited to the authenticated User's explicit instructor relationships.

`assigned_events` is treated as the canonical alias of `own_events`.

### Clubs

A User may have applicable assignments in multiple Clubs. Each returned item retains its own Club/object identity and is independently subject to cross-Club integrity. No Club may be inferred from the User's global identity.

### Filters

The first implementation slice requires only the shared `from`/`to` range and standard pagination. Any future filters must narrow the already relationship-authorized set and must not broaden it.

## 4. Explicit non-goals

- frontend UI or navigation;
- visual/asset work;
- PR #75 integration;
- iCalendar/`.ics`;
- conflict detection;
- notifications;
- self-registration;
- new permissions/scopes;
- a second recurrence/materialization engine;
- implicit authorization from Club ID, GroupMembership, role name or `created_by`.

## 5. Readiness

Instructor Schedule is implementation-ready against this shared contract.

Group Schedule remains blocked by ODR-0002. TH-0083 must remain open until the Product Owner resolves that ODR and the canonical authorization/API documents are synchronized.
