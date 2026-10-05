# TourCRM — Logical Data Model

## 1. Назначение

Документ определяет логическую модель данных TourCRM. Это контракт между доменной моделью, PostgreSQL и application services.

## 8. Recurrence domain

Для регулярного расписания не следует создавать бесконечные Event заранее.

### RecurrenceRule

Описывает правило повторения. В согласованной модели используется RFC 5545-compatible RRULE. В MVP поддерживаются `FREQ`, `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`, `COUNT`, `UNTIL`; входной `UNTIL` нормализуется в `series_end` и не дублируется в канонической модели.

UI работает со структурированными параметрами повторения. Произвольный ввод raw RRULE не является MVP-возможностью. Backend формирует и валидирует каноническое RRULE.

### EventSeries

EventSeries — отдельная от Event сущность и версия логического повторяющегося расписания, принадлежащая одному Club.

Канонические поля:

- id;
- root_series_id;
- version;
- supersedes_series_id nullable;
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

Для первой версии `root_series_id = id`, `version = 1`, `supersedes_series_id = NULL`. Для каждой следующей версии `root_series_id` сохраняется, `version` увеличивается на единицу, а `supersedes_series_id` указывает на непосредственно предыдущую версию.

Текущая версия — терминальная версия цепочки, то есть версия, которую ещё не supersede'нула следующая версия. Отдельное `is_current` не хранится.

Внутри одного `root_series_id` комбинация `(root_series_id, version)` уникальна, и у одной версии не может быть более одного непосредственного successor.

`timezone` обязателен и является IANA timezone. `series_end` и `occurrence_limit` могут использоваться одновременно; действует первое достигнутое ограничение. При отсутствии обоих ограничений серия бессрочная с точки зрения recurrence definition и ограничивается только lifecycle/materialization policy.

EventSeries не является Event и не содержит обязательных связей с группами, инструкторами или участниками. Эти связи относятся к конкретному EventOccurrence.

При операции "this and following" создаётся новая версия EventSeries с точки выбранного будущего `scheduled` occurrence. Историческая версия сохраняется, а последовательность версий одной логической серии должна быть восстанавливаема.

Создание новой версии является транзакционной операцией. Источник изменения проверяется на актуальность под DB-level lock. Если исходная версия уже получила successor, устаревшая операция отклоняется с `409 Conflict`; автоматического rebase нет.

### EventOccurrence

EventOccurrence — конкретный операционный instance: либо materialized проведение EventSeries (recurring), либо (ADR-0033) единственный occurrence обычного, не повторяющегося Event, создаваемый вместе с ним и синхронизируемый с ним. В любом случае это самостоятельная операционная сущность со своими snapshot-полями, а не голый bridge-row.

Канонические поля:

- id;
- series_id nullable — только для recurring;
- event_id nullable — только для non-recurring (ADR-0033); ровно одно из series_id/event_id должно быть заполнено (CHECK);
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

Для идемпотентной материализации система должна иметь детерминированный ключ конкретного recurrence occurrence внутри версии серии. PostgreSQL uniqueness должна защищать от повторного создания одной и той же точки расписания при конкурентной materialization.

Lifecycle:

```text
scheduled
├── in_progress → completed
└── cancelled
```

`completed` и `cancelled` терминальны. Перенос не является отдельным статусом.

