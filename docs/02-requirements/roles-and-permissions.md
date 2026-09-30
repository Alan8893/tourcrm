# TourCRM — Roles and Permissions

## 1. Назначение

Документ определяет модель авторизации TourCRM. Он является нормативным для backend authorization и основанием для ограничения интерфейса.

Каноническая role → permission → scope политика зафиксирована в `docs/02-requirements/role-permission-scope-matrix.md`. При расхождении значение из этой матрицы имеет приоритет над формулировками данного документа. Архитектура scope-модели — ADR-0041 (permission-level scopes, AUTH-2A).

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

Канонический каталог People permissions включает:

- `person.read`
- `person.create`
- `person.update`
- `membership.read`
- `membership.manage`
- `guardian_relationship.read`
- `guardian_relationship.manage`

`person.create` является отдельным каноническим permission и предоставляется только `admin`.

Каталог включает также отдельный, узкий `user.directory.read` (TH-0107) — permission для
операционного справочника пользователей `GET /api/v1/users` (см. `docs/05-api/users-api.md`).
Выдаётся `admin` и `instructor`. Это единственный permission, чья policy не использует
`own_groups`/`self`/`children`/`own_events` — только club-boundary через
`UserRoleAssignment.club_id` (§11 ниже поясняет, почему это намеренное, ограниченное этим одним
permission исключение, а не пересмотр общей scope-модели).

Примеры других permissions:

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
- `document.export`
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
- `account.manage`
- `membership.import`

Каталог включает также отдельный `account.manage` (TH-0113, ADR-0038) — permission для
административного управления User account/credentials из Person Detail: создание User для
существующего Person и выдача одноразового setup/reset challenge через уже существующий
`PasswordResetChallenge` (см. `docs/05-api/people-api.md` §24.2). Выдаётся только `admin`.
Намеренно отдельный от `role.manage`, `settings.manage` и `person.update` (ADR-0038: управление
credentials — отдельная административная операция, не назначение роли и не редактирование
профиля). Полная матрица ролей для `account.manage` не заполняется в рамках этой задачи.

`event.archive`, `event.participant.read`, `event.participant.manage`,
`event.schedule.manage` и `attendance.correct` не являются каноническими
permissions и не должны использоваться как отдельные права.

`guardian_relationship.read`/`guardian_relationship.manage` приняты ADR-0025 §2 для доступа к `GuardianRelationship`; ранее использовавшийся в `docs/05-api/people-api.md` код `guardian.read` не являлся каноническим и заменён этими permissions.

Для People Management действует отдельная объектная policy из ADR-0035: наличие `person.read`, `person.update`, `membership.manage` или `guardian_relationship.manage` само по себе не отменяет scope, object relationship и lifecycle restrictions.

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

### 5.1 Где хранится scope (ADR-0041, AUTH-2A)

Scope является свойством конкретного permission grant роли, а не назначения роли пользователю:

```text
User
  → UserRoleAssignment
      → Role
          → RolePermission
              → RolePermissionScope(s)
                  → Resource/Object
```

