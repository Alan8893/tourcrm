# TourCRM — Roadmap

## Purpose

Дорожная карта определяет порядок превращения утверждённой спецификации в реализованный продукт. Она не заменяет GitHub Issues: каждая реализация выполняется отдельной задачей с трассировкой требований и автоматической проверкой.

## Delivery principles

- Зависимости реализуются раньше зависимых функций.
- Security и модель данных формируются раньше бизнес-модулей.
- Работа ведётся последовательно через GitHub Issues в согласованном с владельцем проекта порядке.
- Issue закрывается только после реализации, автоматической проверки, review против спецификации и обновления документации.
- Большие домены дробятся на небольшие implementation contracts.

## Phase 0 — Specification Foundation

Цель: получить согласованный источник истины для реализации.

Deliverables:

- vision и scope;
- glossary;
- project/system blueprint;
- functional requirements;
- business rules;
- non-functional requirements;
- use cases;
- roles and permissions;
- domain model;
- database specification;
- application architecture;
- infrastructure architecture;
- API conventions и endpoint inventory;
- core API specifications;
- UX information architecture;
- design system;
- security/privacy specification;
- ADR set;
- documentation map;
- implementation Issue template;
- consolidated system specification.

Exit criteria: критические архитектурные зависимости описаны, открытые решения зарегистрированы, термины согласованы, проект можно однозначно декомпозировать на Issues.

## Phase 1 — Platform Foundation

- application/repository skeleton;
- development environment;
- configuration and secrets;
- database connection and migrations;
- API framework and common error handling;
- frontend shell/routing;
- authentication/session foundation;
- authorization engine;
- audit foundation;
- CI baseline;
- health/readiness checks.

## Phase 2 — Identity & Club Core

- User;
- Person;
- Club;
- ClubMembership;
- RoleAssignment;
- registration and approval;
- invitations;
- member import;
- profiles;
- guardian relationships;
- groups;
- sensitive-change audit.

## Phase 3 — Events & Education

- Event;
- EventSeries/EventOccurrence;
- event types;
- calendar;
- recurring schedule;
- registrations;
- instructors/leaders;
- attendance;
- absence reasons;
- cancellation/rescheduling;
- event notifications;
- basic event analytics.

## Phase 4 — Trips & Tourism

- trips;
- trip participation;
- tourism types;
- routes;
- route points;
- GPX/geodata;
- planned/actual distance;
- tourism experience calculations;
- tourist profile;
- portfolio;
- official categories/qualification rules after approval.

## Phase 5 — Achievements & Knowledge

- skills;
- qualifications;
- achievements;
- manual and automatic awards;
- levels/rating, subject to feature settings;
- knowledge base;
- article versions;
- tagging and search;
- educational links between materials, skills, and events.

## Phase 6 — Documents & Finance

- document storage/metadata;
- consents;
- expiry tracking;
- document templates;
- PDF/Excel/CSV/Word exports where approved;
- financial accounts;
- payments;
- participant obligations;
- expenses;
- budgets;
- financial reports.

## Phase 7 — Equipment & Communications

- inventory;
- equipment lifecycle;
- issue/return;
- maintenance/repair;
- notification engine;
- user notification preferences;
- email;
- Telegram;
- MAX;
- internal announcements;
- calendar integrations.

## Phase 8 — TourSlet Integration

Starts only after the existing ZIP archive is technically analysed.

Possible implementation outcomes:

- API adapter;
- shared identity/SSO;
- event/result synchronization;
- participant synchronization;
- deep links;
- import/export adapter;
- other boundary chosen from evidence.

No concrete external contract is assumed before archive analysis.

## Phase 9 — Analytics & Operations

- dashboards;
- attendance analytics;
- participation analytics;
- tourism statistics;
- achievement statistics;
- finance/inventory reports;
- instructor load;
- participant dynamics;
- exports;
- operational dashboards.

## Phase 10 — Production Hardening

- backup/restore drills;
- monitoring and alerting;
- performance profiling;
- security hardening;
- disaster recovery verification;
- rollback procedures;
- operational runbooks;
- final documentation audit.

## MVP baseline

MVP должен обеспечить устойчивое ежедневное управление клубом:

- authentication;
- users/roles;
- members and guardians;
- groups;
- events/calendar;
- attendance;
- basic profiles;
- audit;
- baseline notifications;
- responsive mobile-capable UI;
- secure deployment baseline.

Advanced tourism, achievements, knowledge, documents, finance, equipment, external integrations and advanced analytics are staged after the core is stable unless project priorities explicitly change.

## Roadmap governance

Бизнес-приоритеты определяет владелец проекта. Техническую последовательность и зависимости определяет технический аналитик. Изменение порядка фаз должно быть отражено в соответствующей Issue/ADR.
