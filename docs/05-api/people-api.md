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

People namespace:

`/api/v1/people`

Membership namespace:

`/api/v1/memberships`

Groups namespace:

`/api/v1/groups`

Guardians namespace:

`/api/v1/guardians`

## 4. Получение списка людей

### GET `/api/v1/people`

Permission: `person.read` с подходящим scope.

Поддерживает:

- pagination;
- search по разрешённым полям;
- filter по статусу;
- filter по возрастной группе;
- filter по текущему membership;
- filter по группе;
- sorting по разрешённым полям.

Для участников список не должен превращаться в глобальный каталог персональных данных. Scope определяется ролью и назначением пользователя.

## 5. Получение Person

### GET `/api/v1/people/{person_id}`

Доступ определяется `person.read` + scope.

API возвращает только поля, разрешённые конкретному requester.

Медицинские, контактные, документальные и иные чувствительные данные могут иметь отдельные permissions.

## 6. Создание Person

### POST `/api/v1/people`

Создание Person доступно уполномоченным администраторам/инструкторам согласно permission policy.

Person не создаётся автоматически только из-за существования User без проверки возможного duplicate.

Перед созданием должен выполняться controlled duplicate check по доступным идентификаторам.

## 7. Обновление Person

### PATCH `/api/v1/people/{person_id}`

Частичное обновление.

Rules:

- immutable identity fields изменяются только по определённой политике;
- чувствительные поля могут иметь отдельные permissions;
- каждое изменение значимого персонального поля должно быть audit'ed;
- optimistic concurrency должна использоваться там, где потеря параллельного изменения недопустима.

## 8. Архивирование Person

### POST `/api/v1/people/{person_id}/archive`

Архивирование не должно уничтожать историю мероприятий, походов, документов, финансов и аудита.

Физическое удаление Person по умолчанию запрещено.

## 9. Membership list

### GET `/api/v1/memberships`

Фильтры:

- status;
- membership_type;
- group;
- joined period;
- left period;
- person;
- active/current.

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

Один человек не должен получить несколько одновременно активных конфликтующих membership в одном клубе без явного правила.

## 11. Изменение membership

### PATCH `/api/v1/memberships/{membership_id}`

Изменяются только допустимые атрибуты текущего жизненного цикла.

Переходы статусов должны соответствовать бизнес-правилам.

## 12. Membership status transition

### POST `/api/v1/memberships/{membership_id}/status`

Request:

```json
{
  "status": "active",
  "reason": "..."
}
```

API валидирует допустимость перехода, permission requester и наличие обязательных данных.

## 13. История membership

### GET `/api/v1/memberships/{membership_id}/history`

Возвращает исторические изменения membership и связанные значимые события без раскрытия секретов.

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

Request:

```json
{
  "person_id": "...",
  "starts_at": "2026-09-12T00:00:00Z"
}
```

### POST `/api/v1/groups/{group_id}/members/{person_id}/transfer`

Переводит человека в другую группу.

Перевод должен завершать предыдущую active membership в группе и создавать новый исторический интервал.

## 16. Group membership history

### GET `/api/v1/people/{person_id}/groups`

Возвращает текущую и историческую принадлежность человека к группам.

## 17. Guardians

### GET `/api/v1/people/{person_id}/guardians`

Доступ только при наличии `guardian.read` и scope.

Для участника без соответствующего права наличие guardian relationship не должно автоматически раскрывать все данные законного представителя.

### POST `/api/v1/people/{person_id}/guardians`

Создаёт связь с существующим Person или запускает controlled linking flow.

Request concept:

```json
{
  "guardian_person_id": "...",
  "relationship_type": "parent",
  "is_primary": true,
  "status": "pending"
}
```

### PATCH `/api/v1/guardians/{relationship_id}`

Изменяет relationship type/status/primary contact согласно permission.

### POST `/api/v1/guardians/{relationship_id}/approve`

Подтверждает связь, когда для неё требуется подтверждение.

### POST `/api/v1/guardians/{relationship_id}/terminate`