- `RolePermission` связывает роль с permission; `RolePermissionScope` задаёт один или несколько канонических scopes именно этого grant.
- Если у permission несколько scopes, достаточно совпадения любого из них (с последующей проверкой object relationship и состояния ресурса).
- Scope, заданный для одного permission, не расширяет, не ограничивает и никак не влияет на другой permission — в том числе permission той же роли.
- `RolePermission` без `RolePermissionScope` не даёт доступа.
- `UserRoleAssignment.scope_type` — legacy-поле хранения на переходный период. Оно не является источником authorization и не используется для определения доступа; назначение роли не выбирает и не передаёт scope.

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
| Person: чтение (`person.read`) | `all` | `own_groups` | `self` | `children` |
| Person: изменение (`person.update`) | `all`, все поля | ❌ | `self`, ограниченные поля | `children`, ограниченные поля |
| Person: изменение `email`/`birth_date` | ✅ | ❌ | ❌ | ❌ |
| Person: создание | ✅ `person.create` (`all`) | ❌ | ❌ | ❌ |
| Membership: чтение | `all` | `own_groups` | `self` | `children` |
| Membership: создание | ✅ | ❌ | ❌ | ❌ |
| Membership: изменение type | ✅ | ❌ | ❌ | ❌ |
| Membership: lifecycle | ✅ | ❌ | ❌ | ❌ |
| Participant import job management | ✅ `membership.import` + `all` | ❌ | ❌ | ❌ |
| GuardianRelationship: чтение | authorized global | по `own_groups` | собственные relationship records | собственные relationship records |
| GuardianRelationship: создание | ✅ | ❌ | ❌ | ❌ |
| GuardianRelationship: изменение | ✅ | ❌ | ❌ | ❌ |
| GuardianRelationship: terminate | ✅ | ❌ | ❌ | ❌ |
| Группы: чтение | `all` | `own_groups` | own membership | children's groups |
| Группы: управление | ✅ | ❌ | ❌ | ❌ |
| Event: чтение | `all` | applicable `own_groups` / `own_events` | own participation | children's participation |
| Event: создание | `all` | according to documented event policy and scope | ❌ | ❌ |
| Event: изменение | `all` | assigned/owned scope | ❌ | ❌ |
| Event: отмена | `all` | permission + scope | ❌ | ❌ |
| Event: архивирование | ✅ | по `event.manage` и scope | ❌ | ❌ |
| Event: участники — чтение | по `event.read`/scope | по `event.read`/scope | self/relationship | children/relationship |
| Event: участники — управление | по `event.manage`/scope | по `event.manage`/scope | ❌ | ❌ |
| Attendance: чтение | `all` | assigned/owned | `self` | `children` |
| Attendance: изменение | `all` | assigned/owned | ❌ | ❌ |
| Attendance: correction | по `attendance.update` + reason/audit | по `attendance.update` + scope + reason/audit | ❌ | ❌ |
| Trip: чтение | ✅ | ✅ | self | children |
| Trip: управление | ✅ | assigned/owned | ❌ | ❌ |
| Achievement: чтение | `all` | `own_groups` | `self` | `children` |
| Achievement: выдача | `all` | `own_groups` | ❌ | ❌ |
| Knowledge: чтение | ✅ | ✅ | ✅ | ✅ |
| Knowledge: управление | ✅ | по permission | ❌ | ❌ |
| Documents: чтение | `all` | `own_groups` | `self` | `children` |
| Documents: управление | `all` | ❌ | ❌ | ❌ |
| Consent: чтение | ✅ | по необходимости | self | children |
| Consent: управление | ✅ | по policy | ❌ | ограниченно |
| Inventory / Склад: чтение | ✅ | ❌ | ❌ | ❌ |
| Inventory / Склад: управление | ✅ | ❌ | ❌ | ❌ |
| Finance: чтение | ✅ | по permission | self-related | self-related |
| Finance: управление | ✅ | по permission | ❌ | ❌ |
| Audit: чтение | ✅ | ❌ | ❌ | ❌ |
| System settings | ✅ | ❌ | ❌ | ❌ |
| Roles/permissions | ✅ | ❌ | ❌ | ❌ |
| Account management (create User, password reset) | ✅ | ❌ | ❌ | ❌ |

Матрица является базовой. Значения для Person, Membership, Group, Event, Attendance, Achievement, Document и административных permissions соответствуют `role-permission-scope-matrix.md`, который имеет приоритет при расхождении. Для чувствительных данных действуют дополнительные объектные ограничения.

### 7.1 People Management — каноническая policy

`Person` является Club-neutral identity. В MVP Person не имеет `status`, не архивируется и не удаляется через People API; см. ADR-0034.

**Person**

