# TourCRM — Events & Schedule API

## 1. Назначение

Данный документ определяет API-контракт календаря, мероприятий, расписания, серий повторяющихся занятий, регистрации участников и посещаемости.

## 2. Основная модель

`Event` — логическое мероприятие.

`EventSeries` — правило повторения мероприятия.

`EventOccurrence` — конкретное запланированное проведение мероприятия.

`EventParticipation` — участие (регистрация) человека в конкретном `Event` — не в occurrence (расхождение с более ранней версией этого документа: канонические ADR-0023 §4 и ADR-0037 определяют `EventParticipation` как связь `Event`↔`Person`, `Attendance` же остаётся occurrence-based; см. §19-20 ниже).

Посещаемость хранится относительно occurrence, а не только series/event.

Resolved by ADR-0033 (PO decision): каждый обычный, не повторяющийся `Event` имеет ровно один concrete `EventOccurrence` (создаётся и синхронизируется вместе с Event), поэтому `{event_id}` в §22-25 ниже разрешается детерминированно — либо как Event → его единственный linked occurrence, либо (для recurring) напрямую как `EventOccurrence.id`. Attendance identity остаётся occurrence-only (`(occurrence_id, person_id)`, без `Attendance.event_id`) — см. ADR-0033.

## 3. Общий префикс

`/api/v1/events`

Attendance endpoints (§22-25) are nested under this same prefix
(`/api/v1/events/{event_id}/attendance...`) — there is no separate
top-level `/api/v1/attendance` route family.

## 4. Event list

### GET `/api/v1/events`

Поддерживает:

- `from` / `to`;
- `event_type`;
- `status`;
- `group_id`;
- `participant_id`;
- `instructor_id`;
- `search`;
- pagination;
- sorting.

Backend обязан ограничивать результаты по canonical permission/scope и object relationship requester.

Канонические scopes: `all`, `own_groups`, `own_events`, `self`, `children`, `none`. `assigned_events` является алиасом `own_events`.

## 5. Event details

### GET `/api/v1/events/{event_id}`

Возвращает Event и связанные данные в пределах permission/scope/object policy.

При необходимости подробности специализированного Trip/TourSlet/Competition запрашиваются отдельными endpoint'ами.

## 6. Create event

### POST `/api/v1/events`

Request concept:

```json
{
  "event_type": "lesson",
  "title": "Ориентирование",
  "description": "...",
  "start_at": "2026-09-20T17:00:00+03:00",
  "end_at": "2026-09-20T19:00:00+03:00",
  "timezone": "Europe/Moscow",
  "location": {...},
  "group_ids": ["..."],
  "instructor_ids": ["..."]
}
```

Каноническая Event persistence model использует `location_type`, `location_name`, `location_address`, `location_latitude`, `location_longitude`; отдельного поля `location` в физической модели нет (ADR-0019). API implementation must map request representation to the canonical field model.

**TH-0108 / ADR-0037 §1-§2**: `group_ids`/`instructor_ids` — оба опциональны, по умолчанию пустой список. `group_ids: []` (или отсутствие поля) означает club-wide Event; 1+ значений — targeted Event для указанных Groups (`EventGroupTarget`). `instructor_ids` — ответственные instructors/Users (`EventStaffAssignment`); назначение НЕ требует `GroupInstructorAssignment` и не создаёт его. Event и Event создаются атомарно вместе с этими связями в одной транзакции: если валидация любой связи не проходит (Group другого Club, несуществующий Group/User, User без active ClubMembership в Club события), Event не сохраняется вовсе. Group targeting никогда не создаёт `EventParticipation` или `GroupMembership`. Response (`EventOut`) включает `group_ids`/`instructor_ids` — текущие (active) значения этих связей, чтобы клиент мог восстановить состояние формы.

## 7. Update event

### PATCH `/api/v1/events/{event_id}`

Для одиночного события изменяет будущие свойства.

