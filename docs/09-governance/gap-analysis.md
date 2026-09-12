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

Статус: OPEN / BLOCKER для медицинского профиля.

Нужно определить:

- какие медицинские данные действительно необходимы;
- кто имеет доступ к каждому полю;
- какие данные являются обязательными/необязательными;
- можно ли хранить свободный текст;
- какие документы могут подтверждать ограничения;
- сроки хранения;
- правила маскирования в UI и отчётах.

До утверждения policy Claude не должен самостоятельно расширять модель медицинских данных.

### GAP-03 — Tourism classification and experience rules

Статус: OPEN / требуется нормативное решение.

Нужно утвердить:

- справочник видов туризма;
- справочник категорий сложности;
- правила зачёта километража;
- правила зачёта частичного участия;
- правила признания похода официальным/подтверждённым;
- правила расчёта туристского стажа;
- требования к документальному подтверждению.

### GAP-04 — Rating algorithm

Статус: OPEN.

Платформа должна поддерживать рейтинг как отключаемую функцию, но формула рейтинга пока не утверждена.

Нужно определить:

- метрики;
- веса;
- период расчёта;
- что является источником баллов;
- есть ли нормализация;
- учитывается ли возраст/группа;
- кто может видеть рейтинг;
- что происходит при исправлении исторического факта.

До утверждения алгоритма рейтинг не является источником истины и не должен влиять на базовую статистику.

### GAP-05 — Calendar integrations

Статус: OPEN.

Нужно определить приоритет и контракт интеграций с Google Calendar / Outlook / другими календарями.

Пока система должна иметь внутренний календарь как canonical source.

Внешние календари рассматриваются как производные представления/синхронизация.

### GAP-06 — TourSlet integration

Статус: BLOCKED BY INPUT.

Требуется архив существующего сайта турслётов.

После получения архива необходимо определить:

- стек;
- модель данных;
- auth model;
- точки интеграции;
- API или возможность его создания;
- SSO feasibility;
- mapping участников;
- mapping событий;
- mapping результатов;
- правила синхронизации;
- ownership данных.

До анализа архива конкретные endpoint'ы и mapping не должны считаться утверждёнными.

### GAP-07 — Export / import contracts

Статус: PARTIAL.

Нужно детально стандартизировать:

- CSV import/export;
- XLSX import/export;
- PDF document generation;
- Word/DOCX generation;
- schemas/templates;
- encoding;
- duplicate handling;
- validation errors;
- import preview;
- rollback semantics;
- audit.

### GAP-08 — Backup / Disaster Recovery

Статус: PARTIAL.

Infrastructure specification задаёт baseline, но необходим операционный runbook:

- backup schedule;
- retention;
- off-server copy;
- encryption;
- restore procedure;
- restore validation;
- RPO;
- RTO;
- disaster scenarios;
- ownership and escalation.

### GAP-09 — Observability

Статус: PARTIAL.

Нужно детализировать:

- structured logging schema;
- correlation IDs;
- metrics;
- health/readiness/liveness;
- alert thresholds;
- dashboards;
- error tracking;
- personally identifiable information redaction.

### GAP-10 — Feature settings governance

Статус: PARTIAL.

Нужно определить для каждого feature setting:

- scope (system / club / role / user);
- default value;
- who may change;
- whether change is audited;
- activation conditions;
- backward compatibility requirements.

Feature settings никогда не должны обходить security permissions.

### GAP-11 — Reporting / Analytics semantic model

Статус: PARTIAL.

Dashboard architecture есть, но отсутствует единый metric catalog.

Нужно определить для каждого показателя:

- название;
- формулу;
- источник данных;
- временную базу;
- фильтры;
- права доступа;
- правила пересчёта.

### GAP-12 — Search specification

Статус: PARTIAL.

Нужно детально описать:

- глобальный поиск;
- поиск по участникам;
- поиск по мероприятиям;
- поиск по документам;
- поиск по базе знаний;
- доступные поля;
- ranking;
- permissions-aware filtering;
- пагинацию;
- поведение без результатов.

### GAP-13 — File processing pipeline

Статус: PARTIAL.

Помимо object storage необходимо определить:

- allowed MIME types;
- max sizes;
- antivirus scanning;
- checksum;
- preview generation;
- image resizing;
- GPX validation;
- failed processing state;
- quarantine behavior.

### GAP-14 — Audit and legal audit retention

Статус: PARTIAL.

Технический AuditLog определён, но требуется отдельно утвердить:

- какие действия обязательны для аудита;
- срок хранения audit records;
- кто имеет право просмотра;
- export policy;
- tamper-evidence requirements.

### GAP-15 — Operational administration

Статус: PARTIAL.

Нужен отдельный admin/operations contract для:

- initial setup;
- bootstrap administrator;
- reference data management;
- feature settings;
- integrations;
- maintenance mode;
- backup status;
- background jobs;
- system health;
- audit access.

## 3. Приоритеты

### P0 — требуется до начала соответствующей реализации

- GAP-02 Medical data policy.
- GAP-03 Tourism classification and experience rules.
- GAP-06 TourSlet integration after archive inspection.
- GAP-08 Backup/DR runbook for production launch.
- GAP-09 Observability baseline before production.

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
3. Если бизнес-решение меняет существующую архитектуру, создаётся или обновляется ADR.
4. После принятия решения исходный GAP закрывается и требования синхронизируются с основными документами.

## 5. Результат анализа

Архитектурный фундамент TourCRM сформирован, но проект ещё не готов к слепой реализации всех модулей подряд.

Следующий этап аналитики — закрывать P0/P1 GAP по мере приближения соответствующей разработки и одновременно превращать готовые домены в атомарные GitHub Issues по установленному Issue Contract.
