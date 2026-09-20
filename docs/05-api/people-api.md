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

Контактные поля `phone`, `email`, `address` являются полями Person и не имеют отдельных permissions: они доступны admin/instructor в пределах разрешённого scope и самому Person через `self`. GuardianRelationship не предоставляет доступ к контактам ребёнка.

Медицинские, документальные и иные чувствительные данные не считаются автоматически доступными только из-за `person.read` и регулируются отдельной policy соответствующего домена.

## 6. Создание Person

### POST `/api/v1/persons`

Создание Person выполняется только при наличии канонического permission `person.create`. В MVP `person.create` доступен только `admin`.

Person является Club-neutral identity: сама схема `Person`/`PersonCreateRequest`/`PersonOut` не содержит `club_id`, и создание Person как domain entity остаётся отдельной операцией от создания `ClubMembership` как domain entity.

**TH-0111 / Issue #140**: тем не менее, application-операция `POST /api/v1/persons` атомарно создаёт Person и его начальное активное `ClubMembership` для текущего Club — в одной транзакции: `person.created` + `membership.created` либо оба фиксируются, либо оба откатываются. Это необходимо, чтобы созданный Person сразу был виден создавшему его club-scoped admin (`person_visibility_filter`'s club-scoped `all` предикат требует реальной строки `ClubMembership`) — до TH-0111 Person без ClubMembership был невидим для своего создателя сразу после создания. Membership создаётся с `membership_type="member"`, `status="active"`, `joined_at` = момент создания; `membership_type` не является User Role и не требует отдельного выбора в форме. Текущий Club определяется через существующий канонический механизм — `person.create`-предоставляющий assignment вызывающего (если он club-scoped) либо единственный существующий Club в MVP (если assignment глобальный) — без введения нового current-club механизма и без изменения общего authorization engine. Авторизация остаётся прежней: только `person.create`; `membership.manage` для этой compound-операции не требуется, поскольку все поля создаваемого membership фиксированы и не являются предметом отдельного discretionary-решения. Ни User, ни UserRoleAssignment, ни GuardianRelationship, ни GroupMembership, ни GroupInstructorAssignment, ни EventParticipation этой операцией не создаются — назначение роли и все прочие доменные связи остаются отдельными операциями, инициируемыми позже со страницы Person.

Никакого нового или отдельного endpoint для этого не вводится: `POST /api/v1/persons` остаётся единственным способом добавить человека.

ADR-0025 §9: heuristic duplicate detection (similarity/fuzzy matching, email/phone scoring, автоматическое объединение) не реализуется в текущем MVP slice. `DUPLICATE_PERSON` остаётся зарезервированным error-кодом для потенциального будущего использования, а не требованием текущего slice.

## 7. Обновление Person

### PATCH `/api/v1/persons/{person_id}`

Частичное обновление с audit для значимых изменений.

Authorization:
- `admin` может обновлять Persons в своём разрешённом `all` scope;
- `instructor` может обновлять Persons, доступных через `own_groups`;
- `member` и `guardian` могут обновлять только собственный Person;
- все роли могут изменять собственные `first_name`, `last_name`, `middle_name`, `phone`, `email`, `address`, `photo_file_id`;
- `birth_date` может изменять только `admin`, включая собственный Person;
- `id` неизменяем.

Контактные поля `phone`, `email`, `address` не имеют отдельных permissions.

ADR-0025 §10: в кодовой базе нет уже принятого project-wide optimistic-concurrency механизма (ни у одного реализованного домена — Events, Groups — его нет; `api-contract.md` §19/`api-conventions.md` §17 оставляют выбор конкретного механизма за доменом). Текущий slice не изобретает новый механизм: `PATCH` использует last-write-wins семантику, что явно фиксируется как принятая граница текущего slice, а не как недосмотр.

## 8. Архивирование Person — deferred

В MVP Person не имеет lifecycle archive и не имеет поля `status`/`archived_at`.

Endpoint `POST /api/v1/persons/{person_id}/archive` не является частью текущего API-контракта.

Нельзя имитировать архивирование Person через `User`, `ClubMembership`, `GroupMembership` или `GuardianRelationship`. Физическое удаление Person также не является частью доменного API.

