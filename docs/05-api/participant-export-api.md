# TourCRM — Participant Export API

## 1. Назначение

Participant Export — административный операционный workflow для формирования списков участников клуба.

В MVP экспорт доступен только `Administrator`. Export не является произвольной выгрузкой БД: backend формирует разрешённый dataset по заданному контексту и отдаёт его в одном из поддерживаемых представлений.

Competition document package, medical data и document binaries не входят в Participant Export и остаются отдельными workflows.

## 2. Authorization

Participant Export доступен только пользователю с ролью `admin`.

Frontend не является источником authorization. Backend обязан проверять право на экспорт и границы текущего Club при каждом выполнении операции.

Выбор экспортируемых полей не расширяет права пользователя: frontend может выбирать только поля из canonical export-field allowlist, а backend повторно валидирует каждое поле при формировании результата.

## 3. Export context

Экспорт формируется относительно одного из следующих контекстов:

- `club` — все участники текущего Club, удовлетворяющие дополнительным фильтрам;
- `group` — участники выбранной Group;
- `event` — участники выбранного Event;
- `group + event` — участники выбранной Group, относящиеся к выбранному Event.

`group + event` является пересечением контекстов GroupMembership и EventParticipation согласно canonical Event policy. Frontend не передаёт произвольный список `person_ids` как источник dataset.

Контекст не меняет authorization: Administrator остаётся ограничен текущим Club.

## 4. Filters

Точный набор query/request filters является частью implementation API и не должен дублироваться в frontend-only логике.

MVP поддерживает контекстные фильтры, необходимые для выбора Club/Group/Event dataset, и статусные фильтры соответствующих membership/participation records, если они применимы к выбранному контексту.

Фильтр должен выполняться backend до построения экспортного результата.

### 4.1 Participation status (PO decision, PR #226)

Для текущего MVP canonical-значения фильтра `participation_status` (`EventParticipation.registration_status`, только контексты `event` и `group_event`):

- `registered`;
- `cancelled`.

Основание — ADR-0037 и фактически реализованный self-registration workflow: только эти статусы записываются в `EventParticipation`. Справочные значения ADR-0020 §4 (`invited`, `waitlisted`, `declined`, `removed`) сохраняются в документации как относящиеся к будущему lifecycle и в этот фильтр не входят; расширение списка — отдельное решение PO после реализации admin status management (`events-api.md` §21).

Backend — единственный источник значений и их отображаемых подписей:

- `GET /api/v1/memberships/exports/filters` (Administrator-only, как и остальной export) возвращает `{"participation_status": [{"value", "label"}]}`;
- `POST /api/v1/memberships/exports` отклоняет любое другое значение `participation_status` с `422 invalid_participation_status` (аналогично `invalid_membership_status`); отсутствие фильтра означает все статусы.

Frontend не содержит собственного списка этих значений и не подставляет fallback-список при ошибке загрузки metadata.

## 5. Export fields

Administrator самостоятельно выбирает поля через multi-select.

Participant Export использует canonical allowlist полей. Каждое поле имеет стабильный `field_code`, display label, источник данных и правила доступности.

Минимальный canonical набор полей MVP:

### Person

- `person.last_name`
- `person.first_name`
- `person.middle_name`
- `person.birth_date`
- `person.phone`
- `person.email`
- `person.address`

### Group / membership context

- `group.name`
- `membership.status`

### Event context

- `event.name`
- `event.starts_at`
- `event_participation.status`

### Guardian context

- `guardian.name`
- `guardian.phone`

Guardian fields являются отдельными canonical export fields и не появляются автоматически: Administrator явно включает их в selection.

Состав allowlist является backend-authoritative. Наличие поля в Person, GuardianRelationship, Event или Document API само по себе не делает его доступным для Participant Export.

## 6. Sensitive data boundary

Participant Export не включает:

- medical data;
- document metadata или document binaries, если они относятся к competition/document workflow;
- authentication credentials;
- password-reset data;
- internal authorization fields;
- storage keys, filesystem paths и технические UUID, если они не были отдельно утверждены как export fields.

Добавление нового sensitive field в export allowlist требует отдельного PO decision и canonical documentation update.

## 7. Output formats

MVP поддерживает три output modes:

- `XLSX` — editable spreadsheet;
- `PDF` — print-ready document;
- `Print` — browser/system print representation.

Authorization, context resolution, filtering и field allowlisting выполняются один раз на canonical dataset. Формат является presentation concern и не должен создавать отдельные правила доступа.

## 8. Export dataset semantics

Одна строка результата представляет одного Person в выбранном operational context.

Если выбран Group context, строка содержит Person и выбранный Group context.

Для `group` и `group_event` membership status определяется записью GroupMembership (Issue #218, GAP-2): Person включается по статусу GroupMembership независимо от статуса ClubMembership — например, при ClubMembership `suspended` и GroupMembership `active` Person остаётся в результате.

Если выбран Event context, строка содержит Person и Event context, включая выбранный статус participation при его наличии.

Если выбраны Group + Event, результат содержит только Person, одновременно удовлетворяющих GroupMembership и EventParticipation для выбранного Event, согласно canonical Event visibility/participation policy.

Export не изменяет Person, Membership, GroupMembership или EventParticipation.

## 9. API shape

Participant Export является отдельным operational workflow. Конкретный HTTP endpoint, request schema, response/download semantics, pagination/batching strategy и synchronous/asynchronous execution должны быть определены implementation Issue TH-0118.4 до начала кодирования.

Не следует создавать несколько независимых backend authorization paths для XLSX/PDF/Print. Все форматы должны использовать один canonical export dataset и одну authorization policy.

## 10. MVP exclusions

Не входит в TH-0118.4:

- сохранение пользовательских export templates;
- sharing templates между администраторами;
- автоматические типовые templates для МЧС/организаторов/групп;
- competition document package export;
- medical/document package export;
- instructor access.

Saved/reusable export templates вынесены в технический долг **#217**.
