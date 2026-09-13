# TourCRM — Domain Model

## 1. Назначение

Документ определяет каноническую доменную модель TourCRM. Он является источником требований для последующего проектирования базы данных, API и прикладных сервисов.

## 2. Базовая модель идентичности

TourCRM разделяет четыре понятия:

`Person` — физическое лицо.

`User` — учётная запись для доступа к системе.

`ClubMembership` — участие Person в конкретном Club.

`RoleAssignment` — полномочие User в системе/клубе.

Они не должны смешиваться в одной сущности.

### Пример

Один человек может:

- иметь одну учётную запись;
- быть родителем двух детей;
- быть инструктором;
- одновременно иметь административную роль.

## 3. Club

### Назначение

Представляет конкретный туристский клуб.

### Основные атрибуты

- id;
- name;
- short_name;
- description;
- contacts;
- status;
- created_at;
- updated_at.

Даже при одной реальной организации сущность Club сохраняется для корректной границы домена и будущего масштабирования.

## 4. Person

### Назначение

Каноническая запись о физическом лице.

### Основные группы данных

- ФИО;
- дата рождения;
- контактные данные;
- пол — при необходимости и наличии обоснования;
- адрес — при необходимости;
- фотография;
- системные даты.

Медицинские данные, документы и туристские характеристики не должны без необходимости складываться непосредственно в Person.

## 5. User

### Назначение

Учетная запись аутентификации.

### Основные атрибуты

- id;
- person_id;
- login/email/identifier;
- password credential metadata;
- status;
- email_verified_at;
- last_login_at;
- created_at;
- updated_at.

Пароли в открытом виде никогда не хранятся.

## 6. ClubMembership

### Назначение

Историческая связь Person с Club.

### Основные атрибуты

- id;
- club_id;
- person_id;
- membership_type;
- status;
- joined_at;
- left_at;
- created_at;
- updated_at.

История членства должна сохраняться при переводе человека между группами и статусами.

## 7. Role / Permission

### Role

Логическое именованное множество permissions.

Базовые роли:

- admin;
- instructor;
- member;
- guardian.

### Permission

Атомарное право на действие над ресурсом.

Пример:

`event.read`
`event.create`
`attendance.update`
`finance.read`

Система должна поддерживать scope, например `all`, `own_groups`, `own_events`, `self`, `children`.

## 8. GuardianRelationship

### Назначение

Связывает законного представителя с участником.

### Каноническая persistence-модель

`GuardianRelationship` является Club-neutral Person-to-Person relationship. Она не содержит `club_id`; принадлежность и authorization в конкретном Club определяются через актуальные ClubMembership связанных лиц.

Канонические поля:

- id;
- guardian_person_id;
- child_person_id;
- relationship_type;
- status;
- is_primary_contact;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Канонические значения `status`:

- `active`;
- `inactive`;
- `revoked`.

`guardian_person_id` и `child_person_id` не могут совпадать. Исторические `inactive`/`revoked` записи сохраняются. Для одной пары guardian/child/type не допускаются дублирующиеся active relationships; одновременно допустима не более чем одна действующая primary-contact relationship для ребёнка.

Для Event authorization активной считается relationship, которая имеет `status = active` и действующий временной интервал. Доступ guardian к Event в Club требует также соответствующей ClubMembership policy для guardian и ребёнка.

Детальный persistence/authorization contract определён ADR-0023.

## 9. Group

### Назначение

Учебная/организационная группа клуба.

### Основные атрибуты

- id;
- club_id;
- name;
- description;
- status;
- valid_from;
- valid_to;
- created_at;
- updated_at.

`Group.status` остаётся строковым значением; канонический набор lifecycle-значений требует отдельного business-policy решения.

Группа принадлежит ровно одному Club.

### GroupMembership

Историческая ассоциация `ClubMembership` с `Group`.

Основные атрибуты:

- id;
- group_id;
- club_membership_id;
- valid_from;
- valid_to;
- membership_status;
- created_at;
- updated_at.

`person_id` непосредственно в GroupMembership не хранится: Person определяется через `club_membership_id -> ClubMembership.person_id`.

`is_primary` и `assigned_by` не входят в каноническую persistence-модель первой версии. Вопрос одновременной принадлежности к нескольким группам, основной группы и отдельной domain-сущности инициатора требует отдельного решения.

### GroupInstructorAssignment

