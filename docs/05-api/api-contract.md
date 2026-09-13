# TourCRM — API Contract

## 1. Назначение

Документ определяет единые правила HTTP API TourCRM. Все доменные API должны следовать этим правилам. Специализированные отклонения допускаются только с документированным ADR или явным указанием в модульной спецификации.

API предназначен для web frontend, будущего мобильного клиента и интеграций.

## 2. Базовые принципы

- API — HTTP/JSON REST-oriented.
- Контракт версионируется.
- Текущая публичная версия: `/api/v1`.
- Backend является источником истины для авторизации, валидации и бизнес-правил.
- Frontend не считается доверенной стороной.
- Domain operations должны быть идемпотентными там, где это возможно.
- Необратимые операции требуют явного endpoint/action и проверки полномочий.
- Ошибки имеют единый машиночитаемый формат.
- API не должен раскрывать внутренние traceback, SQL, secrets или инфраструктурные детали.

## 3. Base URL

Production:

`https://<host>/api/v1`

LAN deployment:

`http://<host>/api/v1` допустим только как deployment-specific режим. Для production с персональными данными должен использоваться HTTPS.

## 4. Resource naming

Использовать plural nouns:

- `/users`
- `/persons`
- `/memberships`
- `/guardian-relationships` (ADR-0025 §4 — `/guardians` не используется как alias)
- `/groups`
- `/events`
- `/trips`
- `/routes`
- `/achievements`
- `/documents`
- `/equipment`
- `/payments`

В URL использовать lowercase kebab-case только для многословных ресурсов, если ресурс действительно является самостоятельным REST resource.

Не кодировать role names в URL вместо permission checks.

## 5. HTTP methods

### GET
Чтение. Не изменяет состояние.

### POST
Создание ресурса или выполнение явно command-like операции, когда создание ресурса не является подходящей моделью.

### PATCH
Частичное изменение существующего ресурса.

### PUT
Используется только для полной замены или идемпотентной установки ресурса. Не применять без необходимости.

### DELETE
Удаление только там, где бизнес-модель допускает удаление. Для исторических сущностей предпочитать archive/deactivate action.

## 6. Resource identifiers

API должен использовать непрозрачные для клиента стабильные identifiers. Конкретный тип PK определяется database ADR.

Client не должен предполагать последовательность идентификаторов.

## 7. Common response shape

TourCRM API v1 использует единый канонический response contract, установленный ADR-0014.

### Single resource

Успешный ответ одного ресурса содержит представление ресурса напрямую, без обёртки `data`.

Пример:

```json
{
  "id": "...",
  "name": "..."
}
```

### Collection

Коллекции используют `items` и `pagination`.

Пример:

```json
{
  "items": [
    {"id": "..."},
    {"id": "..."}
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 120,
    "pages": 3
  }
}
```

`data/meta` не является допустимым альтернативным envelope для API v1.

## 8. Pagination

Для коллекций использовать offset/page или cursor pagination в зависимости от характера ресурса.

Правило по умолчанию:

- административные таблицы — page/page_size;
- большие и часто меняющиеся ленты — cursor pagination, если это оправдано.

Ограничить максимальный `page_size` сервером.

## 9. Filtering

Фильтры передаются query parameters.

Пример:

`GET /events?status=scheduled&event_type=lesson`

Несколько значений должны иметь документированный формат.

Запрещены динамические SQL выражения из query parameters.

## 10. Sorting

Использовать whitelist доступных полей.

Пример:

`sort=start_at`

Направление:

`sort=-start_at`

Backend обязан отклонять неизвестные поля сортировки.

## 11. Search

Поиск передаётся параметром `q` или специализированными параметрами, если это повышает понятность контракта.

Full-text search не должен реализовываться через несвязанный набор `ILIKE` по произвольным полям без documented query strategy.

## 12. Dates and time

- Timestamp в API передаются в ISO 8601.
- Для абсолютного времени использовать timezone-aware timestamps.
- `start_at`/`end_at` событий всегда представляют абсолютную дату/время.
- Timezone мероприятия хранится явно.
- Date-only поля не должны искусственно преобразовываться в timestamps.

## 13. Validation

Backend валидирует:

- типы;
- обязательность;
- длины;
- допустимые значения;
- взаимозависимости полей;
- ссылки на существующие сущности;
- permission scope;
- state transition rules.

Ошибка пользовательского ввода не является HTTP 500.

## 14. Error format

Единый канонический формат установлен ADR-0014:

```json
{
  "error": {
    "code": "resource_not_found",
    "message": "Resource was not found",
    "details": {},
    "request_id": "..."
  }
}
```

Правила:

- `code` — стабильный машиночитаемый код;
- `message` — безопасное сообщение, не раскрывающее secrets или внутренние детали;
- `details` — структурированный контекст, особенно для ошибок валидации;
- `request_id` — идентификатор запроса для корреляции с серверными логами.

