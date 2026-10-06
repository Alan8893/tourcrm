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

- `POST /memberships/imports` — multipart CSV/XLSX import-job creation; creates status `uploaded`.
- `GET /memberships/imports/{import_id}` — import job status/statistics; protected by `membership.import` and ImportJob object policy.
- `GET /memberships/imports/{import_id}/errors` — paginated import errors and warnings (optional `severity=error|warning` filter); protected by `membership.import` and ImportJob object policy.
- `POST /memberships/imports/{import_id}/preview` — explicit, synchronous parsing/validation/exact duplicate detection (dry-run) for an `uploaded` job: `200` → `preview_ready`, `404` hidden/nonexistent job, `409` job not in `uploaded`, `422 import_validation_failed` → job `failed`; protected by `membership.import` and ImportJob object policy.
- `POST /memberships/imports/{import_id}/approve` — explicit approval of a `preview_ready` job: `200` → `approved`, `404` hidden/nonexistent job, `409` invalid status; no domain mutation.
- `POST /memberships/imports/{import_id}/apply` — synchronous application of an `approved` job: `200` → terminal status (`completed`, `partially_completed` or `failed`), `404` hidden/nonexistent job, `409` invalid status; creates only Person + User + ClubMembership for valid non-duplicate rows; protected by `membership.import` and ImportJob object policy.

Import jobs are Club-bound and retain `created_by_user_id` and `source_file_id`. Upload never starts parsing; preview never creates or changes domain entities. Creation and preview are not audit-required; import application/execution is audit-required and uses membership.import.applied plus existing domain audit actions.

