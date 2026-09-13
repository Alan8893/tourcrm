# TourCRM — Application Architecture

## 1. Purpose

This document defines the target architecture of TourCRM at application level. It is an implementation contract for Claude and a boundary document for future modules.

## 2. Architectural principles

1. API-first: frontend and external integrations consume explicit backend contracts.
2. Domain-oriented modularity: business domains remain separated even when deployed as one backend application.
3. Stateless HTTP API: application instances should not depend on local process state for correctness.
4. PostgreSQL is the source of truth for transactional business data.
5. Object/file storage is used for binary documents, photographs and GPX files.
6. Background processing is used for non-critical asynchronous work such as notifications and file processing.
7. Authorization is enforced on the backend; hiding a UI element is never a security control.
8. Auditability is a first-class concern for changes to sensitive and historically significant data.
9. LAN and Internet deployment must use the same application contract; deployment topology may differ.
10. Optional modules must be disableable through feature settings without weakening security controls.

## 3. Target logical topology

```text
Browser / PWA-capable Web Client
            |
            | HTTPS / HTTP(S) in trusted LAN
            v
      Reverse Proxy
            |
            v
      Frontend Web App
            |
            | REST/JSON API
            v
       Backend API
        /   |   \
       /    |    \
      v     v     v
 PostgreSQL Redis Object Storage
      |       |
      |       +--> Background Worker / Scheduler
      |
      +--> migrations / transactional data

Backend --> Notification adapters --> Email / Telegram / MAX
Backend <--> TourSlet integration (future, after archive analysis)

Monitoring / Logs / Metrics receive telemetry from all services.
```

The exact reverse proxy, object-storage implementation and worker technology are architecture decisions documented separately before implementation.

## 4. Frontend

### 4.1 Technology

React + TypeScript.

The frontend is a responsive web application, with Progressive Web App capabilities considered where useful. A separate native mobile application is out of scope for the initial product.

### 4.2 Responsibilities

- authentication/session UX;
- routing;
- rendering domain views;
- form validation for user experience;
- API consumption;
- local UI state;
- permissions-aware navigation;
- responsive layouts;
- accessibility;
- error, loading and empty states.

### 4.3 Restrictions

Frontend must not be treated as the source of authorization truth. Every protected operation must be validated by the backend.

Business calculations that determine authoritative records must live on the backend/domain layer.

## 5. Backend

### 5.1 Technology

FastAPI + Python.

SQLAlchemy is the data access/ORM layer and Alembic is the schema migration mechanism.

### 5.2 Responsibilities

- authentication;
- authorization;
- request validation;
- domain rules;
- transactional operations;
- persistence;
- orchestration of background jobs;
- integrations;
- audit logging;
- file metadata and storage orchestration;
- API documentation.

### 5.3 Modular boundaries

The backend should be implemented as a modular monolith initially. The following logical modules are anticipated:

- auth;
- users;
- members;
- guardians;
- groups;
- events;
- attendance;
- trips;
- routes;
- achievements;
- skills;
- qualifications;
- knowledge;
- documents;
- consents;
- equipment;
- finance;
- notifications;
- integrations;
- analytics;
- audit;
- settings.

A module owns its business rules and service interfaces. Direct cross-module database access should be avoided when a domain service/API can express the operation.

## 6. Database

PostgreSQL is the canonical transactional datastore.

Requirements:

- foreign keys for relational integrity;
- explicit indexes for frequent filters/lookups;
- UTC timestamps at persistence layer unless a separate documented local-time field is required;
- transaction boundaries defined for business operations;
- schema changes exclusively through versioned Alembic migrations;
- no application startup behavior that silently mutates production schema.

The detailed relational model is specified in `docs/03-architecture/data-model.md` and the future database schema document.

## 7. Redis and background processing

Redis is not the source of truth for business records.

It may be used for:

- job queue support;
- scheduled jobs;
- short-lived cache;
- rate limiting;
- temporary coordination where justified.

The first implementation should avoid introducing Redis-dependent business correctness where a PostgreSQL transaction can provide the required guarantee.

Candidate background jobs:

- notification delivery;
- retrying failed notification delivery;
- scheduled reminders;
- GPX parsing/metadata extraction;
- derived aggregate recalculation;
- document expiration checks;
- periodic maintenance.

Background jobs must be idempotent or otherwise safe to retry.

## 8. Object/file storage

Binary data should not be stored in PostgreSQL by default.

Candidate file types:

- participant photographs;
- scanned documents;
- consent documents;
- GPX files;
- knowledge-base attachments;
- event/trip media.

The database stores metadata and stable object references. Storage access must be authorized and should not expose private documents through guessable public URLs.

## 9. API style

Primary API style: REST over JSON.

Base path:

`/api/v1/`

The API must use resource-oriented endpoints, explicit HTTP methods and consistent response/error structures.

Example domains:

```text
/api/v1/auth/*
/api/v1/users/*
/api/v1/persons/*
/api/v1/guardian-relationships/*
/api/v1/groups/*
/api/v1/events/*
/api/v1/attendance/*
/api/v1/trips/*
/api/v1/routes/*
/api/v1/achievements/*
/api/v1/documents/*
/api/v1/finance/*
/api/v1/equipment/*
/api/v1/notifications/*
/api/v1/knowledge/*
/api/v1/analytics/*
```

Detailed endpoint contracts are maintained in the API specification document.

## 10. API versioning

The public API must be versioned from the beginning using the URL prefix `/api/v1`.

Breaking changes require a new major API version. Backward-compatible extensions remain within the same major version.

Deprecation requires documented notice and a migration path.

## 11. Authentication

The authentication design must support:

- account login;
- registration;
- administrator approval;
- invitation-based onboarding;
- password reset;
- session/token revocation;
- email verification where email is used as an identifier;
- future SSO/integration capability.

Password credentials must use a strong password hashing algorithm supported by the selected authentication library. Plaintext passwords and reversible password encryption are prohibited.

Exact token/session mechanism is documented in the authentication ADR before implementation.

## 12. Authorization

Authorization is deny-by-default.

The effective access decision is based on:

`User -> RoleAssignment -> Permission -> Scope -> Resource relationship`

Examples of scopes:

- all;
- own_groups;
- self;
- children;
- own_records.

The backend must evaluate object-level relationships, not only role names.

## 13. Error contract

The API must return machine-readable errors with at least:

- stable error code;
- human-readable message safe for the client;
- field-level validation details when applicable;
- request/correlation identifier when appropriate.

Sensitive internals, stack traces and secrets must not be returned to clients.

HTTP status codes must reflect the failure category consistently across modules.

## 14. Pagination, filtering and sorting

Collection endpoints should support consistent server-side pagination where result sets may grow.

Common conventions must be uniform across endpoints for:

- page/limit or equivalent pagination;
- filtering;
- sorting;
- search;
- total/count metadata where appropriate.

The exact convention is part of the API specification and must not vary arbitrarily by module.

## 15. Audit and observability

Sensitive mutations should produce audit records containing actor, action, resource, timestamp and correlation context.

Application logs must be structured and must not contain passwords, access tokens, reset tokens or other credentials.

Operational observability should cover:

- application errors;
- API latency;
- database connectivity;
- background job failures;
- notification delivery failures;
- storage failures;
- resource utilization.

## 16. Configuration

Configuration is environment-dependent and must not be hard-coded.

Expected categories:

- application environment;
- public URLs;
- database connection;
- Redis connection;
- storage configuration;
- authentication secrets;
- email credentials;
- Telegram credentials;
- MAX credentials;
- logging/monitoring settings.

Secrets are supplied through environment-specific secret management. They must never be committed to Git.

## 17. Deployment modes

### LAN

The same application can run on a private network. TLS should be used where practical; if an initial trusted LAN deployment temporarily omits TLS, this must be explicitly documented as a deployment exception rather than an application assumption.

### Internet

Internet-facing deployment requires:

- TLS;
- secure reverse proxy;
- firewalling;
- strong authentication configuration;
- rate limiting where appropriate;
- backup and restore procedures;
- operational monitoring.

## 18. Environment separation

Minimum conceptual environments:

- development;
- test/CI;
- production.

A staging environment may be introduced later.

Production data must not be used in development or automated tests unless explicitly anonymized and authorized.

## 19. Repository architecture

The project should use a monorepo for the initial modular monolith, allowing coordinated changes to frontend, backend and shared documentation.

Recommended top-level structure:

```text
/
├── apps/
│   ├── web/
│   └── api/
├── packages/
│   └── shared-contracts/      # only if genuinely useful
├── docs/
├── infra/
├── tests/
├── .github/
├── docker-compose.yml
└── README.md
```

The actual language-specific directory layout must preserve module boundaries and remain discoverable to new contributors.

## 20. Integration boundary for TourSlet

The existing TourSlet site will be analyzed from the provided archive before selecting the integration pattern.

Possible patterns:

- shared authentication/SSO;
- REST API integration;
- event-driven synchronization;
- shared database only if unavoidable and explicitly approved.

Direct sharing of database tables between separate applications is not the default choice.

## 21. Architectural quality gates

Before a module is accepted:

1. domain rules are documented;
2. authorization is documented;
3. data model is documented;
4. API contract is documented;
5. error cases are documented;
6. audit requirements are defined;
7. test cases cover business rules and authorization;
8. implementation satisfies the documented contract.

## 22. Deferred decisions

The following are intentionally deferred until the relevant analysis:

- exact reverse proxy;
- exact object-storage engine;
- exact auth/session implementation;
- exact Redis job framework;
- TourSlet integration protocol;
- calendar provider integrations;
- PWA/offline capability depth;
- final observability stack.

Each decision must become an ADR before it becomes a non-trivial implementation dependency.
