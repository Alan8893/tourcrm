# TourCRM — Participant Import API

## 1. Назначение

Этот документ является каноническим API-контрактом Participant Import (TH-0118).

Participant Import — административный backend workflow для массового добавления участников из CSV/XLSX. Frontend не является источником бизнес-правил: backend authoritative.

Система TourCRM остаётся single-club на уровне продукта. Сущность `Club` сохраняется как техническая backend-сущность до отдельного рефакторинга; клиент не выбирает Club при импорте.

## 2. Канонический workflow

```text
upload
  → parse
  → validate
  → preview
  → approve
  → apply
  → report
```

Upload, parsing, validation и preview не изменяют domain data.

Изменение domain data происходит только после явного `approve` и `apply`.

Import выполняется синхронно в текущем MVP: отдельная очередь/worker/infrastructure для Import не вводится.

## 3. Authorization

Import — Administrator-only capability.

Backend permission: `membership.import`.

`membership.import` предоставлен системной роли `admin`; новый frontend permission не придумывает.

Доступ к ImportJob использует существующую authorization model и object policy:

- assignment с `membership.import` и `all` scope должен покрывать Club задания;
- читать/просматривать job может её creator при действующем grant либо canonical system Administrator того же Club;
- unauthorized или скрытый job возвращает тот же `404 not_found`, что и несуществующий job (existence hiding).

Frontend не дублирует эту авторизацию.

## 4. Source formats

Поддерживаются:

- CSV;
- XLSX.

Формат определяется расширением файла (`.csv` / `.xlsx`, case-insensitive) на этапе upload. Содержимое файла на upload не разбирается.

### CSV

- UTF-8;
- ведущий BOM разрешён;
- comma delimiter;
- malformed quoting, NUL bytes, undecodable content и несовпадение числа значений с header являются parse errors.

### XLSX

- `openpyxl`;
- читается только первый worksheet;
- `read_only` mode.

## 5. Canonical import columns

Используются только canonical column names. Aliases не поддерживаются.

Header является ошибочным, если колонка неизвестна, повторяется или пуста. Обязательные поля:

- `first_name`;
- `last_name`.

Неизвестные/отсутствующие canonical columns не должны молча переинтерпретироваться frontend или backend.

Полный набор поддерживаемых полей и их validation rules является частью существующего People API/import implementation contract и должен использоваться frontend без собственной копии mapping rules.

## 6. Normalization and validation

Нормализация:

- trim для строк;
- blank → `null`;
- email проходит существующий `normalize_login_identifier()`;
- phone только trim;
- `birth_date` в CSV принимается как `YYYY-MM-DD`;
- в XLSX `birth_date` принимается только из native date/datetime cell.

Canonical validation errors включают:

- `required_field_missing`;
- `invalid_email`;
- `invalid_birth_date`;
- `value_too_long`;
- `invalid_value_type`.

Одна строка может иметь несколько ошибок.

Отсутствующий email сам по себе не является ошибкой.

## 7. Duplicate detection

Проверки выполняются в каноническом порядке:

1. exact `external_id` — только между строками одного импортируемого файла;
2. email — против Person email и User login identifier;
3. phone;
4. `first_name + last_name + birth_date`.

Fuzzy matching в текущем MVP **не выполняется** и отложен.

Exact duplicate является `warning`, а не validation error.

Если duplicate найден:

- строка не создаёт Person/User/Membership;
- автоматический merge запрещён;
- overwrite запрещён;
- существующий Person не переиспользуется автоматически;
- строка получает `duplicate_exact` и после apply считается skipped.

Для duplicate внутри одного файла все конфликтующие строки помечаются.

Для duplicate с существующим Person в preview может возвращаться только его `matched_person_id`; персональные данные существующего Person в preview не раскрываются.

Повторная duplicate-проверка выполняется непосредственно перед созданием записи на apply.

## 8. Preview / dry-run

Endpoint:

`POST /api/v1/memberships/imports/{import_id}/preview`

