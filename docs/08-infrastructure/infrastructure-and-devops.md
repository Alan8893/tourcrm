# TourCRM — Infrastructure & DevOps Specification

## 1. Назначение

Документ определяет целевую инфраструктуру TourCRM, правила окружений, контейнеризации, CI/CD, конфигурации, backup/restore, мониторинга, логирования, обновления и восстановления.

Документ предназначен для реализации инфраструктуры Claude и последующей эксплуатации администратором клуба.

---

## 2. Цели инфраструктуры

Инфраструктура должна:

- поддерживать работу через LAN и Internet;
- запускаться на Linux LTS;
- использовать Docker/Compose как базовый runtime;
- быть переносимой между собственным сервером и VPS;
- обеспечивать изоляцию приложения, БД, фоновых задач и storage;
- поддерживать безопасное обновление и rollback;
- обеспечивать резервное копирование и проверяемое восстановление;
- предоставлять health/readiness/liveness сигналы;
- собирать централизованные application/system logs;
- не требовать Kubernetes на первой стадии.

---

## 3. Рекомендуемая стартовая конфигурация

### 3.1 Production node

- CPU: 4 vCPU;
- RAM: 16 GB;
- storage: 256 GB SSD minimum;
- preferred storage: 512 GB SSD;
- Linux LTS;
- Docker Engine;
- Docker Compose v2;
- Gigabit Ethernet preferable.

128 GB HDD не считается рекомендуемым production storage для основной ноды из-за БД, документов, фотографий, GPX и служебных данных.

### 3.2 Storage strategy

Application data, PostgreSQL data и user files должны иметь понятное разделение.

Минимальная модель:

- PostgreSQL volume;
- application/upload volume или object storage adapter;
- backup destination, желательно вне primary host.

Backups не должны быть единственной копией на том же физическом диске.

---

## 4. Логическая схема

```text
Clients
  |
  v
Reverse Proxy / TLS termination
  |
  +-------------------+
  |                   |
  v                   v
Web frontend       Backend API
                      |
          +-----------+-----------+
          |                       |
          v                       v
     PostgreSQL                 Redis
          |                       |
          |                  Background jobs
          |
          v
Persistent data

Object/file storage <---- Backend

Monitoring / logs <----- all services

Backup system <---------- PostgreSQL + files + configuration metadata
```

Reverse proxy конкретной реализации определяется отдельным ADR, но архитектура должна сохранять возможность заменить его без изменения приложения.

---

## 5. Сервисы Docker Compose

Production baseline:

1. `frontend` — статические web assets/runtime frontend delivery.
2. `backend` — FastAPI application/API.
3. `db` — PostgreSQL.
4. `redis` — Redis, если включены фоновые задачи/кэш.
5. `worker` — background worker при наличии asynchronous jobs.
6. `scheduler` — отдельный single-replica scheduler для периодических system jobs (supercronic, запускает system CLI) — см. `docs/03-architecture/adr/ADR-0044-scheduler-for-periodic-system-jobs.md`. Не зависит от выбора queue/job framework (ODR-005).
7. `telegram-poller` — отдельный процесс long polling Telegram (`python -m app.cli.run_telegram_poller`, ADR-0047), когда используется Telegram; **ровно одна реплика** (см. §9.2).
8. `reverse-proxy` — внешний HTTP/HTTPS entrypoint.

Monitoring stack является отдельной capability и не обязан присутствовать на development-инсталляции.

---

## 6. Docker requirements

### 6.1 General

Все сервисы приложения должны иметь reproducible images.

Требования:

- pinned major/minor dependency strategy;
- deterministic lockfiles;
- non-root container user wherever practical;
- minimal runtime images;
- explicit healthcheck;
- no secrets baked into image;
- immutable image artifacts for release;
- `.dockerignore` обязателен.

### 6.2 Development

Development environment должен запускаться одной документированной командой через Docker Compose.

Допускается hot reload.

Development DB должна быть disposable и не содержать production data по умолчанию.

### 6.3 Production

Production Compose должен отличаться от development по конфигурации, security posture, volumes, secrets и exposure.

Не допускается публикация PostgreSQL/Redis наружу без отдельного обоснования.

---

## 7. Сетевой доступ

### 7.1 LAN

Внутри LAN приложение должно быть доступно по внутреннему DNS/hostname или IP через reverse proxy.

### 7.2 Internet

При публикации в Internet обязательны:

- HTTPS;
- valid certificate;
- secure headers;
- firewall policy;
- отсутствие прямого внешнего доступа к PostgreSQL/Redis;
- rate limiting на публичных authentication endpoints.

### 7.3 TLS

