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

## 3. Persons

- `GET /persons`
- `GET /persons/{id}`
- `POST /persons`
- `PATCH /persons/{id}`
- `POST /persons/{id}/archive`
- `GET /persons/{id}/audit`

## 4. Club memberships

- `GET /memberships`
- `GET /memberships/{id}`
- `POST /memberships`
- `PATCH /memberships/{id}`
- `POST /memberships/{id}/activate`
- `POST /memberships/{id}/suspend`
- `POST /memberships/{id}/end`
- `GET /persons/{id}/memberships`

## 5. Guardians

Canonical resource is `guardian-relationships`, not `guardians` (ADR-0025 §4). The previously listed duplicate `/guardians` CRUD (list/detail/create/update) is removed; the nested per-person collection is renamed to match.

- `GET /persons/{id}/children`
- `GET /persons/{id}/guardian-relationships`
- `POST /persons/{id}/guardian-relationships`
- `PATCH /guardian-relationships/{id}`
- `POST /guardian-relationships/{id}/terminate`

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

The canonical recurrence resource paths are aligned with `docs/05-api/events-api.md` and ADR-0028. The older `/event-series` and `/event-occurrences` paths are not canonical.

### EventSeries

- `POST /events/series`
- `GET /events/series/{series_id}`
- `PATCH /events/series/{series_id}`
- `POST /events/series/{series_id}/exceptions`
- `GET /events/series/{series_id}/occurrences`

### EventOccurrence

- `GET /events/occurrences/{occurrence_id}`
- `PATCH /events/occurrences/{occurrence_id}`

Series/occurrence mutation semantics:

- recurrence changes must declare an explicit update scope;
- `this occurrence` changes only the selected occurrence through an occurrence exception/override;
- `this and following` creates a new EventSeries version beginning at a selected future `scheduled` occurrence;
- `entire series` changes the current series version according to the explicit recurrence policy, without rewriting immutable past occurrences;
- the selected occurrence retains its stable ID when rebound to a new series version;
- a cancelled occurrence cannot be the boundary for a new version;
- concurrent version creation uses transactional DB locking and stale-source detection; stale operations fail with `409 Conflict` rather than being automatically rebased;
- series lifecycle is `active ↔ paused →/active → cancelled → archived` with the exact allowed transitions defined by ADR-0028;
- occurrence lifecycle is `scheduled → in_progress → completed` or `scheduled → cancelled`;
- reschedule is an exception, not a lifecycle status;
- exceptions are auditable and do not delete occurrences.

## 10. Attendance

- `GET /events/{id}/attendance`
- `GET /events/{id}/attendance-summary`
- `PATCH /attendance/{id}`
- `POST /attendance/bulk-mark`
- `POST /attendance/{id}/correct`
- `GET /persons/{id}/attendance`

Attendance correction must respect audit policy.

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
