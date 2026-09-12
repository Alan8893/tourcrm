# TourCRM API (backend skeleton)

FastAPI backend application skeleton for TourCRM. This is Issue #4 (application
skeleton) — no domain models, persistence, or authentication are implemented
here. See `docs/SYSTEM-SPECIFICATION.md` and
`docs/03-architecture/application-architecture.md` for the canonical backend
contract.

## Structure

```text
app/
├── main.py       # application entrypoint — creates the FastAPI instance
└── api/
    └── v1/
        └── router.py   # versioned API boundary (/api/v1), no endpoints yet
tests/
└── test_smoke.py       # import/startup smoke checks
```

Domain modules (`auth`, `members`, `events`, `trips`, ... per
`application-architecture.md` §5.3) are added under `app/` by their own
Issues. This skeleton does not pre-create empty module directories.

Infrastructure concerns (configuration, database access, background jobs)
are introduced by the Issues that need them (PostgreSQL/SQLAlchemy/Alembic is
Issue #5); none of that exists yet.

## Running (development)

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## Smoke checks

```bash
cd apps/api
pip install -r requirements-dev.txt
pytest
```
