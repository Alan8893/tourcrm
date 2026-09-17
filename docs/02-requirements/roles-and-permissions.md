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

Канонический каталог People permissions включает:

- `person.read`
- `person.create`
- `person.update`
- `membership.read`
- `membership.manage`
- `guardian_relationship.read`
- `guardian_relationship.manage`

`person.create` является отдельным каноническим permission и предоставляется только `admin`.

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
| Свой Person: чтение | ✅ | ✅ | ✅ | ✅ |
| Свой Person: изменение | ✅ | ✅ | ✅ | ✅ |
| Свой Person: изменение birth_date | ✅ | ❌ | ❌ | ❌ |
| Любой Person: чтение | ✅ в all scope | по `own_groups` | ❌ | ❌ |
| Любой Person: изменение | ✅ в all scope | по `own_groups` | ❌ | ❌ |
| Person: создание | ✅ `person.create` | ❌ | ❌ | ❌ |
| Membership: чтение | ✅ authorized | `own_groups` | self | children/relationship |
| Membership: создание | ✅ | ❌ | ❌ | ❌ |
| Membership: изменение type | ✅ | ❌ | ❌ | ❌ |
| Membership: lifecycle | ✅ | ❌ | ❌ | ❌ |
| GuardianRelationship: чтение | authorized global | по `own_groups` | собственные relationship records | собственные relationship records |
| GuardianRelationship: создание | ✅ | ❌ | ❌ | ❌ |
| GuardianRelationship: изменение | ✅ | ❌ | ❌ | ❌ |
| GuardianRelationship: terminate | ✅ | ❌ | ❌ | ❌ |
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

### 7.1 People Management — каноническая policy

`Person` является Club-neutral identity. В MVP Person не имеет `status`, не архивируется и не удаляется через People API; см. ADR-0034.

**Person**

- `person.create`: только `admin`.
- `person.read`: каждый пользователь может читать собственный Person; `admin` — Persons в authorized `all` scope; `instructor` — Persons только через `own_groups`; `member` и `guardian` не получают общего доступа к другим Persons.
- `person.update`: каждый пользователь может изменять собственный Person в разрешённых полях; `admin` — Persons в authorized `all` scope; `instructor` — Persons через `own_groups`.
- `id` неизменяем.
- `first_name`, `last_name`, `middle_name`, `phone`, `email`, `address`, `photo` доступны для изменения пользователем в рамках его собственной записи; `admin`/`instructor` также могут изменять эти поля у Persons в своей authorized scope.
- `birth_date` доступен для чтения в authorized scope; изменять его может только `admin`, включая собственный Person.
- Отдельных `person.contact.read/update` permissions нет. Контакты являются полями Person и регулируются той же role/scope/object policy.
- `GuardianRelationship` не даёт автоматического доступа к контактам ребёнка.

**ClubMembership**

- Создание membership — только `admin`.
- Изменение `membership_type` — только `admin`.
- Lifecycle transitions — только `admin`.
- `archived` — terminal; `inactive → active` не допускается.
- Повторное вступление после `inactive` создаёт новый membership period.
- `member`, `guardian`, `instructor` не могут самостоятельно менять lifecycle membership.
- Read: `admin` — authorized full; `instructor` — `own_groups`; `member` — self; `guardian` — children/relationship.
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

Все четыре базовые роли могут читать и изменять собственные `first_name`, `last_name`, `middle_name`, `phone`, `email`, `address` и `photo`. `birth_date` для self доступен всем для чтения, но изменяется только admin.

## 10. Guardian access

`children` не означает доступ к любому ребёнку в клубе. Сервис должен вычислять допустимых детей через активные GuardianRelationship.

GuardianRelationship сам по себе не расширяет доступ guardian к полному Person ребёнка и не предоставляет его контакты. Для `/me/children` используется отдельная безопасная projection policy.

Удалённая/неактивная связь автоматически прекращает актуальный доступ, если отдельное правило не требует сохранения read-only исторического доступа.

Для Event guardian видит только мероприятия и связанные данные, относящиеся к связанным детям и разрешённые object policy. Роль `guardian` сама по себе не предоставляет `all`-доступ к мероприятиям клуба.

## 11. Instructor scope

Инструктор получает доступ только к объектам, для которых он назначен ответственным или которые принадлежат его группам, в соответствии с конкретным permission.

Роль instructor не должна автоматически давать доступ ко всем членам клуба.

Для Event `own_events` означает явное назначение/ответственность за мероприятие; `own_groups` означает ответственность за целевую группу. Инструкторские Event-операции не получают глобальный `all` scope только из роли instructor.

Для People `own_groups` означает цепочку Person → active ClubMembership → active GroupMembership → Group → active GroupInstructorAssignment → requesting User в том же Club; co-membership или одна роль instructor не являются достаточным основанием.

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

Для People mutation + audit выполняются в одной DB transaction; failure audit приводит к rollback/fail-closed согласно ADR-0024 и ADR-0035.