TLS termination выполняется на reverse proxy.

Внутренние container-to-container соединения могут использовать внутреннюю Docker network, но credentials и authorization не должны считаться ненужными только из-за внутреннего сегмента.

---

## 8. Environments

Не менее трёх логических окружений:

### Development

Для локальной разработки и интеграции Claude.

### Staging

Опционально на раннем этапе, обязательно до выхода в стабильную production эксплуатацию.

### Production

Реальные пользовательские данные.

Production credentials никогда не используются в development/test.

---

## 9. Configuration

Конфигурация должна быть environment-driven.

Минимальные категории:

- application environment;
- database URL/credentials;
- Redis URL;
- authentication/session parameters;
- storage configuration;
- email provider configuration;
- Telegram provider configuration;
- MAX provider configuration;
- logging level;
- feature settings;
- public base URLs.

Секреты передаются через environment/secrets mechanism и не попадают в Git.

### 9.1 Ключ шифрования настроек (ADR-0048, Issue #333)

SMTP и Telegram настраиваются администратором в **Settings → Notifications** и хранятся в PostgreSQL; переменные окружения не являются альтернативным источником этих настроек. Секреты (SMTP password, Telegram bot token) хранятся зашифрованными (AES-256-GCM). Единственный deployment-секрет — key ring:

| Переменная | Процессы | Назначение |
|---|---|---|
| `SETTINGS_ENCRYPTION_KEYS` | backend (API), outbox worker, telegram-poller | **секрет**: `<key_id>:<base64 32 байта>[,<key_id>:<…>]`. Первый ключ шифрует новые значения, остальные — только расшифровка. Одинаковое значение во всех трёх процессах |

Генерация ключа: `python -c "import base64,secrets;print(base64.b64encode(secrets.token_bytes(32)).decode())"`; `key_id` — короткая метка (`[A-Za-z0-9_-]{1,32}`, например `k2026a`).

Без ключа, с неверным ключом или при повреждённом ciphertext — fail closed: API отказывает в записи секретов (`settings_encryption_unavailable`), каналы показывают статус «секрет недоступен», worker не отправляет (retryable `channel_adapter_unavailable`), poller простаивает. Открытого хранения и fallback на env нет. Ключ не хранится в БД, не показывается в UI и не пишется в логи. Потеря ключа = потеря сохранённых секретов: их нужно ввести в UI заново.

**Ротация ключа.**

1. Сгенерируйте новый ключ и поставьте его **первым**: `SETTINGS_ENCRYPTION_KEYS=k_new:<новый>,k_old:<старый>`.
2. Перезапустите backend, worker и poller с новым значением.
3. Выполните `python -m app.cli.reencrypt_settings_secrets` — перешифровывает все секреты первым ключом в одной транзакции и печатает только количества; если хоть одно значение не расшифровывается, ничего не записывает и завершается с кодом 1.
4. Когда команда сообщает `remaining_old_key=0`, удалите старый ключ из переменной и снова перезапустите процессы.

### 9.2 Telegram (ADR-0047, ADR-0048)

Telegram использует **long polling** (`getUpdates`): публичный HTTPS endpoint и webhook не нужны, но процессам worker и poller нужен исходящий HTTPS-доступ к `api.telegram.org`. Bot username и bot token задаются в **Settings → Notifications → Telegram**; изменения применяются без перезапуска (worker — на каждую доставку, API — на каждую deep link, poller — между циклами `getUpdates`).

| Переменная | Процессы | Назначение |
|---|---|---|
| `TELEGRAM_POLL_TIMEOUT_SECONDS`, `TELEGRAM_POLL_BACKOFF_BASE_SECONDS`, `TELEGRAM_POLL_BACKOFF_MAX_SECONDS` | poller | ожидание long poll (20), bounded backoff (1…60 с) |
| `TELEGRAM_POLL_CONFIG_INTERVAL_SECONDS` | poller | как часто перечитывать настройки, пока Telegram не настроен или конфигурация неверна (10) |
| `TELEGRAM_API_BASE_URL` | worker, poller | только для тестов/локального fake-сервера; не задавать в production. Допускаются только `https://api.telegram.org` и `http://localhost`/`127.0.0.1`/`[::1]` с явным портом |

Токен никогда не хранится в открытом виде, не возвращается API, не попадает в checkpoint, outbox payload и логи.

**Развёртывание poller.** Отдельный сервис из того же образа, явно включаемый в конфигурации развёртывания (в dev Compose — профиль `telegram`: `docker compose --profile telegram up`). Требования:

