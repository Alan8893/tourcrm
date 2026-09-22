# TourCRM — Domain Model

## 1. Назначение

Документ определяет каноническую доменную модель TourCRM. Он является источником требований для последующего проектирования базы данных, API и прикладных сервисов.

## 2. Базовая модель идентичности

TourCRM разделяет четыре понятия:

`Person` — физическое лицо.

`User` — учётная запись для доступа к системе.

`ClubMembership` — участие Person в конкретном Club.

`RoleAssignment` — полномочие User в системе/клубе.

Они не должны смешиваться в одной сущности.

### Пример

Один человек может:

- иметь одну учётную запись;
- быть родителем двух детей;
- быть инструктором;
- одновременно иметь административную роль.

## 3. Club

### Назначение

Представляет конкретный туристский клуб.

### Основные атрибуты

- id;
- name;
- short_name;
- description;
- contacts;
- status;
- created_at;
- updated_at.

Даже при одной реальной организации сущность Club сохраняется для корректной границы домена и будущего масштабирования.

## 4. Person

### Назначение

Каноническая запись о физическом лице.

### Основные группы данных

- ФИО;
- дата рождения;
- контактные данные;
- пол — при необходимости и наличии обоснования;
- адрес — при необходимости;
- фотография;
- системные даты.

Медицинские данные, документы и туристские характеристики не должны без необходимости складываться непосредственно в Person.

## 5. User

### Назначение

Учетная запись аутентификации.

### Основные атрибуты

- id;
- person_id;
- login/email/identifier (nullable only for the canonical login-less pending-stub created when a Person has no email);
- password credential metadata;
- status;
- email_verified_at;
- last_login_at;
- created_at;
- updated_at.

Пароли в открытом виде никогда не хранятся.

Для поддерживаемых participant-creation/import flows User создаётся вместе с Person: при наличии email — `active` с `login_identifier`; без email — `pending` с `login_identifier = NULL` и без password credential. Такой pending-stub не может выполнять вход до явной активации через account-management flow.

## 6. ClubMembership

### Назначение

Историческая связь Person с Club.

### Основные атрибуты

- id;
- club_id;
- person_id;
- membership_type;
- status;
- joined_at;
- left_at;
- created_at;
- updated_at.

История членства должна сохраняться при переводе человека между группами и статусами.

## 7. Role / Permission

### Role

Логическое именованное множество permissions.

Базовые роли:

- admin;
- instructor;
- member;
- guardian.

### Permission

Атомарное право на действие над ресурсом.

Пример:

`event.read`
`event.create`
`attendance.update`
`finance.read`

Система должна поддерживать scope, например `all`, `own_groups`, `own_events`, `self`, `children`.

## 8. GuardianRelationship

### Назначение

Связывает законного представителя с участником.

### Каноническая persistence-модель

`GuardianRelationship` является Club-neutral Person-to-Person relationship. Она не содержит `club_id`; принадлежность и authorization в конкретном Club определяются через актуальные ClubMembership связанных лиц.

Канонические поля:

- id;
- guardian_person_id;
- child_person_id;
- relationship_type;
- status;
- is_primary_contact;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Канонические значения `status`:

- `active`;
- `inactive`;
- `revoked`.

`guardian_person_id` и `child_person_id` не могут совпадать. Исторические `inactive`/`revoked` записи сохраняются. Для одной пары guardian/child/type не допускаются дублирующиеся active relationships; одновременно допустима не более чем одна действующая primary-contact relationship для ребёнка.

Для Event authorization активной считается relationship, которая имеет `status = active` и действующий временной интервал. Доступ guardian к Event в Club требует также соответствующей ClubMembership policy для guardian и ребёнка.

ADR-0025 §3: явное API-действие `terminate` всегда переводит relationship в `status = revoked`. `inactive` — отдельное, не-revoked историческое состояние, достигаемое иными lifecycle-событиями (например, естественным истечением `valid_to`), а не действием `terminate`.