Явная историческая связь User с Group, определяющая ответственность за группу.

Основные атрибуты:

- id;
- group_id;
- user_id;
- role_in_group;
- is_primary;
- valid_from;
- valid_to;
- created_at;
- updated_at.

`role_in_group` пока не имеет закрытого канонического enum; словарь должен быть согласован с моделью ответственности Event.

`is_primary` позволяет отличать основного ответственного от других явных назначений.

`own_groups` authorization опирается на активный GroupInstructorAssignment, а не только на роль instructor.

### Cross-Club ownership

Group принадлежит одному Club. GroupMembership допустим только если `Group.club_id == ClubMembership.club_id`. GroupInstructorAssignment допустим только если назначаемый User имеет active ClubMembership в Club группы.

Эти invariants являются частью cross-Club ownership contract и должны обеспечиваться на authoritative application/service boundary согласно ADR-0022.

## 10. Event

### Назначение

Унифицированное календарное мероприятие.

### Основные типы

- lesson;
- training;
- trip;
- competition;
- tour_slet;
- excursion;
- meeting;
- other.

### Основные атрибуты

- id;
- club_id;
- event_type;
- title;
- description;
- start_at;
- end_at;
- timezone;
- location_type;
- location_name;
- location_address;
- location_latitude;
- location_longitude;
- status;
- cancellation_reason;
- created_by;
- updated_by;
- created_at;
- updated_at.

Специализированные данные похода/турслёта не должны превращать Event в универсальную таблицу со всеми возможными полями. Для них используются специализированные сущности.

### EventParticipation

Связь Person с Event.

Текущая принятая persistence-модель определена Issue #51 в соответствии с ADR-0023 §4: `id`, `event_id`, `person_id`, `registration_status`, `created_at`, `updated_at`. Не более одной записи на пару Event/Person (DB-level ограничение).

Registration/attendance policy, self-registration, переходы `registration_status` и связанные бизнес-правила остаются отдельным deferred business decision (ADR-0020 §4) и не входят в текущий персистентный контракт — см. также `data-model.md` §"EventParticipation" (текущая модель и отложенные концепции) и §11 "Attendance" ниже.

### EventStaffAssignment

Явная историческая связь User с Event, используемая для Event responsibility и scope `own_events`.

Канонические поля:

- id;
- event_id;
- user_id;
- role_in_event;
- is_primary;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Multiple active staff assignments допустимы; не более одной active assignment для Event может иметь `is_primary = true`. Исторические назначения сохраняются после закрытия `valid_to`.

Назначение допустимо только если Person назначаемого User имеет active ClubMembership в Club мероприятия. `Event.created_by` не является заменой EventStaffAssignment.

### EventGroupTarget

Явная историческая связь Event с Group, определяющая целевую аудиторию мероприятия.

Канонические поля:

- id;
- event_id;
- group_id;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Event может быть адресован нескольким Groups; Group может быть целью нескольких Events. `EventGroupTarget` не создаёт EventParticipation и не означает регистрацию или посещаемость.

Связь допустима только при `Event.club_id == Group.club_id` и проверяется на authoritative application/service boundary согласно ADR-0022.

`own_groups` определяется через active EventGroupTarget и active GroupInstructorAssignment requester.

Детальный контракт этих отношений определён ADR-0023.

## 11. Attendance

Возможно отдельное представление поверх EventParticipation либо отдельная таблица, если требований объёма и аудита будет недостаточно для общей сущности.

Канонические статусы должны быть определены отдельно и использоваться единообразно.

## 12. Trip

### Назначение

Расширение Event для туристского похода/выезда.

### Содержит

- event_id;
- tourism_type;
- difficulty_category;
- region;
- route_id;
- planned_distance;
- actual_distance;
- planned_duration;
- actual_duration;
- leader;
- notes;
- result/status.

## 13. TripParticipant

Специализированные данные участия в походе:

- person_id;
- role_in_trip;
- segment/part, если требуется;
- actual participation;
- completed_distance;
- result;
- notes.

Это позволяет не смешивать обычную регистрацию на событие с туристским стажем.

## 14. Route

Логическая сущность маршрута.

Может включать:

- название;
- вид туризма;
- регион;
- описание;
- плановую/фактическую дистанцию;
- профиль высот;
- точки;
- GPX-файлы;
- внешние ссылки.

## 15. RoutePoint