- **одна реплика на установку** — никогда не масштабировать, не запускать второй экземпляр «для надёжности», не запускать параллельно на staging с тем же токеном;
- restart policy с задержкой (например `on-failure`/`unless-stopped`), без HTTP healthcheck (у poller нет порта);
- grace period остановки не меньше `TELEGRAM_POLL_TIMEOUT_SECONDS` + 10 с (в Compose — 40 с): по SIGTERM poller дожидается текущего long poll, завершает обрабатываемый update и выходит с кодом 0;
- миграции применены (`alembic upgrade head`) до старта.

Выключенный в глобальной политике Telegram не останавливает poller: привязка аккаунтов продолжает работать, блокируется только доставка уведомлений.

**Состояния poller** (строки `telegram poller state=…` в логе, без секретов): `polling` (lock взят, идёт `getUpdates`), `not_configured` (токен не задан), `secret_unavailable` (нет ключа/ключ не подходит/ciphertext повреждён), `token_rejected` (Telegram ответил 401/404), `username_mismatch` (username в настройках не совпадает с ботом токена). Во всех состояниях кроме `polling` lock не удерживается, настройки перечитываются каждые `TELEGRAM_POLL_CONFIG_INTERVAL_SECONDS`. При смене токена poller освобождает lock старого бота и берёт lock нового, продолжая с checkpoint нового `bot_id`.

**Коды выхода:** 0 — штатная остановка; 2 — неверная конфигурация самого poller (`TELEGRAM_POLL_*`); 3 — конфликт: уже активен другой poller для этого бота или webhook. Временная недоступность БД и сети обрабатывается внутри процесса с bounded backoff.

**Диагностика.** Логи `tourcrm.telegram_poller` содержат только `bot_id` (публичный), `update_id`, outcome, state и стабильный `error_code`; никогда — токен, текст сообщений, Telegram user/chat id. Сигналы для мониторинга: рестарты контейнера, долгие состояния кроме `polling`, строки `telegram getUpdates failed error_code=…`, отставание `telegram_update_checkpoints.updated_at` при ожидаемой активности. Готовность каналов видна администратору в Settings → Notifications.

**Восстановление после `409 Conflict` / exit 3.**

1. Строка `another telegram poller is already active for bot_id=…` — второй poller на той же БД: остановите лишний экземпляр (оставьте одну реплику).
2. Строка `telegram getUpdates conflict: another poller or a webhook is active` — токен используется где-то ещё (другая среда/хост с тем же токеном) или на боте установлен webhook. Найдите и остановите другой потребитель; если установлен webhook, удалите его вручную через Bot API `deleteWebhook` (TourCRM webhook не использует и не устанавливает). Если источник не найден или токен мог утечь — отзовите токен в @BotFather, выпустите новый и замените его в Settings → Notifications → Telegram.
3. Перезапустите poller. Прогресс хранится в `telegram_update_checkpoints`: необработанные updates будут получены повторно, уже обработанные (≤ checkpoint) игнорируются идемпотентно. Не редактируйте checkpoint вручную. Telegram хранит неподтверждённые updates ограниченное время (около 24 часов); ссылки привязки, потерянные за это время, пользователь запрашивает заново.

Смена бота (токен другого бота) переключает poller на checkpoint по новому `bot_id`; ранее привязанные пользователи должны нажать Start в новом боте заново, иначе доставка им завершится терминальной ошибкой (`telegram_chat_forbidden`).

`.env.example` хранит только имена переменных и безопасные примерные значения.

---

## 10. Database operations

Migration engine — Alembic.

Требования:

- schema changes versioned;
- migrations reviewed вместе с изменением моделей;
- migration order deterministic;
- destructive migrations не допускаются без отдельного плана совместимости/backup;
- production migration выполняется контролируемо;
- application version и DB migration compatibility должны быть документированы для release.

---

## 11. Backup

### 11.1 Что резервировать

Минимально:

- PostgreSQL logical backup;
- критические uploaded files;
- configuration metadata required for restore;
- deployment manifests/release references, если не восстанавливаются из Git.

### 11.2 Стратегия

Рекомендуемая baseline:

- daily full DB backup;
- более частые incremental/WAL-based возможности рассматривать по мере роста нагрузки;
- daily file backup;
- retention policy документирована;
- минимум одна копия вне основного сервера.

### 11.3 Restore test

Backup считается рабочим только после успешного теста восстановления.

Периодичность restore verification: не реже одного раза в квартал на production-классе окружения.

---

## 12. Recovery targets

Начальные целевые показатели:

- RPO: не более 24 часов для стандартного deployment baseline;
- RTO: не более 8 часов для полного восстановления из backup.

Эти значения должны быть пересмотрены перед production, если бизнес-потребность требует меньших значений.

---

## 13. Monitoring

