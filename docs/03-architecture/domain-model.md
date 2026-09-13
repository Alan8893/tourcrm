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

### Требования

- один guardian может быть связан с несколькими детьми;
- один ребёнок может иметь нескольких guardians;
- связь имеет тип и статус;
- при необходимости хранится признак основного контакта;
- доступ родителя определяется одновременно фактом связи и permissions.

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

Event-to-Group targeting является отдельной связью и не определяется этим Group foundation.

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

## 11. EventParticipation

Связь Person/ClubMembership с Event.

Предусматривает:

- registration_status;
- attendance_status;
- participant_role;
- registered_at;
- attendance_marked_at;
- absence_reason;
- result;
- notes.

Инструкторы и руководители также должны связываться с мероприятием явно, а не определяться косвенно по роли пользователя.

## 12. Attendance

Возможно отдельное представление поверх EventParticipation либо отдельная таблица, если требований объёма и аудита будет недостаточно для общей сущности.

Канонические статусы должны быть определены отдельно и использоваться единообразно.

## 13. Trip

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

## 14. TripParticipant

Специализированные данные участия в походе:

- person_id;
- role_in_trip;
- segment/part, если требуется;
- actual participation;
- completed_distance;
- result;
- notes.

Это позволяет не смешивать обычную регистрацию на событие с туристским стажем.

## 15. Route

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

## 16. RoutePoint

Географическая точка маршрута:

- latitude;
- longitude;
- elevation;
- sequence;
- name;
- point_type;
- description.

## 17. GPX Track / File

GPX хранится как файл/объект хранилища с метаданными.

Исходный файл не должен помещаться непосредственно в PostgreSQL blob без отдельного обоснования.

Производные данные могут индексироваться в БД для поиска и аналитики.

## 18. TouristProfile

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

## 19. Achievement

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

## 20. Skill

Навык участника.

Хранится отдельно от Achievement, поскольку навык отражает уровень/состояние подготовки, а достижение — событие или награду.

## 21. Qualification

Формализованная квалификация, разряд или иной подтверждённый уровень.

Поддерживает срок действия и документальное подтверждение, если применимо.

## 22. KnowledgeArticle

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

## 23. Document

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

## 24. Consent

Отдельная сущность для фиксирования согласий.

Минимально необходимы:

- субъект;
- тип согласия;
- кто дал/подписал;
- дата;
- версия текста/политики;
- статус;
- подтверждающий документ, если применимо.

## 25. Equipment

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

## 26. EquipmentIssue

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

## 27. Finance

Финансовый домен должен быть отделён от UI мероприятий.

Основные сущности первой модели:

- FinancialAccount;
- Payment;
- Expense;
- EventBudget;
- EventExpense.

Конкретная бухгалтерская модель будет уточнена отдельным документом.

## 28. Notification

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

## 29. AuditLog

Аудит должен хранить как минимум:

- actor;
- action;
- target type;
- target id;
- timestamp;
- request/correlation id;
- result/status;
- change summary или diff без утечки секретов.

Секреты, токены, пароли и иные чувствительные credentials в аудит не записываются.

## 30. SystemSetting / FeatureSetting

Настройки клуба и системы должны позволять включать/отключать необязательные функции.

Примеры:

- rating.enabled;
- achievements.enabled;
- telegram.enabled;
- max.enabled;
- email.enabled;
- finance.enabled.

Feature settings не должны использоваться для обхода security permissions.

## 31. Общие правила удаления

По умолчанию исторически значимые сущности не удаляются физически, если это разрушает аудит или историческую достоверность.

Предпочтительный подход:

- active/inactive/archived статус;
- soft delete только там, где он действительно нужен;
- физическое удаление только по явно документированным правилам.

## 32. Общие системные поля

Для большинства изменяемых сущностей рекомендуется наличие:

- id;
- created_at;
- updated_at;
- created_by, если применимо;
- updated_by, если применимо;
- status, если сущность имеет жизненный цикл.

Точная схема БД является отдельным документом и не должна автоматически выводиться из этого документа без проверки бизнес-правил.
