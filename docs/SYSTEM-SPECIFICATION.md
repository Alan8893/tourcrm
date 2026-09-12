# TourCRM — System Specification

## 1. Purpose

This document is the top-level implementation contract for TourCRM. It consolidates the project vision, scope, architecture, domain model, functional and non-functional requirements, security requirements, API conventions, UI architecture, infrastructure constraints, and traceability rules.

Detailed domain/API specifications remain authoritative for their respective areas. If this document conflicts with a more detailed normative specification, the conflict must be resolved explicitly and documented; implementation must not guess.

## 2. Product definition

TourCRM is a multi-user web information and management system for one school tourism club. It combines CRM, educational process management, tourism activity records, knowledge base, documents, finances, equipment management, communications, analytics, and integration with the existing tour-slet system.

The application must support desktop, tablet, and mobile browsers and operate both through LAN and Internet-facing deployment.

## 3. Architectural principles

1. Modular monolith is the default application architecture.
2. Frontend and backend are separated by a versioned REST API.
3. PostgreSQL is the system of record for structured transactional data.
4. Files are stored outside PostgreSQL through a storage abstraction.
5. Redis/background processing is introduced where asynchronous or transient workloads justify it; it is not treated as the source of truth.
6. Authorization is enforced server-side. UI permissions are only a usability layer.
7. Domain facts are authoritative; derived aggregates must be reproducible.
8. Historically significant data is archived rather than silently destroyed.
9. Auditability is mandatory for significant state and data changes.
10. Configuration and feature settings cannot bypass security permissions.
11. External integrations are isolated behind explicit adapters/contracts.
12. Every architectural decision that changes an existing contract must be recorded in ADR.

## 4. Technology baseline

The current target stack is:

- frontend: React + TypeScript;
- backend: Python + FastAPI;
- data access: SQLAlchemy;
- migrations: Alembic;
- database: PostgreSQL;
- background/cache infrastructure: Redis when required by an approved design;
- containerization: Docker + Docker Compose;
- CI/CD: GitHub Actions;
- runtime: Linux LTS;
- reverse proxy/TLS: selected according to ADR-0012;
- file storage: selected according to ADR-0011.

Exact dependency versions are implementation-time constraints and must use supported stable versions compatible with the approved stack.

## 5. Domain boundaries

The system is divided into these logical domains:

- Identity and access;
- People and membership;
- Groups;
- Events and schedule;
- Attendance;
- Trips and routes;
- Tourist profile;
- Achievements, skills, and qualifications;
- Knowledge base;
- Documents and consents;
- Equipment;
- Finance;
- Notifications and communications;
- Analytics and reporting;
- Audit;
- External integrations, including TourSlet.

Modules are logically isolated even though they initially run in one deployable application.

## 6. Identity model

The canonical identity model is:

`Person` — physical person.

`User` — authentication account.

`ClubMembership` — historical membership of a person in the club.

`RoleAssignment` — permissions assigned to a user.

`GuardianRelationship` — relationship between a guardian and a child/member.

A person may simultaneously be a member, instructor, administrator, and guardian. These facts must be modeled independently.

## 7. Roles

Base roles:

- admin;
- instructor;
- member;
- guardian.

The authorization model is permission + scope based. Examples of scopes include `all`, `own_groups`, `self`, and `children`.

Authorization must be evaluated on the server for every protected operation.

## 8. Core event model

`Event` is the canonical calendar entity. Supported types include lesson, training, trip, competition, tour_slet, excursion, meeting, and other.

Recurring activities use an event series with generated occurrences and explicit exceptions. Attendance belongs to a concrete occurrence, not merely to a series.

Specialized domains such as trips must extend the base event model instead of adding all possible specialized fields to Event.

## 9. Tourist activity model

Trip facts are authoritative. Tourist profile values such as total distance or trip count are derived values and must be recalculable.

Trip records must support tourism type, route, planned and actual metrics, leader, participants, roles, results, partial participation, GPX/media, and audit history.

A route may contain points, geodata, and GPX objects. GPX binary content is stored outside PostgreSQL.

Official categories, qualification rules, and tourism-experience calculation rules are subject to separately approved normative/business decisions.

## 10. Documents and data protection

Documents are managed as typed records with metadata, version, owner/subject, status, issue/expiry information where applicable, and access policy.

Consents are distinct domain records and must retain the version of the text/policy under which consent was provided.

The application must support minors and guardian relationships without making assumptions about legal validity. Legal retention periods and consent requirements must be configurable/approved according to applicable policy and law.

## 11. Finance

Finance is a separate domain. At minimum it supports accounts, payments, expenses, event budgets, and event expenses.

The system must distinguish club-level financial transactions from participant-level obligations/costs.

Financial records are auditable and must not be silently overwritten.

## 12. Equipment

Equipment records contain inventory identity, state, storage location, lifecycle status, and acquisition metadata where applicable.

Issue/return records preserve history and link equipment use to people and events. Current inventory state must be derivable from authoritative issue/return operations.

## 13. Notifications and communications

Email, Telegram, and MAX are delivery channels of one notification subsystem.

Notification generation is separated from channel delivery. Channel failures must not corrupt the underlying business event.

The system must support user preferences, club defaults, templates, delivery status, retries, idempotency, and audit.

## 14. Knowledge base

Knowledge articles are versioned and publishable. They may be tagged and linked to skills, events, trips, equipment, and other relevant domains.

