# TourCRM — Trips & Tourist Profile API

## 1. Назначение

Документ является детальным API-контрактом домена туристской деятельности.

Покрывает:

- походы и туристские мероприятия;
- участников походов;
- маршруты и точки маршрутов;
- GPX-файлы и геоданные;
- туристский опыт;
- навыки;
- квалификации;
- достижения и их выдачу.

Документ опирается на:

- `docs/03-architecture/domain-model.md`;
- `docs/03-architecture/data-model.md`;
- `docs/05-api/api-contract.md`;
- `docs/05-api/auth-and-authorization.md`.

Базовый API prefix: `/api/v1`.

## 2. Общие принципы

### 2.1 Source of truth

Первичными фактами туристской деятельности являются:

- `Event`;
- `Trip`;
- `TripParticipant`;
- `Route`;
- загруженные GPX/file objects;
- вручную подтверждённые квалификации;
- `AchievementAward`.

`TouristProfile` является производным представлением и не должен использоваться как единственный источник исторических фактов.

### 2.2 Authorization

Каждый endpoint проверяет authentication и permission/scope.

Доступ инструктору обычно ограничивается собственными группами/мероприятиями, а участнику — собственными данными. Доступ родителя ограничивается связанными детьми.

### 2.3 Historical integrity

После завершения похода изменение фактов, влияющих на стаж, километраж, результаты и достижения, должно проходить через правила аудита и проверки целостности.

Удаление исторически значимого похода физически запрещено обычным пользователям.

---

# 3. Trips API

## 3.1 List trips

`GET /trips`

### Permissions

- `trip.read` + подходящий scope.

### Query parameters

- `status`;
- `tourism_type`;
- `difficulty_category`;
- `region`;
- `leader_id`;
- `participant_id`;
- `from`;
- `to`;
- `search`;
- `page`;
- `page_size`;
- `sort`.

### Rules

- возвращаются только `Trip`, доступные текущему пользователю;
- архивные записи не включаются по умолчанию;
- фильтрация по `participant_id` не должна обходить scope authorization.

## 3.2 Get trip

`GET /trips/{trip_id}`

Возвращает:

- базовое мероприятие;
- туристские характеристики;
- маршрут;
- руководителя;
- состав участников согласно правам;
- результаты;
- связанные документы, если пользователь имеет право;
- агрегированные показатели.

## 3.3 Create trip

`POST /trips`

Создаёт туристское расширение существующего `Event` либо создаёт event вместе с trip в рамках одной транзакции — конкретный вариант должен быть одинаковым во всех реализациях.

### Обязательные данные

- event information;
- `tourism_type`;
- `region`;
- leader;
- статус.

### Валидация

- `start_at < end_at`;
- leader должен иметь соответствующее право/роль;
- event type должен быть совместим с trip;
- planned distance не может быть отрицательной;
- координаты и геоданные валидируются отдельно.

## 3.4 Update trip

`PATCH /trips/{trip_id}`

Редактирует данные, разрешённые текущим статусом похода.

После перехода в финальный статус критические поля изменяются только через специальный correction workflow.

## 3.5 Change trip status

`POST /trips/{trip_id}/status`

Переходы:

`draft -> planned -> registration_open -> in_progress -> completed`

Допустимы технически необходимые состояния:

- `cancelled`;
- `archived`.

Нельзя переводить завершённый поход обратно в активный без административного correction workflow.

## 3.6 Complete trip

`POST /trips/{trip_id}/complete`

Перед завершением проверяются:

- состав участников;
- факт участия;
- фактические даты;
- фактический маршрут при наличии;
- фактическая дистанция;
- результаты;
- обязательные документы, если для типа похода они настроены как обязательные.

После завершения запускается пересчёт производных туристских показателей.

## 3.7 Archive trip

`POST /trips/{trip_id}/archive`

Архивирует поход без удаления исторических фактов.

---

# 4. Trip Participants API

## 4.1 List participants

`GET /trips/{trip_id}/participants`

Возвращает список участников согласно permissions.

Поддерживает фильтры:

- `role_in_trip`;
- `participation_status`;
- `search`.

## 4.2 Add participant

`POST /trips/{trip_id}/participants`

### Request

- `person_id`;
- `role_in_trip`;
- optional notes.

### Rules

- person должен быть допустимым участником клуба/мероприятия;
- несовершеннолетний может участвовать только при выполнении обязательных consent/document rules;
- нельзя создать две активные записи одного человека в одном trip.

