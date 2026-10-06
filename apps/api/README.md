# TourCRM API (backend skeleton)

FastAPI backend for TourCRM. Issue #4 provided the application skeleton,
Issue #5 the PostgreSQL/SQLAlchemy/Alembic storage foundation, Issue #6 the
`/api/v1` HTTP contract foundation (error/collection envelopes, request ID,
OpenAPI), Issue #7 the test harness, Issues #17/#19 the identity/RBAC
persistence, Issue #29 the authorization enforcement engine, and Issue #33
application-managed authentication (registration, login, sessions, email
verification, password reset). No other domain models/business rules are
implemented here — see `docs/SYSTEM-SPECIFICATION.md` and
`docs/03-architecture/application-architecture.md` for the canonical
backend contract.

## Structure

```text
app/
├── main.py            # application entrypoint — FastAPI instance, middleware, handlers
├── api/
│   ├── v1/
│   │   ├── router.py       # versioned API boundary (/api/v1)
│   │   ├── auth.py         # /api/v1/auth/* endpoints (Issue #33)
│   │   └── auth_schemas.py # request/response models for auth.py
│   ├── errors.py          # canonical error contract (ADR-0014) + exception handlers
│   ├── health.py          # liveness/readiness (Issue #10) — outside /api/v1
│   ├── schemas.py         # canonical collection envelope (items/pagination, ADR-0014)
│   ├── request_context.py # request-id middleware (X-Request-ID)
│   └── deps.py             # get_current_principal (real, session-derived — Issue #33),
│                            # require_authenticated_principal, require_csrf_token,
│                            # require_permission (Issue #29)
├── authentication/
│   ├── service.py      # register/login/sessions/verification/reset (Issue #33)
│   ├── passwords.py    # Argon2id hashing + minimum-length policy
│   ├── tokens.py       # opaque secret generation/hashing (sessions, challenges)
│   ├── csrf.py         # double-submit-cookie CSRF check
│   └── rate_limit.py   # RateLimiter boundary (no-op default; no infra invented)
├── authorization/
│   ├── context.py     # ADR-0013 scope vocabulary, ResourceContext (Issue #29)
│   └── service.py     # can()/Authorizer — RBAC + scope decision engine (Issue #29)
├── core/
│   └── config.py      # environment-driven settings (DATABASE_URL, COOKIE_SECURE, ...)
└── db/
    ├── base.py        # shared declarative Base/metadata
    ├── session.py     # engine, session factory, get_db()/session_scope() boundaries
    ├── errors.py       # DatabaseConnectionError (never carries credentials)
    ├── foundation.py  # non-domain FoundationHealthCheck table (migration/ORM smoke checks only)
    ├── identity.py    # Club, Person, User, ClubMembership (Issue #17)
    ├── authorization.py  # Role, Permission, RolePermission, UserRoleAssignment (Issue #19)
    └── authentication.py # AuthenticatedSession, EmailVerificationChallenge, PasswordResetChallenge (Issue #33)
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
- These apply globally regardless of which routes exist; the v1 router
  itself still has zero domain endpoints, and OpenAPI (`/openapi.json`,
  `/docs`) reflects exactly that.

Tests: `pytest tests/api -v` (no database required).

## Authorization foundation (Issue #29)

RBAC + permission scope enforcement, built on Issue #19's `Role`/
`Permission`/`RolePermission`/`UserRoleAssignment` tables — no
role→permission grant is hardcoded or seeded here; `can()` always
queries the real rows.

- `app/authorization/service.py`: `can(session, user_id, permission_code, context)`
  and the `Authorizer` wrapper. Pure Python, no FastAPI/HTTP import — testable
  without a server. Permissions from multiple roles are additive (no explicit
  deny). A club-scoped `UserRoleAssignment` only matches its own club; a
  global one (`club_id IS NULL`) matches any club.
- `app/authorization/context.py`: `ResourceContext` (the explicit,
  backend-resolved facts — `is_self`/`is_child`/`is_own_group`/`is_own_event`/
  `club_id` — scope evaluation checks) and `normalize_scope_type()` (ADR-0013's
  vocabulary; resolves `assigned_events` to `own_events`, rejects everything
  else including `own_records`).
- `app/api/deps.py`: `require_permission(code)` — a FastAPI dependency
  returning an `Authorizer` after a 401 check via `get_current_principal`
  (Issue #33 made this a real, session-derived dependency; the 401/403
  split here is unchanged and is what proves authentication and
  authorization stay independent — see that Issue's section below). The
  endpoint itself resolves the real resource relationship into a
  `ResourceContext` and calls `authorizer.check(context)`; this
  deliberately does not trust a client-supplied resource id for that
  resolution.
- `app/api/errors.py` maps a denied `AuthorizationDenied` to the existing
  canonical 403 envelope — no permission/role/internal detail is included.

No domain endpoint uses this yet (Issue #29 is infrastructure-only, per its
own scope) — see `tests/integration/test_authorization_enforcement.py`'s
test-only probe app for a worked example of the intended usage pattern.

Tests: `pytest tests/unit/test_authorization_service.py -v` (no database),
`pytest tests/integration/test_authorization_service.py tests/integration/test_authorization_enforcement.py -v`
(real PostgreSQL).

## Authentication foundation (Issue #33)

Application-managed authentication per ADR-0009, on the persistence
`docs/03-architecture/authentication-persistence.md` defines
(`app/db/authentication.py`). Deliberately independent of
`app/authorization/`: this layer only ever establishes *who* the caller
is; it grants no permission and touches no `Role`/`RolePermission`/
`UserRoleAssignment` row.

- `app/authentication/service.py` — `register` (always creates a
  `pending` `User`, grants no role), `verify_email`/`resend_verification`,
  `login` (verifies the password before checking account state, so
  "identifier not found" and "wrong password" are indistinguishable;
  rejects every non-`active` state), `resolve_session` (the *only* path
  by which an authenticated identity is derived — from the session
  cookie's server-side row, never a client-supplied id),
  `revoke_session`/`logout_all`/`list_sessions`, and
  `request_password_reset`/`confirm_password_reset` (non-enumerating,
  invalidates existing sessions) /`change_password` (does not).
- `app/api/v1/auth.py` — `/api/v1/auth/{register,verify-email,
  resend-verification,login,logout,me,sessions,sessions/{id},logout-all,
  password-reset/request,password-reset/confirm,password/change}`, per
  `docs/05-api/auth-api.md`. A session cookie (`session_token`, HttpOnly)
  plus a double-submit CSRF cookie/header (`csrf_token`/`X-CSRF-Token`,
  `app/authentication/csrf.py`) are set on login and required on every
  state-changing endpoint reachable via that cookie.
- **Not implemented, intentionally**: invitation creation/acceptance
  (blocked by ODR-014 — `docs/03-architecture/adr/ADR-0008-open-decisions.md`
  — `auth-api.md` names a permission code, `membership.invitation.create`,
  that does not exist in the canonical permission catalog; no substitute
  or new permission was invented); MFA/SSO/OAuth/WebAuthn; an
  admin-approval-of-registration
  endpoint (`auth-api.md` requires the *capability* but never defines
  such an endpoint itself); real email/notification delivery (no such
  channel exists in this codebase yet — verification/reset challenges are
  created and hashed correctly, but nothing sends the raw token anywhere;
  see the PR description); structured `AuditLog` persistence (no
  `audit_logs` table/infrastructure exists yet — security-relevant events
  are logged via the existing `logging.getLogger("tourcrm.api")` pattern
  instead, which is operational logging, not the canonical audit domain);
  and real rate-limiting infrastructure (`app/authentication/rate_limit.py`
  is a `RateLimiter` boundary with a no-op default, wired into every
  security-sensitive endpoint, so a real limiter is a dependency override
  away — no concrete throttling infrastructure exists yet and no
  undocumented numeric limit is invented).

Set `COOKIE_SECURE=false` for local plain-HTTP development/testing (see
`.env.example`) — a browser (and `TestClient`) will not resend a `Secure`
cookie over plain HTTP.

Tests: `pytest tests/unit/test_authentication.py -v` (no database),
`pytest tests/integration/test_authentication_service.py tests/integration/test_authentication_api.py -v`
(real PostgreSQL).

## Initial administrator bootstrap (Issue #99 / TH-0089, ADR-0027)

A fresh installation has no user at all, and public self-registration
(`POST /api/v1/auth/register`) always creates a `pending` User, so it
cannot be the first-login path. This is the one, operator-run,
non-HTTP way to create the first administrator:

```bash
cd apps/api
python -m app.cli.bootstrap_admin
```

You will be prompted interactively for an email and a password (input
hidden, via `getpass`), asked to confirm the password, shown a summary,
and asked to confirm before anything is written. The password is never
accepted as a command-line argument or read from an environment
variable/`.env` file, and is never logged, printed, or stored anywhere
but as its Argon2 hash.

This creates a normal `Person` (`Admin`/`Admin` — a placeholder you can
rename afterward through the ordinary account-management mechanism, not
a mandatory profile-completion step) and a normal, already-`active`
`User`, and assigns the existing canonical `admin` Role with the
installation-wide `RoleAssignment` scope (`scope_type=all`,
`club_id=NULL` — see ADR-0026's TH-0089 amendment). It refuses outright,
without asking for a password, if a global administrator already
exists, and is safe to run concurrently from multiple processes (a
PostgreSQL advisory lock serializes the check-and-create) — at most one
initial administrator is ever created.

After bootstrap, log in exactly like any other user — there is no
special bootstrap login path:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"identifier": "you@example.com", "password": "<your password>"}' \
  -c cookies.txt
curl http://localhost:8000/api/v1/auth/me -b cookies.txt
```

