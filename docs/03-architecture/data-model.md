# TourCRM — Logical Data Model

## 1. Назначение

Документ определяет логическую модель данных TourCRM. Это контракт между доменной моделью, PostgreSQL и application services.

## 8. Recurrence domain

Для регулярного расписания не следует создавать бесконечные Event заранее.

### RecurrenceRule

Описывает правило повторения. В согласованной модели используется RFC 5545-compatible RRULE. В MVP поддерживаются `FREQ`, `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`, `COUNT`, `UNTIL`; входной `UNTIL` нормализуется в `series_end` и не дублируется в канонической модели.

### EventSeries

EventSeries — отдельная от Event сущность и версия логического повторяющегося расписания, принадлежащая одному Club.

Канонические поля:

- id;
- club_id;
- name;
- description;
- event_type;
- series_start;
- series_end nullable;
- occurrence_limit nullable;
- recurrence_rule;
- timezone;
- status;
- created_by;
- updated_by;
- created_at;
- updated_at.

`timezone` обязателен и является IANA timezone. `series_end` и `occurrence_limit` могут использоваться одновременно; действует первое достигнутое ограничение. При отсутствии обоих ограничений серия бессрочная.

EventSeries не является Event и не содержит обязательных связей с группами, инструкторами или участниками. Эти связи относятся к конкретному EventOccurrence.

При операции "this and following" создаётся новая версия EventSeries с точки выбранного будущего `scheduled` occurrence. Историческая версия сохраняется, а последовательность версий одной логической серии должна быть восстанавливаема.

### EventOccurrence

EventOccurrence — конкретное materialized проведение EventSeries. Он является самостоятельной операционной сущностью и не требует промежуточного Event.

Канонические поля:

- id;
- series_id;
- club_id;
- name;
- description;
- event_type;
- starts_at;
- ends_at;
- timezone;
- status;
- cancellation_reason nullable;
- created_by;
- updated_by;
- created_at;
- updated_at.

`name`, `description` и `event_type` являются snapshot данных серии. `starts_at` и `ends_at` — фактическое время конкретного occurrence. `timezone` сохраняет контекст расписания occurrence.

Lifecycle:

```text
scheduled
├── in_progress → completed
└── cancelled
```

`completed` и `cancelled` терминальны. Перенос не является отдельным статусом.

Операционные связи относятся непосредственно к occurrence: `EventGroupTarget`, `EventStaffAssignment`, `EventParticipation` и будущая `Attendance`. Они не наследуются автоматически из EventSeries.

### EventOccurrenceException

Отдельная сущность для текущего отклонения occurrence от правила серии. Для одного occurrence допускается не более одной актуальной exception. История действий хранится через Audit.

MVP-типы: `rescheduled`, `cancelled`.

`rescheduled` хранит исходные и эффективные дату/время. `cancelled` требует причину. Отмена не удаляет occurrence. Перенос сохраняет его `id`.

### Materialization

Материализация соответствует ADR-0015:

- default horizon: 180 дней вперёд;
- horizon может расширяться при запросе периода за пределами материализованного диапазона;
- materialization идемпотентна;
- повторный запуск не создаёт дубликаты;
- существующие occurrences не удаляются автоматически;
- occurrence с operational history сохраняется;
- стабильный occurrence ID сохраняется при изменениях серии.

Создание/изменение серии должно сделать ближайшие occurrences доступными сразу; поддержание полного горизонта выполняется background materialization. Concurrency должна быть безопасной за счёт DB-level uniqueness/idempotency, а не только application lock.

При `this and following` выбранный будущий `scheduled` occurrence сохраняет свой `id`, но переводится на новую версию EventSeries; следующие occurrences принадлежат новой версии. Прошедшие occurrences не изменяются. `cancelled` occurrence не может быть точкой начала новой версии.

Materializer не должен автоматически удалять или отменять уже созданные будущие occurrences только из-за `paused`/`cancelled` Series; отмена конкретного occurrence является отдельной явной операцией.

## 9. Trip domain

### Trip

Расширение Event 1:1.

`Event 1:0..1 Trip`

Поля:

- event_id;
- tourism_type;
- difficulty_category nullable;
- region nullable;
- route_id nullable;
- planned_distance nullable;
- actual_distance nullable;
- planned_duration nullable;
- actual_duration nullable;
- leader_person_id;
- result_status;
- notes.

### TripParticipant

Специализированная связь участника с походом.

`Trip 1:N TripParticipant`
`Person 1:N TripParticipant`

Поля:

- trip_id;
- person_id;
- role_in_trip;
- actual_participation;
- completed_distance nullable;
- result nullable;
- notes nullable.

## 10. Route domain

### Route

Логический маршрут, который может использоваться несколькими мероприятиями.

### RoutePoint

`Route 1:N RoutePoint`

Поля:

- route_id;
- sequence;
- latitude;
- longitude;
- elevation nullable;
- name nullable;
- point_type nullable;
- description nullable.

### RouteFile

Метаданные внешнего/файлового объекта GPX.

`Route 1:N RouteFile`

Исходный GPX не хранится непосредственно в PostgreSQL blob без отдельного ADR.

## 11. Tourist profile

### TouristProfile

`Person 1:0..1 TouristProfile`

Хранит предпочтительно производные/описательные данные.

Первичные факты о походах находятся в Trip/TripParticipant.

Для агрегатов допускается материализованное хранение, если предусмотрен безопасный механизм пересчёта.

### TourismType

Справочник видов туризма.

### DifficultyCategory

Справочник/enum сложности.

## 12. Skills / Qualifications / Achievements

### Skill

Справочник навыков.

### PersonSkill

Связь Person ↔ Skill с уровнем/статусом и датой оценки.

### Qualification

Справочник квалификаций/разрядов.

### PersonQualification

История квалификации конкретного человека с датами и подтверждающими документами.

### Achievement

Каталог достижений.

### AchievementAward

Связь Person ↔ Achievement.

Поля:

- person_id;
- achievement_id;
- awarded_at;
- award_method manual/automatic;
- awarded_by nullable;
- source_event_id nullable;
- metadata;
- revoked_at nullable;
- revoke_reason nullable.