При изменении recurring event необходимо явно указывать scope изменения:

- this occurrence;
- this and following;
- entire series.

Backend должен отклонять неоднозначные запросы.

**TH-0108**: `group_ids`/`instructor_ids` в PATCH — то же правило `exclude_unset`, что и у остальных полей: поле отсутствует в теле запроса → текущие targeting/assignment не меняются; поле присутствует (включая `[]`) → заменяет текущий активный набор: новые значения добавляются, снятые — переводятся в `ended` (`valid_to = now()`, запись никогда не удаляется — тот же historical-interval паттерн, что и у GroupMembership/GroupInstructorAssignment/UserRoleAssignment), уже активные и всё ещё желаемые — не трогаются (повторный идентичный PATCH идемпотентен, дублей не создаёт). Изменение полей Event и targeting/assignment происходит в одной транзакции.

## 8. Event status

### POST `/api/v1/events/{event_id}/status`

Канонические статусы и переходы определены ADR-0018 и совпадают с `business-rules.md` и `events-and-schedule.md`:

- `draft`
- `published`
- `in_progress`
- `completed`
- `cancelled`
- `archived`

Допустимые переходы:

```text
draft -> published
published -> in_progress
published -> cancelled
in_progress -> completed
in_progress -> cancelled
completed -> archived
cancelled -> archived
```

Недопустимые переходы отклоняются. `planned` не является отдельным статусом Event.

Cancellation reason обязателен при отмене.

## 9. Delete/archive

### POST `/api/v1/events/{event_id}/archive`

Требуемое permission: `event.manage` с применимым scope/object policy.

Архивирование не должно уничтожать attendance, participants, audit или финансовые связи.

Hard delete доступен только для ещё не использовавшихся черновиков, если отдельная политика это разрешает.

## 10. Event series

### POST `/api/v1/events/series`

Создаёт recurring event series.

Минимально:

- базовые event fields;
- recurrence rule;
- date range/termination;
- timezone;
- target groups/instructors.

RRULE должен валидироваться backend.

## 11. Series details

### GET `/api/v1/events/series/{series_id}`

Возвращает recurring configuration и список применённых overrides/exceptions согласно pagination/period limits и requester authorization.

## 12. Series update

### PATCH `/api/v1/events/series/{series_id}`

Изменение серии требует explicit update scope.

Исторические occurrences, которые уже прошли, не должны непреднамеренно переписывать историю.

Для schedule/recurrence management используются только канонические Event permissions (`event.update` / `event.manage` согласно конкретной операции); отдельного `event.schedule.manage` нет.

## 13. Series exceptions

### POST `/api/v1/events/series/{series_id}/exceptions`

Позволяет:

- отменить occurrence;
- перенести occurrence;
- изменить отдельные свойства occurrence.

Каждое исключение audit'ed.

## 14. Occurrences

### GET `/api/v1/events/series/{series_id}/occurrences`

Возвращает occurrences за период.

### GET `/api/v1/events/occurrences/{occurrence_id}`

Возвращает конкретное проведение.

## 15. Occurrence update

### PATCH `/api/v1/events/occurrences/{occurrence_id}`

Изменяет конкретное проведение без изменения остальных occurrences.

Изменение времени/места after registrations должно инициировать соответствующие notification side effects.

## 16. Calendar feed

### GET `/api/v1/events/calendar`

Возвращает calendar projection для requester после применения `event.read` и scope/object policy.

#### Query parameters

`from` и `to` обязательны. Они задают интервал календаря **`[from, to)`**: `from` включается, `to` не включается.

Границы должны быть timezone-aware RFC 3339 timestamps. Сервер нормализует их к canonical UTC instant для выборки. Локальное отображение выполняется клиентом с учётом timezone события/пользователя.

Запрос без обеих границ не является валидным календарным запросом.

Поддерживаются дополнительные фильтры, только сужающие уже авторизованный набор:

- `user_id` — текущий пользователь/связанный с ним пользовательский контекст по разрешённой объектной политике;
- `group_id` — группа;
- `event_type` — тип мероприятия;
- `status` — статус.

Фильтры не могут расширять доступ requester.

Calendar collection использует стандартный API v1 pagination envelope. `page` и `page_size` соответствуют общим API conventions; серверные default/max значения являются глобальной конфигурацией.

Результат сортируется по `start_at` по возрастанию. Сортировка является серверной и детерминированной: при одинаковом `start_at` используется стабильный `id` как tie-breaker. Произвольное поле сортировки клиентом не поддерживается.

### Calendar status visibility

Calendar projection возвращает occurrences/events со статусами:

- `published`;
- `in_progress`;
- `completed`;
- `cancelled`.

`draft` и `archived` в обычную calendar projection не входят.

Прошедшие `completed` и `cancelled` записи возвращаются, если попадают в явно запрошенный диапазон. Отменённое проведение не удаляется из calendar projection: оно возвращается со статусом `cancelled`, чтобы календарь мог явно показать отмену.

### Recurring occurrences and materialization

Для recurring events calendar projection работает с persisted `EventOccurrence` и не вычисляет RRULE самостоятельно.

Если запрошенный `to` выходит за текущий materialized planning horizon, backend автоматически расширяет materialization до необходимого диапазона в рамках правил ADR-0015/ADR-0028. Материализация должна оставаться idempotent и concurrency-safe.

Calendar authorization для recurring occurrences использует occurrence-level relationships согласно ADR-0029:

- staff/responsibility → `own_events`;
- group targeting + applicable GroupInstructorAssignment → `own_groups`;
- participation → `self` / `children`.

`occurrence.club_id` сам по себе не предоставляет доступ за пределами разрешённой object/scope policy.

### Calendar response identity

Каждый calendar item представляет одно конкретное occurrence/event и содержит стабильный public opaque `id` исходной сущности. Для recurring occurrence не создаётся второй календарный identity.

The response must identify whether the item is an ordinary Event or EventOccurrence and, for recurring occurrences, expose the governing series identifier/version where that information is part of the public contract.

## 17. iCalendar

### GET `/api/v1/events/calendar.ics`

Предоставляет read-only iCalendar feed в соответствии с доступом requester.

Feed token должен быть отдельным секретом и не должен совпадать с auth/session token.

Отозванный token перестаёт действовать.

## 18. Event participants

### GET `/api/v1/events/{event_id}/participants`

Возвращает список participants с pagination после проверки `event.read` и применимого scope/object policy.

### POST `/api/v1/events/{event_id}/participants`

Записывает человека на мероприятие уполномоченным пользователем с `event.manage` и применимым scope/object policy.

`event.participant.read` и `event.participant.manage` не являются отдельными permissions.

## 19. Self registration

**TH-0108.2 / ADR-0037 (реализовано; заменяет предыдущий зарезервированный контракт этого раздела).**

Ранее этот раздел резервировал `POST /api/v1/me/events/{event_id}/registration` и блокировал реализацию до принятия детерминированной registration policy. ADR-0037 является более новым принятым решением: он задаёт MVP-политику self-registration явно и вводит canonical endpoint под общим префиксом `/api/v1/events` (не `/api/v1/me/...`) — старый путь никогда не был реализован и не используется.

### POST `/api/v1/events/{event_id}/participation`

Регистрирует **самого аутентифицированного пользователя** (через его canonical Person) на опубликованное мероприятие.

Request body отсутствует. Клиент не передаёт `person_id` ни в каком виде — Person определяется исключительно из authenticated principal. Любое поле `person_id` в теле запроса игнорируется backend.

Eligibility (ADR-0037 §3/§5), в этом порядке:

1. `Event.status == published`; иначе `409 event_not_published`. Для `draft`/`completed`/`cancelled`/`archived` регистрация недоступна.
2. Person должен иметь active `ClubMembership` в Club события; иначе `403 not_eligible_for_event`.
3. Если у Event есть 1+ active `EventGroupTarget` — Person должен иметь active `GroupMembership` хотя бы в одной из целевых Group; иначе `403 not_eligible_for_event`. Если целевых Group нет — Event club-wide, и шага 3 достаточно пройти шаг 2.

Операция идемпотентна: повторный вызов при уже `registered` не создаёт вторую запись (unique `(event_id, person_id)` остаётся единственным persistence invariant, без изменений); вызов при существующей `cancelled` записи восстанавливает её в `registered` вместо создания дубликата.

Не создаёт: `Attendance`, `GroupMembership`, `ClubMembership`, `GuardianRelationship`, платёж, доставку уведомления, `User`, `UserRoleAssignment`. Не требует и не проверяет никакой отдельный Event permission — self-registration является self-service операцией (ADR-0037 §12), а не операцией, проходящей через общий `Authorizer`/scope engine.

Response: `EventParticipationOut` (`id`, `event_id`, `person_id`, `registration_status`, `created_at`, `updated_at`).

Guardian registration ребёнка не входит в этот MVP-срез (ADR-0037 §11) — self-registration относится только к собственной Person аутентифицированного пользователя, независимо от роли guardian.

## 20. Cancel self registration

### DELETE `/api/v1/events/{event_id}/participation`

Отменяет **собственную** регистрацию аутентифицированного участника: `EventParticipation.registration_status = cancelled`. Никогда не отменяет регистрацию другого Person.

Строка `EventParticipation` не удаляется физически — сохраняется как историческая запись с `registration_status = cancelled` (существующий паттерн проекта).

Идемпотентна: отсутствие записи вообще и повторная отмена уже `cancelled` записи — оба silent no-op, `204 No Content`, без ошибки.

## 21. Participant status

### POST `/api/v1/events/{event_id}/participants/{person_id}/status`

Изменяет registration status уполномоченным пользователем с `event.manage` и применимым scope/object policy.

Поддерживаемые reference states:

- `invited`;
- `registered`;
- `waitlisted`;
- `declined`;
- `removed`.

Полный transition graph, capacity/waitlist semantics и deadline rules пока не определены и не должны изобретаться реализацией.

## 22. Attendance list

### GET `/api/v1/events/{event_id}/attendance`

Возвращает attendance для occurrence после проверки `attendance.read` и scope/object policy.

Доступ:

- instructor/leader — только в рамках applicable `own_events`/`own_groups` scope;
- admin — согласно назначенному `attendance.read` scope;
- участник — только собственный статус (`self`);
- guardian — только статус/данные связанных детей (`children`) по active GuardianRelationship.

## 23. Mark attendance

### PUT `/api/v1/events/{event_id}/attendance/{person_id}`

Требуется `attendance.update` и применимый scope/object policy.

Idempotent upsert текущего attendance record.

Request concept:

```json
{
  "status": "present",
  "absence_reason": null,
  "comment": null
}
```

Изменение attendance после закрытия мероприятия требует `attendance.update`, обязательную причину correction и audit.

## 24. Bulk attendance

### PUT `/api/v1/events/{event_id}/attendance`

Требуется `attendance.update` и применимый scope/object policy.

Пакетная запись attendance для группы/списка.

Операция должна быть транзакционной для одной логической команды либо явно возвращать частичные результаты с validation errors.

## 25. Attendance correction

### POST `/api/v1/events/{event_id}/attendance/{person_id}/corrections`

Используется после закрытия attendance window.

Требуется `attendance.update`; отдельного `attendance.correct` permission нет.

Correction содержит:

- previous status;
- new status;
- reason — обязательно;
- actor;
- timestamp.

## 26. Group scheduling

### GET `/api/v1/groups/{group_id}/schedule`