Будущее архивирование требует отдельного решения о persistence, lifecycle, User, membership/group, guardian relationships, authorization, API, audit и UI (ADR-0034).

## 9. Membership list

### GET `/api/v1/memberships`

Фильтры: status, membership_type, group, joined period, left period, person, active/current.

## 10. Создание membership

### POST `/api/v1/memberships`

Permission: `membership.manage` и административный scope.

Создаёт связь Person ↔ Club. В MVP создание `ClubMembership` выполняется только `admin`.

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

Permission: `membership.manage` и административный scope.

Изменять `membership_type` может только `admin`. Lifecycle status не изменяется через произвольный PATCH: допустимые переходы выполняются через status endpoint (§12).

## 12. Membership status transition

### POST `/api/v1/memberships/{membership_id}/status`

Permission: `membership.manage` и административный scope. В MVP lifecycle transitions `ClubMembership` выполняет только `admin`.

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

Период каждого assignment — полуоткрытый интервал `[valid_from, valid_to)`, где `valid_to = NULL` означает открытый (бесконечный) конец периода. Два `is_primary = true` assignment одной группы конфликтуют тогда и только тогда, когда их интервалы `[valid_from₁, valid_to₁)` и `[valid_from₂, valid_to₂)` пересекаются, то есть:

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

Cross-Club integrity (ADR-0022 §5): `GroupInstructorAssignment` валиден только если Person назначаемого `User` имеет **активный** `ClubMembership` в Club целевой группы; сам факт наличия глобальной роли `instructor` недостаточен. Проверка и запись выполняются в одной транзакции тем же каноническим service-механизмом, что уже реализован в `apps/api/app/groups/service.py` (implementation evidence) — endpoint должен вызывать этот существующий shared-механизм.

### GET `/api/v1/groups/{group_id}/instructors`

Permission: `group.read` + scope + object relationship на `group_id`.

Pagination обязательна. Канонический фильтр активности называется `has_ended`:

- `has_ended=false` — только действующие назначения, у которых `valid_to IS NULL`;
- `has_ended=true` — только завершённые назначения, у которых `valid_to IS NOT NULL`;
- параметр не передан — без фильтра по активности.

Имя `has_ended` является частью канонического API-контракта для этого endpoint. Фильтр не изменяет point-in-time semantics §16.1.

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
- `guardian_person_id`;
- `child_person_id`;
- `relationship_type`;
- `status`;
- `valid_from`;
- `valid_to`;
- `created_at`;
- `updated_at`.

Primary guardian/contact concept не существует: `is_primary_contact` и `primary_guardian_id` не являются частью модели. Несколько активных representatives равноправны.

Канонические значения `status`: `active`, `inactive`, `revoked`.

Self-link guardian → same person запрещён. Дублирующие активные relationships одного типа для одной пары не допускаются; исторические `inactive`/`revoked` сохраняются. Понятия primary guardian/contact нет, поэтому ограничение на primary relationship отсутствует.

Permissions: `guardian_relationship.read` (чтение), `guardian_relationship.manage` (создание/изменение/terminate) — приняты ADR-0025 §2. `guardian_relationship.manage` в MVP доступен только `admin`; instructor/member/guardian имеют только соответствующее чтение в своих scope. Ранее использовавшийся здесь `guardian.read` не был каноническим permission и заменён. Никакие другие GuardianRelationship-специфичные permissions не вводятся.

URI: канонический ресурс — `guardian-relationships`, не `guardians` (ADR-0025 §4). `/guardians` не сохраняется как alias нигде в этом контракте, включая вложенную коллекцию под Person.

Authorization/scope: `GuardianRelationship` остаётся Club-neutral и не имеет `club_id`. Доступ вычисляется по роли, permission и relationship policy. Для этой сущности применяются только существующие scopes `all`, `self`, `children`, `none`; новые scopes не вводятся.

- `admin`: чтение всех разрешённых relationships в глобальном `all` scope; create/update/terminate.
- `instructor`: только чтение relationships, связанных с Persons, доступными через `own_groups`; mutations запрещены.
- `member`: только чтение собственных relationship records (`self`).
- `guardian`: только чтение собственных relationship records; другие representatives ребёнка не раскрываются.

