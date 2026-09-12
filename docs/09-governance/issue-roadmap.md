# TourCRM — Implementation Roadmap and Issue Hierarchy

## 1. Purpose

This document converts the approved system specification into a controlled implementation backlog for Claude.

Claude is the implementation agent. This document does not authorize autonomous product decisions that are absent from the specification.

## 2. Delivery principle

Implementation proceeds through GitHub Issues in dependency order.

For each Issue:

1. Read the linked requirements and specifications.
2. Verify prerequisites are complete.
3. Implement only the documented scope.
4. Add/update automated tests.
5. Run required validation and CI.
6. Report deviations or discovered contradictions.
7. Do not silently change business rules or architecture.
8. Close the Issue only when Acceptance Criteria and Definition of Done are satisfied.

## 3. Issue hierarchy

### EPIC-00 — Foundation and project governance

Purpose: make the repository and development contract executable.

Candidate Issues:

- establish repository structure and application skeleton;
- configure development environment;
- configure code quality and testing standards;
- establish CI baseline;
- establish migrations and database bootstrap;
- establish configuration and secret management;
- establish logging and health endpoints.

### EPIC-01 — Identity, users and club membership

- bootstrap administrator;
- Person/User model;
- registration;
- approval workflow;
- login/logout/session lifecycle;
- password recovery;
- invitations;
- membership lifecycle;
- roles and permissions;
- guardian relationships;
- groups and group history;
- participant profile;
- audit events for identity changes;
- import participants.

### EPIC-02 — Events, schedule and attendance

- Event model;
- event types;
- event series;
- occurrences;
- calendar;
- creation/editing/cancellation;
- registration;
- participation;
- attendance;
- absence reasons;
- recurring schedule exceptions;
- notifications for schedule changes;
- instructor workload views.

### EPIC-03 — Tourism and trips

- Trip model;
- TripParticipant;
- tourism types;
- classification reference data;
- routes;
- route points;
- GPX upload/validation;
- trip results;
- tourist profile;
- experience aggregation;
- portfolio;
- qualification evidence.

### EPIC-04 — Achievements, skills and rating

- achievement catalog;
- manual awards;
- automatic rules;
- skills;
- qualifications;
- configurable rating engine;
- rating visibility;
- feature setting integration.

### EPIC-05 — Documents, consents and medical data

- document metadata;
- object storage integration;
- consent records;
- document expiration;
- medical profile according to approved policy;
- restricted access views;
- export/masking rules;
- document audit.

### EPIC-06 — Finance

- financial accounts;
- charges/receivables;
- payments;
- allocation;
- expenses;
- event budgets;
- event expenses;
- participant balances;
- financial reporting;
- audit.

### EPIC-07 — Equipment

- equipment catalog;
- inventory identifiers;
- equipment state;
- storage locations;
- issue/return;
- event allocations;
- maintenance/repair;
- history and audit.

### EPIC-08 — Notifications and communications

- notification model;
- templates;
- preferences;
- email channel;
- Telegram channel;
- MAX channel;
- retry and delivery status;
- scheduled reminders;
- club announcements;
- internal notifications.

### EPIC-09 — Knowledge base

- article model;
- categories and tags;
- draft/review/publish workflow;
- versions;
- attachments;
- search;
- links to events, skills, trips and equipment.

### EPIC-10 — Search and analytics

- global search;
- permission-aware search;
- member/event/document/knowledge search;
- dashboard foundation;
- metric catalog;
- analytics queries;
- exports and reports.

### EPIC-11 — Import/export and document generation

- import preview;
- CSV import/export;
- XLSX import/export;
- PDF generation;
- DOCX generation;
- duplicate detection;
- validation errors;
- rollback semantics;
- audit trail.

### EPIC-12 — Operations and production readiness

- operational admin;
- backup automation;
- restore verification;
- monitoring;
- alerting;
- log retention/redaction;
- disaster recovery runbook;
- maintenance mode;
- release/rollback procedure.

### EPIC-13 — TourSlet integration

Blocked until the existing ZIP archive is provided and analyzed.

- source system assessment;
- identity mapping;
- event mapping;
- participant mapping;
- results mapping;
- integration architecture;
- API/event contract;
- SSO feasibility;
- synchronization;
- error/reconciliation flow.

### EPIC-14 — Calendar integrations

Only after internal calendar is stable.

- external calendar provider selection;
- outbound event feed;
- synchronization;
- update/cancellation semantics;
- access and revocation.

## 4. Dependency order

Recommended first implementation sequence:

`EPIC-00 → EPIC-01 → EPIC-02 → EPIC-03`

Then:

`EPIC-04 + EPIC-05 + EPIC-06 + EPIC-07 + EPIC-08 + EPIC-09`

Then:

`EPIC-10 + EPIC-11 + EPIC-12`

And separately, once the archive is available:

`EPIC-13`

`EPIC-14` follows stabilization of the internal calendar.

## 5. Blockers and gates

No Issue may depend on an unresolved Open Decision Record unless the Issue explicitly resolves that decision first.

Known gates:

- medical implementation requires approved medical data policy;
- automatic tourist experience requires approved tourism rules;
- rating requires approved rating formula;
- TourSlet integration requires source archive analysis;
- production launch requires verified backup/restore, observability and security baseline;
- calendar provider integration requires internal calendar contract.

## 6. Traceability requirement

Every implementation Issue must reference:

- Product/functional requirement IDs;
- applicable business rules;
- domain specification;
- database contract;
- API contract;
- UX specification where relevant;
- permissions;
- test expectations;
- relevant ADRs and ODRs.

## 7. Definition of Ready

An Issue is Ready for Claude only when:

- scope is bounded;
- prerequisites are identified;
- business rules are resolved;
- affected entities are known;
- API/UI behavior is specified when applicable;
- acceptance criteria are testable;
- unresolved dependencies are explicit.

## 8. Definition of Done

An Issue is Done only when:

- implementation matches the specification;
- required automated tests exist and pass;
- regression checks pass;
- documentation is updated;
- migrations are safe and reversible where required;
- security/permission behavior is verified;
- CI is green;
- no undocumented architectural deviation remains.

## 9. Change control

If implementation reveals a contradiction:

1. stop the affected scope;
2. identify the conflicting documents;
3. record the issue as an ODR or documentation defect;
4. resolve it at the specification level;
5. update affected documents and traceability;
6. resume implementation.

Claude must not resolve material product ambiguity by silently choosing an implementation.
