# TourCRM — Security & Privacy Specification

## 1. Назначение

Документ определяет обязательные требования к защите TourCRM, включая персональные данные участников клуба, сведения о несовершеннолетних, данные родителей/законных представителей, документы, финансовую информацию, маршруты и технические журналы.

Документ является нормативным для архитектуры, API, БД, frontend, инфраструктуры и будущих задач Claude.

## 2. Принципы безопасности

1. Deny by default: отсутствие явного разрешения означает отсутствие доступа.
2. Backend является источником истины для authorization; скрытие элемента в UI не считается защитой.
3. Permissions и scopes проверяются для каждой защищённой операции.
4. Доступ к данным ребёнка предоставляется только при действующей связи GuardianRelationship и наличии соответствующего permission.
5. Администратор получает расширенный доступ, но не должен обходить audit и другие системные ограничения.
6. Секреты и credentials никогда не попадают в application logs, AuditLog, error responses или telemetry.
7. Исторически значимые записи нельзя удалять без документированного бизнес-основания.
8. Все чувствительные операции должны быть аудируемыми.
9. Сервисы должны работать с минимально необходимыми привилегиями.
10. Безопасность должна быть одинаковой для LAN и Internet deployment.

## 3. Категории защищаемых данных

### 3.1. Идентификационные данные

- ФИО;
- дата рождения;
- контакты;
- адрес;
- фотографии;
- сведения о членстве в клубе.

### 3.2. Данные несовершеннолетних

Должны рассматриваться как чувствительные данные повышенного риска.

### 3.3. Данные представителей

- родитель/законный представитель;
- контактные данные;
- сведения о связи с ребёнком;
- данные согласий и подтверждений.

### 3.4. Медицинские сведения

До отдельного нормативного решения нельзя реализовывать расширенное хранение медицинских данных только по инициативе разработчика. Для любого медицинского поля должны быть определены необходимость, доступ, срок хранения, аудит и политика удаления.

### 3.5. Документы

Документы могут содержать персональные и юридически значимые сведения и должны храниться в защищённом private storage.

### 3.6. Финансы

Платежи, расходы и задолженности доступны только субъектам, которым предоставлены соответствующие permissions.

### 3.7. Геоданные

GPX, маршруты и координаты относятся к operational data. Для приватных или будущих маршрутов должна поддерживаться авторизация на уровне объекта.

## 4. Identity и authentication

### 4.1. Account lifecycle

```text
pending
active
locked
suspended
disabled
archived
```

Точные переходы должны соответствовать auth API specification.

### 4.2. Password policy

- пароль хранится только в виде безопасного password hash;
- plaintext password не хранится и не логируется;
- восстановление пароля выполняется по одноразовому токену с ограниченным сроком действия;
- reset token становится недействительным после использования;
- политика сложности не должна требовать необоснованно слабых/небезопасных компромиссов;
- рекомендуется защита от credential stuffing через rate limiting и account/session controls.

### 4.3. Session security

- сессия должна иметь ограниченный срок жизни;
- refresh/session rotation должна поддерживаться согласно выбранной auth-модели;
- logout должен инвалидировать соответствующий session/refresh credential;
- изменение пароля должно инвалидировать активные сессии согласно security policy;
- доступ к cookie/session credential должен быть защищён средствами браузера, подходящими к выбранной схеме.

## 5. Registration и invitation security

### Самостоятельная регистрация

1. пользователь создаёт заявку;
2. система валидирует identifier;
3. при необходимости подтверждает контакт;
4. заявка получает статус pending;
5. администратор подтверждает или отклоняет заявку;
6. создаётся/активируется членство и необходимые роли.

### Invitation

- приглашение одноразовое или ограниченное по числу использований;
- имеет срок действия;
- должно быть отзывным;
- не должно содержать в URL открытые персональные данные;
- успешное использование обязательно аудитируется;
- повторное использование недействительного приглашения должно завершаться безопасной ошибкой.

## 6. Authorization

Используется комбинация:

```text
User
  -> RoleAssignment
      -> Permission
          -> Scope
              -> Resource/Object
```

### Минимальные scopes

- `all`;
- `own_groups`;
- `self`;
- `children`;
- `assigned_events`;
- `own_records`.

Backend не должен доверять scope, переданному клиентом. Scope вычисляется сервером на основании актуального состояния БД и policy.

## 7. Guardian access

1. Родитель видит только тех детей, с которыми существует действующая GuardianRelationship.
2. Разрыв связи должен немедленно прекращать соответствующий доступ.
3. Родитель не получает автоматически права инструктора или администратора.
4. Родительский доступ должен учитывать статус ребёнка и тип данных.
5. Экспорт данных ребёнка должен быть отдельным permission и аудироваться.

## 8. Особенности несовершеннолетних

TourCRM должен архитектурно поддерживать дополнительные controls для несовершеннолетних:

- обязательная/условная связь с guardian;
- хранение согласий;
- ограничение просмотра отдельных категорий данных;
- подтверждение действий законным представителем, когда это предусмотрено бизнес-правилами;
- отдельные notification policies;
- отсутствие публичной выдачи персональных данных.

Конкретные юридические основания и перечень обязательных согласий фиксируются отдельной политикой до production использования.

## 9. API security

### Обязательное

- authentication для защищённых endpoint'ов;
- authorization на сервере;
- input validation;
- output filtering;
- rate limiting для чувствительных операций;
- защита от массового перечисления ресурсов;
- единая безопасная модель ошибок;
- correlation/request ID;
- CSRF protection, если используется cookie-based authentication;
- CORS allowlist;
- ограничения размера request/upload;
- MIME/type validation для файлов;
- запрет path traversal;
- безопасная обработка URL/redirect parameters.

### IDOR / object-level authorization

Каждый endpoint, принимающий object ID, должен проверять право доступа именно к этому объекту. Наличие валидной авторизации пользователя само по себе недостаточно.

## 10. File security

Документы, фотографии и GPX должны храниться вне публичного web root.

Доступ к private file выдаётся только после authorization check.

Необходимо:

- ограничивать размер файла;
- проверять расширение и content type;
- санитизировать имя файла;
- не использовать имя файла как ключ доступа;
- по возможности выполнять malware/antivirus scanning;
- хранить метаданные отдельно от бинарного объекта;
- поддерживать удаление/архивирование согласно retention policy.

### 10.1 Document domain (ADR-0040, TH-0117 — planned)

The following is the security baseline for the participant-document domain fixed by ADR-0040 (TH-0117.0). It is documentation-only: no `File`/`Document` implementation exists in code yet — see `docs/03-architecture/adr/ADR-0040-document-domain-and-file-storage.md` for the full contract.

- Private storage only, outside any public web root — the general rule above, restated as a hard requirement for `File`.
- `storage_key` is never a public, guessable, or directly browsable URL. It is never returned to a client as an access mechanism; all content access goes through an authorized application endpoint.
- Object-level authorization applies per Document, not merely per Person: `person.read` does **not** grant Document-content access. Three dedicated permissions govern the domain — `document.read`, `document.manage`, `document.export` — required in addition to whatever permission governs the surrounding Person/Group/Event resource.
- Upload size limits, MIME/content-type validation, filename sanitization and path-traversal protection (§9/§10 above) apply to Document uploads exactly as to any other file upload; no separate, weaker rule is introduced for documents.
- Medical certificates (`medical_certificate`) are sensitive documents. An ordinary People/Group view, and an ordinary Group export, must never expose medical-document content or a direct download link to it. An operational workflow may show only the derived validity (`valid`/`missing`/`expired`) without exposing the file.
- A competition/event document package export (ADR-0040 §5) is an explicit, separate operation from an ordinary Group/People export — it requires `document.export`, and the exporting user must receive an explicit warning naming any participant/requirement pair that resolves to `missing` or `expired` before the export completes.
- Sensitive document **download** and **export** are both audit-required (`document.downloaded`, `document.exported` — ADR-0024 §4 as amended by ADR-0040 §7), even when only metadata/derived validity would otherwise be visible without triggering audit.
- No document content — sensitive or otherwise — is ever written to application logs, matching §13's existing rule against logging full document content.
- Malware/antivirus scanning applies to Document uploads where available, per the general file-upload rule in §10.
- File storage backup requirements (§15) cover Document's binary content the same as any other stored file.

## 11. Database security

- приложение использует отдельного DB user;
- application role не получает ненужные административные полномочия;
- secrets не хранятся в Git;
- production DB не должна быть доступна из Internet без необходимости;
- connection credentials передаются через secret management;
- backup должен быть зашифрован/защищён согласно инфраструктурной политике;
- миграции выполняются контролируемо;
- destructive migrations требуют явной проверки.

## 12. Audit Log

Аудит обязателен минимум для:

- login/logout security events;
- failed authentication, когда событие может указывать на атаку;
- password reset;
- account lock/unlock;
- role/permission changes;
- member/guardian relationship changes;
- изменение чувствительных персональных данных;
- доступ/изменение документов;
- финансовые операции;
- изменение походов и официальных результатов;
- выдача/отзыв achievements и qualifications;
- экспорт данных;
- административные настройки;
- действия интеграций.

AuditLog должен быть append-oriented и защищён от обычного редактирования пользователями.

## 13. Logging

В application logs разрешается хранить:

- техническое событие;
- timestamp;
- severity;
- request/correlation ID;
- технический контекст.

Нельзя логировать:

- пароли;
- session/refresh tokens;
- invitation secrets;
- access tokens;
- полные содержимое документов;
- медицинские сведения без отдельного разрешения;
- секреты внешних интеграций.

При необходимости идентификаторы персон следует маскировать/псевдонимизировать.

## 14. Secrets management

Локальная разработка и production используют разные secrets.

