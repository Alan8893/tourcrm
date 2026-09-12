# TourCRM — System Gap Analysis

## Назначение

Документ фиксирует результат сквозной аналитической проверки документации TourCRM и определяет, какие области уже имеют достаточный контракт, а какие требуют дополнительной спецификации до начала реализации соответствующего функционала.

Это не список «ошибок проекта». Это управляемый реестр незавершённых аналитических решений.

## 1. Статус покрытия

### Полностью покрыто на уровне архитектурного контракта

- Product Vision и Scope.
- Glossary.
- Functional Requirements.
- Business Rules.
- Users / Persons / Membership.
- Roles / Permissions / Scopes.
- Events / Schedule / Attendance.
- Trips / Routes / GPX / Tourist Profile.
- Achievements / Skills / Qualifications.
- Documents / Consents.
- Medical data technical baseline.
- Finance.
- Equipment.
- Notifications / Communications.
- Knowledge Base.
- API conventions.
- Authentication / Authorization.
- Logical / physical database design principles.
- UX information architecture.
- Design System.
- Security / Privacy baseline.
- Infrastructure / DevOps baseline.
- ADR process.
- Issue / implementation contract.
- Roadmap.

«Полностью покрыто» означает, что область описана для архитектурного планирования. Перед реализацией конкретной функции её detail-level contract всё равно должен быть указан в соответствующей Issue.

## 2. GAP категории

### GAP-01 — Legal / regulatory policy decisions

Статус: OPEN / требует внешнего бизнес-решения.

Нужно утвердить:

- применимый набор требований к персональным данным;
- правила и сроки хранения отдельных категорий данных;
- текст/версии пользовательских и родительских согласий;
- правила удаления/анонимизации;
- порядок обработки запросов на доступ/исправление/удаление;
- ответственных лиц клуба за обработку данных.

Техническая система должна поддерживать эти правила, но не должна самостоятельно «выдумывать» юридические сроки.

### GAP-02 — Medical data policy

Статус: TECHNICAL BASELINE READY / BUSINESS POLICY OPEN.

Техническая модель зафиксирована в `docs/07-security/medical-data.md`.

Остаётся утвердить:

- точный минимально необходимый состав медицинских данных;
- кто имеет доступ к каждому полю;
- operational-critical поля для инструкторов;
- юридические основания и сроки хранения;
- перечень подтверждающих документов;
- правила экстренного доступа.

До утверждения policy Claude не должен самостоятельно расширять модель медицинских данных.

### GAP-03 — Tourism classification and experience rules

Статус: TECHNICAL BASELINE READY / NORMATIVE POLICY OPEN.

Техническая модель зафиксирована в `docs/04-modules/tourism-classification-and-experience.md`.

Остаётся утвердить:

- официальный набор видов туризма клуба;
- используемую классификацию сложности и источник/версию;
- правила зачёта километража;
- правила полного и частичного участия;
- правила признания похода официальным/подтверждённым;
- правила расчёта туристского стажа;
- требования к документальному подтверждению;
- правила учёта руководства.

### GAP-04 — Rating algorithm

Статус: OPEN.

Платформа должна поддерживать рейтинг как отключаемую функцию, но формула рейтинга пока не утверждена.

Нужно определить:

- метрики;
- веса;
- период расчёта;
- источники баллов;
- нормализацию;
- возрастные/групповые ограничения;
- видимость;
- поведение при исправлении исторического факта.

### GAP-05 — Calendar integrations

Статус: OPEN.

Нужно определить приоритет и контракт интеграций с Google Calendar / Outlook / другими календарями.

Пока внутренний календарь является canonical source.

### GAP-06 — TourSlet integration

Статус: BLOCKED BY INPUT.

Требуется архив существующего сайта турслётов.

После получения архива необходимо определить стек, модель данных, auth model, точки интеграции, API feasibility, SSO feasibility, mapping участников/событий/результатов, синхронизацию и ownership данных.

До анализа архива конкретные endpoint'ы и mapping не считаются утверждёнными.

### GAP-07 — Export / import contracts

Статус: PARTIAL.

Нужно детально стандартизировать CSV/XLSX import/export, PDF/DOCX generation, схемы, encoding, duplicate handling, validation, preview, rollback и audit.

### GAP-08 — Backup / Disaster Recovery

Статус: PARTIAL.

Нужен операционный runbook:

- schedule;
- retention;
- off-server copy;
- encryption;
- restore procedure;
- restore validation;
- RPO;
- RTO;
- disaster scenarios;
- ownership/escalation.

### GAP-09 — Observability

Статус: PARTIAL.

Нужно детализировать structured logging, correlation IDs, metrics, health/readiness/liveness, alerts, dashboards, error tracking и PII redaction.

### GAP-10 — Feature settings governance

Статус: PARTIAL.

Нужно определить scope, defaults, who may change, audit requirements, activation conditions и compatibility для каждого feature setting.

### GAP-11 — Reporting / Analytics semantic model

Статус: PARTIAL.

Нужен metric catalog: название, формула, источник, временная база, фильтры, permissions и правила пересчёта.

### GAP-12 — Search specification

Статус: PARTIAL.

Нужно детально определить global/member/event/document/knowledge search, доступные поля, ranking, permission filtering, pagination и empty states.

### GAP-13 — File processing pipeline

Статус: PARTIAL.

Нужно определить MIME types, max sizes, antivirus scanning, checksum, preview generation, image resizing, GPX validation, failed state и quarantine.

### GAP-14 — Audit retention

Статус: PARTIAL.

Нужно утвердить обязательные audit actions, срок хранения, доступ к audit, export policy и tamper-evidence requirements.

### GAP-15 — Operational administration

Статус: PARTIAL.

Нужен admin/operations contract для initial setup, bootstrap administrator, reference data, feature settings, integrations, maintenance mode, backup status, jobs, health и audit access.

## 3. Приоритеты

### P0 — требуется до начала соответствующей реализации

- GAP-06 TourSlet integration — после получения архива.
- GAP-08 Backup/DR — до production launch.
- GAP-09 Observability — до production launch.
- GAP-02 Medical policy — до медицинского функционала.
- GAP-03 Tourism normative rules — до автоматического расчёта подтверждённого опыта.

### P1 — требуется до реализации соответствующего модуля

- GAP-04 Rating algorithm.
- GAP-05 Calendar integrations.
- GAP-07 Export/import contracts.
- GAP-10 Feature settings governance.
- GAP-11 Reporting metric catalog.
- GAP-12 Search.
- GAP-13 File processing.
- GAP-14 Audit retention.
- GAP-15 Operational administration.

### P2 — governance / business decisions

- GAP-01 Legal/regulatory policy decisions.

## 4. Правило работы с GAP

Наличие GAP не означает, что весь проект заблокирован.

До закрытия GAP:

1. Нельзя выдавать Claude задачу, требующую решения, которого нет в документации.
2. В Issue должна быть ссылка на GAP, если задача зависит от него.
3. Если бизнес-решение меняет архитектуру, создаётся или обновляется ADR.
4. После принятия решения исходный GAP закрывается и требования синхронизируются с основными документами.

## 5. Результат анализа

Архитектурный фундамент TourCRM сформирован. Медицинский и туристский домены получили технические контракты, но нормативные/бизнес-решения по ним ещё не утверждены.

Следующий этап — закрытие оставшихся P0/P1 решений по мере приближения соответствующей разработки и формирование атомарных GitHub Issues по установленному Issue Contract.