## 4.3 Update participant

`PATCH /trips/{trip_id}/participants/{participant_id}`

Изменяет роль, фактическое участие и другие разрешённые атрибуты.

## 4.4 Remove participant

`DELETE /trips/{trip_id}/participants/{participant_id}`

Не удаляет историческую запись, если участник уже участвовал фактически или связанная запись попала в аудит/отчёт.

В таких случаях используется логическое исключение участника из текущего состава.

## 4.5 Record participation result

`POST /trips/{trip_id}/participants/{participant_id}/result`

Может фиксировать:

- actual participation;
- completed distance;
- result;
- notes.

Изменение полей, влияющих на стаж после завершения похода, требует audit.

---

# 5. Routes API

## 5.1 List routes

`GET /routes`

Фильтры:

- `tourism_type`;
- `region`;
- `search`;
- `difficulty_category`;
- pagination/sort.

## 5.2 Get route

`GET /routes/{route_id}`

Возвращает:

- metadata;
- points;
- distance;
- elevation/profile if available;
- associated GPX metadata;
- linked trips согласно правам.

## 5.3 Create route

`POST /routes`

Поля:

- name;
- tourism_type;
- region;
- description;
- planned distance;
- metadata.

## 5.4 Update route

`PATCH /routes/{route_id}`

Изменяет только metadata и редактируемые компоненты.

Фактический трек завершённого похода не должен перезаписывать эталонный логический маршрут без новой версии.

## 5.5 Archive route

`POST /routes/{route_id}/archive`

---

# 6. Route Points API

## 6.1 List points

`GET /routes/{route_id}/points`

Возвращает points в порядке `sequence`.

## 6.2 Create point

`POST /routes/{route_id}/points`

### Validation

- latitude: `-90..90`;
- longitude: `-180..180`;
- sequence уникальна в пределах route;
- elevation может отсутствовать, если источник её не предоставляет.

## 6.3 Update point

`PATCH /routes/{route_id}/points/{point_id}`

## 6.4 Delete point

`DELETE /routes/{route_id}/points/{point_id}`

Для маршрута, уже использованного в завершённых походах, физическое удаление должно быть запрещено либо заменено версионированием.

---

# 7. GPX API

## 7.1 Upload GPX

`POST /routes/{route_id}/gpx`

`multipart/form-data`.

### Requirements

- проверка расширения и MIME;
- ограничение размера файла;
- безопасное имя объекта storage;
- virus/malware scanning при наличии соответствующего сервиса;
- parsing metadata;
- validation GPX structure;
- audit.

### Parsed metadata

Могут быть извлечены:

- distance;
- elevation gain/loss;
- bounds;
- number of points;
- track start/end.

Производные показатели должны иметь признак источника и не подменять вручную подтверждённые значения без правила приоритета.

## 7.2 List GPX files

`GET /routes/{route_id}/gpx`

## 7.3 Get GPX metadata

`GET /routes/{route_id}/gpx/{file_id}`

## 7.4 Download GPX

`GET /routes/{route_id}/gpx/{file_id}/download`

Ответ не должен раскрывать внутренний путь storage.

## 7.5 Delete GPX

`DELETE /routes/{route_id}/gpx/{file_id}`

Физическое удаление ограничивается permissions и правилами retention.

---

# 8. Tourist Profile API

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

## 11.1 List achievement catalog

`GET /achievements`

## 11.2 Create achievement definition

`POST /achievements`

Поля:

- name;
- description;
- category;
- award_mode: `manual|automatic`;
- automatic_rule;
- active.

Automatic rule должна быть валидируемой структурой, а не произвольным исполняемым кодом из БД.

## 11.3 Update achievement definition

`PATCH /achievements/{achievement_id}`

Изменение правила не должно ретроспективно изменять историю уже выданных достижений без отдельного процесса recalculation.

## 11.4 Get person achievements

`GET /people/{person_id}/achievements`

## 11.5 Manually award achievement

`POST /people/{person_id}/achievements`

### Request

- achievement_id;
- awarded_at;
- source/reference;
- note;
- evidence document if needed.

Создатель award фиксируется в audit/history.

## 11.6 Revoke achievement

`POST /people/{person_id}/achievements/{award_id}/revoke`

Не удаляет запись награды, а фиксирует отзыв.

## 11.7 Recalculate automatic achievements

`POST /people/{person_id}/achievements/recalculate`

Операция должна быть idempotent.

