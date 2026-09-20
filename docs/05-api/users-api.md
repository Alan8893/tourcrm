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
только производные от Person поля (имя), поэтому авторизация полностью переиспользует
существующую policy Person, а не вводит отдельную модель:

- Permission: `person.read` (тот же, что и у `GET /persons`).
- Scope: тот же scope model, что и в `roles-and-permissions.md` §7.1 — `admin` в `all` scope,
  `instructor` через `own_groups`, `member`/`guardian` без общего доступа к чужим Persons.
- Ни новый permission, ни новый scope, ни ADR для этого endpoint не потребовались — прямое,
  задокументированное переиспользование `app.people.authorization.person_visibility_filter`.
- Как и `GET /persons`, список **не** возвращает 403 для аутентифицированного пользователя без
  подходящего assignment — он получает пустой `200` (`pagination.total == 0`). Это существующая
  конвенция list-endpoint'ов, а не новое решение для этого endpoint.

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

Это условие независимо и дополнительно к собственной scope-границе запрашивающего
(`person_visibility_filter`) — оба применяются одновременно (`AND`), поэтому ни один из них не
может расширить то, что разрешает другой.

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
