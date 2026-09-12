# TourCRM API (backend skeleton)

FastAPI backend for TourCRM. Issue #4 provided the application skeleton,
Issue #5 the PostgreSQL/SQLAlchemy/Alembic storage foundation, Issue #6 the
`/api/v1` HTTP contract foundation (error/collection envelopes, request ID,
OpenAPI), and Issue #7 the test harness. No domain models, business rules,
or authentication are implemented here — see `docs/SYSTEM-SPECIFICATION.md`
and `docs/03-architecture/application-architecture.md` for the canonical
backend contract.

## Structure

```text
app/
├── main.py            # application entrypoint — FastAPI instance, middleware, handlers
├── api/
│   ├── v1/
│   │   └── router.py     # versioned API boundary (/api/v1), no endpoints yet
│   ├── errors.py          # canonical error contract (ADR-0014) + exception handlers
│   ├── schemas.py         # canonical collection envelope (items/pagination, ADR-0014)
│   ├── request_context.py # request-id middleware (X-Request-ID)
│   └── deps.py             # DI boundary placeholder for the future authorization layer
├── core/
│   └── config.py      # environment-driven settings (DATABASE_URL, ...)
└── db/
    ├── base.py        # shared declarative Base/metadata
    ├── session.py     # engine, session factory, get_db()/session_scope() boundaries
    ├── errors.py       # DatabaseConnectionError (never carries credentials)
    └── foundation.py  # non-domain FoundationHealthCheck table (migration/ORM smoke checks only)
alembic/                 # migrations; URL comes from DATABASE_URL via env.py, never hardcoded
tests/
├── conftest.py         # shared technical fixtures (Issue #7) — no business data
├── factories.py        # shared technical factories (Issue #7) — no business data
├── test_smoke.py       # import/startup smoke checks (Issue #4, no database needed)
├── unit/                # pure unit tests, no HTTP/no DB (Issue #7)
├── api/                  # HTTP contract tests (Issue #6, no database needed)
└── integration/          # real PostgreSQL integration tests (Issue #5)
```

Domain modules (`auth`, `members`, `events`, `trips`, ... per
`application-architecture.md` §5.3) are added under `app/` by their own
Issues. This skeleton does not pre-create empty module directories.

## API foundation (Issue #6)

- Single-resource responses are returned directly (no `data` wrapper).
- Collections use `{"items": [...], "pagination": {...}}` — see
  `app/api/schemas.py`.
- Errors use `{"error": {"code", "message", "details", "request_id"}}` — see
  `app/api/errors.py`. No stack trace, DSN, credentials or filesystem paths
  are ever included; unexpected exceptions are logged server-side only.
- Every request gets a `request_id`, exposed via the `X-Request-ID`
  response header and in every error body; a valid client-supplied
  `X-Request-ID` is honored, otherwise one is generated (`app/api/request_context.py`).
- `app/api/deps.py` is an unused-for-now DI boundary for the future
  authorization layer (authentication mechanism is ODR-001, still open —
  not decided here).
- These apply globally regardless of which routes exist; the v1 router
  itself still has zero domain endpoints, and OpenAPI (`/openapi.json`,
  `/docs`) reflects exactly that.

Tests: `pytest tests/api -v` (no database required).

## Database configuration

Set `DATABASE_URL` in the environment (copy `.env.example` to `.env` for
local development — `.env` is gitignored and must never be committed):

```bash
cp .env.example .env
# edit .env with your local PostgreSQL credentials
```

No default or fallback connection string with real credentials exists in
code, migrations, or `alembic.ini` — `DATABASE_URL` is required.

## Running (development)

Via Docker Compose (recommended — also starts PostgreSQL; see root
`README.md`):

```bash
cd /path/to/tourcrm && cp .env.example .env
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up --build
```

Or directly on the host:

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://tourcrm:<password>@localhost:5432/tourcrm_dev
alembic upgrade head
uvicorn app.main:app --reload
```

## Testing (Issue #7)

Test configuration lives in `pyproject.toml` (`[tool.pytest.ini_options]`,
`[tool.coverage.*]`). Selective runs use directory/path targeting rather
than custom markers.

```bash
cd apps/api
pip install -r requirements-dev.txt

pytest                       # full suite (integration tests skip cleanly without a database)
pytest tests/unit -v         # unit only — no HTTP, no DB
pytest tests/api -v          # API contract tests only — no DB required
pytest tests/integration -v  # DB integration tests only — see below
pytest tests/unit/test_config.py -v                     # one file
pytest tests/unit/test_config.py::test_get_settings_has_no_hardcoded_database_fallback  # one test
```

A failing test returns a non-zero exit code (plain `pytest` behavior; no
custom wrapping). No local PostgreSQL does **not** turn the suite into an
unclear failure: unit and API tests never touch a database, and
`tests/integration` explicitly skips (reported as SKIPPED, not silently
passed, and not a failure) when no test database is configured.

### Integration tests (require PostgreSQL)

Point `DATABASE_URL` (or `TEST_DATABASE_URL`) at a **disposable, non-production**
PostgreSQL database — a local install, or a container you start yourself; a
docker-compose-based dev/test PostgreSQL service is Issue #8's scope, not
duplicated here. Each test resets the `public` schema first, so use a
database dedicated to testing, never a production one:

```bash
cd apps/api
export DATABASE_URL=postgresql+psycopg://tourcrm:<password>@localhost:5432/tourcrm_test
pytest tests/integration -v
```

There is no default/fallback `DATABASE_URL` anywhere in the app or test
config (`app/core/config.py` raises `ConfigurationError` if it is unset —
see `tests/unit/test_config.py`), so tests can never silently fall through
to a production database.

### Coverage

`pytest-cov` is wired in (natively supported by the existing pytest stack)
with `[tool.coverage.*]` in `pyproject.toml`. **No minimum coverage
threshold is enforced** — none of technology-stack.md, application-architecture.md,
security-and-privacy.md or issue-roadmap.md defines one; this is a report
only, not a quality gate:

```bash
pytest --cov=app --cov-report=term-missing
```

### Technical fixtures/factories

`tests/conftest.py` and `tests/factories.py` provide only technical,
non-business helpers (`unique_id()`, `unique_token()`, and the
`technical_id`/`technical_token` fixtures) — opaque synthetic values with no
Person/User/Trip/Finance meaning. Domain-specific fixtures are added by the
Issues that introduce those domain models.