Операционные связи относятся непосредственно к occurrence: `EventGroupTarget`, `EventStaffAssignment`, `EventParticipation` и `Attendance` (ADR-0032, Issue #94 / TH-0087 — `Attendance.occurrence_id`, `NOT NULL` FK на `event_occurrences.id`, каноническая identity `(occurrence_id, person_id)`; работает как для recurring, так и для non-recurring Event через его единственный linked occurrence, ADR-0033 — см. `domain-model.md` §11). Они не наследуются автоматически из EventSeries.

### EventOccurrenceException

Отдельная сущность для текущего отклонения occurrence от правила серии. Для одного occurrence допускается не более одной актуальной exception. История действий хранится через Audit.

Канонические поля:

- id;
- occurrence_id UNIQUE;
- exception_type;
- original_start_at;
- effective_start_at nullable;
- effective_end_at nullable;
- overrides JSONB;
- cancellation_reason nullable;
- created_by;
- created_at;
- updated_at.

MVP-типы: `rescheduled`, `cancelled`.

`overrides` содержит только backend allow-list разрешённых полей EventOccurrence. Каждый override проходит обычную типовую и доменную валидацию. JSONB не является обходом доменных правил.

`rescheduled` хранит исходные и эффективные дату/время и не меняет lifecycle status: occurrence остаётся `scheduled`. `cancelled` требует причину и переводит occurrence в терминальное состояние `cancelled`. Отмена не удаляет occurrence. Перенос сохраняет его `id`.

### Versioning and occurrence rebinding

При "this and following":

- создаётся новый EventSeries version;
- выбранный будущий `scheduled` occurrence сохраняет свой `id`;
- если выбранный occurrence уже materialized, он rebinding'ится на новую версию и получает соответствующий snapshot новой версии;
- следующие occurrences относятся к новой версии;
- прошедшие occurrences не изменяются;
- cancelled occurrence не может быть boundary для новой версии; выбирается следующий `scheduled` occurrence.

Новый occurrence не создаётся только ради rebinding существующего occurrence.

### Materialization

Материализация соответствует ADR-0015 и ADR-0028:

- default horizon: 180 дней вперёд;
- horizon может расширяться при запросе периода за пределами материализованного диапазона;
- materialization идемпотентна;
- повторный запуск не создаёт дубликаты;
- существующие occurrences не удаляются автоматически;
- occurrence с operational history сохраняется;
- стабильный occurrence ID сохраняется при изменениях серии;
- `paused` останавливает создание новых occurrences;
- `cancelled` Series навсегда останавливает дальнейшую генерацию;
- paused/cancelled Series не приводит автоматически к удалению или отмене уже materialized будущих occurrences.

Создание/изменение серии должно сделать ближайшие occurrences доступными сразу; поддержание полного горизонта выполняется background materialization. Concurrency должна быть безопасной за счёт DB-level uniqueness/locking/idempotency, а не только application lock.

## 9. Trip domain

Бизнес-семантика Trip и его туристских фактов определяется `docs/04-modules/trips-and-tourist-profile.md`; этот раздел описывает только логическую структуру и не является отдельным источником бизнес-правил. При расхождении приоритет имеет модульный документ.

### Trip

Туристское расширение Event.

`Event 1:0..1 Trip`

Реализованные поля (Trip Foundation):

- event_id — первичный ключ и обязательная связь с Event типа `trip`;
- created_at;
- updated_at.

Собственного lifecycle/status у Trip нет — используется lifecycle Event.

Туристские факты Trip (семантика — модульный документ §3–§12; физическое хранение определяется при реализации):

- TourismType — 0..1 ссылка на справочник TourismType, не свободный текст;
- Official Difficulty — не более одной официальной классификации (режим `NONE`/`DEGREE`/`CATEGORY`/`WEEKEND`, значение и обязательное основание/source);
- Geography — 0..1 ссылка на Country и 0..1 ссылка на Region, не свободный текст;
- Duration Classification — `ONE_DAY`/`MULTI_DAY`/`UNCLASSIFIED`, отдельно от времени начала/окончания Event;
- Result — `COMPLETED`/`PARTIALLY_COMPLETED`/`NOT_COMPLETED`, отдельно от lifecycle Event;
- Route — физическое описание маршрута Trip (§10).

Прежние поля логической модели `tourism_type` (строка), `difficulty_category`, `region` (строка), `route_id`, `planned_distance`/`actual_distance`, `planned_duration`/`actual_duration`, `leader_person_id`, `result_status`, `notes` не являются каноническими: туристские факты моделируются как указано выше, плановая/фактическая дистанция относится к Planned/Actual представлениям Route, а продолжительность, руководитель и заметки требуют отдельного решения.

### TripParticipant

Специализированное расширение EventParticipation для Trip.

`EventParticipation 1:0..1 TripParticipant`

Реализованные поля:

- event_participation_id — первичный ключ и связь с EventParticipation;
- event_id — Event этого Trip;
- actual_participation;
- created_at;
- updated_at.

Person определяется через EventParticipation и не дублируется; собственного registration status у TripParticipant нет. Роль в походе, пройденная дистанция участника, индивидуальный результат и заметки не являются частью текущего контракта и требуют отдельного решения.

## 10. Route domain

Семантика — `docs/04-modules/trips-and-tourist-profile.md` §11–§12.

### Route

Каноническое физическое описание маршрута, связанное с Trip. Route не является классификатором и не содержит и не определяет TourismType, Official Difficulty, Geography, Duration Classification или Result.

Route содержит раздельные представления:

- Planned — плановая геометрия/точки, planned distance, planned elevation gain (если доступны данные высоты), необязательный канонический Planned GPX;
- Actual — фактическая геометрия/точки, actual distance, actual elevation gain (если доступны данные высоты), необязательный канонический Actual GPX.

Planned и Actual характеристики не смешиваются и не заменяют друг друга; технические характеристики каждого представления воспроизводимы из его собственного источника.

### RoutePoint

`Route 1:N RoutePoint`

Точка принадлежит конкретному представлению маршрута — Planned или Actual; плановые и фактические точки не смешиваются.

Поля:

- route_id;
- sequence;
- latitude;
- longitude;
- elevation nullable;
- name nullable;
- point_type nullable;
- description nullable.

### RouteFile (GPX)

Метаданные внешнего/файлового объекта GPX.

`Route 1:N RouteFile`

- каждый GPX имеет явную роль: `PLANNED` или `ACTUAL`;
- у Route не более одного канонического Planned GPX и не более одного канонического Actual GPX;
- дополнительные файлы являются provenance/архивными артефактами и не становятся каноническими автоматически;
- после завершения Trip канонический Actual GPX — исторический факт; замена — только через будущий Historical Correction Workflow;
- отдельная подсистема версионирования GPX не вводится.

Исходный GPX не хранится непосредственно в PostgreSQL blob без отдельного ADR.

## 11. Tourist profile

### TouristProfile

`Person 1:0..1 TouristProfile`

Хранит предпочтительно производные/описательные данные.

Первичные факты о походах находятся в Trip/TripParticipant.

Для агрегатов допускается материализованное хранение, если предусмотрен безопасный механизм пересчёта.

### TourismType

Справочник видов туризма (`code`, `name`, `active`); деактивация вместо удаления. Значения каталога не утверждены. Семантика — `docs/04-modules/trips-and-tourist-profile.md` §3.

### Official Difficulty

Структурированная официальная классификация сложности Trip (режим, значение, основание/source), а не универсальный enum: применимость режимов/значений зависит от TourismType и нормативного источника. Семантика — `docs/04-modules/trips-and-tourist-profile.md` §4.

### Country / Region

Отдельные справочники географии: Region принадлежит ровно одной Country; деактивация вместо удаления; provenance записи (`source_type`, `source_reference`). Семантика — `docs/04-modules/trips-and-tourist-profile.md` §9.

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
