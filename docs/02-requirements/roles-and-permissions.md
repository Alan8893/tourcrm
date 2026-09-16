# TourCRM — Roles and Permissions

## 1. Назначение

Документ определяет модель авторизации TourCRM. Он является нормативным для backend authorization и основанием для ограничения интерфейса.

## 2. Принципы

1. Role — набор permissions.
2. Permission — атомарное право на действие над ресурсом.
3. Scope — область действия permission.
4. Наличие роли не является достаточным условием доступа к конкретному объекту.
5. Backend является единственным источником истины для authorization.
6. Frontend может скрывать недоступные действия, но никогда не заменяет backend enforcement.

## 3. Базовые роли

### admin

Полное управление клубом в рамках системной модели.

Назначение: руководитель/администратор клуба.

### instructor

Операционная работа с группами, занятиями, мероприятиями, посещаемостью и туристской деятельностью.

### member

Участник клуба. Работа преимущественно со своими данными, мероприятиями и достижениями.

### guardian

Родитель/законный представитель. Доступ к данным связанных детей в пределах разрешённой политики.

## 4. Permission naming

Канонический формат:

`<resource>.<action>`

Канонический каталог Event/attendance permissions включает:

- `event.read`
- `event.create`
- `event.update`
- `event.cancel`
- `event.manage`
- `attendance.read`
- `attendance.update`

Примеры других permissions:

- `person.read`
- `person.update`
- `membership.read`
- `membership.manage`
- `guardian_relationship.read`
- `guardian_relationship.manage`
- `group.read`
- `group.manage`
- `trip.read`
- `trip.manage`
- `achievement.read`
- `achievement.award`
- `knowledge.read`
- `knowledge.manage`
- `document.read`
- `document.manage`
- `consent.read`
- `consent.manage`
- `equipment.read`
- `equipment.manage`
- `finance.read`
- `finance.manage`
- `notification.read`
- `notification.manage`
- `audit.read`
- `settings.manage`
- `role.manage`

`event.archive`, `event.participant.read`, `event.participant.manage`,
`event.schedule.manage` и `attendance.correct` не являются каноническими
permissions и не должны использоваться как отдельные права.

`guardian_relationship.read`/`guardian_relationship.manage` приняты ADR-0025 §2 для доступа к `GuardianRelationship`; ранее использовавшийся в `docs/05-api/people-api.md` код `guardian.read` не являлся каноническим и заменён этими permissions.

## 5. Scope model

Минимальные scopes:

- `all` — все объекты клуба;
- `own_groups` — участники/мероприятия групп, за которые пользователь отвечает;
- `self` — только собственные данные;
- `children` — данные связанных детей;
- `own_events` — мероприятия, где пользователь является ответственным/назначенным;
- `none` — право отсутствует.

`assigned_events` является алиасом `own_events`.

`own_records` не является scope.

В дальнейшем допускаются scopes на базе ownership/relationship и специализированные политики только через отдельное решение.

## 6. Authorization evaluation

Доступ к операции определяется минимум по следующим условиям:

`Authenticated User` + `Permission` + `Scope` + `Object relationship` + `Object status` + `Feature setting`.

Feature setting не может расширить permissions.

Пример:

`finance.enabled = false` означает, что финансовый домен недоступен функционально. `finance.enabled = true` не предоставляет `finance.manage` пользователю, у которого такого permission нет.

## 7. Базовая матрица ролей

| Ресурс/действие | admin | instructor | member | guardian |
|---|---:|---:|---:|---:|
| Auth/self account | ✅ | ✅ | ✅ | ✅ |
| Свой Person | ✅ | ✅ | ✅ | ✅ |
| Любой Person | ✅ | по scope | ❌ | ❌ |
| Управление membership | ✅ | ограниченно | ❌ | ❌ |
| GuardianRelationship: чтение | ✅ | по scope | ❌ | self (собственные связи) |
| GuardianRelationship: управление | ✅ | ограниченно | ❌ | ограниченно (собственный linking flow) |
| Группы: чтение | ✅ | assigned | ограниченно | ограниченно |
| Группы: управление | ✅ | ❌ | ❌ | ❌ |
| Event: чтение | ✅ | по scope | по scope | children/relationship |
| Event: создание | ✅ | по permission | ❌ | ❌ |
| Event: изменение | ✅ | assigned/owned | ❌ | ❌ |
| Event: отмена | ✅ | по permission/scope | ❌ | ❌ |
| Event: архивирование | ✅ | по `event.manage` и scope | ❌ | ❌ |
| Event: участники — чтение | по `event.read`/scope | по `event.read`/scope | self/relationship | children/relationship |
| Event: участники — управление | по `event.manage`/scope | по `event.manage`/scope | ❌ | ❌ |
| Attendance: чтение | ✅ | assigned/owned | self | children |
| Attendance: изменение | ✅ | assigned/owned | ❌ | ❌ |
| Attendance: correction | по `attendance.update` + reason/audit | по `attendance.update` + scope + reason/audit | ❌ | ❌ |
| Trip: чтение | ✅ | ✅ | self | children |
| Trip: управление | ✅ | assigned/owned | ❌ | ❌ |
| Achievement: чтение | ✅ | ✅ | self | children |
| Achievement: выдача | ✅ | ✅ | ❌ | ❌ |
| Knowledge: чтение | ✅ | ✅ | ✅ | ✅ |
| Knowledge: управление | ✅ | по permission | ❌ | ❌ |
| Documents: чтение | ✅ | по scope | self | children |
| Documents: управление | ✅ | по scope | ограниченно | ограниченно |
| Consent: чтение | ✅ | по необходимости | self | children |
| Consent: управление | ✅ | по policy | ❌ | ограниченно |
| Equipment: чтение | ✅ | по назначению | ❌ | ❌ |
| Equipment: управление | ✅ | по permission | ❌ | ❌ |
| Finance: чтение | ✅ | по permission | self-related | self-related |
| Finance: управление | ✅ | по permission | ❌ | ❌ |
| Audit: чтение | ✅ | ❌ по умолчанию | ❌ | ❌ |
| System settings | ✅ | ❌ | ❌ | ❌ |
| Roles/permissions | ✅ | ❌ | ❌ | ❌ |

