# TourCRM API (backend skeleton)

FastAPI backend for TourCRM. Issue #4 provided the application skeleton;
Issue #5 added the PostgreSQL/SQLAlchemy/Alembic storage foundation. No
domain models, business rules, or authentication are implemented here — see
`docs/SYSTEM-SPECIFICATION.md` and
`docs/03-architecture/application-architecture.md` for the canonical backend
contract.

## Structure

```text
app/
├── main.py            # application entrypoint — creates the FastAPI instance
├── api/
│   └── v1/
│       └── router.py  # versioned API boundary (/api/v1), no endpoints yet
├── core/
│   └── config.py      # environment-driven settings (DATABASE_URL, ...)
└── db/
    ├── base.py        # shared declarative Base/metadata
    ├── session.py     # engine, session factory, get_db()/session_scope() boundaries
    ├── errors.py       # DatabaseConnectionError (never carries credentials)
    └── foundation.py  # non-domain FoundationHealthCheck table (migration/ORM smoke checks only)
alembic/                 # migrations; URL comes from DATABASE_URL via env.py, never hardcoded
tests/
├── test_smoke.py                 # import/startup smoke checks (no database needed)
└── integration/test_database.py  # real PostgreSQL integration tests (Issue #5)
```

Domain modules (`auth`, `members`, `events`, `trips`, ... per
`application-architecture.md` §5.3) are added under `app/` by their own
Issues. This skeleton does not pre-create empty module directories.

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

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://tourcrm:<password>@localhost:5432/tourcrm_dev
alembic upgrade head
uvicorn app.main:app --reload
```

## Smoke checks (no database required)

```bash
cd apps/api
pip install -r requirements-dev.txt
pytest tests/test_smoke.py
```

## Integration tests (require PostgreSQL)

Point `DATABASE_URL` (or `TEST_DATABASE_URL`) at a disposable PostgreSQL
database — a local install, or a container you start yourself; a
docker-compose-based dev/test PostgreSQL service is Issue #8's scope, not
duplicated here. Each test resets the `public` schema first, so use a
database dedicated to testing:

```bash
cd apps/api
export DATABASE_URL=postgresql+psycopg://tourcrm:<password>@localhost:5432/tourcrm_test
pytest tests/integration -v
```

Without `DATABASE_URL`/`TEST_DATABASE_URL` set, these tests are skipped
(reported as SKIPPED, not silently passed) rather than faked.