Детальный persistence/authorization contract определён ADR-0023; API permissions (`guardian_relationship.read`/`guardian_relationship.manage`) и URI (`guardian-relationships`) определены ADR-0025.

## 9. Group

### Назначение

Учебная/организационная группа клуба.

### Основные атрибуты

- id;
- club_id;
- name;
- description;
- status;
- valid_from;
- valid_to;
- created_at;
- updated_at.

`Group.status` остаётся строковым значением; канонический набор lifecycle-значений требует отдельного business-policy решения.

Группа принадлежит ровно одному Club.

### GroupMembership

Историческая ассоциация `ClubMembership` с `Group`.

Основные атрибуты:

- id;
- group_id;
- club_membership_id;
- valid_from;
- valid_to;
- membership_status;
- created_at;
- updated_at.

`person_id` непосредственно в GroupMembership не хранится: Person определяется через `club_membership_id -> ClubMembership.person_id`.

`is_primary` и `assigned_by` не входят в каноническую persistence-модель первой версии. Вопрос одновременной принадлежности к нескольким группам, основной группы и отдельной domain-сущности инициатора требует отдельного решения.

### GroupInstructorAssignment

Явная историческая связь User с Group, определяющая ответственность за группу.

Основные атрибуты:

- id;
- group_id;
- user_id;
- role_in_group;
- is_primary;
- valid_from;
- valid_to;
- created_at;
- updated_at.

`role_in_group` пока не имеет закрытого канонического enum; словарь должен быть согласован с моделью ответственности Event.

`is_primary` позволяет отличать основного ответственного от других явных назначений.

`own_groups` authorization опирается на активный GroupInstructorAssignment, а не только на роль instructor.

### Cross-Club ownership

Group принадлежит одному Club. GroupMembership допустим только если `Group.club_id == ClubMembership.club_id`. GroupInstructorAssignment допустим только если назначаемый User имеет active ClubMembership в Club группы.

Эти invariants являются частью cross-Club ownership contract и должны обеспечиваться на authoritative application/service boundary согласно ADR-0022.

## 10. Event

### Назначение

Унифицированное календарное мероприятие.

### Основные типы

- lesson;
- training;
- trip;
- competition;
- tour_slet;
- excursion;
- meeting;
- other.

### Основные атрибуты

- id;
- club_id;
- event_type;
- title;
- description;
- start_at;
- end_at;
- timezone;
- location_type;
- location_name;
- location_address;
- location_latitude;
- location_longitude;
- status;
- cancellation_reason;
- created_by;
- updated_by;
- created_at;
- updated_at.

Специализированные данные похода/турслёта не должны превращать Event в универсальную таблицу со всеми возможными полями. Для них используются специализированные сущности.

### EventParticipation

Связь Person с Event.

Текущая принятая persistence-модель определена Issue #51 в соответствии с ADR-0023 §4: `id`, `event_id`, `person_id`, `registration_status`, `created_at`, `updated_at`. Не более одной записи на пару Event/Person (DB-level ограничение).

Registration/attendance policy, self-registration, переходы `registration_status` и связанные бизнес-правила остаются отдельным deferred business decision (ADR-0020 §4) и не входят в текущий персистентный контракт — см. также `data-model.md` §"EventParticipation" (текущая модель и отложенные концепции) и §11 "Attendance" ниже.

### EventStaffAssignment

Явная историческая связь User с Event, используемая для Event responsibility и scope `own_events`.

Канонические поля:

- id;
- event_id;
- user_id;
- role_in_event;
- is_primary;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Multiple active staff assignments допустимы; не более одной active assignment для Event может иметь `is_primary = true`. Исторические назначения сохраняются после закрытия `valid_to`.

Назначение допустимо только если Person назначаемого User имеет active ClubMembership в Club мероприятия. `Event.created_by` не является заменой EventStaffAssignment.

### EventGroupTarget

Явная историческая связь Event с Group, определяющая целевую аудиторию мероприятия.

Канонические поля:

- id;
- event_id;
- group_id;
- valid_from;
- valid_to;
- created_at;
- updated_at.

