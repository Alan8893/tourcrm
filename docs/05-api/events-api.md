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

Backend обязан ограничивать результаты по permission/scope requester.

## 5. Event details

### GET `/api/v1/events/{event_id}`

Возвращает Event и связанные данные в пределах permission.

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

Статусы определены в business rules.

Недопустимые переходы отклоняются.

Cancellation reason обязателен при отмене.

## 9. Delete/archive

### POST `/api/v1/events/{event_id}/archive`

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

Возвращает recurring configuration и список применённых overrides/exceptions согласно pagination/period limits.

## 12. Series update

### PATCH `/api/v1/events/series/{series_id}`

Изменение серии требует explicit update scope.

Исторические occurrences, которые уже прошли, не должны непреднамеренно переписывать историю.

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

Возвращает calendar projection для requester.

Поддерживает filtering by:

- current user;
- groups;
- event types;
- date range.

## 17. iCalendar

### GET `/api/v1/events/calendar.ics`

Предоставляет read-only iCalendar feed в соответствии с доступом requester.

Feed token должен быть отдельным секретом и не должен совпадать с auth/session token.

Отозванный token перестаёт действовать.

## 18. Event participants

### GET `/api/v1/events/{event_id}/participants`

Возвращает список participants с pagination.

### POST `/api/v1/events/{event_id}/participants`

Записывает человека на мероприятие, если политика допускает самостоятельную/инструкторскую регистрацию.

## 19. Self registration

### POST `/api/v1/me/events/{event_id}/registration`

Участник может зарегистрироваться, только если:

- event открыт для self-registration;
- membership active;
- возраст/группа/другие ограничения соблюдены.

## 20. Cancel self registration

### DELETE `/api/v1/me/events/{event_id}/registration`

Отменяет собственную регистрацию в допустимый период.

После deadline cancellation может быть запрещена.

## 21. Participant status

### POST `/api/v1/events/{event_id}/participants/{person_id}/status`

Изменяет registration status уполномоченным пользователем.

Поддерживаемые состояния должны быть задокументированы enum/reference data.

## 22. Attendance list

### GET `/api/v1/events/{event_id}/attendance`

Возвращает attendance для occurrence.

Доступ:

- instructor/leader с соответствующим scope;
- admin;
- участник — только собственный статус;
- guardian — только по разрешённой parent scope policy.

## 23. Mark attendance

### PUT `/api/v1/events/{event_id}/attendance/{person_id}`

Idempotent upsert текущего attendance record.

Request concept:

```json
{
  "status": "present",
  "absence_reason": null,
  "note": null
}
```

Изменение attendance после закрытия мероприятия требует permission и audit reason.

## 24. Bulk attendance

### PUT `/api/v1/events/{event_id}/attendance`

Пакетная запись attendance для группы/списка.

Операция должна быть транзакционной для одной логической команды либо явно возвращать частичные результаты с validation errors.

## 25. Attendance correction

### POST `/api/v1/events/{event_id}/attendance/{person_id}/corrections`

Используется после закрытия attendance window.

Correction содержит:

- previous status;
- new status;
- reason;
- actor;
- timestamp.

## 26. Group scheduling

### GET `/api/v1/groups/{group_id}/schedule`

Возвращает будущие и недавние мероприятия группы согласно access policy.

## 27. Instructor schedule

### GET `/api/v1/me/instructor-schedule`

Доступно пользователю с instructor permissions.

Возвращает assigned events, а не все события клуба.

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

Примеры permissions:

- `event.read`;
- `event.create`;
- `event.update`;
- `event.cancel`;
- `event.archive`;
- `event.participant.read`;
- `event.participant.manage`;
- `attendance.read`;
- `attendance.update`;
- `attendance.correct`;
- `event.schedule.manage`.

Scope:

- all club;
- own groups;
- assigned events;
- self;
- children.

## 31. Validation

Backend проверяет:

- `start_at < end_at`;
- валидную timezone;
- event type;
- существование и принадлежность объектов Club;
- instructor availability policy;
- group status;
- registration window;
- recurrence validity;
- no invalid historical rewrite;
- cancellation reason;
- attendance status consistency.

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
4. Самостоятельная регистрация соблюдает registration policy.
5. Participant list и calendar соблюдают scope.
6. Parent видит только разрешённые данные детей.
7. Cancellation/critical schedule changes могут инициировать notification events.
8. iCalendar feed не раскрывает данные, недоступные requester.
9. Недопустимые status transitions отклоняются.
10. Attendance correction после закрытия требует permission и reason.
11. Все изменения значимых сущностей audit'ed.
12. API соблюдает общий error/pagination/idempotency contract.
