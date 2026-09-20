# TourCRM — User Directory API

## 1. Назначение

Документ описывает `GET /api/v1/users` — единственный на данный момент реализованный endpoint
из `docs/05-api/endpoint-inventory.md` §2. Это **безопасный операционный справочник
пользователей** (например, для выбора инструктора в фильтре Calendar), а не полноценный admin
User Management API. `GET /users/{id}`, `POST /users`, `PATCH /users/{id}`,
`POST /users/{id}/block|disable|activate|archive` и `GET /users/{id}/sessions` остаются
недокументированной здесь, нереализованной частью §2.

## 2. Идентичность и permission

`User` — учётная запись, связанная 1:1 с `Person` (`User.person_id`). Справочник отображает
только производные от Person поля (имя).

**Permission: `user.directory.read`** — отдельный, узкий canonical permission
(`app.users.authorization`), **не** `person.read`. PO-решение (см. §9 для полного обоснования):
переиспользование `person.read` унаследовало бы его `own_groups`-scope для роли `instructor`
(требующий `GroupInstructorAssignment`), который не может выразить «инструктор A видит инструктора
B того же клуба без общей группы» — а ADR-0035 §11 прямо отвергает голое co-membership в клубе как
достаточное основание для доступа к Person, поэтому этот существующий scope нельзя было просто
расширить.

- Выдаётся ролям: `admin`, `instructor` (migration `95487f3b616b`). Другие базовые роли
  (`member`, `guardian`) permission не получают.
- Policy этого permission не использует `scope_type` вообще — только `UserRoleAssignment.club_id`
  того assignment'а, через который permission получен: `club_id IS NULL` → глобальный доступ;
  иначе — доступ ограничен этим одним клубом (см. §5). Это единственное отступление от общей
  scope-модели (`all`/`own_groups`/`self`/`children`/`own_events`/`none`), и оно намеренно
  ограничено этим одним permission — `person.read`, `event.read` и остальные canonical
  permissions не затронуты и не изменены.
- Уже существующий `UserRoleAssignment` роли `instructor` (обычно `own_groups`-scoped для других
  permissions, но всегда несущий свой `club_id`) автоматически становится достаточным для
  `user.directory.read`, как только RolePermission-грант применён — второй/отдельный
  `UserRoleAssignment` не создаётся и не требуется.
- **В отличие от `GET /persons`, отсутствие `user.directory.read` — это жёсткий `403`
  (`code: "forbidden"`)**, а не пустой список. Это permission существует специально для того,
  чтобы явно предоставлять или не предоставлять весь directory целиком — в отличие от
  `person.read`, который совместно используется многими операциями и там `scope` сужает уже
  общий ресурс.

## 3. `GET /api/v1/users`

### Query-параметры

| Параметр | Тип | Назначение |
|---|---|---|
| `page` | int, ≥1, default 1 | Номер страницы |
| `page_size` | int, 1–100, default 50 | Размер страницы |
| `search` | string, ≤255 | Подстрока для поиска (см. §4) |
| `club_id` | UUID | Ограничить результат пользователями с **активным** `ClubMembership` в этом клубе (см. §5) |
| `role` | string | Ограничить результат пользователями с текущим эффективным `UserRoleAssignment` на эту роль (см. §6) |
| `status` | string | Точное совпадение с `User.status` |

Нет параметра `sort`: результат детерминированно упорядочен по `last_name, first_name` — клиент
не может задавать произвольный порядок для этого endpoint.

### Пагинация

Стандартный envelope `docs/05-api/api-conventions.md` §9/ADR-0014:

```json
{"items": [...], "pagination": {"page": 1, "page_size": 50, "total": 0, "pages": 0}}
```

## 4. Поиск

`search` ищет подстроку (`ILIKE`) по всем трём именным полям Person: `last_name`, `first_name`,
`middle_name`. Нет произвольных/динамических SQL-полей — только эти три, как того требует
задача (выбор человека по имени, не более).

## 5. Club boundary

`club_id` — это **фильтр результата**, не влияющий на `User`/`Person` модель (у `User` по-прежнему
нет собственного `club_id`). Когда параметр указан, в результат попадают только пользователи, чей
`Person` имеет **активный** (`status = 'active'`) `ClubMembership` в этом клубе:

- Историческая/неактивная `ClubMembership` не даёт видимости в рамках `club_id`.
- Отсутствие `ClubMembership` в этом клубе исключает пользователя из результата.
- Пользователь с активным членством в нескольких клубах виден при фильтрации по любому из них,
  без утечки в остальные.

