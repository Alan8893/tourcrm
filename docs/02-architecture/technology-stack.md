# Technology Stack

## Status

Proposed and approved as the foundation for Issue #1.

## Architecture style

TourCRM uses a modular monolithic architecture for the initial product. The application is deployed as a small set of containers and has clear internal module boundaries. This avoids premature microservice complexity while leaving an integration boundary for the future TourSlet service.

## Frontend

- React
- TypeScript
- Vite
- React Router
- TanStack Query for server state
- A component/UI library to be selected during UI foundation work
- Responsive design with mobile-first layouts

### Rationale

React and TypeScript provide a mature ecosystem, strong typing, reusable components and good support for desktop, tablet and mobile web. Vite keeps the development loop fast and produces a simple production build.

The application is a responsive web application rather than a separate native mobile application. Native/mobile-specific applications are out of MVP scope.

## Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- pytest

### Rationale

FastAPI is well suited to a typed, API-first application with a substantial amount of CRUD, validation and integration logic. Pydantic provides explicit request/response models. SQLAlchemy gives a mature database abstraction while keeping PostgreSQL-specific capabilities available.

## Database

PostgreSQL is the primary transactional database.

Rules:

- schema changes are performed through Alembic migrations;
- application code must not depend on manual production schema changes;
- foreign keys, indexes and constraints are defined in migrations;
- UTC is used for stored timestamps;
- business-local timezone is applied at presentation/scheduling boundaries;
- soft deletion is used only where business audit/history requires retention; it is not a universal rule.

## Cache and background jobs

Redis is included as an optional infrastructure component and will be used for:

- background jobs;
- notification delivery;
- short-lived caching;
- rate limiting where needed;
- distributed locks where needed.

Redis is not the source of truth for business data.

Background jobs will initially be implemented with a Python task queue abstraction. The exact queue implementation will be finalized when notification and asynchronous processing requirements are implemented.

## API

The backend exposes a versioned REST API.

Base path:

`/api/v1/...`

Rules:

- OpenAPI documentation is generated from FastAPI;
- request and response models are explicitly typed;
- authentication and authorization are enforced server-side;
- API errors use a documented consistent structure;
- breaking API changes require a new API version or an explicitly documented migration strategy.

## Authentication and authorization

The system is multi-user and supports multiple roles per user.

Authorization uses RBAC. Initial roles:

- club administrator;
- instructor;
- club member;
- parent/legal representative.

A user account, person record and club membership are separate conceptual entities.

## Repository structure

The repository is a monorepo:

```text
apps/
  web/        # React frontend
  api/        # FastAPI backend

packages/
  contracts/  # shared API/domain contracts where useful

infra/
  docker/
  nginx/

scripts/

docs/

.github/
```

The exact directory tree is part of the implementation of Issue #1 and may evolve as the first modules are added.

## Configuration and secrets

Configuration is environment-driven.

Rules:

- `.env` is for local development only;
- real secrets are never committed;
- `.env.example` documents required variables without secret values;
- production secrets are supplied by the deployment environment;
- database credentials, signing keys, bot tokens and API keys are treated as secrets.

## Testing strategy

The project uses several test levels:

1. Unit tests for business logic and utilities.
2. Integration tests for database-backed services and API behavior.
3. API tests for authentication, authorization and contract behavior.
4. Frontend component tests where behavior is non-trivial.
5. End-to-end smoke tests for critical user flows after the application foundation is stable.

Every completed issue must leave the affected automated tests passing.

## CI/CD

GitHub Actions is the CI platform.

Minimum CI checks:

- backend lint;
- backend type checking;
- backend tests;
- frontend lint;
- frontend type checking;
- frontend build;
- migration validation;
- application smoke/integration check.

Deployment automation will be added after the development environment and production deployment model are finalized.

## Runtime infrastructure

Initial target:

- Linux LTS;
- 4 vCPU;
- 16 GB RAM;
- 256 GB SSD minimum;
- 512 GB SSD preferred;
- Docker Engine;
- Docker Compose;
- PostgreSQL;
- reverse proxy;
- monitoring and centralized logs as the project matures.

The application must be portable to a VPS without redesigning the software architecture.

## Networking

The application must support:

- local LAN access;
- Internet access through a reverse proxy;
- HTTPS for Internet deployments;
- separate configuration for local and public deployment modes.

## File storage

Files such as participant documents, photos, GPX tracks and knowledge-base attachments are not stored directly in PostgreSQL blobs by default. A storage abstraction is used so the implementation can start with local filesystem storage and later move to S3-compatible/object storage without changing business logic.

## Future integration boundary

The existing TourSlet site is treated as an external system until its archive is reviewed. The integration contract will be defined after analysis of that application.

Potential integration mechanisms include:

- REST API;
- shared authentication/SSO;
- event/webhook exchange;
- one-way or two-way participant/event synchronization.

No integration-specific schema is frozen before the TourSlet codebase is inspected.
