# TourCRM — API Endpoint Inventory

## Назначение

Документ является каталогом API TourCRM. Он фиксирует границы HTTP API и перечень операций, которые затем детализируются в модульных спецификациях и OpenAPI.

Все endpoints ниже подразумеваются под `/api/v1`.

## 1. Auth

### Public

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/password-reset/request`
- `POST /auth/password-reset/confirm`
- `POST /auth/verify-email/request`
- `POST /auth/verify-email/confirm`
- `POST /auth/invitations/accept`

### Authenticated

- `POST /auth/logout`
- `POST /auth/logout-all`
- `GET /auth/me`
- `POST /auth/change-password`

## 2. Users

- `GET /users`
- `GET /users/{id}`
- `POST /users`
- `PATCH /users/{id}`
- `POST /users/{id}/block`
- `POST /users/{id}/disable`
- `POST /users/{id}/activate`
- `POST /users/{id}/archive`
- `GET /users/{id}/sessions`
- `POST /users/{id}/sessions/revoke`

Только `GET /users` реализован (TH-0107) — как безопасный операционный справочник пользователей
(например, для выбора инструктора в фильтре Calendar), не как полноценный admin User Management
API. См. `docs/05-api/users-api.md` для полного контракта: permission/scope, query-параметры,
club boundary, response projection. Остальные endpoints этого раздела (`GET /users/{id}`,
`POST/PATCH /users`, block/disable/activate/archive, sessions) не реализованы в текущем MVP.

## 3. Persons

People Management endpoints are governed by ADR-0035 and `docs/05-api/people-api.md`.

- `GET /persons`
- `GET /persons/{id}`
- `POST /persons`
- `PATCH /persons/{id}`
- `GET /persons/{id}/audit`
- `POST /persons/wizard`
- `GET /persons/{id}/groups`
- `PATCH /persons/{person_id}/documents/{document_id}` — implemented (TH-0117.8), correction of non-file metadata of the current Document version.

There is no Person archive endpoint in the current MVP. Person archiving is deferred by ADR-0034; related lifecycle entities must not be used to simulate Person archival.

### Person creation wizard (TH-0116 / Issue #150)

`POST /persons/wizard` is additive to `POST /persons` above — it does not replace or change that endpoint's contract. It atomically creates a Person, its technical `ClubMembership`, a `User` (always — active with a first-access challenge if `email` is given, otherwise a login-less `pending` stub, see `people-api.md` §24.2.1), an initial `RoleAssignment` (§24 below) and, depending on the chosen role, `GroupMembership`/`GroupInstructorAssignment`/`GuardianRelationship` records — one business transaction, rolled back entirely on any failure. See `people-api.md` §6.1 for the full contract.

`GET /persons/{id}/groups` returns a Person's current and historical `GroupMembership` records (paginated, same `GroupMembershipOut` shape as `GET /groups/{id}/members`, §7). It was already listed in this inventory and in `people-api.md` §17 before TH-0116 but had no implementation; TH-0116 implements it, backing the Person Detail "Группы" tab that replaces the old, technical "Членство" tab.

### Person role assignments (TH-0112 / ADR-0039)

- `GET /persons/{person_id}/role-assignments`
- `POST /persons/{person_id}/role-assignments`
- `DELETE /persons/{person_id}/role-assignments/{role_code}`

A `role.manage`-gated, canonical-role-code-only view onto the same `RoleAssignment` resource `/role-assignments` (§24) already owns — see `people-api.md` §24 and ADR-0025 §6 (the flat `/role-assignments` collection remains the canonical resource identity; this is a Person-scoped convenience entry point, not a second model or a second API).

### Person account management (TH-0113 / ADR-0038)

- `GET /persons/{person_id}/account`
- `POST /persons/{person_id}/account`
- `POST /persons/{person_id}/account/password-reset`

Administrative User account creation and password reset/first-access setup, gated by the dedicated `account.manage` permission (§24). Reuses the existing `PasswordResetChallenge` mechanism and `POST /auth/password-reset/confirm` (§1) unchanged — no parallel password mechanism. See `people-api.md` §24.2.

### Person profile photo (TH-0119)

- `GET /persons/{person_id}/photo`
- `PUT /persons/{person_id}/photo`
- `DELETE /persons/{person_id}/photo`

These endpoints are authenticated and use the existing Person authorization model; no avatar-specific permission is introduced. Full contract: `docs/05-api/profile-photo-api.md`.

## 4. Club memberships

- `GET /memberships`
- `GET /memberships/{id}`
- `POST /memberships`
- `PATCH /memberships/{id}`
- `POST /memberships/{id}/status`
- `GET /persons/{id}/memberships`

Membership lifecycle is exposed through the canonical `/status` operation. The older separate `/activate`, `/suspend` and `/end` endpoints are not part of the current contract.

### 4.1 Participant import

- `POST /memberships/imports` — import job creation (CSV/XLSX; preview/dry-run before apply).
- `GET /memberships/imports/{import_id}` — import job status/statistics.
- `GET /memberships/imports/{import_id}/errors` — import validation/application errors.

The detailed import workflow and account semantics are defined in `docs/05-api/people-api.md §22` and `docs/04-modules/people-and-membership.md §11`.

### 4.2 Participant export

Participant list export is a separate operational workflow. Product-supported output modes are Excel, PDF and print. The exact export endpoint and detailed filter/column contract require a dedicated implementation Issue; no endpoint is invented here before that contract exists.

## 5. Guardians

Canonical resource is `guardian-relationships`, not `guardians` (ADR-0025 §4).

- `GET /persons/{id}/guardian-relationships`
- `POST /persons/{id}/guardian-relationships`
- `PATCH /guardian-relationships/{id}`
- `POST /guardian-relationships/{id}/terminate`
- `GET /me/children`

`GET /persons/{id}/children` is not canonical and is not part of the current API contract. `/me/children` is the Guardian-only current-children projection and derives the requester identity from the authenticated session.

GuardianRelationship create, update and terminate operations are admin-only according to ADR-0035. There is no primary-guardian/primary-representative concept in the current contract.

## 6. Invitations

- `GET /invitations`
- `POST /invitations`
- `GET /invitations/{id}`
- `POST /invitations/{id}/revoke`
- `POST /invitations/{id}/resend`

Invitation secrets are never returned after creation.

## 7. Groups

- `GET /groups`
- `GET /groups/{id}`
- `POST /groups`
- `PATCH /groups/{id}`
- `POST /groups/{id}/archive`
- `GET /groups/{id}/members`
- `POST /groups/{id}/members`
- `POST /groups/{id}/members/bulk`
- `PATCH /group-memberships/{id}`
- `POST /group-memberships/{id}/end`
- `GET /groups/{id}/instructors`
- `POST /groups/{id}/instructors`
- `POST /group-instructor-assignments/{id}/end`

## 8. Events

- `GET /events`
- `POST /events`
- `GET /events/{id}`
- `PATCH /events/{id}`
- `POST /events/{id}/cancel`
- `POST /events/{id}/restore` where business rules allow
- `POST /events/{id}/archive`
- `GET /events/{id}/participants`
- `POST /events/{id}/participants`
- `POST /events/{id}/participants/bulk`
- `PATCH /event-participations/{id}`
- `POST /event-participations/{id}/cancel`
- `GET /events/{id}/instructors`
- `POST /events/{id}/instructors`
- `POST /event-instructors/{id}/end`
- `GET /events/{id}/audit`

## 9. Recurring event series / occurrences

The canonical recurrence resource paths are aligned with `docs/05-api/event-recurrence-api.md` and ADR-0028. The older `/event-series` and `/event-occurrences` paths are not canonical.

### EventSeries

- `POST /events/series`
- `GET /events/series/{series_id}`
- `PATCH /events/series/{series_id}`
- `POST /events/series/{series_id}/pause`
- `POST /events/series/{series_id}/resume`
- `POST /events/series/{series_id}/cancel`
- `POST /events/series/{series_id}/archive`
- `POST /events/series/{series_id}/exceptions`
- `GET /events/series/{series_id}/occurrences`

### EventOccurrence

- `GET /events/occurrences/{occurrence_id}`
- `PATCH /events/occurrences/{occurrence_id}`

Dedicated occurrence `/cancel` and `/reschedule` endpoints are not canonical. Occurrence-level reschedule, cancellation and allow-listed property overrides use the Series `/exceptions` endpoint.

### Series relationship definitions

Each immutable EventSeries version owns its own relationship-definition snapshot:

- `SeriesStaffAssignment`;
- `SeriesGroupTarget`;
- `SeriesParticipant`.

The detailed CRUD/list/change/end contract for these resources is defined in `docs/05-api/event-recurrence-api.md`. Exact route naming is intentionally not duplicated in this inventory until reconciled with the existing Event relationship API surface; implementation must not create competing endpoints for the same relationship semantics.

Relationship definitions are owned by a concrete Series version, not by the logical root. `this_and_following` snapshot-copies predecessor definitions into the successor version. Future occurrence materialization copies applicable definitions into direct occurrence-level relationship records atomically.

Series/occurrence mutation semantics:

- recurrence changes must declare an explicit update scope;
- `this occurrence` changes only the selected occurrence through an occurrence exception/override;
- `this and following` creates a new EventSeries version beginning at a selected future `scheduled` occurrence;
- `this and following` snapshot-copies Series relationship definitions into the successor before successor-specific changes;
- occurrence-level relationship overrides are protected from later Series propagation;
- `entire series` changes the current series version according to the explicit recurrence policy, without rewriting immutable past occurrences;
- the selected occurrence retains its stable ID when rebound to a new series version;
- a cancelled occurrence cannot be the boundary for a new version;
- concurrent version creation uses transactional DB locking and stale-source detection; stale operations fail with `409 Conflict` rather than being automatically rebased;
- series lifecycle is `active ↔ paused`, `active → cancelled`, `cancelled → archived`;
- occurrence lifecycle is `scheduled → in_progress → completed` or `scheduled → cancelled`;
- reschedule is an exception, not a lifecycle status;
- exceptions are auditable and do not delete occurrences;
- each occurrence `ends_at` is derived as `starts_at + duration_minutes` from its governing Series version;
- recurring occurrence authorization uses direct occurrence-level relationships; `occurrence.club_id` alone never grants non-`all` access and no nullable `event_id` bridge is allowed.

## 10. Attendance

Synced to ADR-0032 (Issue #94 / TH-0087) — occurrence-level identity,
nested under the Event/Occurrence prefix, no separate summary endpoint
(the summary is inline in the GET response) and no Person attendance
history endpoint (explicitly deferred, ADR-0032 §10):

- `GET /events/{event_id}/attendance`
- `PUT /events/{event_id}/attendance/{person_id}`
- `PUT /events/{event_id}/attendance` (partial bulk upsert)
- `POST /events/{event_id}/attendance/{person_id}/corrections`

Attendance correction requires `attendance.update` (no separate
`attendance.correct` permission) plus a mandatory reason, and must
respect audit policy (ADR-0024/ADR-0032 §11).

## 11. Trips

- `GET /trips`
- `POST /trips`
- `GET /trips/{id}`
- `PATCH /trips/{id}`
- `POST /trips/{id}/publish`
- `POST /trips/{id}/complete`
- `POST /trips/{id}/archive`
- `GET /trips/{id}/participants`
- `POST /trips/{id}/participants`
- `PATCH /trip-participants/{id}`
- `POST /trip-participants/{id}/remove`
- `GET /trips/{id}/results`
- `POST /trips/{id}/results`

## 12. Routes and GPX

- `GET /routes`
- `POST /routes`
- `GET /routes/{id}`
- `PATCH /routes/{id}`
- `POST /routes/{id}/archive`
- `GET /routes/{id}/points`
- `POST /routes/{id}/points`
- `PATCH /route-points/{id}`
- `POST /route-points/{id}/delete`
- `POST /routes/{id}/gpx/uploads`
- `GET /gpx-files/{id}`
- `GET /gpx-files/{id}/download`
- `POST /gpx-files/{id}/process`

File access must enforce document/file permissions.

## 13. Tourist profiles

- `GET /persons/{id}/tourist-profile`
- `PATCH /persons/{id}/tourist-profile`
- `GET /persons/{id}/tourism-statistics`
- `GET /persons/{id}/trip-history`
- `GET /persons/{id}/tourist-portfolio`

Derived statistics must be reproducible from source facts.

## 14. Skills

- `GET /skills`
- `POST /skills`
- `GET /skills/{id}`
- `PATCH /skills/{id}`
- `POST /persons/{id}/skills`
- `PATCH /person-skills/{id}`
- `POST /person-skills/{id}/archive`

## 15. Qualifications

- `GET /qualifications`
- `POST /qualifications`
- `GET /qualifications/{id}`
- `PATCH /qualifications/{id}`
- `POST /persons/{id}/qualifications`
- `PATCH /person-qualifications/{id}`
- `POST /person-qualifications/{id}/revoke`

## 16. Achievements

- `GET /achievements`
- `POST /achievements`
- `GET /achievements/{id}`
- `PATCH /achievements/{id}`
- `POST /achievements/{id}/disable`
- `POST /persons/{id}/achievement-awards`
- `POST /achievement-awards/{id}/revoke`
- `GET /persons/{id}/achievements`

Automatic awarding must be deterministic and auditable.

## 17. Knowledge base

- `GET /knowledge/articles`
- `POST /knowledge/articles`
- `GET /knowledge/articles/{id}`
- `PATCH /knowledge/articles/{id}`
- `POST /knowledge/articles/{id}/publish`
- `POST /knowledge/articles/{id}/unpublish`
- `POST /knowledge/articles/{id}/archive`
- `GET /knowledge/categories`
- `POST /knowledge/categories`
- `PATCH /knowledge/categories/{id}`
- `GET /knowledge/tags`
- `POST /knowledge/tags`
- `GET /knowledge/search`

Published article version must remain historically identifiable.

## 18. Documents and consents

**PLANNED — nothing in this section is implemented.** No `documents`/`consents` endpoint exists in current code.

The generic list below predates ADR-0040 and remains an unresolved, broader aspirational sketch (`docs/04-modules/documents-and-consents.md`) for domains other than Person.

- `GET /documents`
- `POST /documents`
- `GET /documents/{id}`
- `PATCH /documents/{id}`
- `POST /documents/{id}/archive`
- `GET /documents/{id}/download`
- `POST /documents/uploads`
- `GET /consents`
- `POST /consents`
- `GET /consents/{id}`
- `PATCH /consents/{id}`
- `POST /consents/{id}/revoke`
- `GET /persons/{id}/consents`

### 18.1 Participant documents — IMPLEMENTED (TH-0117 / ADR-0040)

The participant-document endpoints below are implemented. The concrete, narrower canonical contract for TH-0117's participant documents (medical certificates and similar Person-owned documents), superseding the generic sketch above for this specific case (ADR-0040 §1/§2). Full concept-level API description: `docs/05-api/people-api.md` §32.

- `GET /persons/{person_id}/documents` (implemented, TH-0117.3)
- `POST /persons/{person_id}/documents` (implemented, TH-0117.3)
- `GET /persons/{person_id}/documents/{document_id}` (implemented, TH-0117.3)
- `GET /persons/{person_id}/documents/{document_id}/download` (implemented, TH-0117.3)
- `POST /persons/{person_id}/documents/{document_id}/replace` (implemented, TH-0117.6)
- `POST /persons/{person_id}/documents/{document_id}/revoke` (implemented, TH-0117.7)
- `PATCH /persons/{person_id}/documents/{document_id}` (implemented, TH-0117.8)
- `POST /events/{event_id}/document-package` (implemented, TH-0117.9)

### 18.2 Event document requirements — IMPLEMENTED (TH-0117.4–.5 / ADR-0040)

Persistence, participant requirement checks, and management endpoints are implemented. Full contract: `docs/05-api/events-api.md §31`.

- `GET /events/{event_id}/document-requirements` (implemented, TH-0117.5)
- `POST /events/{event_id}/document-requirements` (implemented, TH-0117.5)
- `PATCH /events/{event_id}/document-requirements/{requirement_id}` (implemented, TH-0117.5)
- `DELETE /events/{event_id}/document-requirements/{requirement_id}` (implemented, TH-0117.5)
- `GET /events/{event_id}/document-requirements/{person_id}` (implemented, TH-0117.4)

## 19. Equipment

- `GET /equipment`
- `POST /equipment`
- `GET /equipment/{id}`
- `PATCH /equipment/{id}`
- `POST /equipment/{id}/retire`
- `GET /equipment/{id}/history`
- `GET /equipment/{id}/issues`
- `POST /equipment-issues`
- `GET /equipment-issues/{id}`
- `PATCH /equipment-issues/{id}`
- `POST /equipment-issues/{id}/return`
- `POST /equipment-issues/{id}/cancel`
- `POST /equipment-maintenance`

## 20. Finance

- `GET /financial-accounts`
- `POST /financial-accounts`
- `GET /financial-accounts/{id}`
- `PATCH /financial-accounts/{id}`
- `GET /payments`
- `POST /payments`
- `GET /payments/{id}`
- `PATCH /payments/{id}` where correction policy allows
- `POST /payments/{id}/refund`
- `GET /expenses`
- `POST /expenses`
- `GET /expenses/{id}`
- `PATCH /expenses/{id}`
- `POST /expenses/{id}/void`
- `GET /event-budgets`
- `POST /event-budgets`
- `GET /event-budgets/{id}`
- `PATCH /event-budgets/{id}`
- `GET /reports/finance`

Financial mutation endpoints require explicit permissions and idempotency where duplicate submission is possible.

## 21. Notifications

- `GET /notifications`
- `GET /notifications/{id}`
- `POST /notifications/{id}/read`
- `POST /notifications/read-all`
- `GET /notification-preferences`
- `PATCH /notification-preferences`
- `GET /notification-rules`
- `POST /notification-rules`
- `PATCH /notification-rules/{id}`
- `POST /notification-rules/{id}/disable`
- `GET /notification-deliveries/{id}`
- `POST /notification-sends`

Mass send operations should normally be asynchronous.

## 22. Communications

- `GET /announcements`
- `POST /announcements`
- `GET /announcements/{id}`
- `PATCH /announcements/{id}`
- `POST /announcements/{id}/publish`
- `POST /announcements/{id}/unpublish`
- `POST /announcements/{id}/archive`
- `GET /communication-campaigns`
- `POST /communication-campaigns`
- `GET /communication-campaigns/{id}`
- `POST /communication-campaigns/{id}/send`
- `POST /communication-campaigns/{id}/cancel`

## 23. Analytics and reports

- `GET /dashboard/admin`
- `GET /dashboard/instructor`
- `GET /dashboard/member`
- `GET /dashboard/guardian`
- `GET /reports/attendance`
- `GET /reports/events`
- `GET /reports/members`
- `GET /reports/trips`
- `GET /reports/achievements`
- `GET /reports/equipment`
- `GET /reports/finance`
- `POST /exports`
- `GET /exports/{id}`
- `GET /exports/{id}/download`

Large exports are asynchronous.

## 24. Settings and administration

- `GET /settings`
- `GET /settings/{key}`
- `PATCH /settings/{key}`
- `GET /feature-settings`
- `PATCH /feature-settings/{key}`
- `GET /roles`
- `GET /permissions`
- `GET /role-assignments`
- `POST /role-assignments`
- `POST /role-assignments/{id}/revoke`
- `GET /audit-logs`
- `GET /audit-logs/{id}`

Security-sensitive settings require elevated permission and audit.

TH-0112 / ADR-0039 adds `GET/POST /persons/{person_id}/role-assignments` and `DELETE /persons/{person_id}/role-assignments/{role_code}` (§3) as a Person Detail-facing, canonical-role-code-only entry point onto this same resource — it does not replace or duplicate the endpoints above.

TH-0113 / ADR-0038 adds the `account.manage` permission and `GET/POST /persons/{person_id}/account` + `POST /persons/{person_id}/account/password-reset` (§3) for administrative User account/credential management — a dedicated permission, never `role.manage`/`settings.manage`/`person.update`.

## 25. Integrations

External integrations must use their own namespace to isolate external contracts.

### TourSlet

- `GET /integrations/tourslet/status`
- `POST /integrations/tourslet/connect`
- `POST /integrations/tourslet/disconnect`
- `POST /integrations/tourslet/sync`
- `GET /integrations/tourslet/jobs/{id}`
- `GET /integrations/tourslet/events`

Actual payloads, authentication and sync mapping are TBD until the existing TourSlet archive is analysed.

### Calendar

- `GET /integrations/calendar/providers`
- `POST /integrations/calendar/connections`
- `POST /integrations/calendar/connections/{id}/disconnect`
- `POST /integrations/calendar/sync`

Provider-specific implementation is TBD.

## 26. System health

- `GET /health/live`
- `GET /health/ready`

Do not expose database credentials, environment variables or detailed dependency information through public health endpoints.

## 27. Endpoint specification rule

An inventory entry is not yet a complete implementation contract.

Before implementation of each module, a module-specific specification must define:

- permission;
- scope;
- path parameters;
- query parameters;
- request schema;
- response schema;
- validation;
- error cases;
- transaction boundary;
- audit behavior;
- concurrency behavior;
- idempotency behavior;
- side effects/events;
- pagination/filter/sort;
- caching, if any.

No Claude implementation task should depend on undocumented endpoint semantics.
