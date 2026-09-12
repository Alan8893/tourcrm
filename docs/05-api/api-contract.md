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
- `/guardians`
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

Успешный ответ одного ресурса должен содержать объект `data`.

Пример:

```json
{
  "data": {
    "id": "...",
    "name": "..."
  }
}
```

Списки:

```json
{
  "data": [
    {"id": "..."},
    {"id": "..."}
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total": 120,
    "has_next": true
  }
}
```

Формат может быть уточнён на уровне конкретного API style guide, но внутри проекта должен быть единообразным.

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

Единый формат:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {
        "field": "email",
        "code": "INVALID_FORMAT",
        "message": "Invalid email format"
      }
    ],
    "request_id": "..."
  }
}
```

`message` предназначен для безопасного диагностического отображения и не должен содержать secrets.

### Минимальный каталог HTTP-кодов

- `200 OK` — успешное чтение/изменение;
- `201 Created` — создание;
- `202 Accepted` — асинхронная операция принята;
- `204 No Content` — успешная операция без body;
- `400 Bad Request` — некорректный запрос;
- `401 Unauthorized` — нет валидной аутентификации;
- `403 Forbidden` — аутентификация есть, полномочий нет;
- `404 Not Found` — ресурс недоступен/не найден;
- `409 Conflict` — конфликт состояния или уникальности;
- `422 Unprocessable Entity` — semantic/domain validation error, если выбранный framework style использует этот код;
- `429 Too Many Requests` — rate limit;
- `500 Internal Server Error` — неожиданная серверная ошибка;
- `503 Service Unavailable` — сервис временно недоступен.

Конкретное использование `400` vs `422` должно быть единообразным на всём API.

## 15. Authentication

Аутентификация является отдельным security boundary.

Основные операции:

- registration;
- verification;
- login;
- logout/session invalidation;
- password reset;
- invitation acceptance;
- account activation/deactivation.

Публичные endpoint'ы должны быть явно перечислены в security documentation.

После аутентификации каждый защищённый endpoint дополнительно проходит authorization.

## 16. Authorization

Нельзя считать факт успешного login достаточным.

Для каждого защищённого endpoint определяется:

`authentication → permission → scope → resource ownership/relationship → operation`

Пример:

`GET /members/{id}`

проверяет не только `member.read`, но и scope, например `self`, `children`, `own_groups` или `all`.

Backend является единственным доверенным местом enforcement.

## 17. Idempotency

Для endpoint'ов, которые могут быть повторно отправлены из-за retry сети, поддерживать Idempotency-Key, когда дубль имеет бизнес-стоимость.

Особенно рассмотреть:

- payments;
- registration commands;
- invitation acceptance;
- notifications send commands;
- external integration events.

Повторная обработка одного ключа должна вернуть согласованный результат, а не создать новую операцию.

## 18. Concurrency

Для критичных ресурсов использовать optimistic concurrency или иное явно выбранное решение.

Конфликты изменения должны возвращать понятный `409 Conflict`.

Особенно важны:

- attendance;
- financial records;
- equipment issue/return;
- event schedule changes;
- member status changes.

## 19. Soft delete / archive API

Если ресурс исторически значим, вместо `DELETE` использовать переход в состояние `archived`, `inactive`, `cancelled` и т. п.

Физическое удаление — отдельное privileged operation с документированной политикой.

## 20. Bulk operations

Массовые операции разрешены только для доменов, где они действительно нужны.

Например:

`POST /attendance/bulk-mark`

Bulk endpoint обязан:

- валидировать каждый элемент;
- возвращать агрегированный результат;
- быть идемпотентным или иметь безопасную retry semantics;
- создавать audit trail.

## 21. File API

Файлы не следует передавать через обычные JSON resources без необходимости.

Рекомендуемый процесс:

1. запросить upload session;
2. загрузить файл в storage;
3. зарегистрировать metadata;
4. провести validation/security checks;
5. вернуть resource reference.

Для скачивания защищённых файлов backend должен проверять authorization перед выдачей доступа.

## 22. Asynchronous operations

Долгие операции могут возвращать `202 Accepted` и operation/job identifier.

Кандидаты:

- импорт CSV;
- массовая рассылка;
- обработка GPX;
- генерация больших отчётов;
- экспорт документов.

Состояние job должно быть наблюдаемым через отдельный endpoint или notification mechanism.

## 23. Audit

Mutating endpoints для значимых доменов должны порождать AuditLog.

Аудит должен фиксировать actor, действие, объект и время. Секреты не логируются.

## 24. Rate limiting

Обязательно для публичных и security-sensitive endpoint'ов:

- login;
- registration;
- password reset;
- verification;
- invitation acceptance.

Лимиты должны зависеть от deployment и документироваться как конфигурация.

## 25. API evolution

Breaking changes требуют новой API version.

Незначительные backward-compatible изменения допускаются в пределах текущей версии при соблюдении compatibility policy.

Удаление поля/endpoint должно иметь deprecation period, если используются внешние клиенты.

## 26. OpenAPI

Backend должен публиковать машинный API contract через OpenAPI.

OpenAPI должен отражать:

- schemas;
- auth requirements;
- parameters;
- request bodies;
- responses;
- errors;
- pagination;
- examples где это полезно.

Документация API должна генерироваться из фактического backend contract, а не поддерживаться как полностью независимый список.

## 27. Health endpoints

Необходимы как минимум:

- liveness;
- readiness.

Readiness должен проверять необходимые зависимости, если это согласуется с deployment model.

Health endpoints не должны раскрывать внутренние credentials или конфиденциальную информацию.

## 28. Correlation / request ID

Каждый запрос получает `request_id`/correlation id.

Он должен:

- присутствовать в логах;
- возвращаться в error response;
- использоваться для трассировки фоновой операции, если применимо.

## 29. Security headers and transport

Production API работает через HTTPS.

Reverse proxy отвечает за transport-level concerns, а application за authorization и бизнес-логику.

Security headers должны быть определены инфраструктурной спецификацией.

## 30. Testing contract

Каждый доменный API должен иметь минимум:

- happy-path tests;
- validation tests;
- authorization tests;
- not-found/conflict tests;
- state transition tests;
- audit tests для значимых mutation operations.

Критичные финансовые и персональные операции также должны иметь regression coverage.

## 31. Domain API inventory

Полный endpoint inventory создаётся по модулям после утверждения доменной модели:

- Auth;
- People & Membership;
- Groups;
- Events & Schedule;
- Attendance;
- Trips;
- Routes / GPX;
- Tourist Profile;
- Achievements / Skills / Qualifications;
- Knowledge Base;
- Documents / Consents;
- Equipment;
- Finance;
- Notifications / Communications;
- Analytics / Reports;
- TourSlet integration;
- Administration / Settings;

Этот список является границей API inventory, но не заменяет модульные endpoint specifications.