Возвращает будущие и недавние мероприятия группы согласно access policy. Фильтрация не может расширять исходный scope requester.

## 27. Instructor schedule

### GET `/api/v1/me/instructor-schedule`

Доступно пользователю с применимым Event permission.

Возвращает assigned events / events in applicable own groups, а не все события клуба. Конкретный scope определяется `own_events`/`own_groups`.

## 28. Conflict detection

### GET `/api/v1/events/conflicts`

Возвращает **информационные, non-blocking конфликты** между авторизованными конкретными `Event`/`EventOccurrence` за указанный период.

#### Query parameters

`from` и `to` обязательны и задают канонический интервал **`[from,to)`**. Значения должны быть timezone-aware RFC 3339 timestamps и нормализуются к UTC instant.

Дополнительные фильтры являются только narrowing-фильтрами уже авторизованного набора:

- `user_id` — ограничивает конфликтные отношения указанным User;
- `group_id` — ограничивает конфликтные отношения указанной Group.

Фильтр не может расширять scope/object authorization requester.

#### Conflict domains

MVP поддерживает три domain/reason values:

- `instructor` — один User имеет применимую staffing/responsibility relationship в обоих объектах;
- `group` — одна Group явно targeted в обоих объектах;
- `participant` — один Person имеет применимую EventParticipation в обоих объектах.

GroupMembership alone, GuardianRelationship alone, role names, `created_by` и shared `club_id` не создают conflict relationship.

Room/location/equipment/generic resource conflicts не входят в MVP.

#### Time and concrete objects

Conflict требует пересечения effective intervals:

`max(a.start_at, b.start_at) < min(a.end_at, b.end_at)`.

Touching boundaries do not conflict.

Обычный Event может конфликтовать с EventOccurrence. Два EventOccurrence также могут конфликтовать. Для recurring объектов используются persisted materialized occurrences; RRULE не вычисляется отдельным conflict engine.

Occurrence reschedule/exception учитывается по effective start/end после применения override/exception.

#### Status participation

Operational conflict candidates:

Event:

- `published`;
- `in_progress`.

EventOccurrence:

- `scheduled`;
- `in_progress`.

`draft`, `completed`, `cancelled`, `archived` Event и `completed`, `cancelled` EventOccurrence не являются operational conflicts.

#### Authorization and IDOR

Conflict detection uses existing `event.read` permission and existing scope/object policy. No new permission or scope is introduced.

Authorization is applied before result count, pagination and serialization. A conflict involving an inaccessible opposing object is not returned and must not reveal that object's existence through ID, title, type, time, count or pagination metadata.

`occurrence.club_id` alone is never sufficient for non-`all` access.

#### Response

Each conflict is a **derived** result, not a persisted business entity. Its stable identity is derived from the canonical unordered pair of concrete object IDs plus the conflict domain.

The result identifies:

- `id` — deterministic conflict identity;
- first concrete object: `object_type`, `object_id`;
- second concrete object: `object_type`, `object_id`;
- `domain`;
- `overlap_start_at`;
- `overlap_end_at`;
- for recurring objects, the governing series/version where already part of the public occurrence contract.

The pair is ordered deterministically. Collection ordering is deterministic by `overlap_start_at ASC, id ASC`.

The endpoint uses the standard API v1 pagination envelope and common error/envelope conventions. Exact JSON field casing follows the existing API conventions.

#### Recurrence materialization

If `to` exceeds the currently materialized recurrence horizon, the existing ADR-0015/ADR-0028 materialization mechanism may extend materialization to satisfy the query. Conflict detection does not generate recurrence instances itself.

#### Mutation behavior

MVP conflict detection is advisory only. Event, EventSeries and EventOccurrence mutations are not rejected solely because a conflict exists. No automatic rescheduling is performed.

There is no Club-configurable conflict severity policy in MVP.

## 29. Notifications side effects

