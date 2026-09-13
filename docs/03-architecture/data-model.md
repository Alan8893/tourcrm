# TourCRM — Logical Data Model

## 1. Назначение

Документ определяет логическую модель данных TourCRM. Это контракт между доменной моделью, PostgreSQL и application services.

Документ намеренно не является готовым SQL schema: физические типы, индексы и конкретные ограничения реализации фиксируются в database ADR и migration specifications.

## 2. Общие соглашения

### 2.1 Identifiers

Каждая самостоятельная доменная сущность имеет стабильный первичный идентификатор `id`.

Чистые association/junction tables, не имеющие самостоятельной доменной идентичности и жизненного цикла, могут использовать составной первичный ключ из своих внешних ключей. Это исключение явно фиксируется в физической database specification.

Предпочтительный внешний формат идентификаторов API — UUID/ULID-подобный opaque identifier.

### 2.2 Timestamps

Все системные временные метки должны быть timezone-aware и однозначными. Базовый формат хранения — UTC.

### 2.3 Audit fields

Для изменяемых сущностей при необходимости используются `created_at`, `updated_at`, `created_by`, `updated_by`. Исторические записи дополнительно используют специализированные audit entities, где одного `updated_at` недостаточно.

## 3. Identity domain

### Person

Физическое лицо. Ключевые поля: `id`, `first_name`, `last_name`, `middle_name`, `birth_date`, `phone`, `email`, `address`, photo reference, timestamps.

### User

Учётная запись. `User N:1 Person`. Ключевые поля: `id`, `person_id`, login/username identifier, normalized identifier, password hash metadata, `status`, `email_verified_at`, `last_login_at`, timestamps.

### Club

Текущий installation domain.

### ClubMembership

Историческое членство Person в Club. `Person 1:N ClubMembership`, `Club 1:N ClubMembership`. Membership принадлежит одному Club и одному Person; исторические membership не удаляются без специальной процедуры.

## 4. Authorization domain

### Role

Каталог ролей.

### Permission

Каталог атомарных прав.

### RolePermission

M:N связь Role ↔ Permission.

### UserRoleAssignment

Связь User ↔ Role с context/scope; может быть ограничена Club scope.

### PermissionScope

Логический справочник/набор scope. Канонический словарь определяется ADR-0013.

## 5. Guardian domain

### GuardianRelationship

Club-neutral связь Person ↔ Person через семантику guardian/child.

Канонические поля:

- `id`;
- `guardian_person_id`;
- `child_person_id`;
- `relationship_type`;
- `status`;
- `is_primary_contact`;
- `valid_from`;
- `valid_to`;
- `created_at`;
- `updated_at`.

Канонические значения `status`: `active`, `inactive`, `revoked`.

`guardian_person_id != child_person_id`. Дублирующие active relationships одной пары/типа не допускаются. Исторические inactive/revoked сохраняются. Для одного ребёнка допускается не более одной одновременно действующей primary-contact relationship.

Club authorization guardian access определяется отдельно через active GuardianRelationship, membership ребёнка и применимую membership policy; сама GuardianRelationship не получает `club_id`.

## 6. Group domain

### Group

Группа клуба.

`Club 1:N Group`

Поля: `id`, `club_id`, `name`, `description`, `status`, `valid_from`, `valid_to`, `created_at`, `updated_at`.

`Group.status` остаётся строковым значением; канонический набор lifecycle-значений этим документом не задаётся.

### GroupMembership

Историческая ассоциация `ClubMembership` с `Group`.

`ClubMembership 1:N GroupMembership`, `Group 1:N GroupMembership`.

Поля: `id`, `group_id`, `club_membership_id`, `valid_from`, `valid_to`, `membership_status`, `created_at`, `updated_at`.

`person_id` не хранится: Person определяется через `club_membership_id -> ClubMembership.person_id`. `membership_status` — каноническое имя поля. `is_primary` и `assigned_by` не являются частью persistence-модели.

