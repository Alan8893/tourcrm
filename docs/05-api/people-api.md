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
- `GroupMembership` — историческая принадлежность к группе;
- `GroupInstructorAssignment` — историческая ответственность инструктора за группу.

Один Person может одновременно быть участником клуба, инструктором и законным представителем.

## 3. Общий префикс

`/api/v1`

Persons namespace: `/api/v1/persons`

Ресурс использует имя `persons`, а не `people`: это приведено в соответствие с ADR-0004 (API-контракт должен соответствовать `docs/05-api/api-contract.md`), `api-contract.md` §4 (canonical resource naming: `/persons`) и `docs/05-api/endpoint-inventory.md` §3, которые уже независимо используют `/persons`. Ранее этот документ использовал `/api/v1/people`, что противоречило обоим документам; исправлено без изменения семантики endpoints.

Membership namespace: `/api/v1/memberships`

Groups namespace: `/api/v1/groups`

Group membership item namespace: `/api/v1/group-memberships` (top-level, по аналогии с `/api/v1/guardian-relationships` — ADR-0025 §4)

Group instructor assignment item namespace: `/api/v1/group-instructor-assignments` (top-level)

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

## 14. Группы (Group)

`Group` — самостоятельная доменная сущность, принадлежащая ровно одному `Club` (ADR-0021 §1). Канонические persistence-поля: `id`, `club_id`, `name`, `description`, `status`, `valid_from`, `valid_to`, `created_at`, `updated_at`.

