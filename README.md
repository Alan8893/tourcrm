# TourCRM

CRM и информационная система школьного туристского клуба.

## Назначение

TourCRM объединяет управление участниками клуба, родителями, инструкторами, группами, занятиями, походами, достижениями, документами, финансами, снаряжением, базой знаний и внешними мероприятиями.

## Основные пользователи

- администратор клуба;
- инструктор;
- участник клуба;
- родитель / законный представитель.

Один пользователь может иметь несколько ролей.

## Архитектура

Начальная архитектура — модульный монолит:

- frontend: React + TypeScript + Vite;
- backend: Python + FastAPI + Pydantic + SQLAlchemy;
- database: PostgreSQL;
- optional infrastructure: Redis;
- deployment: Docker Compose на Linux;
- CI: GitHub Actions.

Подробнее: `docs/03-architecture/`.

## Среда

TourCRM должна работать:

- в локальной сети клуба;
- через Internet;
- на desktop, tablet и mobile web.

## Правило разработки

Работа выполняется по GitHub Issues:

`Issue → implementation → automated tests → CI → review → close Issue → next Issue`

Issue не закрывается при невыполненном acceptance criteria или неуспешном автоматическом тестировании.

## Структура репозитория (application skeleton)

Monorepo с явными границами frontend/backend (Issue #4):

```text
apps/
├── web/   # frontend skeleton — React + TypeScript + Vite, см. apps/web/README.md
└── api/   # backend skeleton — FastAPI + Python, см. apps/api/README.md
tests/     # repository-level smoke checks (структура репозитория)
```

Запуск backend и frontend в development-режиме, а также минимальные smoke
checks описаны в `apps/api/README.md` и `apps/web/README.md`.

## Development environment (Docker Compose, Issue #8)

**Это development-only окружение** — без reverse proxy, TLS, production
secrets/backup; образы — development (dev-зависимости, `--reload`, bind
mount исходников). Канонической production-топологии пока нет — это
отдельная, более поздняя задача (см.
`docs/08-infrastructure/infrastructure-and-devops.md`); этот Compose не
является production-конфигурацией.

### Prerequisites

- Docker Engine;
- Docker Compose v2 (команда `docker compose`, встроена в современный Docker).

### Первый запуск

```bash
git clone <repo-url> && cd tourcrm
cp .env.example .env        # локальная конфигурация; .env никогда не коммитится
# в .env задайте два РАЗНЫХ ключа (см. «Переменные окружения» ниже)
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --build
```

Одна эта команда поднимает **все** сервисы, профилей нет — ничего нужного
не пропускается:

| Сервис | Процесс | Назначение |
|---|---|---|
| `db` | PostgreSQL 16 | данные; healthcheck `pg_isready` |
| `backend` | API (`alembic upgrade head`, затем uvicorn) | **единственный**, кто применяет миграции (идемпотентно); healthy после старта API |
| `frontend` | Vite dev server | веб-приложение, проксирует `/api` на `backend` |
| `scheduler` | `supercronic -json /app/scheduler/crontab` | периодические system jobs (Event lifecycle reconciliation каждую минуту — Issue #289, ADR-0044) |
| `telegram-poller` | `python -m app.cli.run_telegram_poller` | приём обновлений Telegram (ADR-0047); без настроенного бота простаивает |
| `outbox-worker` | `python -m app.cli.run_outbox_worker` | доставка уведомлений из outbox: Email/Telegram, в том числе коды восстановления пароля (Issue #342) |

Порядок старта: `db` (healthy) → `backend` (миграции применены, healthy) →
`scheduler`, `telegram-poller`, `outbox-worker`. От фоновых сервисов не
зависит ни один другой, поэтому приложение можно поднять и без них:
`docker compose up -d db backend frontend` (например, при отладке UI).
Фоновые сервисы не имеют HTTP-порта и healthcheck; при падении
перезапускаются не более 5 раз (`restart: on-failure:5`), после чего
остаются остановленными с причиной в логах — без бесконечного цикла.

#### Переменные окружения (`.env`)

| Переменная | Кому | Зачем |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | все | БД; `DATABASE_URL` собирается в Compose (хост `db`) |
| `SETTINGS_ENCRYPTION_KEYS` | backend, outbox-worker, telegram-poller | шифрование SMTP-пароля и токена бота из Settings → Notifications |
| `AUTH_DELIVERY_ENCRYPTION_KEYS` | backend, outbox-worker | шифрование кода восстановления пароля на время доставки; **отдельный** ключ, не ключ настроек |
| `APP_PUBLIC_BASE_URL` (необязательно) | outbox-worker | адрес frontend для ссылок в сообщениях; некорректное значение — worker не стартует |

Ключ генерируется так (для каждого ключа — свой вызов):
`python -c "import base64,secrets;print(base64.b64encode(secrets.token_bytes(32)).decode())"`,
в `.env` — `SETTINGS_ENCRYPTION_KEYS=dev1:<ключ>` и
`AUTH_DELIVERY_ENCRYPTION_KEYS=devmail1:<другой ключ>`. Каждое значение
задаётся в Compose один раз, поэтому API и outbox-worker всегда получают
одинаковый ключ. Секреты передаются только через окружение — не через
аргументы команд — и не пишутся в логи.

SMTP и Telegram настраиваются **не** через `.env`, а в UI: Settings →
Notifications (адрес SMTP-сервера, отправитель, пароль; токен и username
бота; глобальные переключатели Email/Telegram). Для реальной отправки
email нужен доступный SMTP-сервер и включённый глобальный Email; для
Telegram — бот от @BotFather и исходящий HTTPS к `api.telegram.org`.

**Зачем `HOST_UID`/`HOST_GID`**: `backend`/`frontend` работают от
непривилегированного пользователя (не root), и его uid/gid внутри
контейнера подставляется из этих переменных — по умолчанию `1000:1000`
(частый вариант для однопользовательского Linux), если их не передать.
`./apps/api` и `./apps/web` смонтированы в контейнер как bind mount, поэтому
контейнер видит владельца файлов ровно как на host — если он отличается от
uid/gid контейнера, non-root процесс не сможет писать в `/app` (именно так
проявлялась ошибка Vite `EACCES: ... vite.config.ts.timestamp-*.mjs`).
`HOST_UID=$(id -u) HOST_GID=$(id -g)` подставляет ваш реальный uid/gid —
работает одинаково для любого обычного пользователя Linux, без ручной
правки конфигурации.

Если ваш checkout почему-то принадлежит `root` (например, репозиторий
клонировали от root) — контейнеры **всё равно не должны запускаться от
root**; вместо `HOST_UID=0` сначала исправьте владельца на host:
`sudo chown -R $(id -u):$(id -g) apps/`, затем используйте обычный запуск
выше.

- **Frontend**: http://localhost:5173 (порт настраивается через `FRONTEND_PORT` в `.env`)
- **Backend**: http://localhost:8000 (порт — `BACKEND_PORT`); `/docs`, `/openapi.json`, `/health/live`, `/health/ready` (Issue #10)
- **PostgreSQL**: доступен только внутри Compose-сети по имени сервиса `db`, наружу не публикуется.

### Проверить состояние

```bash
docker compose ps
curl http://localhost:8000/health/live   # {"status": "ok"} — процесс жив, БД не проверяется
curl http://localhost:8000/health/ready  # {"status": "ok"} / 503 {"status": "unavailable"} — зависит от PostgreSQL
```

### Логи

```bash
docker compose logs -f                   # все сервисы
docker compose logs -f backend           # API (миграции, запросы)
docker compose logs -f outbox-worker     # доставка уведомлений
docker compose logs -f scheduler         # периодические jobs (JSON supercronic)
docker compose logs -f telegram-poller   # Telegram
```

### Фоновые процессы: проверка и диагностика

**Outbox и email.** При старте worker пишет состояние ключей:
`outbox worker key_ring=AUTH_DELIVERY_ENCRYPTION_KEYS state=available`
(`missing`/`invalid` — ключ не задан/неверен: коды восстановления не
отправляются). Каждая задача — одна строка
`outbox job job_type=notification.delivery … result=completed|pending|dead error_code=…`.
Проверка восстановления пароля: включите Email и настройте SMTP в Settings →
Notifications, запросите восстановление на странице входа — в логе worker
появится `result=completed`, письмо уйдёт на логин учётной записи. Частые
коды: `channel_adapter_unavailable` (SMTP не настроен),
`delivery_encryption_unavailable` (у worker нет `AUTH_DELIVERY_ENCRYPTION_KEYS`),
`challenge_not_deliverable` (код уже использован, отозван или истёк),
`smtp_*` (ответ SMTP-сервера). Если у API нет ключа, в логе backend —
`auth.password_reset.request outcome=encryption_unavailable`, код не
выпускается. Глобальный Email OFF: доставка ставится на паузу
(`result=deferred`). Кратковременная недоступность БД не роняет worker
(`error_code=database_error retry_in_s=…`).

**Scheduler.** Каждую минуту — строки `starting` / `job succeeded` с выводом
`Event lifecycle reconciliation: …`; `job failed` — смотрите вывод job в той
же строке. Нужен только `DATABASE_URL`.

**Telegram Poller.** Состояния `telegram poller state=…`: `not_configured`
(бот не задан — нормально для локальной разработки), `secret_unavailable`
(нет/неверный `SETTINGS_ENCRYPTION_KEYS`), `token_rejected`,
`username_mismatch`, `polling`. Запускайте **ровно один** poller на бота:
второй (или webhook) завершается с кодом 3. Токен в логи не попадает.

**Состояние контейнеров:** `docker compose ps`; остановленный фоновый
сервис после 5 неудачных рестартов виден как `exited (2)`/`(3)` — причина в
`docker compose logs <service>`, после исправления —
`docker compose up -d <service>`.

### Остановить и запустить снова (данные сохраняются)

```bash
docker compose stop        # остановить, контейнеры сохраняются
docker compose up -d       # запустить снова (или `docker compose start`)
docker compose down        # остановить и удалить контейнеры
```

По SIGTERM worker завершает текущую доставку, poller — текущий long poll,
scheduler — текущий job (grace period 40/40/60 с), API завершается сразу.

Named volume `postgres_data` не удаляется — данные PostgreSQL переживают
`down`/`up`.

### Полный сброс development-окружения (⚠ уничтожает DB-данные)

```bash
docker compose down -v
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d --build
```

`-v` удаляет volumes, включая `postgres_data`. Используйте только когда
осознанно нужна чистая БД — это отдельная, явно деструктивная операция, не
часть обычного `down`/`up`.

## CI (GitHub Actions, Issue #9)

`.github/workflows/ci.yml` запускается на каждый pull request и на push в
`main`. Jobs: `backend` (ruff, mypy, unit+API tests — без БД), `integration`
(тесты с реальным ephemeral PostgreSQL service container, credentials
`test`/`test`/`tourcrm_test`, не production), `frontend` (eslint, tsc, vitest,
vite build), `scheduler` (сборка API image с pinned supercronic и проверкой
SHA-256, `supercronic -test` для crontab без БД, runtime smoke test
scheduler-контейнера — `.github/scripts/scheduler-smoke-test.sh`),
`repository` (root-level structural/Docker Compose config checks). Падение любого обязательного шага — `failure`, без `|| true`.

Локально команды CI воспроизводятся напрямую (см. `apps/api/README.md` и
`apps/web/README.md` за деталями):

```bash
# backend
cd apps/api && pip install -r requirements-dev.txt
ruff check . && mypy app && pytest tests/unit tests/api tests/test_smoke.py -v

# integration (нужен свой disposable PostgreSQL — см. apps/api/README.md)
DATABASE_URL=postgresql+psycopg://test:test@localhost:5432/tourcrm_test pytest tests/integration -v

# frontend
cd apps/web && npm ci
npm run lint && npm run typecheck && npm test && npm run build

# repository
pytest tests/ -v
```

## Документация

- `docs/01-product/` — продуктовая концепция;
- `docs/02-requirements/` — требования и бизнес-правила;
- `docs/03-architecture/` — архитектура, технология и ADR;
- `docs/04-modules/` — документация модулей;
- `docs/05-api/` — API;
- `docs/06-ui/` — UI/UX и дизайн-система;
- `docs/07-security/` — безопасность и приватность;
- `docs/08-infrastructure/` — инфраструктура и DevOps;
- `docs/09-governance/` — governance, roadmap, аудит и трассировка требований;
- `docs/DOCUMENTATION-MAP.md` — карта и иерархия источников документации;
- `docs/03-architecture/adr/` — Architecture Decision Records.
