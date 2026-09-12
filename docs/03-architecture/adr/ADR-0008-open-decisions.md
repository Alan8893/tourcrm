# ADR-0008: Реестр ещё не принятых архитектурных решений

## Статус
Living document

Этот документ содержит решения, которые пока нельзя считать окончательно утверждёнными. До их фиксации Claude не должен самостоятельно выбирать вариант, если выбор влияет на внешний контракт, данные, безопасность или совместимость.

## ODR-001: точный механизм web authentication — закрыт

Базовая архитектура аутентификации принята в `ADR-0009 — Authentication mechanism`.

`ADR-0009` фиксирует application-managed authentication через TourCRM backend, server-side session/revocation model, password hashing, email verification, password reset и invitation tokens. Конкретные библиотеки и версии остаются implementation detail и выбираются при реализации с учётом актуального поддерживаемого стека.

Дальнейшая реализация authentication должна следовать `ADR-0009` и `docs/05-api/auth-and-authorization.md` и не должна трактовать ODR-001 как открытое архитектурное решение.

## ODR-003: reverse proxy

Нужно выбрать production reverse proxy (например, Nginx/Caddy/Traefik) и определить TLS/certificate automation.

## ODR-005: background worker

Нужно выбрать конкретный Redis worker framework после появления первой обязательной фоновой задачи.

## ODR-006: financial/accounting scope

Нужно определить, является ли финансовый модуль внутренним управленческим учётом или должен соответствовать внешнему бухгалтерскому/налоговому учёту.

## ODR-007: medical data model

Нужно определить минимальный набор медицинских данных, основания хранения, срок хранения, видимость и процедуру удаления/архивирования.

## ODR-008: tourism classification rules

Нужно утвердить используемые виды туризма, категории сложности, нормативы и правила зачёта туристского опыта.

## ODR-009: rating formula

Нужно выбрать формулу и область действия рейтинга либо оставить рейтинг полностью выключенным по умолчанию.

## ODR-010: calendar integrations

Нужно определить приоритет и технический контракт Google/Apple/Outlook calendar integrations.

## ODR-011: TourSlet integration

Решение возможно только после анализа предоставленного ZIP и фактической архитектуры существующего сайта.

## ODR-012: MAX integration

Нужно уточнить конкретный API/ботовский сценарий, доступность API и ограничения на момент реализации.

## ODR-013: retention and deletion policy

Нужно определить сроки хранения различных категорий персональных данных, файлов, аудита, финансовых операций и security events с учётом применимого законодательства и политики клуба.

## ODR-014: invitation creation permission code

`docs/05-api/auth-api.md` currently references permission code `membership.invitation.create` for invitation creation. The canonical permission catalog in `docs/02-requirements/roles-and-permissions.md` does not contain this code.

This discrepancy is intentionally unresolved. No implementation may silently substitute another permission, invent a new permission, or infer a role grant. The product/specification owner must decide whether to reuse an existing canonical permission or introduce a new permission through the normal documentation/ADR change process.

The persistence contract for authentication is defined in `docs/03-architecture/authentication-persistence.md`, but invitation authorization remains blocked by this ODR.

## Правило

Наличие записи в этом документе не означает, что решение отложено навсегда. Каждое ODR должно быть закрыто отдельным ADR или обновлением соответствующей спецификации до момента, когда оно становится необходимым для реализации.

## Закрытые ранее ODR

- ODR-001 web authentication — закрыт `ADR-0009`.
- ODR-002 primary key strategy — закрыт `ADR-0010`.
- ODR-004 object storage implementation — закрыт `ADR-0011`.
- Scope vocabulary — закрыт `ADR-0013`.
- API response envelope — закрыт `ADR-0014`.
- Event occurrence materialization — закрыт `ADR-0015`.
- Document ownership — закрыт `ADR-0016`.
