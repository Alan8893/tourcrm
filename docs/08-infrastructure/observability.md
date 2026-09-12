# TourCRM — Observability Specification

## 1. Назначение

Определяет требования к логированию, метрикам, health checks, tracing/correlation и error tracking.

## 2. Pillars

TourCRM использует четыре основных направления:

- structured logs;
- metrics;
- traces/correlation;
- error tracking.

## 3. Structured logging

Application logs должны быть структурированными и пригодными для машинного анализа.

Минимальные поля:

- timestamp;
- level;
- service;
- environment;
- event_name;
- correlation_id;
- request_id, если HTTP request;
- actor_id, только если допустимо политикой privacy;
- duration_ms, если применимо;
- outcome;
- error_code/error_class, если есть.

Секреты, tokens, passwords и чувствительные персональные данные не логируются.

## 4. Correlation ID

Каждый входящий request должен получить correlation/request identifier.

При вызовах background jobs и integrations correlation context должен передаваться либо явно заменяться новым job execution id с сохранением parent reference.

## 5. Metrics

Минимальный baseline:

- HTTP request count/latency/error rate;
- authentication success/failure;
- authorization denials;
- DB connection/latency/error;
- background job success/failure/retry/age;
- notification delivery success/failure;
- file processing success/failure;
- backup success/failure/age;
- application resource health;
- active sessions, если безопасно агрегировать.

## 6. Health endpoints

Должны быть различены:

- liveness — процесс жив;
- readiness — instance готов обслуживать запросы;
- dependency health — состояние критических зависимостей.

Health endpoints не должны раскрывать secrets, credentials или чувствительные данные.

## 7. Error tracking

Unhandled application errors должны попадать в централизованный error tracker либо эквивалентный механизм.

Каждая ошибка должна по возможности содержать:

- correlation id;
- environment;
- release/version;
- stack trace;
- safe contextual metadata.

PII redaction обязательна до отправки в external error tracking service.

## 8. Alerts

До production launch необходимо определить alert rules минимум для:

- application unavailable;
- elevated 5xx rate;
- elevated latency;
- database unavailable;
- background job backlog;
- notification failure surge;
- file processing failures;
- backup stale/failed;
- disk space threshold;
- certificate expiration.

Пороговые значения должны быть конфигурируемыми и документированы.

## 9. Dashboards

Production dashboard должен показывать:

- availability;
- request rate;
- latency;
- errors;
- DB health;
- workers/jobs;
- notification health;
- storage health;
- backup state.

## 10. Privacy

Observability data является потенциально чувствительным. Для неё применяются те же privacy principles, что и к application data.

## 11. Acceptance criteria

- [ ] logs structured;
- [ ] correlation IDs работают сквозь HTTP и background processing;
- [ ] health/readiness endpoints определены;
- [ ] базовые metrics собираются;
- [ ] error tracking имеет PII redaction;
- [ ] critical alerts определены;
- [ ] backup health видна через monitoring;
- [ ] observability не раскрывает секреты.
