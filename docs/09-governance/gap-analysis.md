# TourCRM — System Gap Analysis

## Назначение

Документ фиксирует результат сквозной аналитической проверки документации TourCRM и определяет, какие области уже имеют достаточный контракт, а какие требуют дополнительного бизнес-решения или входных данных.

Это не список «ошибок проекта». Это управляемый реестр незавершённых аналитических решений.

## 1. Статус покрытия

### Полностью покрыто на уровне архитектурного/технического контракта

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
- Backup / Disaster Recovery technical baseline.
- Observability baseline.
- Feature settings governance.
- Analytics metric catalog baseline.
- Search specification baseline.
- File processing pipeline baseline.
- Operational administration baseline.
- ADR process.
- Issue / implementation contract.
- Roadmap.

«Полностью покрыто» означает, что область описана достаточно для архитектурного планирования и постановки детальной Issue. Перед реализацией конкретной функции её detail-level contract всё равно должен быть указан в соответствующей Issue.

## 2. Оставшиеся GAP

### GAP-01 — Legal / regulatory policy decisions

Статус: OPEN / BUSINESS + LEGAL INPUT REQUIRED.

Нужно утвердить:

- применимый набор требований к персональным данным;
- правила и сроки хранения отдельных категорий данных;
- тексты/версии пользовательских и родительских согласий;
- правила удаления/анонимизации;
- порядок обработки запросов на доступ/исправление/удаление;
- ответственных лиц клуба за обработку данных.

Техническая система должна поддерживать эти правила, но не должна самостоятельно выдумывать юридические сроки.

### GAP-02 — Medical data policy

Статус: TECHNICAL READY / BUSINESS POLICY REQUIRED.

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

Статус: TECHNICAL READY / NORMATIVE POLICY REQUIRED.

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

Платформа поддерживает рейтинг как отключаемую функцию, но формула рейтинга пока не утверждена.

### GAP-05 — Calendar integrations

Статус: OPEN.

Внутренний календарь является canonical source. Внешние календари — производная синхронизация.

Нужно отдельно утвердить приоритет внешних календарей и способ синхронизации.

### GAP-06 — TourSlet integration

Статус: BLOCKED BY INPUT.

Требуется архив существующего сайта турслётов. После получения архива необходимо определить фактический стек, модель данных, auth model, точки интеграции, API/SSO feasibility, mapping, synchronization и ownership данных.

### GAP-07 — Export / import contracts

Статус: PARTIAL.

Техническая архитектура определена, но ещё требуется module-level contract для конкретных CSV/XLSX/PDF/DOCX сценариев и шаблонов.

### GAP-14 — Audit retention

Статус: TECHNICAL READY / POLICY REQUIRED.

AuditLog и правила privileged access определены. Остаются срок хранения, перечень обязательных audit events и tamper-evidence policy.

## 3. Снятые технические GAP

Следующие области получили спецификацию и больше не считаются архитектурным пробелом:

- GAP-08 Backup / Disaster Recovery — `docs/08-infrastructure/backup-and-disaster-recovery.md`.
- GAP-09 Observability — `docs/08-infrastructure/observability.md`.
- GAP-10 Feature settings governance — `docs/09-governance/feature-settings.md`.
- GAP-11 Reporting / Analytics semantic model — `docs/09-governance/analytics-metric-catalog.md`.
- GAP-12 Search — `docs/05-api/search-specification.md`.
- GAP-13 File processing — `docs/03-architecture/file-processing.md`.
- GAP-15 Operational administration — `docs/09-governance/operational-administration.md`.

Для каждого из них остаётся детальная конкретизация в реализации соответствующего модуля, но базовые правила больше не являются неформализованными.

## 4. Приоритеты

### P0 — до соответствующей реализации / production

- GAP-06 TourSlet integration — после получения архива.
- GAP-02 Medical policy — до медицинского функционала.
- GAP-03 Tourism normative rules — до автоматического расчёта подтверждённого опыта.
- GAP-08 Backup/DR operational parameters — до production launch.
- GAP-09 Observability operational thresholds — до production launch.

### P1 — до реализации соответствующего модуля

- GAP-04 Rating algorithm.
- GAP-05 Calendar integrations.
- GAP-07 Export/import contracts.
- GAP-14 Audit retention.

### P2 — governance / business decisions

- GAP-01 Legal/regulatory policy decisions.

## 5. Правило работы с GAP

1. Нельзя выдавать Claude задачу, требующую решения, которого нет в документации.
2. В Issue должна быть ссылка на GAP, если задача зависит от него.
3. Если бизнес-решение меняет архитектуру, создаётся или обновляется ADR.
4. После принятия решения GAP закрывается и связанные документы синхронизируются.

## 6. Результат анализа

Архитектурный фундамент TourCRM сформирован. Большинство ранее выявленных технических пробелов получили отдельные спецификации. Остались в основном внешние нормативные решения, конкретизация отдельных функций и интеграция существующего TourSlet после получения входного архива.

Следующий этап — финальная сверка требований и формирование атомарных GitHub Issues по установленному Issue Contract.