Secrets должны поступать через environment/secret manager и не коммититься в репозиторий.

Необходимо разделить:

```text
application config
non-secret settings
secrets
external integration credentials
```

Rotation procedure должна быть документирована для production.

## 15. Backup и restore

Backup является частью security boundary.

Минимально:

- регулярные PostgreSQL backups;
- отдельное хранение от production storage;
- контроль успешности backup;
- retention policy;
- периодическая проверка восстановления;
- резервирование файлового storage;
- документированный recovery procedure.

Backup, который ни разу не проверяли восстановлением, не считается надёжным.

## 16. Retention и deletion

Для каждой категории данных должна быть определена политика:

- operational retention;
- archival period;
- deletion/anonymization;
- legal hold, если применимо.

Удаление пользователя не должно автоматически разрушать исторические данные клуба, если они необходимы для учёта и аудита. Должен использоваться документированный процесс деактивации/архивирования/анонимизации.

## 17. Privacy by design

При проектировании каждого нового поля необходимо ответить:

1. зачем оно необходимо;
2. кто его видит;
3. сколько времени хранится;
4. можно ли обойтись без него;
5. требуется ли аудит изменения;
6. требуется ли consent/legal basis.

Фраза «может пригодиться потом» не является достаточным основанием для хранения чувствительных данных.

## 18. Threat model

Минимально рассматриваются угрозы:

- credential theft;
- brute force / credential stuffing;
- session theft;
- IDOR/BOLA;
- privilege escalation;
- malicious invitation reuse;
- unauthorized guardian access;
- malicious file upload;
- SQL injection;
- XSS;
- CSRF при соответствующей auth-схеме;
- SSRF для функций, работающих с внешними URL;
- path traversal;
- data exfiltration через export/search/filter;
- accidental exposure через logs;
- compromised integration credentials;
- ransomware/data destruction;
- backup compromise.

Каждый критичный риск должен иметь хотя бы одну preventative control и одну detection/recovery control, когда это применимо.

## 19. Security headers и browser policy

Frontend deployment должен использовать подходящие security headers, включая CSP и защиту от framing/type sniffing согласно реальной архитектуре reverse proxy.

Точные значения заголовков фиксируются инфраструктурным ADR после выбора reverse proxy и auth strategy.

## 20. Network security

### LAN

Работа внутри LAN не считается доверенной зоной автоматически.

### Internet

При публикации наружу обязательно:

- TLS;
- reverse proxy;
- firewall;
- ограничение административных интерфейсов;
- защищённые secrets;
- monitoring;
- backup.

## 21. Integrations

Telegram, MAX, email и будущий TourSlet integration должны использовать отдельные credential sets.

Интеграция не получает доступ ко всей БД. Доступ предоставляется через application service/API с минимальным scope.

Webhook endpoints должны:

- проверять подлинность источника;
- иметь replay protection, где применимо;
- логировать correlation metadata без секретов;
- быть идемпотентными, где возможно.

## 22. Security monitoring

Production должен собирать минимум:

- authentication failures;
- unusual authorization failures;
- repeated invitation failures;
- spikes in export/download operations;
- repeated failed file uploads;
- service/database health;
- backup failures.

Реализация алертинга может быть поэтапной.

## 23. Incident response

Минимальный процесс:

```text
Detect
  -> Triage
  -> Contain
  -> Preserve evidence
  -> Recover
  -> Review
  -> Corrective action
```

Должны быть документированы контакты/ответственные лица и порядок принятия решений до production launch.

## 24. Secure development requirements

Claude при реализации security-sensitive задач обязан:

- писать негативные тесты на unauthorized access;
- проверять object-level permissions;
- тестировать boundary conditions;
- не коммитить реальные secrets;
- обновлять security documentation при изменении security behavior;
- не отключать защитные механизмы только ради прохождения теста.

## 25. Security acceptance criteria

Функциональность считается готовой только при выполнении всех применимых условий:

- [ ] все защищённые endpoint'ы требуют authentication;
- [ ] permissions проверяются backend;
- [ ] object-level access контролируется;
- [ ] guardian access ограничен реальными связями;
- [ ] security-sensitive operations покрыты негативными тестами;
- [ ] secrets отсутствуют в репозитории;
- [ ] чувствительные данные отсутствуют в обычных логах;
- [ ] audit events создаются там, где требуется;
- [ ] file uploads защищены;
- [ ] rate limiting применён к чувствительным операциям;
- [ ] backup/restore требования для затронутого модуля задокументированы;
- [ ] документация обновлена при изменении security model.

## 26. Open policy decisions

До production необходимо отдельно утвердить:

- конкретную правовую политику обработки персональных данных;
- перечень обязательных consent types;
- retention periods;
- правила работы с медицинскими сведениями;
- необходимость/режим MFA;
- конкретную auth/session technology;
- инфраструктурный secret manager;
- backup encryption/storage policy;
- точные security headers/CSP.