Lifecycle:

```text
uploaded → parsing → validating → preview_ready
```

Ошибки чтения/парсинга переводят job в `failed`. Ошибка завершения validation также переводит job в `failed`.

Preview:

- синхронный;
- не создаёт Person;
- не создаёт User;
- не создаёт ClubMembership;
- не создаёт RoleAssignment;
- не создаёт GroupMembership;
- не создаёт GuardianRelationship;
- не создаёт EventParticipation;
- не создаёт audit records.

Preview изменяет только состояние ImportJob, counters и собственные `ImportJobError` records.

`duplicate_exact` является warning. Строка только с warning считается valid. Строка хотя бы с одним error считается invalid.

## 9. ImportJob lifecycle

Initial status:

`uploaded`

Canonical statuses включают стадии parsing/validation/preview/approval/apply и terminal states:

- `completed`;
- `partially_completed`;
- `failed`;
- `cancelled`.

Переходы выполняются только через canonical lifecycle service с row lock; endpoint не меняет status напрямую.

Terminal status не может быть переведён в другое состояние.

## 10. Approval

Endpoint:

`POST /api/v1/memberships/imports/{import_id}/approve`

Approve разрешён только из `preview_ready`.

Approve:

- переводит job в `approved`;
- не изменяет domain entities;
- не создаёт audit record.

Approval является явным действием администратора.

## 11. Apply

Endpoint:

`POST /api/v1/memberships/imports/{import_id}/apply`

Apply разрешён только для approved job.

Apply синхронный и переводит job:

```text
approved → applying → completed
                       ↘ partially_completed
                       ↘ failed
```

### 11.1. Domain entities в текущем slice

TH-0118.3 применяет только:

- `Person`;
- `User`;
- `ClubMembership`.

`RoleAssignment`, `GroupMembership`, `GroupInstructorAssignment` и `GuardianRelationship` **не создаются импортом в текущем MVP slice**. Их импортирование является отдельным будущим расширением и не должно быть придумано implementation agent.

Каждая успешно импортированная строка создаёт Person + User + ClubMembership атомарно.

### 11.2. User provisioning

Если email присутствует:

- `User.status = active`;
- `login_identifier = normalized(email)`;
- password не создаётся;
- first-access credential не выдаётся.

Если email отсутствует:

- `User.status = pending`;
- `login_identifier = NULL`;
- password отсутствует;
- first-access credential отсутствует.

Fake login/email значения запрещены.

Первичный доступ после импорта оформляется существующим password-reset flow, а не Import API.

### 11.3. Existing Person / duplicate apply

Automatic update, merge или reuse существующего Person запрещены.

Перед созданием каждой строки apply повторяет exact duplicate detection. При обнаружении duplicate:

- ничего не создаётся и не изменяется;
- записывается `duplicate_exact` warning;
- `skipped_records` увеличивается.

## 12. Transaction and concurrency semantics

Каждая строка применяется в собственной транзакции.

Person + User + ClubMembership + соответствующие audit records commit together or roll back together.

Ошибка одной строки не откатывает уже успешно применённые строки и не останавливает обработку остальных строк.

Import subsystem использует transaction-scoped PostgreSQL advisory lock и повторную duplicate-проверку непосредственно перед созданием. Это защищает от параллельного применения двух ImportJob, которые создавали бы одного и того же участника по canonical duplicate dimensions.

Гарантия относится только к Import subsystem; ручное создание Person вне Import не включается в этот advisory-lock protocol.

## 13. Result / report

Отдельная Report entity не создаётся.

После apply результат доступен через существующие:

- `GET /api/v1/memberships/imports/{import_id}`;
- `GET /api/v1/memberships/imports/{import_id}/errors`.

Используются status, counters и row-level errors/warnings.

Для apply:

- `created_records` — фактически созданные записи;
- `updated_records = 0` в текущем slice;
- `skipped_records` включает invalid и duplicate rows.

