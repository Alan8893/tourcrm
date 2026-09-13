# TourCRM — Events & Schedule API

## 1. Назначение

Данный документ определяет API-контракт календаря, мероприятий, расписания, серий повторяющихся занятий, регистрации участников и посещаемости.

## 2. Основная модель

`Event` — логическое мероприятие.

`EventSeries` — правило повторения мероприятия.

`EventOccurrence` — конкретное запланированное проведение мероприятия.

`EventParticipation` — участие человека в конкретном occurrence.

Посещаемость хранится относительно occurrence, а не только series/event.

## 3. Общий префикс

`/api/v1/events`

Attendance:

`/api/v1/attendance`

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

## 7. Update event

### PATCH `/api/v1/events/{event_id}`

Для одиночного события изменяет будущие свойства.

При изменении recurring event необходимо явно указывать scope изменения:

- this occurrence;
- this and following;
- entire series.

Backend должен отклонять неоднозначные запросы.

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

Поддерживает filtering by:

- current user;
- groups;
- event types;
- date range.

Фильтры не могут расширять доступ.

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

### POST `/api/v1/me/events/{event_id}/registration`

Endpoint зарезервирован контрактом, но **не входит в реализационно готовый первый срез**.

Реализация заблокирована до принятия детерминированной registration policy, включающей как минимум:

- event self-registration flag;
- registration window;
- membership state;
- age/group restrictions;
- capacity/waitlist rules;
- допустимые registration status transitions;
- cancellation deadline.

Нельзя выводить эти правила из роли пользователя или из названия статуса без отдельного принятого правила.

## 20. Cancel self registration

### DELETE `/api/v1/me/events/{event_id}/registration`

Endpoint зарезервирован, но реализация также блокируется до принятия детерминированной registration policy и cancellation deadline.

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
  "note": null
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

Принимает период и optional actor/resource IDs.

Conflict detection должна учитывать:

- overlapping times;
- instructor assignment;
- room/location/resource, если они моделируются;
- participant constraints только когда явно включены в policy.

Conflict warning может быть non-blocking, если бизнес-правила позволяют.

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
- `children` — Event data related to persons connected through an active GuardianRelationship;
- `none` — no access.

Guardian `children` scope never becomes unrestricted `all`. Instructor role alone never becomes unrestricted Event access.

## 31. Validation

Backend проверяет:

- `start_at < end_at`;
- валидную timezone;
- event type;
- существование и принадлежность объектов Club;
- instructor availability policy;
- group status;
- registration policy where such policy has been accepted;
- recurrence validity;
- no invalid historical rewrite;
- cancellation reason;
- attendance status consistency.

Until registration policy is accepted, self-registration operations are not implemented.

## 32. Audit

Audit обязателен для:

- create/update/cancel/archive event;
- recurrence changes;
- occurrence exceptions;
- participant status changes;
- attendance changes;
- attendance corrections;
- instructor/leader assignment changes.

## 33. Acceptance Criteria

1. Нельзя изменить recurring schedule неоднозначно.
2. Прошедшая посещаемость сохраняется при переносе мероприятия.
3. Attendance относится к конкретному occurrence.
4. Самостоятельная регистрация реализуется только после принятия и соблюдения registration policy.
5. Participant list и calendar соблюдают canonical permission/scope/object policy.
6. Parent видит только разрешённые данные детей через active GuardianRelationship.
7. Cancellation/critical schedule changes могут инициировать notification events.
8. iCalendar feed не раскрывает данные, недоступные requester.
9. Недопустимые status transitions отклоняются.
10. Attendance correction после закрытия требует `attendance.update`, reason и audit.
11. Все изменения значимых сущностей audit'ed.
12. API соблюдает общий error/pagination/idempotency contract.
13. API не использует неканонические permissions `event.archive`, `event.participant.read`, `event.participant.manage`, `attendance.correct`, `event.schedule.manage`.