`children` используется для вычисления связанных детей, но не превращает GuardianRelationship в общий доступ к любым полям Person ребёнка. `own_groups`/`own_events` не применяются напрямую к самой Club-neutral сущности, а instructor access определяется через reachable Persons.

### GET `/api/v1/persons/{person_id}/guardian-relationships`

Доступ только при наличии `guardian_relationship.read` и подходящего scope.

### POST `/api/v1/persons/{person_id}/guardian-relationships`

Создаёт `GuardianRelationship` с существующим Person непосредственно. Permission: `guardian_relationship.manage`. В MVP создание выполняется только `admin`; guardian/member/instructor не могут создавать relationship.

Request concept:

```json
{
  "guardian_person_id": "...",
  "relationship_type": "parent",
  "status": "active"
}
```

Создаваемый relationship всегда имеет `status = active`. Состояние `pending` для `GuardianRelationship` не существует и backend не должен его принимать.

Отдельный confirmation/controlled-linking workflow в текущем MVP не используется. Если в будущем потребуется подтверждение связи, оно должно быть реализовано как отдельный workflow/entity (аналогично `RegistrationRequest` для `ClubMembership`, ADR-0025 §5) и не должно вводить `pending` в lifecycle `GuardianRelationship`.

### PATCH `/api/v1/guardian-relationships/{relationship_id}`

Изменяет допустимые non-lifecycle attributes согласно permission `guardian_relationship.manage`. В MVP update выполняется только `admin`.

`status` не изменяется напрямую — `terminate` переводит relationship в `revoked`, а истечение `valid_to` даёт read-time производный статус `inactive`.

### POST `/api/v1/guardian-relationships/{relationship_id}/terminate`

Прекращает актуальность связи без уничтожения истории. Permission: `guardian_relationship.manage`. В MVP terminate выполняется только `admin`.

Каноническая семантика (ADR-0025 §3): `terminate` всегда переводит relationship в `status = revoked`. Альтернативного исхода нет; `terminate` уже `revoked` relationship отклоняется (соответствующий HTTP status, canonical error code `guardian_link_not_allowed` — см. §29). `inactive` — отдельное, не-revoked историческое состояние и никогда не является результатом `terminate`.

### Lifecycle: stored status и read-time expiry

Canonical stored-значения `status`: `active`, `inactive`, `revoked`. Других значений (в частности `pending`, `verified`, `rejected`, `terminated`) не существует.

`inactive` достигается естественным истечением `valid_to`, а не отдельным действием API. Это оценивается **at read time**: если stored `status = active`, но `valid_to` уже в прошлом, API при чтении (в списках, в детальном представлении, при authorization-проверках) рассматривает relationship как `inactive`. При этом stored значение в БД не переписывается никаким write-действием, и для этого не используется background job, scheduler или отдельный worker — производный статус вычисляется непосредственно в момент запроса.

## 19. My children

### GET `/api/v1/me/children`

Возвращает детей текущего authenticated guardian только по active, interval-valid `GuardianRelationship` и при выполнении authorization policy (`guardian_relationship.read`).

Endpoint не принимает `guardian_id`, `person_id` или любой другой client-supplied UUID, который мог бы подменить собой authenticated principal — единственный источник идентичности requester это сессия. Наличие такого параметра в query не является и не может являться доказательством права доступа.

Возвращает только собственных детей requester: Persons, для которых существует relationship с `guardian_person_id = requester`, `status = active` и текущим моментом внутри `[valid_from, valid_to)` (см. §18 "Lifecycle: stored status и read-time expiry"). `revoked` relationships исключаются всегда; relationships с истёкшим `valid_to` исключаются как не-active по той же read-time-логике.

Parent-visible projection для каждого ребёнка ограничена полями:

```text
id
last_name
first_name
middle_name
birth_date
photo_file_id
```

`phone`, `email`, `address` и другие чувствительные Person-поля в этой projection не возвращаются (см. §26).

## 20. Child context

Для родителя frontend может выбирать active child context, но backend на каждом запросе самостоятельно проверяет relationship и permission.

Наличие `child_id` в URL или query не является доказательством права доступа.

## 21. Pending registrations — вынесено из текущего контракта

