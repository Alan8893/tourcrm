# TourCRM — Requirements Traceability Matrix

## 1. Purpose

This matrix defines how requirements are traced through the project lifecycle.

Canonical chain:

`Business Goal → FR/NFR → Business Rule → Domain → Data Model → API → UI → Permission → Test → GitHub Issue`

Every implementation feature must be traceable through this chain where the artifact type applies.

## 2. Master domain mapping

| Domain | Primary requirements | Domain contract | Data contract | API contract | UX contract | Main Epic |
|---|---|---|---|---|---|---|
| Identity | FR-AUTH, FR-USER | People & Membership | Database schema | Auth + People API | UX | EPIC-01 |
| Profile Avatar | TH-0119 | Person identity/profile | `Person.photo_file_id` + FileStorage | Profile Photo API | Avatar Photo UX | EPIC-01 |
| Guardians | FR-GUARD | People & Membership | Database schema | People API | UX | EPIC-01 |
| Groups | FR-GROUP | People & Membership | Database schema | People API | UX | EPIC-01 |
| Events | FR-EVENT | Events & Schedule | Database schema | Events API | UX | EPIC-02 |
| Attendance | FR-ATT | Events & Schedule | Database schema | Events API | UX | EPIC-02 |
| Tourism | FR-TRIP | Trips & Tourist Profile | Database schema | Trips API | UX | EPIC-03 |
| Achievements | FR-ACH | Achievements & Skills | Database schema | Trips/Tourist API | UX | EPIC-04 |
| Documents | FR-DOC | Documents & Consents | Database schema | API conventions | UX | EPIC-05 |
| Medical | FR-MED | Medical Data | Database schema | Security/Auth API | UX | EPIC-05 |
| Finance | FR-FIN | Finance | Database schema | API conventions | UX | EPIC-06 |
| Equipment | FR-EQ | Equipment | Database schema | API conventions | UX | EPIC-07 |
| Notifications | FR-NOTIFY | Notifications & Communications | Database schema | API conventions | UX | EPIC-08 |
| Knowledge | FR-KB | Knowledge Base | Database schema | API conventions | UX | EPIC-09 |
| Search | FR-SEARCH | Search specification | Database/search design | API conventions | UX | EPIC-10 |
| Analytics | FR-ANALYTICS | Metric catalog | Analytics model | API conventions | UX | EPIC-10 |
| Import/Export | FR-IO | Import/export contract | Database schema | API conventions | UX | EPIC-11 |
| Operations | NFR-OPS | Infrastructure/Operations | Infrastructure | API health/admin | Admin UX | EPIC-12 |
| TourSlet | FR-TS | TourSlet integration | Integration mapping | Integration contract after archive | UX | EPIC-13 |
| Calendars | FR-CAL | Calendar integration | Event model | Integration contract | UX | EPIC-14 |

## 3. Requirement status categories

- **Specified** — requirement is documented sufficiently for detailed design.
- **Ready** — all dependencies are resolved and an implementation Issue may be created.
- **Blocked** — implementation depends on an Open Decision or missing input.
- **Implemented** — implementation exists and passed required tests.
- **Verified** — implementation has been accepted against the Issue contract.

## 4. Test traceability

Tests must reference the requirement or behavior they verify whenever practical.

Minimum categories:

- unit test;
- integration test;
- API/contract test;
- permission/security test;
- migration test;
- E2E test;
- smoke test;
- regression test.

Critical security and business rules must have deterministic automated coverage.

## 5. Change impact analysis

When a requirement changes, impact analysis must consider:

1. business rules;
2. domain/data model;
3. database migrations;
4. API contract;
5. UI/UX;
6. permissions;
7. notifications and side effects;
8. analytics/derived data;
9. tests;
10. related Issues and ADRs.

## 6. Traceability rule for Claude

Claude must not implement a material feature from a prose request alone when a canonical requirement or domain contract should exist.

The Issue must provide the authoritative references.

If an implementation discovers that two authoritative documents disagree, Claude must stop the affected work and report the conflict rather than silently selecting one interpretation.

## 7. Acceptance rule

An implementation is considered complete only when the Issue can be traced to:

`Requirement → Rules → Design → Implementation → Automated Test → CI Result`

and all relevant links are documented in the Issue or its completion report.