**Known limitation (documented GAP, not introduced by this Issue):**
the canonical `admin` Role's `RolePermission` grants are not seeded
anywhere in this codebase (Issue #19 §9 deliberately deferred mapping
`docs/02-requirements/roles-and-permissions.md`'s matrix into concrete
grants to a future issue, and this Issue's own non-goals forbid
`RolePermission` administration) — so the bootstrapped administrator
currently holds the `admin` Role but no permission grants are guaranteed
to exist yet. This is a pre-existing, system-wide condition (no user of
any kind can be an effective administrator until that future issue
lands), not something bootstrap introduces or silently works around;
bootstrap fails clearly (`AdminRoleInconsistentError`) if the `admin`
Role itself is missing or is not the expected system role, but
deliberately does not check — or invent — any `RolePermission` grant.

Tests: `pytest tests/integration/test_authentication_bootstrap.py -v`
(real PostgreSQL).

## Health endpoints (Issue #10)

Operational, not versioned business API — outside `/api/v1`, per
`docs/05-api/api-conventions.md` §2. URLs match the existing canonical
naming in `docs/05-api/endpoint-inventory.md` §26.

| Endpoint | Checks | Success | Failure |
|---|---|---|---|
| `GET /health/live` | Nothing but the process itself — never touches PostgreSQL | `200 {"status": "ok"}` | (not expected — process is up or it isn't answering at all) |
| `GET /health/ready` | PostgreSQL connectivity, via the existing `app.db.session.check_connection()` (Issue #5) — no second DB-probe mechanism | `200 {"status": "ok"}` | `503 {"status": "unavailable"}` |

Both return minimal operational JSON — never the business error envelope
(ADR-0014), never a DSN/credential/traceback. `/health/live` is what the
Docker `HEALTHCHECK` uses (never `/health/ready`: a container healthcheck
answers "is this process alive", not "is its database also up").

Tests: `pytest tests/api/test_health.py -v` (no database — the DB-unavailable
path is exercised by monkeypatching `check_connection`) and
`pytest tests/integration/test_health.py -v` (real PostgreSQL, success path).

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

## Lint & type checking (Issue #9)

`ruff` (lint) and `mypy` (type checking) — the CI minimums from
`docs/03-architecture/technology-stack.md`. Config lives in `pyproject.toml`
(`[tool.ruff]`, `[tool.mypy]`).

```bash
cd apps/api
pip install -r requirements-dev.txt
ruff check .
mypy app
```

## Testing (Issue #7)

Test configuration lives in `pyproject.toml` (`[tool.pytest.ini_options]`,
`[tool.coverage.*]`). Selective runs use directory/path targeting rather
than custom markers. These are exactly the commands CI (`.github/workflows/ci.yml`,
Issue #9) runs — see its `backend`/`integration` jobs.

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

## Event lifecycle reconciliation (Issue #281, ADR-0018)

Event status follows the schedule for the two time-driven transitions:
`published -> in_progress` once `start_at` is reached and
`in_progress -> completed` once `end_at` is reached. The backend applies
them with a system CLI:

```bash
cd apps/api
python -m app.cli.reconcile_event_lifecycle
```

The [scheduler](#scheduler-for-periodic-system-jobs-issue-289-adr-0044)
runs it **every minute**; it can also be run by hand at any time (in
development:
`docker compose exec backend python -m app.cli.reconcile_event_lifecycle`).
A run converges every missed transition in one pass (an Event whose
`end_at` already passed goes `published -> in_progress -> completed`), so
the service never has to be running at the exact moment. It never
publishes a `draft` and never changes a `cancelled`, `completed` or
`archived` Event. It is idempotent and safe to run concurrently (each
Event is reconciled under its row lock); it exits non-zero if any Event
could not be reconciled, and the next run retries it.

## Scheduler for periodic system jobs (Issue #289, ADR-0044)

Periodic system jobs are run by a dedicated scheduler process,
[supercronic](https://github.com/aptible/supercronic), never by the
FastAPI process (no startup task, background loop or in-process
scheduler; no Redis/broker). The scheduler only invokes official system
CLIs; it holds no business logic. It is meant to run as **exactly one
replica per deployment** (ADR-0044 §3).

| | |
|---|---|
| Technology | supercronic `v0.2.49`, pinned in `apps/api/Dockerfile` with SHA-256 checksums for `amd64`/`arm64`; the build fails on a mismatch |
| Crontab | `apps/api/scheduler/crontab` (version-controlled; `/app/scheduler/crontab` in the image) |
| Command | `supercronic -json /app/scheduler/crontab` — the container's main process (PID 1) |
| Image | the API image (`apps/api/Dockerfile`), same code and `DATABASE_URL` as the backend; runs as the non-root `app` user |
| Jobs | `* * * * *` `timeout --verbose --kill-after=5s 50s python -m app.cli.reconcile_event_lifecycle` |
| Network | serves no HTTP, publishes no port, needs only PostgreSQL |

### Behaviour

- **Every minute** supercronic starts the reconciliation CLI. It never
  starts it again while the previous run is still going (the
  `-overlapping` flag is not used); a run that outlives its minute is
  reported as `job took too long to run`, and the skipped start as
  `not starting: job is still running`.
- **Failure.** A run that exits non-zero (an Event that could not be
  reconciled, the database unreachable, an unhandled error) is logged as
  `"level":"error","msg":"error running command: exit status N"`, with the
  CLI's own output/traceback before it. The scheduler keeps running and
  the next minute's run is the retry — there is no other retry loop.
  The CLI's per-Event failure isolation is unchanged.
- **Timeout.** One run is bounded: after 50 s `timeout` sends `SIGTERM`
  (exit status `124`, logged as `timeout: sending signal TERM to command
  'python'`), and `SIGKILL` 5 s later if it is still alive (exit status
  `137`). Why 50 s:
  - a normal run is short — measured against PostgreSQL 16: ~0.6 s with
    nothing due (interpreter start-up dominates), ~1 s for 100 and ~4.7 s
    for 1 000 missed `published -> completed` Events (~4 ms per Event);
    the codebase sets no database statement/lock timeouts, so the only
    open-ended waits are a row lock held by a concurrent writer or a hung
    connection, which is what the bound is for;
  - 50 s + 5 s stays under the one-minute interval, so a timed-out run
    ends before the next minute's run is due and never delays it;
  - a timed-out run loses no work: each Event is reconciled and committed
    in its own transaction, and due Events are processed in a stable
    order, so the next run continues from the first Event not yet
    reconciled — even a catch-up larger than one run can handle converges
    over successive minutes.
- **Database state after a killed run.** Committed Events stay committed;
  the Event in progress has an uncommitted transaction, which PostgreSQL
  rolls back when the connection closes — no partial transition, no open
  session or lock left for the next run (covered by
  `tests/integration/test_scheduler_crontab.py`). If the run was killed
  while its server-side statement was waiting on a row lock, PostgreSQL
  finishes that wait and then aborts it; until then the next run waits on
  the same row, and if the lock is held longer than the bound that run
  also times out, visibly, every minute. A completed Event's achievement
  hook runs after its commit; if a kill lands exactly there, the existing
  achievement reconciliation (`python -m app.cli.reconcile_achievements`)
  is what creates any missed Award, as for any other hook failure.
- **Restart.** Missed minutes are not replayed; the first run after the
  scheduler (or the database) comes back converges every missed
  transition (ADR-0018). Restarting a stopped scheduler is the deployment
  topology's job (restart policy).
- **Shutdown.** On `SIGTERM` supercronic stops starting runs, waits for a
  running one and exits `0`. Allow a stop grace period of at least 55 s
  (the development Compose service uses 60 s).

### Logs

supercronic writes everything to the container's stderr: one JSON line
per event (`starting`, each line of the CLI's output with
`"channel":"stdout"`/`"stderr"`, `job succeeded` or
`error running command: exit status N`), tagged with `job.command`,
`job.schedule` and `iteration`. Neither the scheduler nor the CLI prints
the environment; a connection error names the host/port, never the
password.

```bash
docker compose logs -f scheduler
```

### Running it in development

The development `docker-compose.yml` has a `scheduler` service behind the
opt-in `scheduler` profile, so a plain `docker compose up` still starts the
application without it:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose --profile scheduler up --build
```

It waits for `backend` to be healthy (i.e. migrations applied) and uses
the same bind-mounted source. Like the rest of that file it is
**development only** — no production deployment topology exists yet
(ADR-0044 §7); a future one must run this image with
`supercronic -json /app/scheduler/crontab` as a single, dedicated replica.

To check the scheduler configuration locally:

```bash
# crontab syntax (no database needed)
docker compose run --rm --no-deps scheduler supercronic -test /app/scheduler/crontab
# the exact crontab command against PostgreSQL
DATABASE_URL=postgresql+psycopg://test:test@localhost:5432/tourcrm_test \
  pytest tests/integration/test_scheduler_crontab.py -v
```

CI (`scheduler` job) builds the image, checks the installed supercronic
version against the pin, runs `supercronic -test` on the crontab without a
database, checks the non-root user, and runs
`.github/scripts/scheduler-smoke-test.sh`: real every-minute iterations
against PostgreSQL (success) and against an unreachable database (logged
failures, scheduler still running, next run still happens, no password in
the logs), then a graceful `SIGTERM` stop.
