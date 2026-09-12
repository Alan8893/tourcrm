# TourCRM — Analytics Metric Catalog

## 1. Назначение

Каталог является каноническим источником определения метрик и KPI. UI, API, отчёты и экспорт не должны самостоятельно реализовывать разные формулы одного показателя.

## 2. Metric definition

Каждая метрика должна содержать:

- metric_key;
- human-readable name;
- description;
- formula;
- source entities/fields;
- aggregation method;
- time basis;
- timezone semantics;
- default filters;
- supported filters;
- visibility/permission requirement;
- null/empty semantics;
- rounding/display rules;
- refresh/recalculation semantics;
- whether historical snapshots are required.

## 3. Metric classes

Поддерживаются:

- operational metrics — текущая работа клуба;
- participation metrics — участие и посещаемость;
- tourism metrics — походы и туристский опыт;
- financial metrics — платежи, расходы, задолженность;
- equipment metrics — остатки и эксплуатация;
- communication metrics — уведомления/доставка;
- system metrics — техническое состояние.

## 4. Canonical source

Метрики должны вычисляться из первичных доменных фактов. Производные агрегаты могут кэшироваться, но не становятся самостоятельным источником истины без документированного snapshot use case.

## 5. Example baseline metrics

- active_members_count;
- active_instructors_count;
- upcoming_events_count;
- attendance_rate;
- completed_trips_count;
- confirmed_tourism_distance;
- achievements_awarded_count;
- outstanding_balance;
- equipment_items_in_stock;
- notification_delivery_rate.

Точные формулы и разрешённые фильтры должны быть утверждены в module-specific specs перед реализацией конкретной метрики.

## 6. Permissions

Metric visibility должна проверяться после применения domain scope. Недостаток permission должен исключать данные, а не только скрывать UI-карточку.

## 7. Time semantics

Для событий с timezone должны использоваться canonical instants в storage и явная локальная timezone при презентации. Метрики за день/неделю/месяц должны иметь документированную timezone basis.

## 8. Historical corrections

Изменение первичных фактов может изменить производную метрику. Система должна пересчитывать её по canonical facts либо применять versioned snapshot policy, если это явно определено.

## 9. Acceptance criteria

- [ ] единый catalog metric definitions;
- [ ] каждая метрика имеет формулу и источник;
- [ ] permissions определены;
- [ ] timezone semantics определены;
- [ ] отсутствует дублирование формул между UI/API/reporting;
- [ ] correction/recalculation behavior определён.