The detailed import workflow, lifecycle, authorization and account semantics are defined in `docs/05-api/people-api.md §22` and `docs/04-modules/people-and-membership.md §11`.

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
- `DELETE /groups/{id}` — permanent delete, Administrator only; explicit destructive confirmation; atomic deletion of Group + historical GroupMembership/GroupInstructorAssignment + removable EventGroupTarget/SeriesGroupTarget relationships; rejected if the Group is the sole remaining target of an affected targeted Event/EventSeries; Events/EventSeries/EventOccurrences are never cascade-deleted or converted to club-wide; detailed contract in `docs/05-api/people-api.md`.
- `GET /groups/{id}/members`
- `POST /groups/{id}/members`
- Bulk GroupMembership transfer is not part of the current MVP implementation contract (Issue #286); no bulk transfer endpoint is to be implemented by this slice. The absence of this endpoint does not withdraw the business requirement: bulk transfer remains a deferred capability (`docs/02-requirements/business-rules.md` §9 item 6, Issue #296) whose API contract is defined by a separate future specification.
- `PATCH /group-memberships/{id}`
- `POST /group-memberships/{id}/end`
- `POST /group-memberships/{id}/transfer` — atomic single-participant transfer to another active Group; preserves historical source membership and prevents two simultaneously active memberships for this explicit transfer operation.
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
- `GET /events/{id}/participants` — implemented (read-only, registered participants; `events-api.md` §18)
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

Implemented Trip endpoints and their exact contract (incl. `PATCH /trips/{event_id}` for ordinary editing, Issue #264, the structured `official_difficulty` on Trip create/read/update, Issue #268 — no separate Difficulty endpoint, `country_id`/`region_id` on Trip create/read/update, Issue #271, `duration_classification` on Trip create/read/update, Issue #274 — no separate Duration endpoint, and `result` on Trip create/read/update, Issue #276 — the legacy `GET/POST /trips/{id}/results` drafts above are not implemented): `docs/05-api/trips-and-tourist-profile-api.md` §3–§4.

### 11.1 TourismType catalog — IMPLEMENTED (Issue #264)

- `GET /tourism-types`
- `POST /tourism-types`
- `GET /tourism-types/{id}`
- `PATCH /tourism-types/{id}`
- `POST /tourism-types/{id}/activate`
- `POST /tourism-types/{id}/deactivate`

No DELETE. Reads require `trip.read`; mutations require `trip.manage` with `all` scope (Administrator). No TourismType-specific permission.

### 11.2 Country / Region catalogs — IMPLEMENTED (Issue #271)

- `GET /countries`
- `POST /countries`
- `GET /countries/{id}`
- `PATCH /countries/{id}`
- `POST /countries/{id}/activate`
- `POST /countries/{id}/deactivate`
- `GET /regions`
- `POST /regions`
- `GET /regions/{id}`
- `PATCH /regions/{id}`
- `POST /regions/{id}/activate`
- `POST /regions/{id}/deactivate`

No DELETE. Reads require `trip.read`; mutations require `trip.manage` with `all` scope (Administrator). No Geography-specific permission. Contract: `docs/05-api/trips-and-tourist-profile-api.md` §3.8.

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

Domain semantics for this section are defined by `docs/04-modules/trips-and-tourist-profile.md` §11–§12 (Tourism Facts v2, Issue #259 TD4–TD9); this inventory does not redefine them:

- A Route is the physical route description associated with a Trip, not a classifier. It holds separate Planned and Actual representations (geometry/points, distance, elevation gain where elevation is available); Planned and Actual data are never mixed or substituted for one another.
- Route points (`/routes/{id}/points`, `/route-points/{id}`) belong to one representation — Planned or Actual.
- A GPX file uploaded through `POST /routes/{id}/gpx/uploads` carries an explicit role, `PLANNED` or `ACTUAL`. A Route has at most one canonical Planned GPX and at most one canonical Actual GPX; additional files are provenance/archive artifacts and do not become canonical automatically.
- `POST /gpx-files/{id}/process` derives technical characteristics only for the representation matching the file's role; results must be reproducible from that source.
- Before the Trip is completed, Actual GPX may be uploaded/replaced; at `Trip → completed` the current Actual GPX becomes a historical canonical fact; missing Actual GPX does not block completion. After completion, none of these endpoints may replace the historical Actual GPX through ordinary editing — replacement belongs to the future Historical Correction Workflow, which has no endpoint in this inventory.
- Route/GPX operations never set or change TourismType, Official Difficulty, Geography, Duration Classification or Result.
- Route/GPX management is authorized by the existing `trip.manage` and its scope (Administrator — authorized Trip scope; Instructor — assigned/owned Trip; Member/Guardian — none). No Route/GPX-specific permission (`gpx.upload`, `route.manage` or similar) exists.
- No endpoint is added for these semantics. Per §27, each of these entries still requires a module-specific contract (permission, scope, request/response schema, validation, errors, transaction boundary, audit, concurrency, idempotency, side effects) before implementation; `docs/05-api/trips-and-tourist-profile-api.md` must be reconciled with these semantics first.

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

## 19. Inventory / Склад

Прежний список `/equipment*`, `/equipment-issues*`, `/equipment-maintenance` **отозван** (Issue #230 / TH-0121): он описывал заменённую Equipment-модель. Канонический домен — `docs/04-domain/inventory.md`. Все складские эндпоинты — только для Administrator (чтение включительно; Instructor/Member/Guardian — `403`), в пространстве `/api/v1/inventory/*`; конкретные эндпоинты вносятся сюда реализующими срезами. Физического удаления нет — только необратимое `archive`.

Foundation (TH-0121, implemented):

- `GET /inventory/categories`, `POST /inventory/categories`
- `GET /inventory/categories/{category_id}`, `PATCH /inventory/categories/{category_id}`
- `POST /inventory/categories/{category_id}/archive`
- `GET /inventory/units`, `POST /inventory/units` (системные единицы `шт`, `м`, `комплект`, `пара` — только чтение)
- `GET /inventory/units/{unit_id}`, `PATCH /inventory/units/{unit_id}`
- `POST /inventory/units/{unit_id}/archive`
- `GET /inventory/storage-locations`, `POST /inventory/storage-locations`
- `GET /inventory/storage-locations/{location_id}`, `PATCH /inventory/storage-locations/{location_id}` (переименование и смена родителя)
- `POST /inventory/storage-locations/{location_id}/archive`
- `GET /inventory/items`, `POST /inventory/items`
- `GET /inventory/items/{item_id}`, `PATCH /inventory/items/{item_id}`
- `POST /inventory/items/{item_id}/archive`

Списки принимают `status=active|archived|all` (по умолчанию `active`) и `page`/`page_size`.

Slice 2 — экземпляры (implemented):

- `GET /inventory/instances` — фильтры `item_id`, `state`, `storage_location_id`, `page`/`page_size`; без `state` возвращаются все экземпляры, кроме `written_off`
- `POST /inventory/instances` — поступление экземпляра (`receipt`): `item_id`, `storage_location_id`, необязательные `unit_cost_minor`, `manufacturer_barcode`, `manufacturer_serial_number`, `description`
- `GET /inventory/instances/{instance_id}`, `PATCH /inventory/instances/{instance_id}` (только `manufacturer_barcode`, `manufacturer_serial_number`, `description`; не для `written_off`)
- `POST /inventory/instances/{instance_id}/transfer` — `to_location_id`, необязательный `comment`
- `POST /inventory/instances/{instance_id}/repair-start`, `POST /inventory/instances/{instance_id}/repair-end` — без тела
- `POST /inventory/instances/{instance_id}/write-off` — обязательный `comment` (причина)
- `GET /inventory/instances/{instance_id}/movements` — хронологическая история экземпляра
- `POST /inventory/movements/{movement_id}/reverse` — отмена списания экземпляра; необязательный `storage_location_id`, обязателен только если исходное место архивировано

Slice 3 — quantity-остатки и движения (implemented):

- `GET /inventory/stock` — ненулевые остатки quantity-номенклатуры по местам; фильтры `item_id`, `storage_location_id`, `page`/`page_size`
- `GET /inventory/items/{item_id}/stock` — ненулевые остатки одной quantity-номенклатуры по местам
- `GET /inventory/items/{item_id}/movements` — хронологическая история номенклатуры; фильтр `storage_location_id` (движения из места или в место), `page`/`page_size`
- `POST /inventory/items/{item_id}/receipts` — `storage_location_id`, `quantity`, необязательные `unit_cost_minor` (за единицу), `comment`
- `POST /inventory/items/{item_id}/transfers` — `from_location_id`, `to_location_id`, `quantity`, необязательный `comment`
- `POST /inventory/items/{item_id}/write-offs` — `storage_location_id`, `quantity`, обязательный `comment` (причина)
- `POST /inventory/items/{item_id}/write-offs/{movement_id}/reverse` — полная отмена quantity-списания; необязательный `storage_location_id`, обязателен только если исходное место архивировано

Операции возвращают созданное движение (`201`). Прямого изменения остатка (`PUT`/`PATCH`/`DELETE`) нет.

Slice 4 — выдача и возврат (implemented, Issue #236; правила — `docs/04-domain/inventory.md` §14, §25):

- `GET /inventory/issues` — документы выдачи, новые первыми; фильтры `status` (`issued` | `cancelled`), `recipient_type`, `recipient_id`, `event_id`, `page`/`page_size`; у каждого — производный признак `has_outstanding`
- `POST /inventory/issues` — создание = выдача: `recipient_type` (`member` → Person id, `instructor` → User id, `group` → Group id), `recipient_id`, необязательные `event_id`, `planned_return_date`, `comment`, `lines[]` — `{item_id, quantity}` для quantity-номенклатуры (места распределяются автоматически) или `{item_id, instance_ids[]}` для instance-номенклатуры; одна номенклатура — одна строка
- `GET /inventory/issues/{issue_id}` — документ с активными строками (`issued_quantity`, `returned_quantity`, `outstanding_quantity`, `outstanding_instance_ids`)
- `PATCH /inventory/issues/{issue_id}` — `recipient_type` + `recipient_id` (вместе), `event_id`, `planned_return_date`, `comment` (`null` очищает); только пока документ не отменён и по нему что-то числится выданным
- `POST /inventory/issues/{issue_id}/lines` — `lines[]` как при создании; номенклатура, уже присутствующая в документе, дополняется новым движением `issue` в существующую строку
- `GET /inventory/issues/{issue_id}/lines` — строки документа; `status=active|removed|all` (по умолчанию `active`), `page`/`page_size`; у удалённой строки заполнены `removed_at`/`removed_by`
- `DELETE /inventory/issues/{issue_id}/lines/{line_id}` — удаление строки из рабочего состава (`inventory.md` §14 п.16): только при `outstanding = 0` и редактируемом документе (у полностью возвращённого документа — `409 issue_fully_returned`); физического удаления нет — строка помечается `removed_at`/`removed_by`, её движения сохраняются; повторная выдача той же номенклатуры создаёт новую активную строку; возвращает документ (`200`)
- `POST /inventory/issues/{issue_id}/returns` — `storage_location_id` (обязательно), `quantities[]` (`{line_id, quantity}`), `instance_ids[]`, необязательный `comment`; частичный и полный возврат
- `POST /inventory/issues/{issue_id}/cancel` — `storage_location_id`; всё невозвращённое возвращается туда, статус `cancelled`; полностью возвращённую выдачу отменить нельзя
- `POST /inventory/issues/{issue_id}/lost` — `instance_id`, `storage_location_id`, обязательный `reason`, необязательный `comment`; возврат + немедленное списание экземпляра одной транзакцией
- `GET /inventory/issues/{issue_id}/movements` — хронологическая история документа (`issue`, `return`, списания утерянных экземпляров), `page`/`page_size`

Создание и добавление строк возвращают документ (`201`), остальные операции — документ (`200`). `DELETE` документа нет; `DELETE` строки только помечает её удалённой (`404 not_found` — строки нет в этом документе, `409 issue_line_outstanding` — по строке что-то выдано, `409 issue_line_removed` — строка уже удалена; удалённая строка в `returns` — `409 issue_line_removed`); отменённый и полностью возвращённый документ неизменяемы (`409 issue_cancelled` / `409 issue_fully_returned`). Остальные коды ошибок: `409 insufficient_stock`, `409 invalid_state_transition`, `409 return_exceeds_outstanding`, `409 instance_not_issued`, `409 item_has_outstanding_issues` (архивирование номенклатуры), `422 invalid_recipient`, `422 invalid_reference`, `422 archived_reference`, `422 item_not_quantity_mode` / `item_not_instance_mode`, `422 invalid_inventory_data`. Движения во всех ответах содержат `issue_line_id`.

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