ADR-0025 §5: самостоятельная регистрация — отдельная сущность/workflow `RegistrationRequest` (`docs/04-modules/people-and-membership.md` §10), а не переход `ClubMembership.status`. Ранее описанные здесь `GET /api/v1/memberships/pending`, `POST /api/v1/memberships/{membership_id}/approve`, `POST /api/v1/memberships/{membership_id}/reject` описывали конфликтующую модель и удалены из контракта.

`RegistrationRequest` persistence-модель, API и approval workflow остаются вне scope текущего implementation slice People & Membership и требуют отдельного Issue после отдельной спецификации.

## 22. Import

### POST `/api/v1/memberships/imports`

Создаёт import job для загрузки участников из согласованного формата.

Import должен быть асинхронным, если размер превышает синхронный лимит.

### GET `/api/v1/memberships/imports/{import_id}`

Возвращает статус и статистику import job.

### GET `/api/v1/memberships/imports/{import_id}/errors`

Возвращает строки/ошибки импорта без раскрытия чужих конфиденциальных данных сверх прав requester.

Import должен поддерживать dry-run до применения изменений.

## 23. Invitation

Auth contract определён в `docs/05-api/auth-api.md`.

People API предоставляет административное представление приглашённого membership после успешной активации.

## 24. Role assignment — вынесено из текущего контракта

ADR-0025 §6: канонический API-ресурс — top-level `/api/v1/role-assignments` (совпадает с `docs/05-api/endpoint-inventory.md` §24), а не вложенный `/api/v1/users/{user_id}/roles`, ранее описанный здесь. Role assignment API реализуется отдельным Issue вне текущего implementation slice People & Membership.

Role assignment не меняет Person.

### 24.1 Person-scoped role management (TH-0112, ADR-0039)

Person Detail управляет системными ролями человека через отдельный, узкий вход в тот же канонический ресурс `RoleAssignment` — ADR-0025 §6 не пересматривается: `/api/v1/role-assignments` остаётся канонической идентичностью ресурса, а эндпоинты ниже — Person-scoped представление/действие над ним, по аналогии с `GET /persons/{id}/memberships` и `GET/POST /persons/{id}/guardian-relationships`.

- `GET /api/v1/persons/{person_id}/role-assignments` — все текущие активные роли этого Person. Требует `role.manage`. Пустой список как для «ролей нет», так и для «у Person ещё нет User» (различить эти случаи может только попытка `POST`).
- `POST /api/v1/persons/{person_id}/role-assignments` — тело `{"role_code": "admin"|"instructor"|"member"|"guardian"}`. Требует `role.manage`. Никогда не принимает `user_id`, `role_id`, `scope_type` или `club_id` от клиента — identity разрешается backend строго через Person → User, scope и Club выбираются backend'ом (см. `app.role_assignments.person_roles`). Ошибки: `404` (Person не найден), `422 invalid_role_code`, `422 person_has_no_user_account` (у Person ещё нет User — это не account-creation workflow, и этот endpoint никогда не создаёт User), `422 role_assignment_club_membership_missing`, `409 duplicate_role_assignment` (роль уже активна — та же canonical conflict, что и у top-level `POST /role-assignments`).
- `DELETE /api/v1/persons/{person_id}/role-assignments/{role_code}` — снимает только конкретную активную роль этого Person; другие роли и другие Persons не затрагиваются. Existence-hiding: отсутствие роли для снятия (включая «Person без User») — `404 role_assignment_not_found`, как и у top-level `POST /role-assignments/{id}/revoke`.

Назначение/снятие роли никогда не создаёт и не изменяет: Person, ClubMembership, membership_type/status, GroupMembership, GroupInstructorAssignment, EventStaffAssignment, EventParticipation, GuardianRelationship (ADR-0039 §5-§9). Guardian-роль не создаёт GuardianRelationship автоматически — Person Detail лишь предлагает переход в уже существующий workflow `POST /persons/{child_person_id}/guardian-relationships` (§18) с текущим Person как `guardian_person_id`.

Аудит — существующие `role_assignment.created`/`role_assignment.revoked` (ADR-0024/ADR-0026); новый audit action не вводится.

