# TourCRM — Logical Data Model

## 1. Назначение

Документ определяет логическую модель данных TourCRM. Это контракт между доменной моделью, PostgreSQL и application services.

Документ намеренно не является готовым SQL schema: физические типы, индексы и конкретные ограничения реализации фиксируются в database ADR и migration specifications.

## 2. Общие соглашения

### 2.1 Identifiers

Каждая самостоятельная доменная сущность имеет стабильный первичный идентификатор `id`.

Чистые association/junction tables, не имеющие самостоятельной доменной идентичности и жизненного цикла, могут использовать составной первичный ключ из своих внешних ключей. Это исключение явно фиксируется в физической database specification.

Предпочтительный внешний формат идентификаторов API — UUID/ULID-подобный opaque identifier. Конкретный выбор фиксируется database ADR.

### 2.2 Timestamps

Все системные временные метки должны быть timezone-aware и однозначными. Базовый формат хранения — UTC.

### 2.3 Audit fields

Для изменяемых сущностей при необходимости:

- `created_at`;
- `updated_at`;
- `created_by`;
- `updated_by`.

Исторические записи дополнительно используют специализированные audit entities, где одного updated_at недостаточно.

## 3. Identity domain

### Person

Представляет физическое лицо.

Ключевые поля:

- id;
- first_name;
- last_name;
- middle_name nullable;
- birth_date nullable;
- phone nullable;
- email nullable;
- address nullable;
- photo reference nullable;
- created_at;
- updated_at.

Медицинские, финансовые и документные данные не встраиваются в Person без отдельного обоснования.

### User

Представляет учётную запись.

Связь:

`User N:1 Person`

Ключевые поля:

- id;
- person_id;
- login/username identifier;
- normalized identifier;
- password hash metadata;
- status;
- email_verified_at;
- last_login_at;
- created_at;
- updated_at.

### Club

Текущий installation domain.

`Club 1:N ClubMembership`

### ClubMembership

Историческое членство Person в Club.

`Person 1:N ClubMembership`
`Club 1:N ClubMembership`

Ограничения:

- membership принадлежит одному club;
- membership относится к одному person;
- исторические membership не удаляются без специальной процедуры;
- одновременно active membership для одного person и club не должно дублироваться.

## 4. Authorization domain

### Role

Каталог ролей.

### Permission

Каталог атомарных прав.

### RolePermission

M:N связь Role ↔ Permission.

### UserRoleAssignment

M:N/assignment связь User ↔ Role с context/scope.

Связь может быть ограничена club scope.

### PermissionScope

Может быть отдельным справочником или enum/configuration entity, если этого требует реализация.

## 5. Guardian domain

### GuardianRelationship

M:N Person ↔ Person через семантику guardian/child.

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

Канонические значения `status`: `active`, `inactive`, `revoked`.

Ограничение: guardian_person_id != child_person_id. Дублирующие active relationships одной пары и relationship_type не допускаются; исторические inactive/revoked сохраняются. Для одного ребёнка допускается не более одной одновременно действующей primary-contact relationship.

GuardianRelationship не содержит `club_id`; Club authorization определяется через active relationship, membership ребёнка и применимую membership policy.

## 6. Group domain

### Group

Группа клуба.

`Club 1:N Group`

Канонические поля:

- id;
- club_id;
- name;
- description;
- status;
- valid_from;
- valid_to;
- created_at;
- updated_at.

`Group.status` остаётся строковым значением. Канонический набор lifecycle-значений этим документом не задаётся.

### GroupMembership

Историческая ассоциация `ClubMembership` с `Group`.

`ClubMembership 1:N GroupMembership`
`Group 1:N GroupMembership`

Канонические поля:

- id;
- group_id;
- club_membership_id;
- valid_from;
- valid_to;
- membership_status;
- created_at;
- updated_at.

`person_id` в `GroupMembership` не хранится: Person определяется через `club_membership_id -> ClubMembership.person_id`.

`membership_status` — каноническое имя поля; отдельного `status` для этой association-модели нет.

`is_primary` не является частью первой persistence-модели. Возможность одновременной принадлежности к нескольким группам и концепция основной группы требуют отдельного business-policy решения.

`assigned_by` не является доменным полем GroupMembership; информация об инициаторе изменения относится к применимой audit/created-by инфраструктуре.

Исторические записи сохраняются. Закрытие периода не удаляет запись.

### GroupInstructorAssignment

Явная историческая связь пользователя с группой для определения ответственности.