- `person.create`: только `admin`. Авторизуется effective grant `person.create` со scope `all` (`RolePermissionScope`, ADR-0041) через активное назначение роли; `assignment.club_id` не участвует в решении (не обязан быть `NULL`) — у Person ещё нет target Club, относительно которого проверялась бы club boundary (TH-0106 / Issue #131).
- TH-0111 / Issue #140: `POST /api/v1/persons` атомарно создаёт также начальное активное `ClubMembership` для текущего Club (`member`/`active`) в той же транзакции — см. ADR-0035 §2 (amendment) и `people-api.md` §6. Это не новый permission и не требует `membership.manage`: набор полей membership полностью фиксирован политикой, а не является предметом отдельного решения вызывающего.
- `person.read`: `admin` — `all`; `instructor` — только `own_groups`; `member` — `self`; `guardian` — `children`. Других путей чтения Person (в том числе неявного доступа к собственной записи вне этих grants) нет.
- `person.update`: `admin` — `all`, любые поля Person, включая `email` и `birth_date` (намеренное административное исключение для исправления данных); `instructor` — **не имеет `person.update`**; `member` — `self`, только разрешённые поля; `guardian` — `children`, только разрешённые поля.
- Разрешённые поля для `member`/`self` и `guardian`/`children`: `first_name`, `last_name`, `middle_name`, `phone`, `address`, `photo` / avatar. `email`, `birth_date`, `id` и любые административные поля Person им изменять нельзя.
- Запрос, содержащий поле вне разрешённого для вызывающего набора, отклоняется backend authorization/validation; скрытие полей во frontend недостаточно.
- `id` неизменяем.
- `birth_date` доступен для чтения в authorized scope; изменять его может только `admin`.
- Отдельных `person.contact.read/update` permissions нет. Контакты являются полями Person и регулируются той же role/scope/object policy.
- `GuardianRelationship` сама по себе не является permission: доступ guardian к Person ребёнка определяется grants `person.read(children)` / `person.update(children)` (с ограничением полей выше) и проверкой активной `GuardianRelationship`.

**ClubMembership**

- Создание membership — только `admin`.
- Изменение `membership_type` — только `admin`.
- Lifecycle transitions — только `admin`.
- `archived` — terminal; `inactive → active` не допускается.
- Повторное вступление после `inactive` создаёт новый membership period.
- `member`, `guardian`, `instructor` не могут самостоятельно менять lifecycle membership.
- Read (`membership.read`): `admin` — `all`; `instructor` — `own_groups`; `member` — `self`; `guardian` — `children`.
- История читается через `GET /persons/{person_id}/memberships`; отдельный history endpoint не вводится.

**GuardianRelationship**

- Create/update/terminate — только `admin`.
- `instructor` может читать relationships в пределах Persons, достижимых через `own_groups`, но не изменяет их.
- `member` читает собственные relationship records.
- `guardian` читает собственные relationship records; доступ к другим представителям того же ребёнка не предоставляется.
- Не существует `primary_guardian`, `is_primary` или приоритета по порядку создания.
- `terminate` переводит relationship в `revoked`; `revoked` terminal, restore не предусмотрен.
- Потребность в прекращении связи, обнаруженная instructor, передаётся admin вне системы; отдельный request workflow не вводится.

**`/me/children`**

- Доступно только `guardian`.
- Возвращает только текущих детей с активной и interval-valid GuardianRelationship.
- Inactive/revoked/expired relationships исключаются.
- Projection: `id`, `last_name`, `first_name`, `middle_name`, `birth_date`, `photo_file_id`.
- Контакты ребёнка и сведения о других представителях не возвращаются.

## 8. Sensitive data

К sensitive domain относятся как минимум:

- документы;
- согласия;
- медицинская информация;
- финансовая информация;
- контактные данные несовершеннолетних;
- экстренные контакты.

Для People контактные поля `phone`, `email`, `address` не имеют отдельных permissions: доступ определяется канонической People role/scope/object policy из §7.1. Доступ к другим sensitive дочерним объектам Person проверяется отдельно. Нельзя считать, что доступ к Person автоматически означает доступ ко всем дочерним объектам Person.

## 9. Self access

`self` применяется только к данным, которые пользователь имеет право видеть о себе. Например, member может видеть свой профиль, свои мероприятия, свои достижения и свою историю посещения, но не получает право просматривать другого member через подмену идентификатора ресурса.

Для Person `self` предоставляется только тем ролям, чьи grants его содержат: `member` — `person.read(self)` и `person.update(self)`, изменение ограничено полями `first_name`, `last_name`, `middle_name`, `phone`, `address`, `photo` / avatar; `email` и `birth_date` member не изменяет. `admin` работает с Person через `all`. `instructor` и `guardian` не получают Person-доступ через `self` (см. §7.1).

## 10. Guardian access

`children` не означает доступ к любому ребёнку в клубе. Сервис должен вычислять допустимых детей через активные GuardianRelationship.

GuardianRelationship сама по себе не является grant: доступ guardian к Person ребёнка определяется `person.read(children)` / `person.update(children)` (изменение только разрешённых полей, §7.1). Для `/me/children` используется отдельная безопасная projection policy.

Удалённая/неактивная связь автоматически прекращает актуальный доступ, если отдельное правило не требует сохранения read-only исторического доступа.

Для Event guardian видит только мероприятия и связанные данные, относящиеся к связанным детям и разрешённые object policy. Роль `guardian` сама по себе не предоставляет `all`-доступ к мероприятиям клуба.

## 11. Instructor scope

Инструктор получает доступ только к объектам, для которых он назначен ответственным или которые принадлежат его группам, в соответствии с конкретным permission.

Роль instructor не должна автоматически давать доступ ко всем членам клуба.

Для Event `own_events` означает явное назначение/ответственность за мероприятие; `own_groups` означает ответственность за целевую группу. Инструкторские Event-операции не получают глобальный `all` scope только из роли instructor.

Для People `own_groups` означает цепочку Person → active ClubMembership → active GroupMembership → Group → active GroupInstructorAssignment → requesting User в том же Club; co-membership или одна роль instructor не являются достаточным основанием.

**Явное, единственное исключение (TH-0107, PO-решение):** `user.directory.read` (§4,
`docs/05-api/users-api.md`) — это НЕ `person.read` и не расширяет `person.read`/`own_groups`.
Это отдельный permission специально для операционного справочника пользователей
(`GET /api/v1/users`, используется Calendar), чья policy состоит только из club-boundary
(`UserRoleAssignment.club_id`) — без проверки `GroupInstructorAssignment`. В рамках именно этого
одного permission инструктор видит других инструкторов своего клуба; это не отменяет и не
ослабляет ничего из вышесказанного для `person.read`, `event.read`, `group.read` или любого
другого permission — co-membership по-прежнему не даёт доступа к Person/Event/Group данным.

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

Итоговые permissions формируются объединением permissions назначенных ролей с последующей проверкой scope и object-level policies. Каждый permission сохраняет собственные scopes: например, при `instructor + guardian` `own_groups` не превращается в `all` из-за наличия другой роли, а scopes одного permission не переносятся на другой.

Будущие explicit denies допускаются только после отдельного ADR, поскольку неверная реализация deny поверх role union может сделать модель трудно предсказуемой.


### 13.1 Person role management — ADR-0039

System roles are managed from Person Detail through the canonical RoleAssignment model/API.

The initial Person creation form does not ask for a system role. Person creation and initial ClubMembership creation remain separate from role assignment.

Canonical MVP roles:
- admin;
- instructor;
- member;
- guardian.

One Person/User may have multiple active roles simultaneously. Each active role is represented by exactly one `UserRoleAssignment` per Club; the same role is never assigned several times to express several scopes — scopes belong to the role's permission grants (ADR-0041). Role assignment (Person Detail, the Person creation wizard and the generic `/api/v1/role-assignments` API) does not choose or accept an authorization scope.

Role assignment/removal does not mutate Person, ClubMembership, membership status, or membership_type.

Role-specific relationship rules:
- instructor does not automatically assign GroupInstructorAssignment;
- instructor does not automatically become responsible for Events;
- guardian does not automatically create GuardianRelationship;
- after assigning guardian, the Person Detail workflow should allow an admin to link one or more children through GuardianRelationship;
- member requires no additional role-specific relationship setup;
- admin grants the existing admin role permissions and does not change Person or ClubMembership.

ClubMembership.membership_type is not a system role selector. For the current Person creation workflow, the initial membership remains fixed as member/active.

Implemented via `GET/POST /api/v1/persons/{person_id}/role-assignments` and `DELETE /api/v1/persons/{person_id}/role-assignments/{role_code}` (people-api.md §24.1) — a canonical-role-code-only, Person-scoped entry point onto the same `RoleAssignment` resource the flat `/api/v1/role-assignments` API already owns (ADR-0025 §6, ADR-0026); not a second model or a second API.

See ADR-0039.

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

Для People mutation + audit выполняются в одной DB transaction; failure audit приводит к rollback/fail-closed согласно ADR-0024 и ADR-0035.