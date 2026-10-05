# TourCRM — Trips & Tourist Profile API

## 1. Назначение

Документ является детальным API-контрактом домена туристской деятельности.

Покрывает:

- походы (Trip) и факт участия (TripParticipant);
- маршруты, точки маршрутов и GPX-файлы;
- туристский профиль, навыки, квалификации (черновые, не реализованы);
- ссылки на Achievement API.

Документ опирается на:

- `docs/04-modules/trips-and-tourist-profile.md` — семантика туристских фактов (Tourism Facts v2);
- `docs/03-architecture/domain-model.md`;
- `docs/03-architecture/data-model.md`;
- `docs/05-api/endpoint-inventory.md`;
- `docs/05-api/api-contract.md`;
- `docs/05-api/auth-and-authorization.md`.

Базовый API prefix: `/api/v1`.

## 2. Общие принципы

### 2.1 Source of truth

Бизнес-семантика туристских фактов (Tourism Facts v2) определяется `docs/04-modules/trips-and-tourist-profile.md`; логическая модель — `docs/03-architecture/data-model.md`. Этот документ описывает API-контракт и не вводит собственных бизнес-правил.

Первичными фактами туристской деятельности являются:

- `Event` — владелец lifecycle (у Trip собственного lifecycle нет);
- `Trip` — туристское расширение Event;
- `EventParticipation` — канонический источник участия/регистрации;
- `TripParticipant` — туристское расширение EventParticipation (`actual_participation`);
- туристские факты Trip: TourismType, Official Difficulty, Geography, Duration Classification, Result, Route с Planned/Actual представлениями и GPX;
- вручную подтверждённые квалификации;
- `AchievementAward`.

`TouristProfile` является производным представлением и не должен использоваться как единственный источник исторических фактов.

### 2.2 Authorization

Каждый endpoint проверяет authentication и permission/scope.

Trip, TripParticipant, Route и Planned/Actual GPX используют существующие `trip.read`/`trip.manage` и их scope (`docs/02-requirements/roles-and-permissions.md` §7, §11.1). Отдельные Route/GPX permissions (`gpx.upload`, `route.manage` и аналоги) не существуют.

### 2.3 Historical integrity

Исторический туристский факт после завершения Trip (`Event.status = completed`) не изменяется обычной операцией; исправление выполняется только через будущий Historical Correction Workflow (`trips-and-tourist-profile.md` §13), который не реализован и API которого не определён (§13 ниже).

Удаление исторически значимого похода физически запрещено обычным пользователям.

### 2.4 Статус разделов

