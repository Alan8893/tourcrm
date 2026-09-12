# TourCRM — Search Specification

## 1. Назначение

Поиск должен быть единообразным, permission-aware и возвращать только объекты, доступные текущему пользователю.

## 2. Search types

- global search;
- people search;
- events search;
- trips/routes search;
- documents search;
- knowledge base search;
- equipment search;
- finance search, только при наличии соответствующего permission.

## 3. Search contract

Каждый search endpoint должен поддерживать при необходимости:

- query;
- filters;
- sort;
- page/page_size или cursor;
- requested fields/projection;
- result type metadata.

## 4. Security

Filtering by permissions and scopes происходит на сервере до формирования результата. Frontend filtering не считается механизмом защиты.

## 5. People search

Поиск может учитывать:

- ФИО;
- идентификаторы участника;
- email/телефон в пределах разрешённого scope;
- группу;
- статус membership.

Чувствительные поля не должны становиться searchable по умолчанию.

## 6. Knowledge/document search

Поддерживается полнотекстовый поиск по разрешённым индексируемым полям. Binary file contents индексируются только при наличии явно определённого extractor pipeline.

## 7. Ranking

Начальный ranking должен быть deterministic и документирован. Search provider abstraction должна позволять заменить реализацию без изменения API contract.

## 8. Empty/error behavior

Пустой результат возвращается как валидный ответ с total/has_more semantics. Ошибки валидации query и filter должны использовать стандартный API error format.

## 9. Acceptance criteria

- [ ] поиск permission-aware;
- [ ] единый pagination contract;
- [ ] фильтры документированы;
- [ ] sensitive fields excluded by default;
- [ ] empty state deterministic;
- [ ] ranking reproducible;
- [ ] UI не является security boundary.
