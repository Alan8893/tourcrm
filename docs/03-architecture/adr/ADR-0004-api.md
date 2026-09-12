# ADR-0004: API architecture

## Статус
Accepted

## Решение

Основной внешний контракт приложения — versioned REST API под `/api/v1`.

Контракт API должен описываться через OpenAPI и соответствовать `docs/05-api/api-contract.md`.

Правила:

- resource-oriented URLs;
- стандартные HTTP semantics;
- единый envelope для ошибок;
- cursor/page based pagination по типу endpoint;
- RFC 3339/ISO 8601 для timestamps;
- UUID/opaque identifiers, конкретный PK strategy фиксируется отдельным ADR;
- authorization проверяется server-side на каждом защищённом endpoint;
- изменение данных требует audit там, где это предусмотрено доменной спецификацией;
- повторяемые небезопасные операции должны либо поддерживать idempotency key, либо быть явно описаны как non-idempotent.

## Версионирование

Breaking changes не вносятся молча. При несовместимом изменении создаётся новая major API version.

## Альтернативы

GraphQL не выбран как основной API из-за избыточности для текущих задач и необходимости жёстко контролировать authorization/filtering.

gRPC не выбран как внешний web API; может быть рассмотрен позднее для внутренних service-to-service коммуникаций при выделении сервисов.