### GroupInstructorAssignment

Явная историческая связь User с Group для определения ответственности.

Поля: `id`, `group_id`, `user_id`, `role_in_group`, `is_primary`, `valid_from`, `valid_to`, `created_at`, `updated_at`.

`own_groups` основывается на active explicit `GroupInstructorAssignment`, а не только на глобальной роли instructor.

Cross-Club invariant: `Group.club_id == ClubMembership.club_id` для GroupMembership; assigned User должен иметь active ClubMembership в Club группы для GroupInstructorAssignment. Enforcement выполняется application/service layer согласно ADR-0022.

### EventGroupTarget

Каноническая связь `Event ↔ Group`, определённая ADR-0023.

Поля:

- `id`;
- `event_id`;
- `group_id`;
- `valid_from`;
- `valid_to`;
- `created_at`;
- `updated_at`.

Event может иметь несколько targets, Group может быть target нескольких Events. История сохраняется. Связь допустима только при `Event.club_id == Group.club_id` и проверяется через authoritative application/service boundary согласно ADR-0022.

`own_groups` = active EventGroupTarget + active GroupInstructorAssignment requester. Targeting не создаёт EventParticipation.

## 7. Event domain

### Event

Базовое мероприятие. `Club 1:N Event`.

Поля по ADR-0019:

- `id`;
- `club_id`;
- `event_type`;
- `title`;
- `description`;
- `start_at`;
- `end_at`;
- `timezone`;
- `location_type`;
- `location_name`;
- `location_address`;
- `location_latitude`;
- `location_longitude`;
- `status`;
- `cancellation_reason`;
- `created_by`;
- `updated_by`;
- `created_at`;
- `updated_at`.

Lifecycle определяется ADR-0018.

### EventParticipation

Отдельная связь Person с Event, необходимая для `self` и участнических сценариев.

Минимальная dependency-модель должна сохранять как минимум `event_id`, `person_id`, registration state и timestamps; для полной attendance/registration модели дополнительные поля фиксируются отдельным контрактом.

Наиболее важное правило: `EventGroupTarget` не создаёт EventParticipation автоматически.

### EventStaffAssignment

Каноническая явная связь `User ↔ Event`, определённая ADR-0023.

Поля:

- `id`;
- `event_id`;
- `user_id`;
- `role_in_event`;
- `is_primary`;
- `valid_from`;
- `valid_to`;
- `created_at`;
- `updated_at`.

Допускается несколько active assignments, но не более одной active primary assignment для Event. История сохраняется.

Assigned User допустим только при active ClubMembership его Person в Club Event. `Event.created_by` не является заменой EventStaffAssignment и не является источником `own_events`.

`own_events` основывается на active EventStaffAssignment.

## 8. Recurrence domain

Для регулярного расписания не следует создавать бесконечные Event заранее.

### RecurrenceRule

Описывает правило повторения.

### EventSeries

Логический контейнер серии мероприятий.

### EventOccurrence

Материализованный occurrence-слой согласно ADR-0015. Series остаётся источником recurrence truth; materialization выполняется в конфигурируемом горизонте, по умолчанию 180 дней.

Единичные исключения не должны уничтожать историю серии.

## 9. Trip domain

### Trip

Расширение Event 1:1. `Event 1:0..1 Trip`.

Поля включают tourism type, difficulty, region, route, planned/actual distance, planned/actual duration, leader и result data согласно специализированной модели.

### TripParticipant

Специализированная связь участника с походом.

## 10. Route domain

### Route

Логический маршрут, который может использоваться несколькими мероприятиями.

### RoutePoint

`Route 1:N RoutePoint` с sequence, coordinates, elevation и описательными полями.

### RouteFile

Метаданные внешнего/файлового объекта GPX. Исходный GPX не хранится непосредственно в PostgreSQL blob без отдельного ADR.

## 11. Tourist profile

### TouristProfile