Автоматическая выдача не должна создавать дубликаты при повторном запуске.

---

# 12. Tourist Experience

## 12.1 List experience history

`GET /people/{person_id}/tourist-experience`

Возвращает историю подтверждённых фактов участия, а не только aggregate counters.

## 12.2 Get experience summary

`GET /people/{person_id}/tourist-experience/summary`

Рекомендуемые поля:

- trips_count;
- completed_trips_count;
- distance_total;
- distance_by_tourism_type;
- trips_by_difficulty;
- first_trip_date;
- latest_trip_date.

Любой aggregate должен иметь определённое правило включения.

### Default inclusion rule

В агрегаты включаются только завершённые походы и фактически подтверждённое участие. Запланированные/отменённые мероприятия не учитываются.

---

# 13. Correction Workflow

Изменения, которые могут повлиять на исторические показатели, должны выполняться через отдельный процесс.

## 13.1 Create correction request

`POST /trips/{trip_id}/corrections`

Содержит:

- target field;
- current value;
- proposed value;
- reason;
- evidence/document reference.

## 13.2 Approve correction

`POST /trips/{trip_id}/corrections/{correction_id}/approve`

Требуется повышенное permission.

## 13.3 Reject correction

`POST /trips/{trip_id}/corrections/{correction_id}/reject`

История решения сохраняется.

---

# 14. Common Errors

Все endpoint'ы используют общий error envelope из `api-contract.md`.

Дополнительные доменные ошибки:

- `TRIP_NOT_EDITABLE_IN_STATUS`;
- `INVALID_TRIP_STATUS_TRANSITION`;
- `PARTICIPANT_ALREADY_EXISTS`;
- `PARTICIPANT_NOT_ELIGIBLE`;
- `REQUIRED_CONSENT_MISSING`;
- `ROUTE_VERSION_CONFLICT`;
- `GPX_INVALID_FORMAT`;
- `GPX_TOO_LARGE`;
- `QUALIFICATION_EXPIRED`;
- `ACHIEVEMENT_DUPLICATE`;
- `CORRECTION_REQUIRES_APPROVAL`.

---

# 15. Transaction and side effects

Критические операции должны выполняться транзакционно.

### Create/complete trip

Минимальная атомарная граница включает изменение trip state и критические связанные records.

### Complete trip

После commit допускаются asynchronous side effects:

- recalculation tourist profile;
- automatic achievements;
- notifications;
- analytics refresh.

Side effects должны быть retry-safe.

---

# 16. Audit requirements

Audit обязателен для:

- создание/изменение/архивирование похода;
- изменение состава завершённого похода;
- изменение фактической дистанции;
- correction workflow;
- загрузка/удаление GPX;
- выдача/отзыв достижения;
- создание/изменение квалификации;
- ручное изменение туристского опыта.

---

# 17. Acceptance Criteria

## Trips

- [ ] CRUD trip реализован согласно permissions.
- [ ] Status transitions валидируются.
- [ ] Завершённый поход защищён от неконтролируемых изменений.
- [ ] Состав участников сохраняет исторические данные.

## Routes/GPX

- [ ] Route CRUD соблюдает historical integrity.
- [ ] Route points валидируются.
- [ ] GPX проходит size/type/format validation.
- [ ] Внутренний storage path не раскрывается.
- [ ] Производные GPX metrics не подменяют подтверждённые факты автоматически.

## Tourist Profile

- [ ] Profile вычисляется из первичных фактов.
- [ ] Recalculate idempotent.
- [ ] В aggregate входят только данные по утверждённым правилам.

## Achievements/Skills/Qualifications

- [ ] Manual и automatic achievements поддерживаются.
- [ ] Дубликаты наград предотвращаются.
- [ ] Revoke не уничтожает историю.
- [ ] Qualification history сохраняется.

## Security

- [ ] Participant не видит чужой туристский профиль.
- [ ] Guardian видит только связанные accounts/children согласно scope.
- [ ] Instructor не получает административные права через trip endpoints.
- [ ] Все sensitive changes аудируются.

---

# 18. Implementation note for Claude

Claude должен реализовывать API только после сверки этого документа с:

- `domain-model.md`;
- `data-model.md`;
- `roles-and-permissions.md`;
- `api-contract.md`;
- `auth-and-authorization.md`.

При обнаружении противоречия между документами реализация не должна выбирать вариант самостоятельно. Сначала создаётся отдельная Issue на устранение противоречия или ADR.