## 25. Instructor assignment

Инструктор — Person/User с соответствующим role assignment. Само наличие роли не означает ответственность за конкретную группу или Event.

Для группы используется `GroupInstructorAssignment` — полный контракт (permission/scope/lifecycle/is_primary-invariant/cross-Club integrity) определён в §16.

Для мероприятия используется `EventStaffAssignment`, определённая ADR-0023. Она является явным источником `own_events`; `Event.created_by` не является заменой этой связи. `GroupInstructorAssignment` и `EventStaffAssignment` — разные сущности; ответственность за группу не подразумевает автоматическую ответственность за Event, и наоборот.

## 26. Sensitive profile sections

Не следует выдавать полный Person object любому requester с общим `person.read`.

`phone`, `email` и `address` не имеют отдельных permissions. Они доступны:
- `admin` — для Persons в разрешённом `all` scope;
- `instructor` — для Persons в разрешённом `own_groups` scope;
- `member` — только для собственного Person;
- `guardian` — только для собственного Person.

GuardianRelationship не предоставляет guardian доступ к контактам ребёнка.

Медицинские, документальные, emergency и иные чувствительные данные регулируются отдельной policy соответствующего домена; их наличие не выводится автоматически из `person.read`.

## 27. Validation

Минимальные проверки:

- корректность форматов дат;
- отсутствие невозможных интервалов membership;
- корректность guardian relationship lifecycle;
- отсутствие self-link и дублирующих активных GuardianRelationship одного типа для одной пары;
- невозможность создать `GroupMembership` или `GroupInstructorAssignment` для архивной (`status = archived`) группы (§14.1, §15, §16);
- отсутствие дублирующего активного `GroupMembership` для одной и той же пары `(group_id, club_membership_id)` (§15.2 — обязательный DB invariant, реализованный на уровне БД текущего Group API implementation slice);
- отсутствие пересекающихся по интервалу `[valid_from, valid_to)` `is_primary = true` записей `GroupInstructorAssignment` на одну группу (§16.2 — обязательный DB invariant, реализованный на уровне БД текущего Group API implementation slice);
- проверка существования и принадлежности объектов одному Club там, где это применимо (в том числе `Group.club_id == ClubMembership.club_id` для `GroupMembership`, ADR-0022 §4, и активный `ClubMembership` для `GroupInstructorAssignment`, ADR-0022 §5);
- Guardian authorization учитывает active relationship и interval validity.

## 28. Audit

Audit обязателен для создания/изменения Person, membership status, создания/изменения/архивирования Group, создания/изменения/завершения GroupMembership, создания/завершения GroupInstructorAssignment, создания/изменения/терминации GuardianRelationship, role assignments и import execution. Person archive не аудируется, поскольку endpoint и lifecycle в MVP отсутствуют.

Используется исключительно закрытый словарь `action` ADR-0024 §4 — новые audit action codes этим контрактом не вводятся:

- `POST /api/v1/groups` → `group.created`;
- `PATCH /api/v1/groups/{group_id}` → `group.updated`;
- `POST /api/v1/groups/{group_id}/archive` → `group.updated` (ADR-0024 не содержит отдельного `group.archived`/`group.status_changed`; архивирование сознательно отображается на существующий `group.updated`, а не порождает новый код — Issue #69, PO decision);
- `POST /api/v1/groups/{group_id}/members` → `group_membership.created`;
- `PATCH /api/v1/group-memberships/{id}` → `group_membership.updated`;
- `POST /api/v1/group-memberships/{id}/end` → `group_membership.ended`;
- `POST /api/v1/groups/{group_id}/instructors` → `group_instructor_assignment.created`;
- `POST /api/v1/group-instructor-assignments/{id}/end` → `group_instructor_assignment.ended`.

`group_instructor_assignment.updated` входит в закрытый словарь ADR-0024, но не используется ни одним endpoint этого контракта: `GroupInstructorAssignment` не имеет `PATCH` (§16.1).

## 29. Ошибки

Используется общий error contract.

Типовые ошибки (перечислены в исходном/каноническом написании этого раздела; фактический machine-readable `code` в реализованных доменах — lowercase snake_case, например `guardian_relationship_not_found`, `invalid_membership_transition`):

- `PERSON_NOT_FOUND`;
- `MEMBERSHIP_NOT_FOUND`;
- `GROUP_NOT_FOUND` (реализуется как `group_not_found`, HTTP 404, existence-hiding — см. ниже);
- `GUARDIAN_RELATIONSHIP_NOT_FOUND` (реализовано как `guardian_relationship_not_found`, HTTP 404);
- `DUPLICATE_PERSON`;
- `INVALID_MEMBERSHIP_TRANSITION`;
- `GUARDIAN_LINK_NOT_ALLOWED` (реализовано как `guardian_link_not_allowed`);
- `INSUFFICIENT_SCOPE`;
- `ROLE_ASSIGNMENT_NOT_ALLOWED`;
- `IMPORT_VALIDATION_FAILED`.

Group/GroupMembership/GroupInstructorAssignment-специфичные machine-readable коды (§14–§16), все — lowercase snake_case, согласно установленной конвенции:

- `group_not_found` — HTTP 404, existence-hiding (см. ниже);
- `group_membership_not_found` — HTTP 404, existence-hiding;
- `group_instructor_assignment_not_found` — HTTP 404, existence-hiding;
- `invalid_group_status_transition` — HTTP 409 (§14.1, например повторный `archive`);
- `invalid_group_membership_transition` — HTTP 409 (§15.1, например повторный `end`);
- `invalid_group_instructor_assignment_transition` — HTTP 409 (§16.1, например повторный `end`);
- `group_archived` — HTTP 409 (§15, §16 — попытка создать membership/instructor assignment для архивной группы);
- `group_membership_immutable_field` — HTTP 422 (§15 `PATCH` — попытка изменить `group_id`/`club_membership_id`/`membership_status`);
- `duplicate_group_membership` — HTTP 409 (§15.2 — дублирующее активное membership в той же группе);
- `duplicate_primary_instructor` — HTTP 409 (§16.2 — `is_primary = true` запись с интервалом, пересекающимся с уже существующей `is_primary = true` записью той же группы);
- `group_membership_club_mismatch` — HTTP 422 (ADR-0022 §4 cross-Club validation, соответствует `GroupMembershipClubMismatchError` в `apps/api/app/groups/service.py`);
- `instructor_club_membership_missing` — HTTP 422 (ADR-0022 §5 cross-Club validation, соответствует `InstructorClubMembershipMissingError`).

`INVALID_GROUP_TRANSFER` удалён из контракта: отдельного transfer-endpoint не существует (§15.3), поэтому отдельный error code для несуществующей операции не нужен.

### Group / GroupMembership / GroupInstructorAssignment: existence-hiding для item-level operations

`GET /api/v1/groups/{group_id}`, `PATCH /api/v1/groups/{group_id}`, `POST /api/v1/groups/{group_id}/archive`, `PATCH /api/v1/group-memberships/{id}`, `POST /api/v1/group-memberships/{id}/end`, `GET /api/v1/groups/{group_id}/instructors`, `POST /api/v1/groups/{group_id}/instructors` и `POST /api/v1/group-instructor-assignments/{id}/end` защищены от IDOR через existence-hiding по тому же паттерну, что и GuardianRelationship (§18, см. также ниже в этом разделе): объект, который реально не существует, и объект, который существует, но requester к нему не авторизован (permission есть, но scope/object relationship не подходит), возвращают одинаковый HTTP 404 с одинаковым machine-readable кодом (`group_not_found`/`group_membership_not_found`/`group_instructor_assignment_not_found` соответственно).

### GuardianRelationship: existence-hiding для `PATCH`/`terminate`

`PATCH /api/v1/guardian-relationships/{relationship_id}` и `POST /api/v1/guardian-relationships/{relationship_id}/terminate` защищены от IDOR через existence-hiding: relationship, который реально не существует, и relationship, который существует, но requester к нему не авторизован, возвращают одинаковый HTTP 404 с одинаковым machine-readable кодом `guardian_relationship_not_found` — по публичному ответу их невозможно отличить друг от друга.

Для validation/business-link ошибок (self-link, дублирующая active relationship, повторный `terminate` уже `revoked` relationship) используется canonical machine-readable код `guardian_link_not_allowed` с соответствующим HTTP status (422 для validation-ошибок при создании/изменении, 409 для повторного `terminate`).

### `INSUFFICIENT_SCOPE`

Authorization denial (нет permission, либо permission есть, но scope/object relationship не подходит) во всех реализованных доменах — Person, Membership, GuardianRelationship и Event — использует единый generic-механизм и возвращает `forbidden`, не раскрывая, какая именно permission или scope не подошли. Это намеренная security-политика: ответ не должен давать requester информацию, полезную для подбора доступа. `INSUFFICIENT_SCOPE` как отдельный machine-readable код в реализованных доменах не эмитируется; в этом контракте он остаётся зарезервированным/непроверенным написанием, а не описанием фактического поведения API. Group/GroupMembership/GroupInstructorAssignment endpoints следуют тому же generic-механизму — отдельный machine-readable код авторизационного отказа для них не вводится.

## 30. Concurrency and idempotency — сводка для Group domain

Group/GroupMembership/GroupInstructorAssignment endpoints не вводят project-wide или domain-specific optimistic-concurrency механизм (version column, ETag/If-Match) и не вводят `Idempotency-Key` — согласуется с `api-contract.md` §18-19 и уже принятым прецедентом для Person/Membership/GuardianRelationship (§7, ADR-0025 §10). `PATCH`/`POST .../archive`/`POST .../end` используют last-write-wins семантику. Cross-Club validation (ADR-0022 §6) и `is_primary`-invariant (§16.2) используют транзакционную блокировку/защиту целостности на уровне service/DB layer (см. `apps/api/app/groups/service.py` как implementation evidence существующего паттерна и миграцию Group constraints) — это механизм целостности данных, а не client-facing idempotency/concurrency contract, и не заменяет и не расширяет пункты выше.

## 31. Acceptance Criteria

1. Person и User не смешиваются.
2. Один Person может иметь несколько доменных ролей.
3. Membership сохраняет историю.
4. Group membership сохраняет историю.
5. Один guardian может иметь несколько детей.
6. Один ребёнок может иметь несколько guardians.
7. Parent API показывает только разрешённых детей по GuardianRelationship.
8. Child id никогда не заменяет authorization check.
9. Sensitive fields защищены role/scope/object policy; отдельные permissions для `phone`/`email`/`address` не вводятся.
10. Membership lifecycle transitions выполняются только admin; повторное вступление после inactive создаёт новый membership period.
11. Import поддерживает dry-run.
12. Role changes не изменяют Person или Membership.
13. Исторически значимые записи не удаляются физически по обычным CRUD endpoint'ам.
14. Значимые операции попадают в audit.
15. Все endpoint'ы соблюдают общие правила API и security.
16. `Group.status` имеет ровно два значения (`active`, `archived`) и ровно один допустимый переход; нет отдельного restore/unarchive endpoint.
17. `GroupMembership.membership_status` имеет ровно два значения (`active`, `ended`) и ровно один допустимый переход.
18. Один человек может одновременно состоять в нескольких разных группах, включая пересекающиеся интервалы; дублирующее активное членство в одной и той же группе запрещено.
19. Отдельного `/transfer` endpoint для GroupMembership не существует нигде в этом контракте.
20. Группа может иметь несколько инструкторов; на одну группу не допускается двух `is_primary = true` записей с пересекающимися интервалами `[valid_from, valid_to)` (последовательные непересекающиеся primary-назначения допустимы); автоматическое понижение существующего primary не выполняется.
21. Один инструктор может иметь `GroupInstructorAssignment` в нескольких группах.
22. `Group` не имеет обязательного типа/категории; `Program`/`Section`/`Direction` не вводятся.
23. `Event` и `Group` — разные сущности и не объединяются.
24. Guardian/родитель не становится инструктором группы через `GroupInstructorAssignment`.
25. Все Group/GroupMembership/GroupInstructorAssignment permissions, scopes и audit action codes взяты из существующих канонических каталогов (`roles-and-permissions.md` §4-5, ADR-0013, ADR-0024) — новые не введены.
26. `docs/05-api/endpoint-inventory.md` §7 и данный контракт не противоречат друг другу.