Матрица является базовой. Для чувствительных данных действуют дополнительные объектные ограничения.

## 8. Sensitive data

К sensitive domain относятся как минимум:

- документы;
- согласия;
- медицинская информация;
- финансовая информация;
- контактные данные несовершеннолетних;
- экстренные контакты.

Доступ к ним должен проверяться отдельно. Нельзя считать, что доступ к Person автоматически означает доступ ко всем дочерним объектам Person.

## 9. Self access

`self` применяется только к данным, которые пользователь имеет право видеть о себе. Например, member может видеть свой профиль, свои мероприятия, свои достижения и свою историю посещения, но не получает право просматривать другого member через подмену идентификатора ресурса.

## 10. Guardian access

`children` не означает доступ к любому ребёнку в клубе. Сервис должен вычислять допустимых детей через активные GuardianRelationship.

Удалённая/неактивная связь автоматически прекращает актуальный доступ, если отдельное правило не требует сохранения read-only исторического доступа.

Для Event guardian видит только мероприятия и связанные данные, относящиеся к связанным детям и разрешённые object policy. Роль `guardian` сама по себе не предоставляет `all`-доступ к мероприятиям клуба.

## 11. Instructor scope

Инструктор получает доступ только к объектам, для которых он назначен ответственным или которые принадлежат его группам, в соответствии с конкретным permission.

Роль instructor не должна автоматически давать доступ ко всем членам клуба.

Для Event `own_events` означает явное назначение/ответственность за мероприятие; `own_groups` означает ответственность за целевую группу. Инструкторские Event-операции не получают глобальный `all` scope только из роли instructor.

## 12. Event authorization contract

Для Event и связанных API действует следующий нормативный mapping:

| Операция | Permission |
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
| Attendance correction after normal window | `attendance.update` + mandatory reason + audit |

Scope и object relationship проверяются после определения permission. Отдельные
permissions для archive, participant management, schedule management и correction
не создаются.

Для Event list/detail/calendar используются только объекты, которые прошли
permission + scope + object relationship checks. Фильтры API не могут расширять
область доступа.

## 13. Multiple roles

Один пользователь может иметь несколько ролей.

Итоговые permissions формируются объединением permissions назначенных ролей с последующей проверкой scope и object-level policies.

Будущие explicit denies допускаются только после отдельного ADR, поскольку неверная реализация deny поверх role union может сделать модель трудно предсказуемой.

## 14. Participation and self-registration

`EventParticipation` является отдельной сущностью и не создаётся автоматически
только из membership/group targeting.

Регистрация и посещаемость — разные факты: наличие registration не означает
attendance.

На текущем этапе self-registration не является реализационно готовой операцией.
Её реализация блокируется до принятия отдельной детерминированной политики,
включающей registration window, age/group restrictions, capacity/waitlist,
статусы и допустимые переходы, а также deadline отмены.

Reference registration statuses:

- `invited`;
- `registered`;
- `waitlisted`;
- `declined`;
- `removed`.

Эти значения не являются основанием для самостоятельного вывода переходов или
автоматических role grants.

## 15. Admin

Admin обладает расширенными правами клуба, но системные операции уровня инфраструктуры/операционной системы не являются частью application admin и не должны имитироваться внутри CRM.

## 16. Audit requirements

Изменения следующих прав должны аудироваться:

- назначение/снятие роли;
- изменение permissions;
- изменение системных/feature settings;
- доступ к чувствительным административным функциям, если это будет предусмотрено policy.

