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

Для Administrator операции работы с массовыми данными участников логически относятся к разделу **Отчёты** и должны быть доступны из его рабочего контекста.

Таким образом, `Отчёты` является основным navigation entry point для административных операций, связанных с подготовкой выгрузок участников.

## 4. Contextual actions

Помимо основного entry point через `Отчёты`, Import / Export должен быть доступен как contextual action там, где пользователь уже находится в соответствующем доменном контексте.

### 4.1 Group context

В контексте конкретной группы Administrator может выполнить:

- Export участников группы;
- Import участников в группу — после появления соответствующего backend import contract.

Context передаётся в backend как `group` или другой соответствующий canonical context; frontend не пересчитывает dataset самостоятельно.

### 4.2 Event context

В контексте конкретного события Administrator может выполнить:

- Export участников события;
- Export участников группы в контексте события, если это поддержано backend contract;
- Import в событие только после отдельного утверждения соответствующего domain contract.

Frontend не должен самостоятельно определять membership/participation intersection.

### 4.3 Club-level context

Для выгрузки всего клуба основной entry point — `Отчёты`.

UI должен предоставлять административный workflow для выбора контекста и набора данных, соответствующий backend export contract.

## 5. Export UI

Backend Participant Export API уже реализован.

Frontend Export UI должен использовать существующий API и не дублировать его бизнес-правила.

Минимальный workflow:

1. Administrator открывает `Отчёты` или contextual action.
2. Выбирает контекст выгрузки.
3. При необходимости выбирает группу и/или событие.
4. Выбирает фильтры, доступные для выбранного контекста.
5. Выбирает поля через backend field allowlist.
6. Выбирает формат:
   - XLSX;
   - PDF;
   - Print.
7. Запускает экспорт.
8. Frontend показывает результат и ошибки backend.

Состав и доступность полей определяет backend endpoint `GET /api/v1/memberships/exports/fields`. Frontend не должен поддерживать второй независимый allowlist.

## 6. Import UI

Import UI реализуется **после завершения backend Import slices**.

Frontend не должен придумывать API или бизнес-правила до появления canonical backend contract.

Целевой workflow:

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

## 7. Implementation order

Реализация выполняется в следующем порядке:

1. Participant Export backend — **done (#219)**.
2. Participant Import backend — следующий этап.
3. Единый frontend slice Import + Export UI — после готовности Import backend contract.
4. Saved export templates — отдельный technical debt (`#217`), не включается автоматически в UI slice.

## 8. Navigation and role visibility

Только Administrator получает navigation/action visibility для Import / Export.

Instructor, Member и Guardian не получают Import / Export UI.

Если пользователь пытается открыть недоступный route/action напрямую, действует стандартная модель frontend section guard + backend authorization.

## 9. Single-club product constraint

TourCRM остаётся одноклубным продуктом на уровне бизнес-модели.

Существующий backend `Club` сохраняется только как техническая сущность до будущего рефакторинга. UI не должен вводить выбор клуба или multi-club workflows.

## 10. Future extension

Будущий шаблонный механизм экспорта может позволить Administrator сохранять часто используемые наборы полей/фильтров. Это не входит в текущий Import / Export UI scope и остаётся отдельным technical debt (`#217`).

## 11. Canonical references

- `docs/04-ux/information-architecture.md`
- `docs/05-api/participant-export-api.md`
- `docs/05-api/people-api.md`
- GitHub Issue #174 — TH-0118: Participant Import & Export
- GitHub PR #219 — Participant Export backend
