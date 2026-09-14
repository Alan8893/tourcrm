# TourCRM — People & Membership API

## 1. Назначение

Данный документ является детальным API-контрактом домена людей, членства клуба, групп, законных представителей, регистрации и приглашений.

Канонические сущности и термины определены в `docs/03-architecture/domain-model.md` и `docs/01-product/glossary.md`.

## 2. Основной принцип идентичности

API не смешивает:

- `Person` — физическое лицо;
- `User` — учётную запись;
- `ClubMembership` — членство человека в клубе;
- `RoleAssignment` — полномочия пользователя;
- `GuardianRelationship` — связь законного представителя с ребёнком;
- `GroupMembership` — историческая принадлежность к группе.

Один Person может одновременно быть участником клуба, инструктором и законным представителем.

## 3. Общий префикс

`/api/v1`

Persons namespace: `/api/v1/persons`

Ресурс использует имя `persons`, а не `people`: это приведено в соответствие с ADR-0004 (API-контракт должен соответствовать `docs/05-api/api-contract.md`), `api-contract.md` §4 (canonical resource naming: `/persons`) и `docs/05-api/endpoint-inventory.md` §3, которые уже независимо используют `/persons`. Ранее этот документ использовал `/api/v1/people`, что противоречило обоим документам; исправлено без изменения семантики endpoints.

Membership namespace: `/api/v1/memberships`

Groups namespace: `/api/v1/groups`

GuardianRelationship namespace: `/api/v1/guardian-relationships` (ADR-0025 §4 — `/guardians` is not used as an alias)

## 4. Получение списка людей

### GET `/api/v1/persons`

Permission: `person.read` с подходящим scope.

Поддерживает pagination, search, filter и sorting по разрешённым полям.

Для участников список не должен превращаться в глобальный каталог персональных данных. Scope определяется ролью и назначением пользователя.

## 5. Получение Person

### GET `/api/v1/persons/{person_id}`

Доступ определяется `person.read` + scope.

API возвращает только поля, разрешённые конкретному requester.

Медицинские, контактные, документальные и иные чувствительные данные могут иметь отдельные permissions.

## 6. Создание Person

### POST `/api/v1/persons`

Создание Person доступно уполномоченным администраторам/инструкторам согласно permission policy.

ADR-0025 §9: heuristic duplicate detection (similarity/fuzzy matching, email/phone scoring, автоматическое объединение) не реализуется в текущем MVP slice. `DUPLICATE_PERSON` остаётся зарезервированным error-кодом для потенциального будущего использования, а не требованием текущего slice.

## 7. Обновление Person

### PATCH `/api/v1/persons/{person_id}`

Частичное обновление с audit для значимых изменений.

ADR-0025 §10: в кодовой базе нет уже принятого project-wide optimistic-concurrency механизма (ни у одного реализованного домена — Events, Groups — его нет; `api-contract.md` §19/`api-conventions.md` §17 оставляют выбор конкретного механизма за доменом). Текущий slice не изобретает новый механизм: `PATCH` использует last-write-wins семантику, что явно фиксируется как принятая граница текущего slice, а не как недосмотр.

## 8. Архивирование Person

### POST `/api/v1/persons/{person_id}/archive`

Архивирование не уничтожает историю мероприятий, походов, документов, финансов и аудита.

Физическое удаление Person по умолчанию запрещено.

## 9. Membership list

### GET `/api/v1/memberships`

Фильтры: status, membership_type, group, joined period, left period, person, active/current.

## 10. Создание membership

### POST `/api/v1/memberships`

Создаёт связь Person ↔ Club.

Request concept:

```json
{
  "person_id": "...",
  "membership_type": "member",
  "status": "pending",
  "joined_at": "2026-09-12T00:00:00Z"
}
```

## 11. Изменение membership

### PATCH `/api/v1/memberships/{membership_id}`

Изменяются только допустимые атрибуты текущего жизненного цикла.

## 12. Membership status transition

### POST `/api/v1/memberships/{membership_id}/status`

Request:

```json
{
  "status": "active",
  "reason": "..."
}
```

API валидирует допустимость перехода, permission requester и обязательные данные.

## 13. Membership history

### GET `/api/v1/persons/{person_id}/memberships`

Возвращает membership-периоды указанного Person, включая текущие и исторические периоды, согласно canonical `ClubMembership` lifecycle.