Географическая точка маршрута:

- latitude;
- longitude;
- elevation;
- sequence;
- name;
- point_type;
- description.

## 16. GPX Track / File

GPX хранится как файл/объект хранилища с метаданными.

Исходный файл не должен помещаться непосредственно в PostgreSQL blob без отдельного обоснования.

Производные данные могут индексироваться в БД для поиска и аналитики.

## 17. TouristProfile

Расширение участника туристскими характеристиками.

Сюда относятся агрегированные или справочные данные:

- виды туризма;
- суммарный подтверждённый опыт;
- километраж;
- количество походов;
- квалификации;
- навыки;
- разряды;
- достижения.

Каноническим источником фактов о походах остаются Trip/TripParticipant; агрегаты профиля могут пересчитываться.

## 18. Achievement

Достижение должно поддерживать:

- название;
- описание;
- категорию;
- иконку/медиа при необходимости;
- способ получения: manual/automatic;
- правило автоматической выдачи;
- активность;
- дату выдачи;
- кто выдал.

AchievementAward связывает достижение с человеком и хранит историю выдачи.

## 19. Skill

Навык участника.

Хранится отдельно от Achievement, поскольку навык отражает уровень/состояние подготовки, а достижение — событие или награду.

## 20. Qualification

Формализованная квалификация, разряд или иной подтверждённый уровень.

Поддерживает срок действия и документальное подтверждение, если применимо.

## 21. KnowledgeArticle

Материал базы знаний.

Имеет:

- category;
- title;
- summary;
- body/content;
- status;
- author;
- version;
- publication metadata;
- tags;
- attachments.

## 22. Document

Унифицированный объект документа.

Может относиться к:

- Person;
- GuardianRelationship;
- Event;
- Trip;
- Club;
- Finance record;
- Equipment.

Нужны тип документа, владелец, статус, дата выдачи, срок действия и версия.

## 23. Consent

Отдельная сущность для фиксирования согласий.

Минимально необходимы:

- субъект;
- тип согласия;
- кто дал/подписал;
- дата;
- версия текста/политики;
- статус;
- подтверждающий документ, если применимо.

## 24. Equipment

Единица имущества клуба.

Хранит:

- инвентарный идентификатор;
- название;
- категорию;
- состояние;
- место хранения;
- статус;
- дату покупки/поступления;
- стоимость, если нужно;
- серийный номер, если есть.

## 25. EquipmentIssue

История выдачи оборудования:

- equipment_id;
- issued_to;
- event_id;
- issued_at;
- expected_return_at;
- returned_at;
- condition_before;
- condition_after;
- notes.

## 26. Finance

Финансовый домен должен быть отделён от UI мероприятий.

Основные сущности первой модели:

- FinancialAccount;
- Payment;
- Expense;
- EventBudget;
- EventExpense.

Конкретная бухгалтерская модель будет уточнена отдельным документом.

## 27. Notification

Унифицированное системное уведомление.

Нужно различать:

- событие, породившее уведомление;
- получателя;
- канал;
- шаблон;
- состояние доставки;
- время отправки;
- retry/error metadata.

Поддерживаемые целевые каналы:

- email;
- Telegram;
- MAX.

## 28. AuditLog

Аудит должен хранить как минимум:

- actor;
- action;
- target type;
- target id;
- timestamp;
- request/correlation id;
- before/after или diff, если применимо.

Аудит не должен содержать секреты и чувствительные значения сверх необходимого для расследования.

## 29. System Settings

Настройки клуба и feature flags управляют доступностью функциональности.

Feature flag не заменяет authorization: отключённая функция должна быть недоступна даже пользователю с permission.

## 30. Общие доменные принципы

- Исторические факты не перезаписываются без необходимости.
- Производные данные должны быть пересчитываемыми из первичных фактов.
- Межклубные связи проверяются на authoritative boundary.
- Authorization не определяется только названием роли.
- Чувствительные домены имеют отдельные policy/permission boundaries.
- Новая самостоятельная доменная сущность требует документированного основания и, при архитектурном влиянии, ADR.

## 31. Traceability

Канонические Event relationship и GuardianRelationship решения: ADR-0023.

Cross-Club ownership: ADR-0022.

Event lifecycle: ADR-0018.

Event field model: ADR-0019.

Event authorization/participation contract: ADR-0020.

Group persistence: ADR-0021.