`Person 1:0..1 TouristProfile`. Производные/описательные данные. Первичные факты походов находятся в Trip/TripParticipant.

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

Связь Person ↔ Achievement с `person_id`, `achievement_id`, `awarded_at`, award method, `awarded_by`, optional `source_event_id`, metadata и revoke fields.

## 13. Knowledge domain

### KnowledgeCategory

Категория базы знаний.

### KnowledgeArticle

Материал базы знаний.

### KnowledgeArticleVersion

Версия опубликованного/изменённого материала.

### KnowledgeTag / KnowledgeArticleTag

Теги и M:N связь статьи с тегами.

### KnowledgeRelation

Связь статьи с Event, Skill, Qualification или другими объектами.

## 14. Documents / Consent domain

### Document

Унифицированный документ/файл с метаданными. Security-sensitive ownership должен использовать явные FK-backed ownership relations согласно ADR-0016.

### DocumentType / DocumentVersion

Тип документа и история версий.

### ConsentType / ConsentVersion / ConsentRecord

Тип согласия, версия текста и факт предоставления/отзыва согласия.

## 15. Equipment domain

### Equipment

Инвентарная единица.

### EquipmentCategory

Категория.

### EquipmentIssue

История выдачи с привязкой к Equipment, Person и optional Event.

## 16. Finance domain

### FinancialAccount

Счёт/касса/кошелёк клуба.

### Payment / Expense

Поступление/платёж и расход.

### EventBudget / EventExpense

Бюджет и расходы мероприятия.

### ParticipantCharge / ParticipantPaymentAllocation

Начисление участнику и связь оплаты с начислением/основанием.

## 17. Notification domain

### NotificationTemplate

Шаблон уведомления.

### NotificationPreference

Настройки пользователя/канала.

### Notification

Логическое уведомление.

### NotificationDelivery

Попытка/результат доставки по конкретному каналу.

## 18. Audit domain

### AuditEvent

Нормализованный audit event с actor, action, object reference, timestamp и metadata. Secrets и необоснованные чувствительные данные не записываются.

## 19. Calendar / integration domain

Календарные внешние связи, iCalendar feeds и интеграции должны быть отдельными сущностями/адаптерами и не должны изменять базовый Event contract без ADR.

## 20. Relationship and authorization traceability

Канонические источники relationship-based scopes:

| Scope | Источник relationship |
|---|---|
| `all` | Club-level permission/object policy |
| `own_groups` | active EventGroupTarget + active GroupInstructorAssignment |
| `own_events` | active EventStaffAssignment |
| `self` | requester Person's eligible EventParticipation |
| `children` | active GuardianRelationship + child EventParticipation or active Group membership through EventGroupTarget |
| `none` | no access |

`assigned_events` — alias `own_events`; `own_records` не является canonical scope.

## 21. Cross-Club ownership

User является Club-neutral и может участвовать в нескольких Clubs.

Все Group/Event relationship writes проходят authoritative ownership validation в application/service layer. Проверка ownership и запись выполняются в одной транзакции с необходимой concurrency protection.

Database отвечает за обычную referential integrity, interval constraints, uniqueness/exclusion и deletion protection; отдельные redundant `club_id` и DB triggers для этих relationship invariants не вводятся без отдельного ADR.

## 22. Канонические ADR

- ADR-0013 — scope vocabulary.
- ADR-0016 — document ownership.
- ADR-0018 — Event lifecycle.
- ADR-0019 — Event field model.
- ADR-0020 — Event authorization and participation contract.
- ADR-0021 — Group persistence model.
- ADR-0022 — Cross-Club ownership integrity.
- ADR-0023 — Event responsibility, EventGroupTarget and GuardianRelationship persistence.

## 23. Правило изменения модели

Новая доменная сущность или изменение существующего relationship contract не считается согласованным только по реализации. Сначала обновляется нормативная документация и при необходимости создаётся/обновляется ADR; затем Issue implementation становится исполнимым без изобретения бизнес-семантики.