Изменения, которые потенциально меняют обязательства участников, должны создавать notification events:

- cancellation;
- reschedule;
- location change;
- instructor change;
- registration approval/rejection;
- attendance-related notification по настройке клуба.

Доставка уведомления не является частью транзакции event mutation.

## 30. Authorization

Канонический Event/attendance permission mapping определён ADR-0020:

| Operation | Permission |
|---|---|
| Event read, participant read, calendar projection | `event.read` |
| Event create | `event.create` |
| Event update | `event.update` |
| Recurrence/occurrence scheduling management | `event.update` / `event.manage` согласно операции |
| Event cancel | `event.cancel` |
| Event archive | `event.manage` |
| Participant management | `event.manage` |
| Attendance read | `attendance.read` |
| Attendance update | `attendance.update` |
| Attendance correction | `attendance.update` + mandatory reason + audit |
| Conflict query | `event.read` |
| Self-registration (`POST/DELETE .../participation`) | none — self-service, gated by identity + active ClubMembership + targeted GroupMembership + Event lifecycle (ADR-0037 §12); never `event.read`/any Event permission |

Canonical scopes:

- `all`;
- `own_groups`;
- `own_events`;
- `self`;
- `children`;
- `none`.

`assigned_events` is an alias of `own_events`; `own_records` is not a scope.

Object-level policy is mandatory. A role name alone does not grant unrestricted Event visibility.

### Scope semantics for first implementation slice

- `all` — all eligible club Events after permission, feature and object-policy checks;
- `own_groups` — Events targeted to groups for which requester has an applicable responsible relationship;
- `own_events` — Events explicitly assigned/responsible to requester;
- `self` — requester's own participation/registration or explicitly self-visible Event data;
- `children` — data for Persons linked through active GuardianRelationship and otherwise eligible under object policy;
- `none` — no access.

## 31. Event document requirements — PLANNED (TH-0117 / ADR-0040)

**Nothing in this section is implemented.** This is the canonical planned contract fixed by the documentation-only baseline ADR-0040 (TH-0117.0) — none of the endpoints or concepts below exist in current code. `Document` is not implemented either (see `docs/05-api/people-api.md` §32, also planned).

`Document` is deliberately **not** coupled directly to `Event`. Instead, a separate `EventDocumentRequirement` concept (`event_id`, `document_type`, `required` — ADR-0040 §5) expresses "this Event requires document type X from its participants."

- **List/set requirements** — `GET/POST /events/{event_id}/document-requirements` (planned) — minimal fields `document_type`, `required`; at most one requirement row per `(event_id, document_type)`.
- **Check a participant's requirement status** — `GET /events/{event_id}/document-requirements/{requirement_id}/status` or an equivalent per-participant projection (exact shape not fixed by this ADR) — a read-only computation, never a persisted row, producing exactly one of `valid`, `missing`, `expired` (ADR-0040 §5) for a given participant. `missing` is never a persisted `Document.status` — it is this check's own result when no Document of the required type exists at all for that Person. A current Document version with stored `status = 'revoked'` maps to the derived result `expired`; `revoked` remains a distinct persisted Document lifecycle state and is not exposed as a fourth requirement-check result. Historical valid versions do not override a current revoked version.
- **Competition/event document package export** — before any export that packages participant documents for an Event, the exporting user must receive an explicit warning naming every participant/requirement pair that currently resolves to `missing` or `expired`. This ADR does not fix the export's exact endpoint, format, or UI.

This does **not** change existing Event or EventParticipation authorization (§30 above, ADR-0020/ADR-0023/ADR-0037) in any way — `EventDocumentRequirement` management and checking use the dedicated `document.read`/`document.manage`/`document.export` permissions (ADR-0040 §6, `docs/05-api/people-api.md` §32), never `event.read`/`event.manage` alone. An Event permission is necessary to see the Event itself, but not sufficient to see participant document content or a `missing`/`expired` roster derived from it.