Event/attendance mutations также должны соблюдать audit requirements домена.

## 17. UI requirements

Frontend должен:

- не показывать пользователю заведомо недоступные действия, где это улучшает UX;
- корректно обрабатывать HTTP 401/403;
- не полагаться на скрытие кнопок как на security mechanism;
- предотвращать случайные действия вне scope через форму и навигацию.

## 18. Future roles

Архитектура должна допускать появление ролей:

- club_manager;
- senior_instructor;
- trainee_instructor;
- finance_manager;
- document_manager;
- medical_responsible;

без переписывания модели authorization.

## 19. RoleAssignment contract

`RoleAssignment` связывает User с существующей Role и несёт собственный authorization scope. Один User может иметь несколько RoleAssignment; effective permissions являются additive union с последующей проверкой scope и object-level policies.

### 19.1 Validity and revoke

RoleAssignment имеет временной интервал `[valid_from, valid_to)`.

- `valid_from` — серверно/доменом определяемое начало действия назначения;
- `valid_to = NULL` означает открытый, действующий интервал;
- revoke не удаляет assignment, а закрывает его интервал установкой `valid_to` в серверное UTC-время операции;
- завершённый assignment сохраняется как историческая запись;
- повторный revoke уже завершённого assignment запрещён;
- повторное назначение после revoke создаётся отдельным RoleAssignment, а старый интервал не переоткрывается.

Эффективным считается только assignment, действующий в момент проверки authorization и не нарушающий остальные условия доступа, включая состояние User. Является ли активное состояние ClubMembership дополнительным условием эффективности, регулируется ADR-0027 и специфичными для домена политиками, а не данным разделом (см. также §19.4).

### 19.2 Scope combinations

Для RoleAssignment канонические комбинации таковы:

| Scope | `club_id` | `scope_ref_id` |
|---|---|---|
| `all` | опционален (см. ниже) | `NULL` |
| `self` | обязателен | `NULL` |
| `children` | обязателен | `NULL` |
| `own_groups` | обязателен | `NULL` |
| `own_events` | обязателен | `NULL` |
| `none` | `NULL` | `NULL` |

`scope_ref_id` в MVP не используется для этих scopes. В частности, `own_groups` определяется через доменную связь пользователя с Group, а `own_events` — через EventStaffAssignment, а не через ссылку на конкретный объект в RoleAssignment.

`self`, `children`, `own_groups` и `own_events` всегда ограничены указанным Club. Global `self`/`children`/`own_groups`/`own_events` assignments в MVP не поддерживаются.

**Amendment (TH-0089 / Issue #99, ADR-0026's own amendment section):** `all` — единственный scope, для которого `club_id` может быть либо конкретным Club (club-wide authority), либо `NULL` (installation-wide authority). Это устраняет ранее существовавшее внутреннее противоречие с §19.5 (`role.manage`), который уже описывал `all + club_id = NULL` как валидную комбинацию ("может управлять RoleAssignment в любом Club"), хотя эта таблица до амендмента этого не допускала. `all + club_id = NULL` используется, в частности, для initial administrator bootstrap (ADR-0027).

### 19.3 Role catalog mutability

Базовые/system roles (`admin`, `instructor`, `member`, `guardian`) и их permission sets не управляются через RoleAssignment API. Текущий slice не предоставляет Role CRUD, Permission CRUD или RolePermission CRUD.

`role.manage` не предоставляет права изменять каталог ролей или их permissions.

### 19.4 Cross-Club integrity

Для club-scoped RoleAssignment целевой User должен иметь активный `ClubMembership` в указанном Club.

Club-scoped assignment не может быть создан для User, который не состоит в соответствующем Club.

Если ClubMembership целевого User впоследствии заканчивается, RoleAssignment не удаляется, не revoke'ится и не изменяется автоматически. Является ли последующее отсутствие активного членства дополнительным условием эффективной authorization (в отличие от целостности на момент создания), определяется ADR-0027, а не данным разделом.

### 19.5 `role.manage` authorization

`role.manage` является permission управления RoleAssignment, но не Role/Permission catalog.

Для MVP `role.manage` допускается только со scope `all`:

- `all + club_id = NULL` — управление RoleAssignment в любом Club;
- `all + конкретный club_id` — управление RoleAssignment только в этом Club;
- `self`, `children`, `own_groups`, `own_events` и `none` не являются допустимыми scopes для `role.manage`.

Scope вызывающего пользователя определяет разрешённый target Club. Остальные проверки целевого RoleAssignment применяются независимо от права вызывающего.

### 19.6 RoleAssignment audit

Назначение, изменение и отзыв RoleAssignment используют закрытые audit action codes `role_assignment.created`, `role_assignment.changed` и `role_assignment.revoked` из ADR-0024. Audit mutation и business mutation выполняются в одной транзакции по общим правилам ADR-0024.