`User 1:N GroupInstructorAssignment`
`Group 1:N GroupInstructorAssignment`

Канонические поля:

- id;
- group_id;
- user_id;
- role_in_group;
- is_primary;
- valid_from;
- valid_to;
- created_at;
- updated_at.

`user_id` используется потому, что ответственность для authorization относится к аутентифицированному User principal.

`role_in_group` пока не закрывается enum-справочником; его допустимый словарь должен быть согласован с моделью ответственности Event.

`is_primary` различает основного ответственного инструктора и другие явные назначения.

Scope `own_groups` основывается на активном явном `GroupInstructorAssignment`, а не только на глобальной роли instructor.

Group и все связанные с ним объекты должны принадлежать одному Club. GroupMembership допустим только при совпадении `Group.club_id` и `ClubMembership.club_id`. Для GroupInstructorAssignment назначаемый User должен иметь active ClubMembership в Club группы. Enforcement этого cross-Club invariant выполняется application/service layer согласно ADR-0022.

### EventGroupTarget

Каноническая связь Event с Group, определённая ADR-0023.

Поля:

- id;
- event_id;
- group_id;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Event может быть адресован нескольким Groups, Group может быть целью нескольких Events. История сохраняется. `Event.club_id == Group.club_id` обязателен и проверяется через authoritative application/service boundary согласно ADR-0022.

`own_groups` основывается на active EventGroupTarget + active GroupInstructorAssignment requester. Targeting не создаёт EventParticipation.

## 7. Event domain

### Event

Базовое мероприятие.

`Club 1:N Event`

Поля верхнего уровня:

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
- location_address nullable;
- location_latitude nullable;
- location_longitude nullable;
- status;
- cancellation_reason nullable;
- created_by;
- updated_by;
- timestamps.

### EventParticipation

Связь Person с Event.

`Event 1:N EventParticipation`
`Person 1:N EventParticipation`