Event может быть адресован нескольким Groups; Group может быть целью нескольких Events. `EventGroupTarget` не создаёт EventParticipation и не означает регистрацию или посещаемость.

Связь допустима только при `Event.club_id == Group.club_id` и проверяется на authoritative application/service boundary согласно ADR-0022.

`own_groups` определяется через active EventGroupTarget и active GroupInstructorAssignment requester.

Детальный контракт этих отношений определён ADR-0023.

## 11. Attendance

Resolved by ADR-0032 (Issue #94 / TH-0087): a separate `Attendance` entity, not a representation layered over `EventParticipation`. Attendance identity is exactly `(occurrence_id, person_id)` for a concrete `EventOccurrence` (ADR-0032 §1) — `occurrence_id` is a real, `NOT NULL` FK to `event_occurrences.id`, not a nullable/polymorphic column on `Attendance` itself.

**Event → occurrence mapping (ADR-0033):** every ordinary, non-recurring `Event` now has exactly one linked `EventOccurrence` (`EventOccurrence.event_id`, nullable, set only for this case; `EventOccurrence.series_id` is correspondingly nullable too, set only for the recurring case) — created together with the Event and kept in sync with it (app.events.crud). This is a deliberate, narrow amendment to ADR-0028 §13's original "no bridge" stance for exactly this one case, not a general precedent; the recurring materialization/versioning/exception model is otherwise unchanged. See ADR-0033 for the full decision, including why Calendar/Conflict Detection's own Event/EventOccurrence candidate model needed a corresponding, narrow update.

Attendance requires participation for the same occurrence/Person — `EventParticipation` for the occurrence backing an ordinary Event, `EventOccurrenceParticipant` for a genuinely recurring occurrence (the same duality already established for `EventStaffAssignment`/`EventOccurrenceStaffAssignment` and `EventGroupTarget`/`EventOccurrenceGroupTarget`) — it never creates participation and is never deleted when participation ends or the occurrence completes/cancels.

Canonical statuses: exactly `present`/`absent`. Absence reasons are a closed MVP vocabulary (`sick`, `family_reason`, `injury`, `education`, `work`, `other`), not club-configurable and not a CRUD entity.

## 12. Trip

### Назначение

Расширение Event для туристского похода/выезда.

### Содержит

- event_id;
- tourism_type;
- difficulty_category;
- region;
- route_id;
- planned_distance;
- actual_distance;
- planned_duration;
- actual_duration;
- leader;
- notes;
- result/status.

## 13. TripParticipant

Специализированные данные участия в походе:

- person_id;
- role_in_trip;
- segment/part, если требуется;
- actual participation;
- completed_distance;
- result;
- notes.

Это позволяет не смешивать обычную регистрацию на событие с туристским стажем.

## 14. Route

Логическая сущность маршрута.

Может включать:

- название;
- вид туризма;
- регион;
- описание;
- плановую/фактическую дистанцию;
- профиль высот;
- точки;
- GPX-файлы;
- внешние ссылки.

## 15. RoutePoint

Географическая точка маршрута:

- latitude;
- longitude;
- elevation;
- sequence;
- name;
- point_type;
- description.

## 16. GPX Track / File

GPX хранится как файл/объект хранилища с метаданными.

Исходный файл не должен помещаться непосредственно в PostgreSQL blob без отдельного обоснования.

Производные данные могут индексироваться в БД для поиска и аналитики.

## 17. TouristProfile

Расширение участника туристскими характеристиками.

Сюда относятся агрегированные или справочные данные:

- виды туризма;
- суммарный подтверждённый опыт;
- километраж;
- количество походов;
- квалификации;
- навыки;
- разряды;
- достижения.

Каноническим источником фактов о походах остаются Trip/TripParticipant; агрегаты профиля могут пересчитываться.

## 18. Achievement

Достижение должно поддерживать:

- название;
- описание;
- категорию;
- иконку/медиа при необходимости;
- способ получения: manual/automatic;
- правило автоматической выдачи;
- активность;
- дату выдачи;
- кто выдал.

AchievementAward связывает достижение с человеком и хранит историю выдачи.

## 19. Skill

Навык участника.

Хранится отдельно от Achievement, поскольку навык отражает уровень/состояние подготовки, а достижение — событие или награду.

## 20. Qualification

Формализованная квалификация, разряд или иной подтверждённый уровень.

Поддерживает срок действия и документальное подтверждение, если применимо.

## 21. KnowledgeArticle

Материал базы знаний.

Имеет:

- category;
- title;
- summary;
- body/content;
- status;
- author;
- version;
- publication metadata;
- tags;
- attachments.

## 22. Document

### 22.1 File vs Document (ADR-0040, TH-0117)

TourCRM разделяет физический бинарный артефакт и бизнес/legal-запись о нём — это не одна сущность:

`File` — immutable физический артефакт: `id`, `storage_key`, `original_name`, `mime_type`, `size_bytes`, `checksum`, `storage_backend`, `created_by`, timestamps. После создания не мутируется; замена содержимого не перезаписывает существующую запись — создаётся новая.

`Document` — бизнес/legal-запись об участнике: `person_id`, `document_type`, `status`, `issued_at`, `expires_at`, `file_id`, version/history metadata, `uploaded_by`, timestamps.

Domain/business-код никогда не работает с filesystem/object storage напрямую — только через storage abstraction (`FileStorage`: put/get/exists/revoke), не привязанную к конкретному storage provider. `storage_key` никогда не является публичным URL; доступ к содержимому — только через авторизованный application endpoint.

### 22.2 Document ↔ Person (явная, не polymorphic связь)

Для документов участников (`medical_certificate` и аналогичные) используется явная связь:

```text
Document.person_id -> Person.id
```

Свободная polymorphic-модель `subject_type` + `subject_id` НЕ используется как основной механизм для документов участников — это прямо реализует уже принятое ADR-0016 требование (генерическая polymorphic-ссылка не может быть единственным механизмом владения для security-sensitive документа). Обобщённая модель владения документами для других доменов (Trip, Equipment, Finance, Club-level) остаётся отдельным, не решённым здесь вопросом.

### 22.3 Sensitivity — доступ к Person НЕ означает доступ к документам

Медицинские документы — sensitive data. `person.read` НЕ предоставляет доступ к содержимому Document — это отдельные permissions: `document.read`, `document.manage`, `document.export` (ADR-0040 §6).

Это согласуется с уже принятым принципом `roles-and-permissions.md` §8: доступ к Person не означает автоматический доступ ко всем чувствительным дочерним объектам Person. Тот же принцип, что уже применяется к Guardian access (§8 выше), instructor `own_groups` scope и multiple-roles authorization, — ни один из них не меняется этим разделом. Обычные People/Group views и обычный Group export не должны раскрывать содержимое медицинских документов; операционный workflow может показывать производный статус (`valid`/`missing`/`expired`, §22.5) без раскрытия самого файла.

### 22.4 Lifecycle и history

Документы историчны и версионируются: замена файла создаёт новую версию (новый `File` + новая версия `Document`), а не перезаписывает существующую. Для `medical_certificate` исторические сертификаты сохраняются — не удаляются при замене.

Канонические lifecycle-значения `status`: `active`, `expired`, `revoked`. `revoked` — явное действие над текущей версией на месте (не создаёт новую версию), по аналогии с `GuardianRelationship`'s `terminate` (ADR-0025 §3). Текущая валидность вычисляется из `status` + `expires_at` at read time — так же, как read-time expiry у `GuardianRelationship` (§8 выше) — без background job, переводящего `status` в `expired`.

**`missing` не является persisted значением `Document.status`.** Это результат проверки `EventDocumentRequirement` (§22.5) — отсутствие какого-либо `Document` нужного `document_type` у Person, а не lifecycle-состояние самого документа.

### 22.5 EventDocumentRequirement (ADR-0040 §5)

`Document` не связывается напрямую с `Event`. Вместо этого — отдельная сущность:

`EventDocumentRequirement`: `event_id`, `document_type`, `required`.

Проверка требования против документов конкретного участника — read-only вычисление (не persisted запись), дающее один из результатов: `valid`, `missing`, `expired`. Поведение для случая, когда текущая версия документа имеет `status = revoked`, этим ADR не определено — `revoked` остаётся отдельным lifecycle-состоянием (§22.4) и не считается автоматически `expired`; конкретный mapping — отдельное business-решение будущего implementation slice, не введённое здесь.

Перед экспортом пакета документов мероприятия (например, competition document package) пользователь должен получить явное предупреждение о участниках/требованиях, для которых результат — `missing` или `expired`.

Это не меняет существующую Event/EventParticipation authorization (ADR-0020, ADR-0023, ADR-0037, §10 выше) — `EventDocumentRequirement` является дополнительной, отдельно авторизуемой концепцией.

### 22.6 Статус реализации

Полный контракт (поля БД, permissions, audit vocabulary) определён ADR-0040 (TH-0117.0) — documentation-only decision baseline. `Person.photo_file_id` уже существует в коде как поле без FK-ограничения на `files`, поскольку таблица `files` ещё не реализована; добавление constraint — отдельная будущая implementation-задача, не входящая в ADR-0040.

## 23. Consent

Отдельная сущность для фиксирования согласий.

Минимально необходимы:

- субъект;
- тип согласия;
- кто дал/подписал;
- дата;
- версия текста/политики;
- статус;
- подтверждающий документ, если применимо.

## 24. Equipment

Единица имущества клуба.

Хранит:

- инвентарный идентификатор;
- название;
- категорию;
- состояние;
- место хранения;
- статус;
- дату покупки/поступления;
- стоимость, если нужно;
- серийный номер, если есть.

## 25. EquipmentIssue

История выдачи оборудования:

- equipment_id;
- issued_to;
- event_id;
- issued_at;
- expected_return_at;
- returned_at;
- condition_before;
- condition_after;
- notes.

## 26. Finance

Финансовый домен должен быть отделён от UI мероприятий.

Основные сущности первой модели:

- FinancialAccount;
- Payment;
- Expense;
- EventBudget;
- EventExpense.

Конкретная бухгалтерская модель будет уточнена отдельным документом.

## 27. Notification

Унифицированное системное уведомление.

Нужно различать:

- событие, породившее уведомление;
- получателя;
- канал;
- шаблон;
- состояние доставки;
- время отправки;
- retry/error metadata.

Поддерживаемые целевые каналы:

- email;
- Telegram;
- MAX.

## 28. AuditLog

Аудит должен хранить как минимум:

- actor;
- action;
- target type;
- target id;
- timestamp;
- request/correlation id;
- before/after или diff, если применимо.

Аудит не должен содержать секреты и чувствительные значения сверх необходимого для расследования.

## 29. System Settings

Настройки клуба и feature flags управляют доступностью функциональности.

Feature flag не заменяет authorization: отключённая функция должна быть недоступна даже пользователю с permission.

## 30. Общие доменные принципы

- Исторические факты не перезаписываются без необходимости.
- Производные данные должны быть пересчитываемыми из первичных фактов.
- Межклубные связи проверяются на authoritative boundary.
- Authorization не определяется только названием роли.
- Чувствительные домены имеют отдельные policy/permission boundaries.
- Новая самостоятельная доменная сущность требует документированного основания и, при архитектурном влиянии, ADR.

## 31. Traceability

Канонические Event relationship и GuardianRelationship решения: ADR-0023.

Cross-Club ownership: ADR-0022.

Event lifecycle: ADR-0018.

Event field model: ADR-0019.

Event authorization/participation contract: ADR-0020.

Group persistence: ADR-0021.

Document domain and file storage (File vs Document, Person association, storage abstraction, lifecycle/history, EventDocumentRequirement, permissions, audit): ADR-0040 (TH-0117.0, documentation-only baseline — no application code yet).