`Group` остаётся универсальной сущностью без обязательного типа/категории. Это решение окончательно (Issue #69, PO decision): API не вводит поле типа/категории группы и не вводит отдельные сущности `Program`/`Section`/`Direction`. Конкретные туристские поездки/сборы/соревнования моделируются как `Event`, а не как `Group` — `Group` и `Event` не являются взаимозаменяемыми понятиями и не объединяются в этом контракте.

### 14.1. Lifecycle: `Group.status`

Канонический закрытый словарь `status` (Issue #69, PO decision — ADR-0021 §1 намеренно оставлял этот словарь открытым до отдельного business-policy решения, которое теперь принято):

- `active` — начальное и рабочее состояние; создаётся при `POST /api/v1/groups`;
- `archived` — терминальное состояние.

Единственный допустимый переход: `active → archived`, выполняется только через `POST /api/v1/groups/{group_id}/archive`. Обратного перехода (`archived → active`) не существует; отдельный "restore"/"unarchive" endpoint не вводится. `PATCH /api/v1/groups/{group_id}` не может изменять `status` напрямую.

Для архивной группы запрещено создание новых `GroupMembership` и `GroupInstructorAssignment` (см. §26). Завершение (`end`) уже существующих активных `GroupMembership`/`GroupInstructorAssignment` архивной группы остаётся разрешённым: это не создаёт новую ответственность/принадлежность и не противоречит терминальности архивирования.

### GET `/api/v1/groups`

Permission: `group.read`. Scope: `all` или `own_groups` (ADR-0021 §4 — `own_groups` определяется через explicit active `GroupInstructorAssignment` requester, а не через глобальную роль instructor).

Поддерживает pagination (`page`/`page_size`, ADR-0014/api-contract.md §7-8), фильтр `status` (`active`/`archived`, whitelist) и сортировку по whitelisted полям (`name`, `created_at`). Неизвестные поля сортировки/фильтра отклоняются (api-contract.md §9-10).

Результат ограничен объектами того же `Club`, что и requester context, и дополнительно — object-relationship policy применимого scope.

### GET `/api/v1/groups/{group_id}`

Permission: `group.read` + scope + object relationship (`own_groups`: требуется active `GroupInstructorAssignment` requester на данную группу).

IDOR/existence-hiding: несуществующая группа и группа, к которой requester не авторизован, возвращают одинаковый HTTP 404 с кодом `group_not_found` (см. §29, по аналогии с существующим GuardianRelationship-паттерном §18).

### POST `/api/v1/groups`

Permission: `group.manage`. Для создания требуется как минимум scope `all` — object-relationship scopes (`own_groups`/`self`/`children`) не имеют смысла для операции создания, поскольку целевого объекта ещё не существует, относительно которого можно было бы вычислить object relationship.

Созданная группа всегда получает `status = active` (§14.1); клиент не может передать `status` в теле запроса.

Request:

```json
{
  "club_id": "...",
  "name": "...",
  "description": "...",
  "valid_from": "2026-09-12T00:00:00Z",
  "valid_to": null
}
```

Response: `201 Created`, представление `Group` напрямую (без обёртки, ADR-0014).

### PATCH `/api/v1/groups/{group_id}`

Permission: `group.manage` + scope + object relationship (как в GET item).

Изменяемые поля: `name`, `description`, `valid_from`, `valid_to`. `status` и `club_id` не изменяются через этот endpoint (`club_id` неизменяем после создания; `status` изменяется только через `POST .../archive`, §14.1).

Concurrency/idempotency: как и `PATCH /api/v1/persons/{person_id}` (§7), в кодовой базе нет project-wide optimistic-concurrency механизма ни у одного реализованного домена; `PATCH` использует last-write-wins семантику — принятая граница текущего slice.

### POST `/api/v1/groups/{group_id}/archive`

Permission: `group.manage` + scope + object relationship.

Выполняет единственный допустимый переход `active → archived` (§14.1). Повторный вызов для уже `archived` группы отклоняется с кодом `invalid_group_status_transition`, HTTP 409. История группы (её `GroupMembership`/`GroupInstructorAssignment`) не уничтожается.

## 15. Group membership (GroupMembership)

`GroupMembership` — историческая связь между `ClubMembership` и `Group` (ADR-0021 §2). Канонические persistence-поля: `id`, `group_id`, `club_membership_id`, `valid_from`, `valid_to`, `membership_status`, `created_at`, `updated_at`. `person_id` не хранится — Person разрешается через `club_membership_id -> ClubMembership.person_id`.

### 15.1. Lifecycle: `GroupMembership.membership_status`

Канонический закрытый словарь `membership_status` (Issue #69, PO decision):

- `active` — начальное состояние; создаётся при `POST /api/v1/groups/{group_id}/members`;
- `ended` — терминальное состояние.

Единственный допустимый переход: `active → ended`, выполняется только через `POST /api/v1/group-memberships/{id}/end`. Обратного перехода нет.

### 15.2. Multi-group membership и cross-Club integrity

Один человек может одновременно иметь несколько активных `GroupMembership` в **разных** группах, включая пересекающиеся по времени интервалы `[valid_from, valid_to)` — ограничение на это не вводится (Issue #69, PO decision).

В пределах **одной и той же** группы дублирующее одновременное активное членство запрещено: не более одной записи `GroupMembership` со `membership_status = active` может существовать для одной и той же пары `(group_id, club_membership_id)` одновременно (Issue #69, PO decision). Это — обязательный database invariant и реализовано на уровне БД текущим Group API implementation slice через temporal/exclusion constraint на `[valid_from, valid_to)` для активных записей. Простого partial unique index недостаточно, поскольку каноническое правило допускает последовательные исторические интервалы.

Cross-Club integrity (ADR-0022 §4): `GroupMembership` валиден только при `Group.club_id == ClubMembership.club_id`. Проверка и запись выполняются в одной транзакции тем же каноническим service-механизмом, что реализован в `apps/api/app/groups/service.py` (implementation evidence, не источник бизнес-решения) — endpoint должен вызывать этот существующий shared-механизм, а не дублировать проверку.

### 15.3. Перевод между группами — нет отдельного endpoint

Отдельного endpoint `.../members/{person_id}/transfer` не существует (Issue #69, PO decision, закрывает GAP-GROUP-004). Перевод человека в другую группу выполняется клиентом как две отдельные операции: `POST /api/v1/group-memberships/{id}/end` для текущей группы и `POST /api/v1/groups/{new_group_id}/members` для новой. Автоматической атомарной операции массового перевода API не предоставляет.

### 15.4. Bulk endpoint — отложен

`POST /api/v1/groups/{id}/members/bulk` (упомянут в `docs/05-api/endpoint-inventory.md` §7) не специфицируется в рамках этого контракта и не реализуется текущим implementation slice. Он остаётся deferred/not-MVP: отдельной независимой необходимости в нём не установлено, и данный документ не проектирует его request/response contract.

### GET `/api/v1/groups/{group_id}/members`

Permission: `group.read` + scope + object relationship на `group_id` (как в §14 GET item).

Pagination обязательна. Фильтр `membership_status` (`active`/`ended`, whitelist).

### POST `/api/v1/groups/{group_id}/members`

Permission: `group.manage` + scope + object relationship на `group_id`.

API принимает `person_id` в теле запроса; backend обязан разрешить активный `ClubMembership` этого Person в Club целевой группы и выполнить cross-Club validation (§15.2) — persistence-модель хранит `club_membership_id`, не `person_id` (ADR-0021 §2).

Создание запрещено, если `Group.status = archived` (§14.1) — код ошибки `group_archived`, HTTP 409.

Создаваемая запись всегда получает `membership_status = active`; клиент не передаёт `membership_status`.

Request:

```json
{
  "person_id": "...",
  "valid_from": "2026-09-12T00:00:00Z",
  "valid_to": null
}
```

`starts_at` не является каноническим именем поля; использовать `valid_from` (согласовано с ADR-0021 §2 и остальным контрактом).

Response: `201 Created`.

### PATCH `/api/v1/group-memberships/{id}`

Permission: `group.manage` + scope + object relationship на группу, к которой принадлежит membership.

Изменяемое поле: только `valid_from` (корректировка даты начала интервала — не lifecycle-переход). `group_id`, `club_membership_id` и `membership_status` через `PATCH` не изменяются: изменение `group_id`/`club_membership_id` было бы переводом в другую группу, что не поддерживается этим endpoint (§15.3); изменение `membership_status` выполняется только через `POST .../end` (§15.1). Попытка передать любое из этих полей отклоняется с кодом `group_membership_immutable_field`, HTTP 422.

IDOR/existence-hiding: как в §29.

### POST `/api/v1/group-memberships/{id}/end`

Permission: `group.manage` + scope + object relationship.

Выполняет единственный допустимый переход `active → ended` (§15.1), устанавливает `valid_to`. Повторный вызов для уже `ended` записи отклоняется с кодом `invalid_group_membership_transition`, HTTP 409.

## 16. Group instructor assignment (GroupInstructorAssignment)

`GroupInstructorAssignment` — явная историческая ответственность `User` за `Group` (ADR-0021 §3). Глобальная роль `instructor` сама по себе не создаёт такой ответственности. Канонические persistence-поля: `id`, `group_id`, `user_id`, `role_in_group`, `is_primary`, `valid_from`, `valid_to`, `created_at`, `updated_at`.

`role_in_group` — свободное текстовое поле; закрытый словарь допустимых значений этим контрактом не вводится (ADR-0021 §3 оставляет его согласование с моделью Event-ответственности будущим решением) — API не валидирует его по closed enum.

Эта сущность не заменяет `EventStaffAssignment` (ADR-0023) и не используется для мероприятий; ответственность инструктора за конкретный `Event` — отдельная связь (см. §25). Родители/guardians не становятся инструкторами группы через этот механизм — участие guardian в Event остаётся отдельной, не связанной с `GroupInstructorAssignment` областью.

### 16.1. Lifecycle

`GroupInstructorAssignment` не имеет отдельного полевого статуса — его период действия представлен полуоткрытым интервалом `[valid_from, valid_to)`: `valid_from` — момент начала действия, `valid_to = NULL` — открытый (бесконечный) конец периода. На заданный момент времени `t` запись считается действующей, если `valid_from <= t` и (`valid_to` не установлен либо `t < valid_to`). Завершение выполняется только через `POST /api/v1/group-instructor-assignments/{id}/end`, который устанавливает `valid_to`. Отдельного `PATCH` endpoint для этой сущности нет (см. `docs/05-api/endpoint-inventory.md` §7) — изменение `role_in_group`/`is_primary` существующей записи не поддерживается; для этого запись завершается и создаётся новая.

### 16.2. `is_primary` — не более одного одновременно действующего primary на группу

Для одной и той же `group_id` не допускается существование двух записей `GroupInstructorAssignment` с `is_primary = true`, чьи периоды действия **пересекаются** (Issue #69, PO decision, закрывает GAP-GROUP-005). Каноническое правило оперирует именно пересечением интервалов, а не «активностью на текущий момент» — назначение primary на будущий или прошедший исторический период учитывается наравне с назначением, действующим прямо сейчас.

Период каждого assignment — полуоткрытый интервал `[valid_from, valid_to)`, где `valid_to = NULL` означает открытый (бесконечный) конец. Два `is_primary = true` assignment одной группы конфликтуют тогда и только тогда, когда их интервалы `[valid_from₁, valid_to₁)` и `[valid_from₂, valid_to₂)` пересекаются, то есть:

```text
valid_from₁ < valid_to₂ (или valid_to₂ не установлен)
И
valid_from₂ < valid_to₁ (или valid_to₁ не установлен)
```

Совпадение границы — `valid_to` одного assignment равен `valid_from` другого — пересечением **не считается**: последовательные исторические primary-назначения без временного наложения допустимы. Например:

- Допустимо: A = `[2026-09-01, 2026-10-01)`, B = `[2026-10-01, NULL)` — границы совпадают, интервалы не пересекаются.
- Допустимо: A = `[2026-09-01, 2026-09-30)`, B = `[2026-10-01, NULL)` — непересекающиеся исторические/текущий период.
- Запрещено: A = `[2026-09-01, 2026-09-30)`, B = `[2026-09-15, 2026-10-15)` — частичное пересечение.
- Запрещено: A = `[2026-09-01, NULL)`, B = `[2026-10-01, NULL)` — оба открытых периода пересекаются после 2026-10-01.

Это — обязательный database invariant и реализовано на уровне БД текущим Group API implementation slice через temporal/exclusion constraint на `[valid_from, valid_to)` для `is_primary = true` записей. Простой partial unique index по `group_id WHERE is_primary = true` не используется, поскольку он запретил бы допустимые последовательные исторические primary-назначения.

Попытка создать `is_primary = true` запись, чей интервал пересекается с интервалом уже существующей `is_primary = true` записи той же группы, отклоняется с кодом `duplicate_primary_instructor`, HTTP 409. Автоматическое понижение (demotion) существующего primary-инструктора **не выполняется** — это не является канонически принятым поведением (Issue #69, PO decision explicitly excludes automatic demotion). Чтобы назначить нового primary-инструктора на пересекающийся период, клиент должен сначала завершить (сократить интервал) существующую конфликтующую primary-запись (`POST .../end`), а затем создать новую с `is_primary = true`.

### 16.3. Cross-Club integrity

Cross-Club integrity (ADR-0022 §5): `GroupInstructorAssignment` валиден только если Person назначаемого `User` имеет **активный** `ClubMembership` в Club целевой группы; сам факт наличия глобальной роли `instructor` недостаточен. Проверка и запись выполняются в одной транзакции тем же каноническим service-механизмом, что реализован в `apps/api/app/groups/service.py` (implementation evidence) — endpoint должен вызывать этот существующий shared-механизм.

### GET `/api/v1/groups/{group_id}/instructors`

Permission: `group.read` + scope + object relationship на `group_id`.

Pagination обязательна. Канонический фильтр активности называется `has_ended`:

- `has_ended=false` — только действующие назначения, у которых `valid_to IS NULL`;
- `has_ended=true` — только завершённые назначения, у которых `valid_to IS NOT NULL`;
- параметр не передан — без фильтра по активности.

Имя `has_ended` выбрано как каноническое query-поле для явного различения активных и завершённых исторических assignment и является частью текущего API-контракта. Фильтр не изменяет point-in-time semantics §16.1.

### POST `/api/v1/groups/{group_id}/instructors`

Permission: `group.manage` + scope + object relationship на `group_id`.

Создание запрещено, если `Group.status = archived` (§14.1) — код `group_archived`, HTTP 409. Cross-Club validation — см. §16.3. `is_primary`-invariant — см. §16.2.

Request:

```json
{
  "user_id": "...",
  "role_in_group": "instructor",
  "is_primary": false,
  "valid_from": "2026-09-12T00:00:00Z",
  "valid_to": null
}
```

Response: `201 Created`.

### POST `/api/v1/group-instructor-assignments/{id}/end`

Permission: `group.manage` + scope + object relationship.

Устанавливает `valid_to` (§16.1). Повторный вызов для уже завершённой записи отклоняется с кодом `invalid_group_instructor_assignment_transition`, HTTP 409.

## 17. Group membership history

### GET `/api/v1/persons/{person_id}/groups`

Возвращает текущую и историческую принадлежность человека к группам.

## 18. Guardians

`GuardianRelationship` — Club-neutral связь Person ↔ Person. Канонические persistence-поля:

- `id`;