Текущая принятая persistence-модель (Issue #51, в соответствии с ADR-0023 §4):

- id;
- event_id;
- person_id;
- registration_status;
- created_at;
- updated_at.

Для одного Event и одного Person допускается не более одной записи EventParticipation — ограничение обеспечивается на уровне БД (`UNIQUE(event_id, person_id)`), а не только application-кодом.

`registration_status` — обычная строка без закрытого словаря/CHECK-ограничения на уровне БД. ADR-0020 §4 упоминает `invited`, `registered`, `waitlisted`, `declined`, `removed` как documented reference values, а не как принятый и enforced словарь; полный transition graph и связанная registration policy остаются отдельным deferred business decision.

#### Отложенные (не реализованные) концепции

Следующие атрибуты являются возможными будущими расширениями и **не входят** в текущую реализованную persistence-модель: `attendance_status`, `participant_role`, `registered_at`, `attendance_marked_at`, `absence_reason`, `result`, `notes`. Их введение требует отдельного принятого архитектурного/бизнес-решения; посещаемость (attendance) в частности рассматривается как отдельная от EventParticipation зависимость (ADR-0023 §4; см. также `domain-model.md` §11 "Attendance").

### EventStaffAssignment

Явная связь Event с ответственными/инструкторами.

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

Допускается несколько active assignments, но не более одной active primary assignment для Event. История сохраняется.

Assigned User допустим только если Person имеет active ClubMembership в Club Event. `Event.created_by` не заменяет EventStaffAssignment и не является источником `own_events`.

## 8. Recurrence domain

Для регулярного расписания не следует создавать бесконечные Event заранее.

### RecurrenceRule

Описывает правило повторения.

### EventSeries

Логический контейнер серии мероприятий.

### EventOccurrence

Может быть представлением/слоем для конкретного occurrence; физическая стратегия выбирается при проектировании schedule service.

Каноническая стратегия materialization определяется ADR-0015: series является recurrence source of truth, occurrence materialization выполняется в конфигурируемом горизонте, по умолчанию 180 дней.

Ключевое требование: единичный перенесённый/отменённый occurrence не должен ломать правило всей серии.

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

## 13. Knowledge domain

### KnowledgeCategory

Категория базы знаний.

### KnowledgeArticle

Материал базы знаний.

### KnowledgeArticleVersion

Версия опубликованного/изменённого материала.

### KnowledgeTag

Тег.

### KnowledgeArticleTag

M:N связь статьи и тегов.

### KnowledgeRelation

Связь статьи с Event, Skill, Qualification или другими объектами.

## 14. Documents / Consent domain

### Document

Унифицированный документ/файл с метаданными.

Поскольку объект может относиться к разным доменам, физическая модель должна избегать неограниченного числа nullable foreign keys без архитектурного решения.

Возможные стратегии:

- polymorphic reference;
- отдельные link tables;
- document aggregate ownership.

Выбор фиксируется ADR.

### DocumentType

Тип документа.

### DocumentVersion

История версий файла/метаданных, если требуется immutable history.

### ConsentType

Тип согласия/политики.

### ConsentVersion

Конкретная версия текста согласия.

### ConsentRecord

Факт предоставления/отзыва согласия конкретным субъектом.

## 15. Equipment domain

### Equipment

Инвентарная единица.

### EquipmentCategory

Категория.

### EquipmentIssue

История выдачи.

Связи:

`Equipment 1:N EquipmentIssue`
`Person 1:N EquipmentIssue`
`Event 1:N EquipmentIssue` optional.

## 16. Finance domain

### FinancialAccount

Счёт/касса/кошелёк в логике клуба.

### Payment

Поступление/платёж.

### Expense

Расход.

### EventBudget

Бюджет мероприятия.

### EventExpense

Связь расхода с мероприятием.

### ParticipantCharge

Начисление конкретному участнику при необходимости.

### ParticipantPaymentAllocation

Связь оплаты с начислением/основанием.

Физическая финансовая модель должна обеспечивать трассируемость суммы от операции до основания.

## 17. Notification domain

### NotificationTemplate

Шаблон уведомления.

### NotificationPreference

Настройки пользователя/канала.

### Notification

Логическое уведомление.

### NotificationDelivery

Отдельная попытка доставки по конкретному каналу.

Это позволяет один объект Notification доставлять по нескольким каналам без дублирования бизнес-события.

## 18. Communication domain

### Announcement

Официальное сообщение клуба.

### Message/Conversation

Может быть добавлено позже после отдельного требования и ADR. Не считать встроенный чат частью MVP без явного решения.

## 19. Audit domain

### AuditLog

Неизменяемая прикладная запись аудита.

Минимальные поля:

- id;
- actor_user_id;
- action;
- target_type;
- target_id;
- occurred_at;
- request_id/correlation_id;
- outcome;
- change_summary/diff reference.

## 20. Settings domain

### SystemSetting

Техническая/системная настройка.

### FeatureSetting

Настройка доступности функции.

Должна иметь scope (например club), значение и аудит изменения.

## 21. Главные кардинальности

```text
Club 1:N Person через ClubMembership
Person 1:0..1 User
Person M:N Person через GuardianRelationship
Club 1:N Group
ClubMembership 1:N GroupMembership
Group 1:N GroupMembership
User 1:N GroupInstructorAssignment
Group 1:N GroupInstructorAssignment
Club 1:N Event
Event M:N Person через EventParticipation
Event M:N Group через EventGroupTarget
Event 1:N EventStaffAssignment
Event 1:0..1 Trip
Trip M:N Person через TripParticipant
Route 1:N RoutePoint
Route 1:N RouteFile
Person 1:0..1 TouristProfile
Person M:N Skill через PersonSkill
Person M:N Qualification через PersonQualification
Person M:N Achievement через AchievementAward
Person 1:N Document links/ownership
Person M:N Equipment через EquipmentIssue history
Event 1:N Finance/Expense relations
Notification 1:N NotificationDelivery
User 1:N AuditLog
```

## 22. Referential integrity

Удаление родительской сущности должно быть запрещено, каскадным либо архивирующим только после определения семантики для каждой связи.

Особенно запрещается бездумный cascade delete для:

- Person;
- User;
- Event;
- Trip;
- Payment;
- Expense;
- AchievementAward;
- ConsentRecord;
- AuditLog.

Cross-Club ownership is mandatory for Group relationships: a GroupMembership is valid only when Group and ClubMembership belong to the same Club; a GroupInstructorAssignment is valid only when the assigned User has an active ClubMembership in the Group's Club. EventGroupTarget additionally requires Event.club_id == Group.club_id. Application/service-layer enforcement follows ADR-0022.

## 23. Indexing principles

Физическая БД должна иметь индексы на:

- уникальные логины/идентификаторы;
- внешние ключи;
- поля поиска участников;
- поля фильтрации Event по датам/status/club;
- EventParticipation по event/person;
- TripParticipant по trip/person;
- актуальные membership/group membership;
- document expiration;
- notification delivery state;
- audit timestamp/target.

Полный набор индексов определяется после API/query design.