Это условие независимо и дополнительно к собственной authorization-границе запрашивающего
(`app.users.authorization.directory_reach_filter`, §2) — оба применяются одновременно (`AND`),
поэтому ни один из них не может расширить то, что разрешает другой. В частности, если
запрашивающий имеет `user.directory.read` через club-scoped assignment (Club A), а клиент
запрашивает `club_id=<Club B>`, результат будет пустым — `club_id` сужает уже авторизованный
набор, но никогда не расширяет его за пределы клуба(ов) запрашивающего. Если `club_id` вообще не
передан, действует именно эта собственная граница запрашивающего (а не «все клубы»).

## 6. `role=instructor` и семантика "эффективной роли"

`role` фильтрует по факту "у пользователя сейчас есть эффективный `UserRoleAssignment` с этим
`Role.code`" — временной предикат (`valid_from <= now() < valid_to` либо `valid_to IS NULL`),
идентичный тому, что уже использует `app.authorization.service.applicable_assignments`.

**`GroupInstructorAssignment` не требуется и не проверяется.** Ответственность за конкретную
группу (`GroupInstructorAssignment`) — отдельное понятие от факта "этот пользователь является
инструктором клуба", и directory-listing не должен их путать: инструктор без единого назначения
на группу всё равно присутствует в результате.

## 7. Response projection

`GET /users` возвращает **только**:

```json
{"id": "...", "person_id": "...", "first_name": "...", "last_name": "...", "middle_name": null}
```

Никогда не возвращаются: `password_hash`, `login_identifier`/`normalized_login_identifier`,
`status`, `email_verified_at`, `last_login_at`, `created_at`/`updated_at`, любые данные о ролях,
сессиях или RBAC-назначениях.

## 8. Использование в Calendar

Calendar использует этот endpoint для выбора конкретного инструктора/пользователя:

```
GET /api/v1/users?role=instructor&club_id=<club>&search=<query>
```

Выбранный `id` передаётся в `GET /api/v1/events/calendar?user_id=<id>` (существующий параметр,
`docs/05-api/events-api.md` §16). Чекбокс «Только мои события» — это удобный частный случай того
же `user_id` (значение — id текущего пользователя), а не отдельный или альтернативный фильтр.

## 9. Почему `person.read` не был переиспользован (PO-решение)

Изначальная реализация (первая версия этого документа) переиспользовала `person.read` +
`person_visibility_filter` напрямую. Ревью обнаружило архитектурный blocker: `person.read`
role `instructor` имеет scope `own_groups`
(`Person → active ClubMembership → active GroupMembership → Group → active
GroupInstructorAssignment → requesting User`, `roles-and-permissions.md` §11) — то есть
инструктор A мог увидеть в directory только людей, состоящих в группах, которые A ведёт. Другого
инструктора B того же клуба, не связанного с группами A, найти было невозможно — а это ломает
основной сценарий Calendar «выбрать другого инструктора».

Аудит (перед изменением кода) не нашёл ни одного существующего в кодовой базе authorization
primitive, дающего инструктору видимость «все того же клуба» — ни в Event (`own_groups` тоже
требует `GroupInstructorAssignment`, `app/events/authorization.py`), ни в Group
(`app/groups/authorization.py`), ни в `app.authorization.club_ownership` (используется только как
write-side ownership gate, не read-side visibility). Более того, это не просто отсутствие
возможности: **ADR-0035 §11** прямо утверждает — *"Being an Instructor, or being in the same
Club, is not sufficient to access every Person"* — и `roles-and-permissions.md` §11 то же самое:
*"co-membership или одна роль instructor не являются достаточным основанием"*. То есть
расширение `person.read`/`own_groups` было бы прямым нарушением уже принятого решения, а не
устранением недосмотра.

**Принятое PO-решение**: не трогать `person.read` (и, соответственно, Event/Group authorization)
вообще. Вместо этого — отдельный, узкий permission `user.directory.read` (§2) с собственной,
предельно простой policy (`app.users.authorization`): только club-boundary через
`UserRoleAssignment.club_id`, без scope-уровней `own_groups`/`self`/`children`/`own_events` (у
«просмотра справочника пользователей» нет естественного под-клубного отношения, которое имело бы
смысл сужать). Это осознанное, явно задокументированное и PO-одобренное единственное исключение
из общей scope-модели — не прецедент для остальных permissions и не пересмотр ADR-0035.
Отдельный ADR не потребовался: решение полностью укладывается в существующий механизм
`Role → RolePermission → UserRoleAssignment` (Issue #19/ADR-0005) — меняется только *какой*
permission выдан `instructor`, а не то, как работает сам механизм авторизации.