The knowledge base is an operational content domain, not arbitrary file storage.

## 15. API contract

The API is versioned under `/api/v1`.

All protected endpoints require authenticated context and server-side authorization.

Resources use stable resource-oriented URLs. POST creates resources or commands where creation semantics are appropriate; PUT replaces complete representations when supported; PATCH performs partial updates; DELETE is used only where destruction semantics are approved.

List endpoints must define pagination, filtering, sorting, and stable ordering rules.

Errors use one consistent JSON error envelope with a machine-readable error code, human-readable message, optional details, and request/correlation identifier.

Date/time values use ISO 8601. Absolute timestamps are stored with unambiguous timezone semantics. UI may display localized values.

Long-running operations must return a trackable operation/job representation rather than holding a request indefinitely.

Idempotency must be supported for operations where retries could otherwise duplicate effects.

The public API contract must be represented by generated/validated OpenAPI documentation.

## 16. Frontend information architecture

The interface must expose role-appropriate navigation. The main functional areas are:

- Dashboard;
- Calendar / Events;
- Members;
- Groups;
- Trips;
- Achievements / Skills;
- Knowledge Base;
- Documents;
- Equipment;
- Finance;
- Notifications;
- Reports;
- Settings;
- Audit (administrative users).

The UI must hide inaccessible actions, but hidden UI must never be treated as the security mechanism.

Desktop layouts may use multi-column tables and persistent navigation. Mobile layouts must prioritize task completion, cards, condensed filters, bottom/compact navigation, and touch-friendly controls.

Forms and tables use the project design system. Loading, empty, validation-error, permission-denied, conflict, and network-error states are required.

## 17. Security baseline

The system must implement:

- secure password handling;
- session/token expiration and revocation;
- login throttling/rate limiting;
- server-side authorization;
- scope-aware access control;
- secure file download authorization;
- audit logging;
- secrets separation;
- protection against common web/API attack classes;
- safe handling of personal and minor-related data;
- no credentials or secrets in logs/audit.

Security-sensitive actions and permission changes must be auditable.

## 18. Data lifecycle

Entities with historical significance must not be hard-deleted merely for convenience.

Preferred lifecycle states include active, inactive, suspended, archived, cancelled, and other domain-specific states where required.

Deletion/anonymization policies are documented separately and must not break required audit/history relationships.

Derived statistics must be rebuildable from source facts.

## 19. Infrastructure baseline

Initial deployment target:

- 4 vCPU;
- 16 GB RAM;
- 256 GB SSD minimum, 512 GB preferred;
- Linux LTS;
- Docker + Docker Compose;
- one primary application server for the initial stage;
- separate backup destination where possible.

Internal services such as PostgreSQL and Redis must not be directly exposed to the public network.

Production deployment must have health checks, structured logs, monitoring, backup/restore procedures, and a rollback strategy.

## 20. Testing contract

Every implementation task must define automated verification.

Required layers as applicable:

- unit tests;
- integration tests;
- API tests;
- authorization/security tests;
- database/migration tests;
- E2E tests for critical user flows;
- smoke tests after deployment/build;
- regression tests for fixed defects.

A feature is not complete because it compiles; its acceptance criteria and relevant automated tests must pass.

## 21. CI/CD contract

CI must at minimum validate applicable:

- lint;
- formatting;
- type checking;
- unit/integration tests;
- build;
- migration consistency;
- API/OpenAPI consistency where applicable.

Deployment must be traceable to a Git revision and support rollback.

## 22. Traceability

Requirements use stable identifiers, for example `FR-*` for functional requirements and `NFR-*` for non-functional requirements.

Every implementation Issue must reference:

1. relevant functional/non-functional requirements;
2. affected domain/module;
3. relevant data entities;
4. relevant permissions;
5. API/UI impact;
6. automated verification;
7. documentation updates.

A requirement with no implementation/test trace is considered untracked.

## 23. Definition of Done

An implementation Issue may be closed only when:

- acceptance criteria are satisfied;
- automated tests relevant to the change pass;
- CI passes;
- security/permissions are verified where relevant;
- migrations are valid where relevant;
- documentation and API/schema contracts are updated;
- no known regression has been introduced;
- the result is reviewed against the corresponding specification.

## 24. Open decisions

The authoritative list of unresolved decisions is maintained in the open-decision register and ADR directory.

Claude must not invent policy or architecture for unresolved items. When an implementation depends on an open decision, the task must either remain blocked or include the decision as an explicit prerequisite.

## 25. Source-of-truth hierarchy

When resolving documentation questions, use this order:

1. approved business decision from the project owner;
2. normative detailed domain/module specification;
3. approved ADR;
4. this system specification;
5. general implementation notes.

A lower-level implementation must never silently override a higher-level requirement.

## 26. Change control

Changes to domain entities, permissions, API contracts, lifecycle rules, persistence semantics, external integrations, or security boundaries require documentation updates before or together with implementation.

Architectural changes require ADR update/new ADR.

Requirements changes should be reflected in the relevant requirement IDs and affected Issues before implementation proceeds.

## 27. Implementation handoff rule for Claude

Claude receives implementation work as GitHub Issues generated from this specification set. Each Issue must be self-contained enough to implement without guessing.

When an Issue references an ambiguous term, missing permission, undefined field, or unresolved decision, implementation must stop at that ambiguity rather than silently creating a new interpretation.
