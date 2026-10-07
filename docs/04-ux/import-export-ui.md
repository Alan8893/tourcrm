# TourCRM — Participant Import & Export UI

## 1. Назначение

Документ фиксирует каноническое UX-решение для пользовательского интерфейса Participant Import / Export.

Он дополняет `docs/04-ux/information-architecture.md` и `docs/05-api/participant-export-api.md`. Документ не вводит новый глобальный navigation item.

## 2. Product decision

Participant Import / Export является **Administrator-only** функциональностью.

Frontend visibility не заменяет backend authorization. Backend остаётся источником истины для проверки полномочий.

Import и Export не являются самостоятельными глобальными разделами навигации.

## 3. Global navigation placement

Каноническая глобальная навигация TourCRM сохраняется без изменений:

1. Главная
2. Люди
3. Группы
4. События
5. Достижения
6. Отчёты
7. Настройки

Новый пункт `Экспорт`, `Импорт` или `Импорт / Экспорт` в глобальную navigation не добавляется.

### 3.1 Import → Люди

**Основной entry point для Participant Import — раздел `Люди`.**

Логика: Import изменяет состав людей и membership-данные системы, поэтому пользовательский смысл операции относится к управлению людьми, а не к отчётности.

В разделе `Люди` Administrator получает действие/подраздел `Импорт`.

Целевой сценарий:

`Люди → Импорт → upload → preview → approve → apply → report`

### 3.2 Export → Отчёты

**Основной entry point для Participant Export — раздел `Отчёты`.**

Participant Export и отчёт «Участники мероприятий» используют один canonical dataset и одну authorization policy. Отчёт не является вторым независимым механизмом выборки участников.

### 3.3 Отчёт «Участники мероприятий»

Это Administrator-only read/report workflow для проверки фактической регистрации участников на Event.

Целевой сценарий:

`Отчёты → Участники мероприятий → выбор контекста → фильтры → preview → XLSX/PDF/Print`

Контексты:
- club;
- group;
- event;
- group + event.

Для Event/group+event доступны canonical `participation_status`:
- registered;
- cancelled.

Отчёт использует canonical `EventParticipation` и существующий Participant Export field allowlist. Frontend не создаёт собственный список статусов/полей и не выполняет локальную фильтрацию более широкого dataset.

Минимальный preview:
- ФИО;
- группа;
- мероприятие;
- дата/время;
- статус регистрации.

`XLSX`, `PDF` и `Print` являются разными представлениями одного dataset. Новая permission для отчёта не вводится: доступ наследуется от административного раздела `Отчёты`, а backend остаётся authoritative.


Export рассматривается как **master-модуль формирования выгрузки**, а не как одна фиксированная операция «скачать участников».

Целевой сценарий:

`Отчёты → Экспорт → выбор датасета → выбор полей → фильтры → формат → экспорт`

## 4. Export master module

Export UI должен быть универсальным мастером, который позволяет Administrator сформировать нужный набор данных под конкретную задачу.

### 4.1 Dataset / context

Administrator сначала выбирает, **что именно выгружается**. В текущем backend contract поддерживаются соответствующие canonical contexts, включая:

- club;
- group;
- event;
- group + event.

Frontend не должен самостоятельно рассчитывать dataset. Контекст и ограничения передаются в backend, который является authoritative.

### 4.2 Fields

После выбора dataset Administrator выбирает необходимые поля через **multi-select**.

Набор доступных полей приходит с backend через:

`GET /api/v1/memberships/exports/fields`

Frontend не создаёт второй независимый справочник разрешённых полей.

Причина: разные задачи требуют разного состава данных. Например:

- заявка/список для МЧС может требовать ФИО, дату рождения, телефоны и представителей;
- список для организаторов мероприятия может требовать другой набор;
- рабочий список инструктора может требовать только операционно необходимые поля.

### 4.3 Filters

После выбора dataset Administrator задаёт доступные для него фильтры, например:

- группа;
- событие;
- статус членства;
- статус участия.

Состав и допустимые комбинации фильтров определяются backend contract.

### 4.4 Output format

Administrator выбирает:

- XLSX — рабочая/редактируемая выгрузка;
- PDF — готовая к распространению/печати выгрузка;
- Print — печатное представление.

Все форматы должны использовать один и тот же выбранный dataset и один набор выбранных полей.

### 4.5 Future extension

В дальнейшем мастер может получить сохранённые шаблоны наборов полей и фильтров. Это **не входит в текущий UI slice** и остаётся technical debt `#217`.

## 5. Contextual actions

Помимо основных entry points через `Люди` и `Отчёты`, операции могут быть доступны как contextual action там, где пользователь уже находится в соответствующем доменном контексте.

### 5.1 Group context

В контексте конкретной группы Administrator может выполнить:

- Export участников группы;
- Import участников в группу — **только после отдельного backend/domain contract**, так как текущий Participant Import MVP не создаёт GroupMembership.

### 5.2 Event context

В контексте конкретного события Administrator может выполнить:

- Export участников события;
- Export участников группы в контексте события, если это поддержано backend contract;
- Import в событие — только после отдельного утверждения соответствующего domain contract.

Frontend не должен самостоятельно определять membership/participation intersection.

### 5.3 People context

В контексте раздела `Люди` Administrator получает contextual action `Импорт` как shortcut к основному Import workflow.

Если в будущем появятся операции массового изменения существующих людей, их placement должен рассматриваться отдельно; Import не должен автоматически превращаться в generic bulk-edit механизм.

## 6. Import UI

Backend Participant Import уже реализован и является authoritative для frontend.

Frontend использует существующий workflow:

```text
upload
→ parse
→ validate
→ preview
→ approve
→ apply
→ report
```

Upload не должен автоматически применять изменения.

Текущий MVP Import создаёт только:

- Person;
- User;
- ClubMembership.

Import UI **не должен** добавлять выбор или редактирование RoleAssignment, GroupMembership, GroupInstructorAssignment или GuardianRelationship до отдельного утверждения соответствующего backend contract.

### 6.1 Source-file handling

После успешного завершения импорта исходный CSV/XLSX удаляется.

Результат ImportJob, counters, errors/warnings и audit history сохраняются.

Для `failed` и `partially_completed` исходный файл пока сохраняется, чтобы администратор мог разобраться с результатом или повторить операцию.

## 7. Implementation order

Реализация выполняется в следующем порядке:

1. Participant Export backend — **done (#219)**.
2. Participant Import backend — **done (#187, #188, #194)**.
3. Единый frontend slice Import + Export UI — **текущий этап (#225)**.
4. Saved export templates — отдельный technical debt (`#217`), не включается автоматически в UI slice.

## 8. Navigation and role visibility

Только Administrator получает navigation/action visibility для Import / Export.

Instructor, Member и Guardian не получают Import / Export UI.

Если пользователь пытается открыть недоступный route/action напрямую, действует стандартная модель frontend section guard + backend authorization.

## 9. Single-club product constraint

TourCRM остаётся одноклубным продуктом на уровне бизнес-модели.

Существующий backend `Club` сохраняется только как техническая сущность до будущего рефакторинга. UI не должен вводить выбор клуба или multi-club workflows.

## 10. Canonical references

- `docs/04-ux/information-architecture.md`
- `docs/05-api/participant-import-api.md`
- `docs/05-api/participant-export-api.md`
- `docs/05-api/people-api.md`
- GitHub Issue #174 — TH-0118: Participant Import & Export
- GitHub Issue #225 — TH-0118.5: Participant Import + Export frontend UI
- GitHub PR #219 — Participant Export backend