Прекращает актуальность связи без уничтожения истории.

## 18. My children

### GET `/api/v1/me/children`

Возвращает детей текущего authenticated guardian, только по подтверждённым актуальным relationship.

Для каждого ребёнка возвращается только разрешённый parent-visible projection.

## 19. Child context

Для родителя frontend может выбирать активный child context, но backend на каждом запросе самостоятельно проверяет relationship и permission.

Наличие `child_id` в URL или query не является доказательством права доступа.

## 20. Pending registrations

### GET `/api/v1/memberships/pending`

Доступ администратора.

Возвращает заявки, ожидающие approval.

### POST `/api/v1/memberships/{membership_id}/approve`

Одобряет membership.

### POST `/api/v1/memberships/{membership_id}/reject`

Отклоняет заявку с обязательным reason.

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

## 23. Role assignment

### GET `/api/v1/users/{user_id}/roles`

Доступ администратора или requester с соответствующим permission.

### POST `/api/v1/users/{user_id}/roles`

Назначает роль в допустимом scope.

### DELETE `/api/v1/users/{user_id}/roles/{role_id}`

Удаляет назначение роли.

Правила:

- нельзя назначить роль выше собственных полномочий;
- системно критические административные роли требуют отдельной permission policy;
- смена ролей audit'ed;
- role assignment не меняет Person.

## 24. Instructor assignment

Инструктор — Person/User с соответствующим role assignment.

Связь инструктора с группой или мероприятием создаётся отдельной доменной сущностью, а не определяется только ролью.

## 25. Sensitive profile sections

API должен поддерживать отдельные policy areas для:

- contact data;
- address;
- medical/safety data;
- documents;
- emergency contacts;
- guardian data.

Не следует выдавать полный Person object любому requester с общим `person.read`.

## 26. Validation

Минимальные проверки:

- корректность форматов дат;
- логика возрастных данных;
- email/phone normalization;
- отсутствие невозможных интервалов membership;
- отсутствие двух active primary guardian relationships, если доменная политика запрещает это;
- невозможность привязать Person к архивной группе;
- невозможность создать relationship со статусом active без требуемого подтверждения;
- проверка существования и принадлежности объектов одному Club.

## 27. Audit

Audit обязателен для:

- создания/изменения/архивирования Person;
- изменения membership status;
- переводов между группами;
- создания/изменения/терминации guardian relationship;
- approval/rejection membership;
- role assignments;
- import execution.

## 28. Ошибки

Используется общий error contract.

Типовые ошибки:

- `PERSON_NOT_FOUND`;
- `MEMBERSHIP_NOT_FOUND`;
- `GROUP_NOT_FOUND`;
- `GUARDIAN_RELATIONSHIP_NOT_FOUND`;
- `DUPLICATE_PERSON`;
- `INVALID_MEMBERSHIP_TRANSITION`;
- `INVALID_GROUP_TRANSFER`;
- `GUARDIAN_LINK_NOT_ALLOWED`;
- `INSUFFICIENT_SCOPE`;
- `ROLE_ASSIGNMENT_NOT_ALLOWED`;
- `IMPORT_VALIDATION_FAILED`.

## 29. Acceptance Criteria

1. Person и User не смешиваются.
2. Один Person может иметь несколько доменных ролей.
3. Membership сохраняет историю.
4. Group membership сохраняет историю переходов.
5. Один guardian может иметь несколько детей.
6. Один ребёнок может иметь несколько guardians.
7. Parent API показывает только подтверждённых детей.
8. Child id никогда не заменяет authorization check.
9. Sensitive fields защищены отдельными permissions/scopes.
10. Pending membership не становится active без требуемого approval.
11. Import поддерживает dry-run и не разрушает существующие данные при частичной ошибке.
12. Role changes не изменяют Person или Membership.
13. Исторически значимые записи не удаляются физически по обычным CRUD endpoint'ам.
14. Значимые операции попадают в audit.
15. Все endpoint'ы соблюдают общие правила API и security.