Минимальные наблюдаемые сигналы:

- application availability;
- API error rate;
- latency;
- PostgreSQL availability;
- Redis availability when enabled;
- disk usage;
- memory usage;
- CPU usage;
- container restarts;
- backup success/failure;
- certificate expiry;
- queue/job failures.

Monitoring implementation may evolve from a lightweight baseline to Prometheus/Grafana or equivalent without changing application contracts.

---

## 14. Logging

### 14.1 Application logs

Structured logs should contain, where applicable:

- timestamp;
- level;
- service;
- environment;
- request/correlation ID;
- user ID where safe;
- operation/result;
- duration.

### 14.2 Security

Do not log:

- passwords;
- session secrets;
- access/refresh tokens;
- raw credentials;
- sensitive document contents;
- unnecessary medical or personal data.

### 14.3 Retention

Production log retention must be configurable and documented before launch.

---

## 15. CI

GitHub Actions is the authoritative CI system.

Every relevant pull request should run at minimum:

1. lint;
2. typecheck/static analysis;
3. unit tests;
4. integration tests where applicable;
5. build;
6. migration validation where applicable.

Security/dependency scanning should be enabled as a separate CI capability.

No merge into protected release branch should be considered valid when required CI checks fail.

---

## 16. CD / Release

Release process should support:

```text
PR
  -> CI
  -> review
  -> merge
  -> release artifact/image
  -> deployment
  -> migration
  -> smoke test
  -> health verification
```

Production deployment must be traceable to a Git commit/tag/image digest.

Rollback plan must be documented for every release capable of changing application code.

Database rollback is not assumed to be automatic. Destructive schema changes require expand/contract or another explicitly documented compatibility strategy.

---

## 17. Health endpoints

Backend должен предоставлять минимум:

- liveness check;
- readiness check;
- version/build information endpoint for authenticated/admin diagnostics where appropriate.

Readiness must verify dependencies required for serving traffic, while liveness must remain lightweight.

---

## 18. Security baseline

Production host:

- regular OS security updates;
- host firewall enabled;
- SSH key authentication preferred;
- password-based root login disabled;
- least privilege;
- Docker socket exposure minimized;
- only required ports exposed.

Application:

- HTTPS externally;
- secure cookies where cookies are used;
- CSRF protection where relevant to authentication model;
- security headers;
- rate limiting for authentication and sensitive endpoints;
- secret rotation procedure.

---

## 19. File storage

Files are not assumed to belong inside PostgreSQL.

The application should use a storage abstraction so the backend is independent from a specific storage implementation.

Required file categories include:

- profile photos;
- participant documents;
- event documents;
- GPX;
- knowledge-base attachments;
- equipment media;
- other approved club media.

Storage implementation may begin as filesystem volume and later move to S3-compatible/object storage without changing domain contracts.

---

## 20. Updates

Application updates:

- release via CI/CD;
- backup before risky migration;
- health-check after deployment;
- rollback plan.

Host updates:

- regular package/security updates;
- controlled Docker engine upgrades;
- documented maintenance windows if downtime is expected.

---

## 21. Disaster recovery

A restore procedure must document:

1. provision clean Linux host;
2. install Docker/Compose;
3. restore configuration/secrets through secure process;
4. restore PostgreSQL;
5. restore files;
6. deploy known-good application image;
7. run migrations to compatible target version;
8. run smoke checks;
9. verify authentication and critical workflows.

Recovery documentation must be executable by a technically competent operator who did not originally deploy the system.

---

## 22. Scaling path

Initial architecture is single-host.

Future scaling path:

```text
Single host
   -> separated database/storage
   -> multiple backend replicas
   -> external managed/object storage
   -> managed PostgreSQL
   -> dedicated worker nodes
```

No current requirement justifies Kubernetes.

---

## 23. Operational acceptance criteria

Infrastructure implementation is accepted when:

- application starts from documented deployment procedure;
- frontend/backend/DB connectivity verified;
- healthchecks work;
- external DB/Redis ports are not exposed unintentionally;
- CI passes;
- production configuration contains no committed secrets;
- backup job succeeds;
- restore procedure has been validated;
- deployment is traceable to a commit/image;
- rollback procedure is documented and tested for the supported release model;
- logs contain correlation IDs without leaking secrets;
- resource monitoring is available for production.

## 24. Decisions requiring ADR

Before production, create ADRs for:

- reverse proxy implementation;
- authentication/session transport;
- storage backend;
- backup tooling;
- monitoring stack;
- worker/scheduler technology (periodic system jobs — `ADR-0044`; asynchronous worker — `ADR-0046`);
- deployment topology;
- secret management mechanism.