Один `ClubMembership` представляет один непрерывный период членства. Повторное вступление после `inactive` создаёт новый membership period, а не переиспользует существующую запись (Issue #62 accepted decisions).

Изменения membership и значимые lifecycle transitions (создание, `membership_type`, status transitions) фиксируются через audit infrastructure согласно ADR-0024, а не через отдельный read API.

Отдельный endpoint `GET /api/v1/memberships/{membership_id}/history` не используется и не является частью текущего контракта: после реализации Issue #62 он был явно исключён из API slice (см. implementation report Issue #62), поскольку для `ClubMembership` не существует отдельной history/versioning persistence-модели, а полноценный audit-read API не входит в non-goals текущего slice.

## 14. Группы

### GET `/api/v1/groups`

Возвращает группы, доступные requester.

### POST `/api/v1/groups`

Создаёт группу.

### GET `/api/v1/groups/{group_id}`

Получение группы с метаданными и текущим составом, если scope разрешён.

### PATCH `/api/v1/groups/{group_id}`

Изменение группы.

### POST `/api/v1/groups/{group_id}/archive`

Архивирует группу, сохраняя историю.

## 15. Group membership

### GET `/api/v1/groups/{group_id}/members`

Список участников группы с pagination.

### POST `/api/v1/groups/{group_id}/members`

Добавляет человека в группу.

API может принимать `person_id` как идентификатор человека, но persistence-модель `GroupMembership` хранит `club_membership_id`; backend обязан разрешить Person в membership целевого Club и выполнить cross-Club validation согласно ADR-0022.

Request:

```json
{
  "person_id": "...",
  "starts_at": "2026-09-12T00:00:00Z"
}
```

### POST `/api/v1/groups/{group_id}/members/{person_id}/transfer`

Переводит человека в другую группу.

Перевод должен завершать предыдущий актуальный исторический интервал и создавать новый согласно канонической persistence-модели.

## 16. Group membership history

### GET `/api/v1/persons/{person_id}/groups`

Возвращает текущую и историческую принадлежность человека к группам.

## 17. Guardians

`GuardianRelationship` — Club-neutral связь Person ↔ Person. Канонические persistence-поля:

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

Self-link guardian → same person запрещён. Дублирующие активные relationships одного типа для одной пары не допускаются; исторические `inactive`/`revoked` сохраняются. Для ребёнка допускается не более одной одновременно действующей primary-contact relationship.

Permissions: `guardian_relationship.read` (чтение), `guardian_relationship.manage` (создание/изменение/terminate) — приняты ADR-0025 §2. Ранее использовавшийся здесь `guardian.read` не был каноническим permission и заменён. Никакие другие GuardianRelationship-специфичные permissions не вводятся.

URI: канонический ресурс — `guardian-relationships`, не `guardians` (ADR-0025 §4). `/guardians` не сохраняется как alias нигде в этом контракте, включая вложенную коллекцию под Person.

Authorization/scope: `GuardianRelationship` остаётся Club-neutral и не имеет `club_id` (ADR-0023 §3) — обычное club-scoped assignment само по себе не превращается в доступ к `GuardianRelationship`: только глобальное (без `club_id`) assignment авторизует доступ к этой сущности. Из канонического scope vocabulary (ADR-0013) для `GuardianRelationship` применимы `all`, `self`, `children`, `none`; `own_groups`/`own_events` к этой сущности неприменимы (нет Group/Event relationship) и всегда fail closed. `self` означает, что requester сам является `child_person_id` конкретного relationship. `children` означает, что requester — активный (`status = active`, в пределах `[valid_from, valid_to)`) guardian ребёнка, к которому относится relationship. Новые scopes не вводятся.

### GET `/api/v1/persons/{person_id}/guardian-relationships`

Доступ только при наличии `guardian_relationship.read` и подходящего scope.

### POST `/api/v1/persons/{person_id}/guardian-relationships`

Создаёт `GuardianRelationship` с существующим Person непосредственно. Permission: `guardian_relationship.manage`.

Request concept:

```json
{
  "guardian_person_id": "...",
  "relationship_type": "parent",
  "is_primary_contact": true,
  "status": "active"
}
```

Создаваемый relationship всегда имеет `status = active`. Состояние `pending` для `GuardianRelationship` не существует и backend не должен его принимать.

Отдельный confirmation/controlled-linking workflow в текущем MVP не используется. Если в будущем потребуется подтверждение связи, оно должно быть реализовано как отдельный workflow/entity (аналогично `RegistrationRequest` для `ClubMembership`, ADR-0025 §5) и не должно вводить `pending` в lifecycle `GuardianRelationship`.

### PATCH `/api/v1/guardian-relationships/{relationship_id}`

Изменяет relationship type/primary contact согласно permission (`guardian_relationship.manage`) и lifecycle rules. Не изменяет `status` напрямую — переходы `status` выполняются только через `terminate` (ниже) либо natural lifecycle (истечение `valid_to`).

### POST `/api/v1/guardian-relationships/{relationship_id}/terminate`

Прекращает актуальность связи без уничтожения истории. Permission: `guardian_relationship.manage`.

Каноническая семантика (ADR-0025 §3): `terminate` всегда переводит relationship в `status = revoked`. Альтернативного исхода нет; `terminate` уже `revoked` relationship отклоняется (соответствующий HTTP status, canonical error code `guardian_link_not_allowed` — см. §28). `inactive` — отдельное, не-revoked историческое состояние и никогда не является результатом `terminate`.

### Lifecycle: stored status и read-time expiry

Canonical stored-значения `status`: `active`, `inactive`, `revoked`. Других значений (в частности `pending`, `verified`, `rejected`, `terminated`) не существует.

`inactive` достигается естественным истечением `valid_to`, а не отдельным действием API. Это оценивается **at read time**: если stored `status = active`, но `valid_to` уже в прошлом, API при чтении (в списках, в детальном представлении, при authorization-проверках) рассматривает relationship как `inactive`. При этом stored значение в БД не переписывается никаким write-действием, и для этого не используется background job, scheduler или отдельный worker — производный статус вычисляется непосредственно в момент запроса.

## 18. My children

### GET `/api/v1/me/children`

Возвращает детей текущего authenticated guardian только по active, interval-valid `GuardianRelationship` и при выполнении authorization policy (`guardian_relationship.read`).

Endpoint не принимает `guardian_id`, `person_id` или любой другой client-supplied UUID, который мог бы подменить собой authenticated principal — единственный источник идентичности requester это сессия. Наличие такого параметра в query не является и не может являться доказательством права доступа.

Возвращает только собственных детей requester: Persons, для которых существует relationship с `guardian_person_id = requester`, `status = active` и текущим моментом внутри `[valid_from, valid_to)` (см. §17 "Lifecycle: stored status и read-time expiry"). `revoked` relationships исключаются всегда; relationships с истёкшим `valid_to` исключаются как не-active по той же read-time-логике.

Parent-visible projection для каждого ребёнка ограничена полями:

```text
id
full_name
birth_date
photo_file_id
```

`phone`, `email`, `address` и другие чувствительные Person-поля в этой projection не возвращаются (см. §25).

## 19. Child context

Для родителя frontend может выбирать active child context, но backend на каждом запросе самостоятельно проверяет relationship и permission.

Наличие `child_id` в URL или query не является доказательством права доступа.

## 20. Pending registrations — вынесено из текущего контракта

ADR-0025 §5: самостоятельная регистрация — отдельная сущность/workflow `RegistrationRequest` (`docs/04-modules/people-and-membership.md` §10), а не переход `ClubMembership.status`. Ранее описанные здесь `GET /api/v1/memberships/pending`, `POST /api/v1/memberships/{membership_id}/approve`, `POST /api/v1/memberships/{membership_id}/reject` описывали конфликтующую модель и удалены из контракта.

`RegistrationRequest` persistence-модель, API и approval workflow остаются вне scope текущего implementation slice People & Membership и требуют отдельного Issue после отдельной спецификации.

## 21. Import

### POST `/api/v1/memberships/imports`

Создаёт import job для загрузки участников из согласованного формата.

Import должен быть асинхронным, если размер превышает синхронный лимит.

### GET `/api/v1/memberships/imports/{import_id}`

Возвращает статус и статистику import job.

### GET `/api/v1/memberships/imports/{import_id}/errors`

Возвращает строки/ошибки импорта без раскрытия чужих конфиденциальных данных сверх прав requester.

Import должен поддерживать dry-run до применения изменений.

## 22. Invitation

Auth contract определён в `docs/05-api/auth-api.md`.

People API предоставляет административное представление приглашённого membership после успешной активации.

## 23. Role assignment — вынесено из текущего контракта

ADR-0025 §6: канонический API-ресурс — top-level `/api/v1/role-assignments` (совпадает с `docs/05-api/endpoint-inventory.md` §24), а не вложенный `/api/v1/users/{user_id}/roles`, ранее описанный здесь. Role assignment API реализуется отдельным Issue вне текущего implementation slice People & Membership.

Role assignment не меняет Person.

## 24. Instructor assignment

Инструктор — Person/User с соответствующим role assignment. Само наличие роли не означает ответственность за конкретную группу или Event.

Для группы используется `GroupInstructorAssignment`.

Для мероприятия используется `EventStaffAssignment`, определённая ADR-0023. Она является явным источником `own_events`; `Event.created_by` не является заменой этой связи.

## 25. Sensitive profile sections

API должен поддерживать отдельные policy areas для contact data, address, medical/safety data, documents, emergency contacts и guardian data.

Не следует выдавать полный Person object любому requester с общим `person.read`.

ADR-0025 §8: до определения отдельной permission/scope policy для этих полей `phone`, `email` и `address` не выдаются через baseline Person API ни одному requester (включая обладателя `person.read`). Это принятое ограничение scope текущего implementation slice, а не временный недосмотр.

## 26. Validation

Минимальные проверки:

- корректность форматов дат;
- отсутствие невозможных интервалов membership;
- корректность guardian relationship lifecycle;
- отсутствие более одной действующей primary-contact relationship для ребёнка;
- невозможность привязать Person к архивной группе;
- проверка существования и принадлежности объектов одному Club там, где это применимо;
- Guardian authorization учитывает active relationship и interval validity.

## 27. Audit

Audit обязателен для создания/изменения/архивирования Person, membership status, переводов между группами, создания/изменения/терминации GuardianRelationship, role assignments и import execution.

## 28. Ошибки

Используется общий error contract.

Типовые ошибки (перечислены в исходном/каноническом написании этого раздела; фактический machine-readable `code` в реализованных доменах — lowercase snake_case, например `guardian_relationship_not_found`, `invalid_membership_transition`):

- `PERSON_NOT_FOUND`;
- `MEMBERSHIP_NOT_FOUND`;
- `GROUP_NOT_FOUND`;
- `GUARDIAN_RELATIONSHIP_NOT_FOUND` (реализовано как `guardian_relationship_not_found`, HTTP 404);
- `DUPLICATE_PERSON`;
- `INVALID_MEMBERSHIP_TRANSITION`;
- `INVALID_GROUP_TRANSFER`;
- `GUARDIAN_LINK_NOT_ALLOWED` (реализовано как `guardian_link_not_allowed`);
- `INSUFFICIENT_SCOPE`;
- `ROLE_ASSIGNMENT_NOT_ALLOWED`;
- `IMPORT_VALIDATION_FAILED`.

### GuardianRelationship: existence-hiding для `PATCH`/`terminate`

`PATCH /api/v1/guardian-relationships/{relationship_id}` и `POST /api/v1/guardian-relationships/{relationship_id}/terminate` защищены от IDOR через existence-hiding: relationship, который реально не существует, и relationship, который существует, но requester к нему не авторизован, возвращают одинаковый HTTP 404 с одинаковым machine-readable кодом `guardian_relationship_not_found` — по публичному ответу их невозможно отличить друг от друга.

Для validation/business-link ошибок (self-link, дублирующая active relationship, дублирующая primary-contact relationship, повторный `terminate` уже `revoked` relationship) используется canonical machine-readable код `guardian_link_not_allowed` с соответствующим HTTP status (422 для validation-ошибок при создании/изменении, 409 для повторного `terminate`).

### `INSUFFICIENT_SCOPE`

Authorization denial (нет permission, либо permission есть, но scope/object relationship не подходит) во всех реализованных доменах — Person, Membership, GuardianRelationship и Event — использует единый generic-механизм и возвращает `forbidden`, не раскрывая, какая именно permission или scope не подошли. Это намеренная security-политика: ответ не должен давать requester информацию, полезную для подбора доступа. `INSUFFICIENT_SCOPE` как отдельный machine-readable код в реализованных доменах не эмитируется; в этом контракте он остаётся зарезервированным/непроверенным написанием, а не описанием фактического поведения API.

## 29. Acceptance Criteria

1. Person и User не смешиваются.
2. Один Person может иметь несколько доменных ролей.
3. Membership сохраняет историю.
4. Group membership сохраняет историю.
5. Один guardian может иметь несколько детей.
6. Один ребёнок может иметь несколько guardians.
7. Parent API показывает только разрешённых детей по GuardianRelationship.
8. Child id никогда не заменяет authorization check.
9. Sensitive fields защищены отдельными permissions/scopes.
10. Pending membership не становится active без требуемого approval.
11. Import поддерживает dry-run.
12. Role changes не изменяют Person или Membership.
13. Исторически значимые записи не удаляются физически по обычным CRUD endpoint'ам.
14. Значимые операции попадают в audit.
15. Все endpoint'ы соблюдают общие правила API и security.