API не должен возвращать внутренние exception details, traceback, SQL, secrets или инфраструктурные детали.

## 15. Recommended HTTP status semantics

- `200 OK` — успешное чтение/обновление/command с response body;
- `201 Created` — ресурс успешно создан;
- `202 Accepted` — асинхронная операция принята;
- `204 No Content` — успешная операция без response body;
- `400 Bad Request` — некорректный запрос, не относящийся к field validation semantics;
- `401 Unauthorized` — отсутствует/некорректна аутентификация;
- `403 Forbidden` — аутентифицированный пользователь не имеет требуемых полномочий;
- `404 Not Found` — запрошенный ресурс недоступен вызывающей стороне в соответствии с семантикой endpoint;
- `409 Conflict` — конфликт состояния/уникальности;
- `422 Unprocessable Content` — синтаксически корректный запрос с ошибками валидации/входных бизнес-условий, когда такое различие используется;
- `429 Too Many Requests` — превышен rate limit;
- `500 Internal Server Error` — непредвиденная ошибка сервера;
- `503 Service Unavailable` — недоступна зависимость/служба, где это применимо.

Одинаковые категории ошибок не должны получать разные значения HTTP status в разных модулях.

## 16. Authorization

Authentication и authorization — разные concerns.

Каждый защищённый endpoint должен проверять authorization на стороне сервера. Эффективное решение может зависеть от:

- role;
- permission;
- scope;
- resource ownership;
- group membership;
- guardian relationship;
- event assignment;
- club membership;
- feature settings.

Проверка permission на frontend является только UX-оптимизацией.

## 17. Authentication context

Аутентифицированный запрос должен иметь canonical server-side identity context, содержащий как минимум:

- user identifier;
- person identifier при наличии связи;
- effective roles/permissions при необходимости;
- club context;
- session/token metadata, необходимую для security.

Конкретный authentication/session mechanism определяется authentication ADR.

## 18. Idempotency

Операции, которые могут безопасно повторяться клиентами или gateway, должны проектироваться с учётом idempotency.

Create-операции, повтор которых из-за сетевого сбоя может привести к дублям, должны поддерживать механизм idempotency, когда это необходимо; особенно для финансовых операций, внешних уведомлений и интеграций.

Конкретная политика `Idempotency-Key` определяется до реализации соответствующих модулей.

## 19. Concurrency and optimistic safety

Mutating endpoints должны учитывать concurrent edits там, где возможна потеря данных.

Для сущностей вроде профиля участника, события и финансовых записей при необходимости должен использоваться явный механизм optimistic concurrency.

Конкретный механизм (version column, ETag/If-Match и т.п.) выбирается для затронутого домена и должен быть согласован внутри модуля.

## 20. File uploads

File uploads используют multipart/form-data или документированный upload protocol.

API должен валидировать:

- authenticated user;
- authorization на прикрепление файла;
- MIME/content type, где возможно;
- размер файла;
- разрешённый extension/type policy;
- storage destination;
- malware scanning strategy, если введена.

Присланное клиентом имя файла не должно использоваться как storage identifier.

## 21. Async operations

Длительные операции, такие как большие exports, обработка GPX или массовые notifications, должны возвращать `202 Accepted` с operation/job identifier, когда требуется асинхронная обработка.

Клиент должен иметь документированный способ получить статус/результат или получить notification.

## 22. Audit behavior

Защищённые mutation-операции, требующие аудита, должны создавать audit records в рамках той же логической business operation. Поведение при ошибке аудита документируется для каждой категории; security-critical mutations не должны молча выполняться без требуемой auditability.

## 23. Transactions

API layer должен делегировать transaction boundaries application/domain service layer, а не открывать произвольные независимые transactions в route handlers.

Cross-entity operations, являющиеся одной business action, должны коммититься атомарно, если это поддерживается database.

## 24. Database leakage prevention

API models должны быть явными response schemas. ORM entities нельзя возвращать напрямую как public API contracts.

API не должен раскрывать:

- password hashes;
- authentication secrets;
- internal storage credentials;
- private infrastructure details;
- internal database exception text.

## 25. OpenAPI

Backend должен генерировать и публиковать OpenAPI schema для реализованной versioned API.

Generated schema является implementation artifact документированного contract и не заменяет документацию domain requirements.

Изменения public API contracts требуют соответствующих изменений документации.

## 26. Deprecation

Deprecated endpoints должны быть явно документированы и иметь:

- deprecation date/version;
- replacement endpoint;
- migration guidance;
- planned removal version, если применимо.

Silent breaking changes запрещены.

## 27. API acceptance checklist

Перед принятием API endpoint должны быть документированы/проверены:

- authentication requirement;
- authorization requirement;
- request schema;
- response schema;
- validation и business errors;
- relevant status codes;
- audit requirement;
- idempotency/concurrency requirements;
- OpenAPI implementation;
- automated tests для endpoint и соответствующих permissions.
