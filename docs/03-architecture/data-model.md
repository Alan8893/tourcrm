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

Минимальные поля:

- guardian_person_id;
- child_person_id;
- relationship_type;
- status;
- is_primary_contact;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Ограничение: guardian_person_id != child_person_id.

## 6. Group domain

### Group

Группа клуба.

`Club 1:N Group`

### GroupMembership

История принадлежности Person к Group.

`Person 1:N GroupMembership`
`Group 1:N GroupMembership`

Минимальные поля:

- person_id;
- group_id;
- valid_from;
- valid_to;
- membership_status;
- is_primary.

Ограничения непрерывности/единственности primary должны быть отражены на уровне business policy.

### GroupInstructorAssignment

Явная связь instructor User/Person с Group.

Нельзя вычислять instructor группы только по глобальной роли.

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
- location;
- status;
- cancellation_reason nullable;
- created_by;
- updated_by;
- timestamps.

### EventParticipation

Связь Person/ClubMembership с Event.

`Event 1:N EventParticipation`
`Person 1:N EventParticipation`

Ключевые поля:

- event_id;
- person_id;
- registration_status;
- attendance_status;
- participant_role nullable;
- registered_at;
- attendance_marked_at nullable;
- absence_reason nullable;
- result nullable;
- notes nullable.

Должно существовать ограничение на дублирование участия одного человека в одном событии.

### EventStaffAssignment

Явная связь Event с ответственными/инструкторами.

Ключевые поля:

- event_id;
- person/user reference;
- role_in_event;
- primary_flag;
- valid state.

## 8. Recurrence domain

Для регулярного расписания не следует создавать бесконечные Event заранее.

### RecurrenceRule

Описывает правило повторения.

### EventSeries

Логический контейнер серии мероприятий.

### EventOccurrence

Может быть представлением/слоем для конкретного occurrence; физическая стратегия выбирается при проектировании schedule service.

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
Person M:N Group через GroupMembership
Club 1:N Event
Event M:N Person через EventParticipation
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
