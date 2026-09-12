# TourCRM — Documentation Map

## 1. Purpose

This document is the navigation map for the project specification. Claude and human contributors should use it to find the authoritative document for a question before making an implementation decision.

## 2. Source-of-truth hierarchy

1. Product owner business decision.
2. Normative detailed requirement/module specification.
3. Approved ADR.
4. `docs/SYSTEM-SPECIFICATION.md`.
5. General implementation notes.

When documents conflict, do not guess. Record the conflict and resolve it explicitly.

## 3. Product and requirements

| Area | Primary document | Purpose |
|---|---|---|
| Vision | `01-product/vision.md` | Why TourCRM exists |
| Blueprint | `01-product/project-blueprint.md` | Product/system overview |
| Scope/phases | `01-product/scope-and-phases.md` | Delivery boundaries |
| Glossary | `01-product/glossary.md` | Canonical terminology |
| Functional requirements | `02-requirements/functional-requirements.md` | FR requirements |
| Business rules | `02-requirements/business-rules.md` | Domain rules |
| Non-functional requirements | `02-requirements/non-functional-requirements.md` | Quality/operational requirements |
| Roles/permissions | `02-requirements/roles-and-permissions.md` | Authorization matrix |
| Use cases | `02-requirements/use-cases.md` | User scenarios |
| Scope | `02-requirements/scope.md` | Functional scope |

## 4. Architecture

| Area | Primary document |
|---|---|
| System specification | `SYSTEM-SPECIFICATION.md` |
| Architecture overview | `03-architecture/overview.md` |
| Application architecture | `03-architecture/application-architecture.md` |
| Domain model | `03-architecture/domain-model.md` |
| Logical data model | `03-architecture/data-model.md` |
| Database schema | `03-architecture/database-schema.md` |
| Authentication persistence | `03-architecture/authentication-persistence.md` |
| Infrastructure | `03-architecture/infrastructure.md` |
| Technology stack | `03-architecture/technology-stack.md` |
| Data retention | `03-architecture/data-retention-and-deletion.md` |

## 5. Architecture Decision Records

Canonical ADR location: `03-architecture/adr/`.

ADR directory contains accepted architecture decisions and the explicit open-decision register.

Before changing a decision, check for an existing ADR. Superseded decisions must remain historically visible and be replaced by a new ADR rather than silently edited into a different decision.

## 6. Domain modules

| Domain | Primary document |
|---|---|
| People and membership | `04-modules/people-and-membership.md` |
| Events and schedule | `04-modules/events-and-schedule.md` |
| Trips and tourist profile | `04-modules/trips-and-tourist-profile.md` |
| Achievements/skills/rating | `04-modules/achievements-skills-and-rating.md` |
| Documents and consents | `04-modules/documents-and-consents.md` |
| Finance | `04-modules/finance.md` |
| Equipment | `04-modules/equipment.md` |
| Notifications/communications | `04-modules/notifications-and-communications.md` |
| Knowledge base | `04-modules/knowledge-base.md` |
| TourSlet integration | `04-modules/tourslet-integration.md` |

## 7. API

| Area | Primary document |
|---|---|
| API conventions | `05-api/api-contract.md` |
| Endpoint inventory | `05-api/endpoint-inventory.md` |
| Authentication/authorization API | `05-api/auth-api.md` |
| People API | `05-api/people-api.md` |
| Events API | `05-api/events-api.md` |
| Trips/tourist profile API | `05-api/trips-and-tourist-profile-api.md` |
| Auth/access architecture | `05-api/auth-and-authorization.md` |

## 8. UI

| Area | Primary document |
|---|---|
| Information architecture | `06-ui/ux-specification.md` |
| Design system | `06-ui/design-system.md` |

## 9. Security and operations

| Area | Primary document |
|---|---|
| Security/privacy | `07-security/security-and-privacy.md` |
| Infrastructure/DevOps | `08-infrastructure/infrastructure-and-devops.md` |

## 10. Requirement-to-implementation trace

Every implementation issue should carry a trace section with:

- Requirement IDs (`FR-*`, `NFR-*`);
- Business rule IDs where applicable;
- Domain document;
- Data entities;
- API endpoint(s);
- UI screens/components;
- Required permissions/scopes;
- Automated test expectations;
- Relevant ADRs;
- Documentation files to update.

## 11. Implementation rule

Claude must read the relevant documents from this map before implementation. A GitHub Issue is allowed to narrow scope, but it must not silently contradict a higher-level normative document.

If a specification gap blocks implementation, create or update an open decision instead of inventing a local rule.

## 12. Legacy documentation

The former `docs/02-architecture/technology-stack.md` and `docs/02-architecture/overview.md` paths were legacy locations and have been canonicalized under `docs/03-architecture/`. Current documentation must use the `docs/03-architecture/` paths.