- §3–§4 — **реализованный** API Trip Foundation (Issue #245) и TourismType (Issue #264, §3.5–§3.6).
- §5–§7 — Route/GPX: перечень endpoints из `docs/05-api/endpoint-inventory.md` §12 с канонической семантикой; **не реализовано**, полный контракт определяется перед реализацией.
- §8–§10, §12 — **не реализовано**; черновые описания, требующие отдельных решений.
- §11 — Achievements: контракт определён в другом месте.

---

# 3. Trips API

Реализовано (Issue #245). Trip идентифицируется id своего Event (`event_id`). Существование и отказ в доступе неразличимы: отсутствующий Event, отсутствующий Trip и Event, на который у вызывающего нет нужного permission/scope, дают одинаковый `404`. Изменяющие запросы требуют CSRF-токен.

## 3.1 List trips

`GET /trips`

### Permissions

- `trip.read` + scope (`all`/`own_events`/`own_groups`/`self`/`children`), применяемый к Event похода внутри SQL-запроса.

### Query parameters

- `status` — статус Event;
- `page`;
- `page_size` (1–100).

### Rules

- возвращаются только Trip, доступные текущему пользователю;
- Trip с `Event.status = archived` не включаются, если `status` не запрошен явно;
- сортировка по `Event.start_at`, затем `event_id`.

Фильтры по туристским фактам (TourismType, Difficulty, Geography и т.п.) не реализованы.

## 3.2 Get trip

`GET /trips/{event_id}`

`trip.read` + scope. Возвращает `event_id`, `tourism_type_id` (nullable), `created_at`, `updated_at`. Остальные туристские факты Trip пока не реализованы и в ответе отсутствуют.

## 3.3 Create trip

`POST /trips`

### Permissions

- `trip.manage` + scope на Event.

### Request

- `event_id` — существующий Event;
- `tourism_type_id` — необязательная ссылка на активную запись справочника TourismType (§3.6).

### Rules

- Trip — расширение существующего Event; Event создаётся через Events API;
- Event должен иметь `event_type = trip`, иначе `422 event_not_trip`;
- Event не должен быть `cancelled`/`archived`, иначе `409 trip_event_lifecycle_closed`;
- не более одного Trip на Event, иначе `409 trip_already_exists`;
- `tourism_type_id` несуществующей записи — `422 tourism_type_not_found`, неактивной — `422 tourism_type_inactive`;
- создание фиксируется в audit (`trip.created`) в той же транзакции.

TourismType, Difficulty, Geography, Result и Route не обязательны для создания Trip; из них в текущем контракте передаётся только `tourism_type_id`.

## 3.4 Lifecycle

Отдельного lifecycle, статус-endpoint, complete- или archive-endpoint у Trip нет. Lifecycle Trip — это lifecycle связанного Event (ADR-0018, `docs/05-api/events-api.md`): `draft`/`published`/`in_progress`/`completed`/`cancelled`/`archived`.

Завершение Event не требует наличия Route, Actual GPX, Result, дистанции или других туристских фактов.

Изменение TourismType — §3.5. Изменение остальных туристских фактов Trip (Official Difficulty, Geography, Duration Classification, Result) через API пока не реализовано; при реализации действуют правила авторизации и исторической фиксации из `trips-and-tourist-profile.md` §3–§12.

## 3.5 Update trip

`PATCH /trips/{event_id}`

Обычное редактирование Trip (Issue #264). Изменяются только поля, присутствующие в запросе.

### Permissions

- `trip.manage` + scope на Event (Administrator — scope Trip; Instructor — только assigned/owned Trip; Member/Guardian — нет). Отсутствие права — `404` (existence-hiding).

### Request

- `tourism_type_id` — ссылка на активную запись TourismType или `null` (снять).

### Rules

- редактирование открыто при `Event.status` `draft`/`published`/`in_progress`; при `completed` (исторический факт) и `cancelled`/`archived` — `409 trip_editing_closed`;
- назначаемая запись должна существовать (`422 tourism_type_not_found`) и быть активной (`422 tourism_type_inactive`);
- повтор уже сохранённого значения — no-op;
- TourismType не выводится автоматически ни из каких данных.

## 3.6 TourismType catalog

Реализовано (Issue #264; семантика — `trips-and-tourist-profile.md` §3). Значения каталога не предустановлены.

- `GET /tourism-types` — `page`, `page_size` (1–100), `active`; сортировка по `name`, `id`;
- `POST /tourism-types` — `code` (уникальный), `name`; создаётся активной;
- `GET /tourism-types/{tourism_type_id}`;
- `PATCH /tourism-types/{tourism_type_id}` — `code`, `name`;
- `POST /tourism-types/{tourism_type_id}/activate`;
- `POST /tourism-types/{tourism_type_id}/deactivate`.

DELETE отсутствует: записи не удаляются физически; на запись, использованную Trip, действует FK RESTRICT. Деактивация не изменяет Trip, уже ссылающиеся на запись.

Authorization (отдельного permission нет): чтение — любой grant `trip.read`; создание/изменение/активация/деактивация — `trip.manage` с scope `all` (Administrator). Ошибки: `404 not_found`, `409 tourism_type_code_conflict`, `422 invalid_tourism_type`.

---

# 4. Trip Participants API

Реализовано (Issue #245). TripParticipant — расширение существующей `EventParticipation` Event этого Trip (1:0..1). Добавление/удаление участников и их регистрация выполняются только через Event participation API (ADR-0037); собственного registration status, роли в походе, дистанции, результата или заметок у TripParticipant нет.

## 4.1 List participants

`GET /trips/{event_id}/participants`

- `trip.read` + scope на Event; видимые строки — по тем же правилам, что и состав участников Event (`all`/`own_events`/`own_groups` — все, `self` — своя строка, `children` — строки активных детей);
- `page`, `page_size` (1–100);
- сортировка по фамилии, имени, id Person;
- элемент: `event_participation_id`, `event_id`, `person_id`, `actual_participation`, `created_at`, `updated_at`.

## 4.2 Record actual participation

`PUT /trips/{event_id}/participants/{person_id}`

### Permissions

- `trip.manage` + scope на Event.

### Request

- `actual_participation` — boolean.

### Rules

- у Person должна быть `EventParticipation` в Event этого Trip (любой `registration_status`), иначе `422 participation_missing`;
- Event `in_progress` — факт создаётся или изменяется;
- Event `completed` — факт можно создать, если он ещё не записан; записанный факт исторически закрыт: повтор того же значения — no-op, изменение — `409 trip_participant_historically_closed` (исправление — будущий correction workflow);
- прочие статусы Event — `409 actual_participation_lifecycle_closed`;
- повтор уже сохранённого значения не пишет ничего и не создаёт audit record;
- создание — audit `trip_participant.actual_participation_recorded`, изменение — `trip_participant.actual_participation_changed`;
- EventParticipation не изменяется; отмена регистрации не удаляет и не сбрасывает факт.

---

# 5. Routes API

**Не реализовано.** Перечень соответствует `docs/05-api/endpoint-inventory.md` §12; семантика — `trips-and-tourist-profile.md` §11–§12. Перед реализацией для каждого endpoint должен быть определён полный контракт (`endpoint-inventory.md` §27), включая способ связи Route с Trip.

Каноническая семантика:

- Route — физическое описание маршрута, связанного с Trip; не классификатор;
- Route содержит раздельные представления Planned и Actual (геометрия/точки, distance, elevation gain, если доступны данные высоты); они не смешиваются и не подменяют друг друга;
- Route не содержит и не определяет TourismType, Official Difficulty, Geography, Duration Classification или Result; фильтры/поля Route по этим фактам не предусмотрены;
- управление — `trip.manage` + scope Trip.

Endpoints:

- `GET /routes`
- `POST /routes`
- `GET /routes/{id}`
- `PATCH /routes/{id}`
- `POST /routes/{id}/archive`

---

# 6. Route Points API

**Не реализовано.** Endpoints (`endpoint-inventory.md` §12):

- `GET /routes/{id}/points`
- `POST /routes/{id}/points`
- `PATCH /route-points/{id}`
- `POST /route-points/{id}/delete`

Каноническая семантика:

- точка принадлежит одному представлению — Planned или Actual;
- latitude `-90..90`, longitude `-180..180`; elevation может отсутствовать, если источник её не предоставляет;
- фактические данные завершённого Trip не изменяются обычным редактированием (`trips-and-tourist-profile.md` §13).

---

# 7. GPX API

**Не реализовано.** Endpoints (`endpoint-inventory.md` §12):

- `POST /routes/{id}/gpx/uploads`
- `GET /gpx-files/{id}`
- `GET /gpx-files/{id}/download`
- `POST /gpx-files/{id}/process`

Каноническая семантика (`trips-and-tourist-profile.md` §11.4–§11.7):

- загружаемый GPX имеет явную роль `PLANNED` или `ACTUAL`;
- у Route не более одного канонического Planned GPX и не более одного канонического Actual GPX; дополнительные файлы — provenance/архив и канонических фактов автоматически не создают;
- Planned GPX не становится Actual, Actual GPX не перезаписывает Planned;
- до завершения Trip Actual GPX может загружаться/заменяться в рамках `trip.manage` (Administrator — scope Trip; Instructor — только assigned/owned Trip, в том числе после похода; Member/Guardian — нет); последний загруженный — текущий канонический;
- при `Trip → completed` текущий Actual GPX фиксируется как исторический факт; отсутствие Actual GPX не мешает завершению; после завершения обычное редактирование его не заменяет — только будущий Historical Correction Workflow;
- обработка (`process`) вычисляет технические характеристики только для представления, соответствующего роли файла; значения воспроизводимы из этого источника;
- загрузка или обработка GPX не изменяет TourismType, Official Difficulty, Geography, Duration Classification или Result;
- общая подсистема версионирования GPX и несколько канонических Actual-треков не вводятся;
- ответ не раскрывает внутренний путь storage; доступ к файлу — через авторизованный endpoint.

---

# 8. Tourist Profile API

**Не реализовано; черновик.** Состав производных показателей и правила их расчёта требуют отдельного PO-решения (`trips-and-tourist-profile.md` §16–§17); перечень ниже не утверждает конкретные агрегаты.

## 8.1 Get own tourist profile

`GET /me/tourist-profile`

Возвращает производные показатели и ссылки на первичные факты.

## 8.2 Get participant tourist profile

`GET /people/{person_id}/tourist-profile`

Доступ только при соответствующем scope.

### Recommended response sections

- summary;
- tourism types;
- completed trips count;
- confirmed distance;
- participation history;
- skills;
- qualifications;
- achievements;
- certifications/documents where permitted.

## 8.3 Recalculate profile

`POST /people/{person_id}/tourist-profile/recalculate`

Операция должна быть idempotent.

Источник пересчёта:

- completed trips;
- valid trip participation facts;
- confirmed results;
- valid awards/qualifications.

Пересчёт не должен изменять первичные факты.

Для массового пересчёта администратором допускается отдельная background job.

---

# 9. Skills API

**Не реализовано; черновик.**

## 9.1 List skill catalog

`GET /skills`

Доступен каталог активных навыков.

## 9.2 Create skill

`POST /skills`

Для администратора.

## 9.3 Update skill

`PATCH /skills/{skill_id}`

Изменение опубликованного skill не должно уничтожать исторические значения.

## 9.4 Assign skill to person

`POST /people/{person_id}/skills`

Поля:

- skill;
- level;
- status;
- assessed_at;
- assessor;
- evidence document if applicable;
- notes.

## 9.5 Update assigned skill

`PATCH /people/{person_id}/skills/{assignment_id}`

История оценок должна сохраняться, если изменение меняет подтверждённый уровень.

---

# 10. Qualifications API

**Не реализовано; черновик.** Квалификация не выводится автоматически из количества походов или километража без отдельно утверждённого правила.

## 10.1 List qualifications

`GET /qualifications`

## 10.2 Create qualification

`POST /qualifications`

Только пользователи с соответствующим permission.

### Fields

- qualification type;
- level/category;
- issued date;
- valid until;
- issuer;
- document reference;
- status.

## 10.3 Get person qualifications

`GET /people/{person_id}/qualifications`

## 10.4 Add qualification to person

`POST /people/{person_id}/qualifications`

Перед добавлением проверяется уникальность/совместимость в соответствии с правилами конкретного qualification type.

## 10.5 Revoke qualification

`POST /people/{person_id}/qualifications/{qualification_id}/revoke`

Удаление вместо revoke запрещено для исторически подтверждённой квалификации.

---

# 11. Achievements API

Контракт Achievement Domain определён в `docs/04-modules/achievements-and-norms.md` (A1–A16) и реализован как `/api/v1/achievements/*` (Issue #220). Прежнее описание этого раздела (`/achievements`, `/people/{person_id}/achievements`, `award_mode`, `automatic_rule`) заменено и не является контрактом.

Туристские факты Trip становятся источником Achievement metrics только после отдельного PO-решения; на текущем этапе утверждён единственный metric `completed_trips`.

---

# 12. Tourist Experience

**Отложено.** Семантика туристского опыта, стажа, километража и квалификационных агрегатов (включая правила включения, разбивку по видам туризма и сложности) не утверждена и требует отдельного PO-решения и отдельной задачи (`trips-and-tourist-profile.md` §16–§17, `docs/04-modules/tourism-classification-and-experience.md`). Endpoints этого раздела не определены.

---

# 13. Correction Workflow

Исторические туристские факты завершённого Trip исправляются только через будущий Historical Correction Workflow (`trips-and-tourist-profile.md` §13): correction сохраняет исходное значение, новое значение, причину и инициатора и фиксируется в audit. Права, подтверждение, API и структура correction не утверждены; endpoints не определены.

---

# 14. Common Errors

Все endpoint'ы используют общий error envelope из `api-contract.md`.

Реализованные доменные коды Trips API (§3–§4):

- `event_not_trip` (422);
- `trip_event_lifecycle_closed` (409);
- `trip_already_exists` (409);
- `trip_editing_closed` (409);
- `tourism_type_not_found` (422);
- `tourism_type_inactive` (422);
- `tourism_type_code_conflict` (409, каталог);
- `participation_missing` (422);
- `actual_participation_lifecycle_closed` (409);
- `trip_participant_historically_closed` (409).

Коды ошибок нереализованных разделов определяются их контрактом при реализации.

---

# 15. Transaction and side effects

Критические операции выполняются транзакционно.

- Создание Trip и запись `actual_participation` выполняются в одной транзакции со своим audit record (fail-closed).
- После commit записи `actual_participation` и после перехода Event в `completed` запускается event-driven оценка Achievement Engine; её сбой не откатывает туристский факт, пропуски закрывает reconciliation (`achievements-and-norms.md` §22).
- Пересчёт производных туристских показателей не определён до утверждения их семантики (§12).

---

# 16. Audit requirements

Реализовано (`trips-and-tourist-profile.md` §19): `trip.created`, `trip_participant.actual_participation_recorded`, `trip_participant.actual_participation_changed`.

При реализации остальных разделов audit обязателен как минимум для: изменения туристских фактов Trip, загрузки/замены GPX, correction workflow, создания/изменения квалификации. Конкретные audit actions добавляются в словарь ADR-0024 вместе с реализацией.

---

# 17. Acceptance Criteria

## Trips (реализовано)

- [x] Trip создаётся только как расширение Event типа `trip`; не более одного на Event.
- [x] У Trip нет собственного lifecycle; используется lifecycle Event.
- [x] `actual_participation` — отдельный факт поверх EventParticipation; исторически закрыт после завершения.
- [x] Authorization через `trip.read`/`trip.manage` и scope Event.

## Routes/GPX (при реализации)

- [ ] Planned и Actual представления разделены.
- [ ] GPX имеет роль `PLANNED`/`ACTUAL`; не более одного канонического файла каждой роли.
- [ ] Actual GPX фиксируется при завершении Trip; после завершения не заменяется обычным редактированием.
- [ ] Route/GPX не определяет TourismType, Official Difficulty, Geography, Duration Classification или Result.
- [ ] GPX проходит size/type/format validation; внутренний storage path не раскрывается.
- [ ] Используется `trip.manage`; новые permissions не вводятся.

## Security

- [ ] Participant не видит чужой туристский профиль.
- [ ] Guardian видит только связанных детей согласно scope.
- [ ] Instructor не получает административные права через trip endpoints.
- [ ] Все sensitive changes аудируются.

---

# 18. Implementation note for Claude

Claude должен реализовывать API только после сверки этого документа с:

- `trips-and-tourist-profile.md`;
- `domain-model.md`;
- `data-model.md`;
- `roles-and-permissions.md`;
- `api-contract.md`;
- `auth-and-authorization.md`.

При обнаружении противоречия между документами реализация не должна выбирать вариант самостоятельно. Сначала создаётся отдельная Issue на устранение противоречия или ADR.