Неудачная строка получает `import_apply_failed` с `row_number`, если ошибка относится к конкретной строке.

Нечитаемый source file на apply получает `import_file_unreadable`.

## 14. Audit

Preview и approve audit record не создают.

Apply создаёт:

- `person.created` для реально созданного Person;
- `membership.created` для реально созданного ClubMembership;
- `user.created` для реально созданного User;
- ровно один `membership.import.applied` на execution apply.

Audit details не содержат email, phone, password, token или credential.

## 15. Error records

`ImportJobError` содержит как минимум:

- `row_number` — optional;
- `field` — optional;
- `code`;
- `message`;
- `severity`: `error | warning`;
- `matched_person_id` — optional для duplicate match с существующим Person.

`GET /api/v1/memberships/imports/{import_id}/errors` поддерживает фильтр по severity и стандартную pagination.

Сообщения validation не должны повторять исходные cell values.

## 16. Current API surface

Текущие endpoints TH-0118 Import:

```text
POST /api/v1/memberships/imports
GET  /api/v1/memberships/imports/{import_id}
GET  /api/v1/memberships/imports/{import_id}/errors
POST /api/v1/memberships/imports/{import_id}/preview
POST /api/v1/memberships/imports/{import_id}/approve
POST /api/v1/memberships/imports/{import_id}/apply
```

PATCH/PUT/DELETE для ImportJob не предусмотрены.

## 17. Security and data isolation

- Backend является authoritative.
- Import доступен только Administrator.
- Club boundary определяется существующим single-club backend mechanism.
- Hidden jobs используют existence hiding (`404`).
- Internal UUIDs, storage keys и filesystem paths не являются пользовательскими import data.
- Upload не применяет изменения автоматически.
- Ordinary participant export/import workflows не должны раскрывать medical/document content.

## 18. Explicitly deferred / technical debt

Следующие возможности не являются частью текущего Import MVP:

- fuzzy duplicate matching;
- automatic merge/update/reuse существующего Person;
- импорт RoleAssignment;
- импорт GroupMembership / GroupInstructorAssignment;
- импорт GuardianRelationship;
- reusable import/export templates;
- background worker/async import;
- multi-club UI/business model.

## 19. Remaining PO decisions before extending Import

Эти вопросы не блокируют уже реализованный TH-0118.1–TH-0118.3, но должны быть решены до расширения контракта или нового implementation slice:

### 19.1. Source-file retention

Не зафиксировано, сколько времени после завершения/ошибки ImportJob хранится исходный CSV/XLSX и когда он удаляется.

**PO decision required:** retention policy и момент cleanup.

### 19.2. Upload size / row limits

Канонического максимального размера файла или количества строк пока нет.

**PO decision required:** нужен ли лимит в MVP и какие значения использовать.

### 19.3. Future contextual relations

Если в будущем Import должен создавать RoleAssignment, GroupMembership, GroupInstructorAssignment или GuardianRelationship, это требует отдельного PO contract: правила выбора существующих сущностей, конфликтов, duplicates и rollback нельзя выводить из текущего MVP.

### 19.4. Historical import

Импорт туристского опыта, существовавшего до TourCRM, остаётся отдельным technical debt (#221), а не частью Participant Import.

## 20. Source of truth / implementation history

Бизнес- и API-контракт сформирован решениями TH-0118 и реализован следующими backend slices:

- TH-0118.1 — Import Job foundation and lifecycle;
- TH-0118.2 — Parsing, validation, duplicate detection and preview;
- TH-0118.3 — Approval, apply, report and audit.

Parent Issue #174 содержит историческую формулировку, в которой перечислены более широкие потенциальные domain relations. Для **текущего MVP authoritative является реализованный и согласованный TH-0118.3 contract из этого документа**: Person + User + ClubMembership. Более широкие relations не следует считать реализованными требованиями.

Frontend Import/Export UI выполняется отдельным slice после завершения backend contract и использует только эти API.
