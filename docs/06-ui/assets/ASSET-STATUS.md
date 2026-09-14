# TourCRM UI Assets — Production Status

This document is the hand-off contract between the UI asset work and the main development workflow.

## Source of truth

Production assets live under `assets/ui/`. Documentation and manifest files under `docs/06-ui/assets/` describe their semantic role and approved usage.

## Current production batch

| Area | Status | Scope |
|---|---|---|
| Brand | approved concept | production files to be added in this PR series |
| Navigation | approved | production set follows approved Navigation Icons v1 |
| Actions | approved | production set follows approved Actions Icons v1 |
| Status | approved | production set follows approved Status Icons v1 |
| Domain | approved | production set follows approved Domain Icons v1 |
| Achievements | approved concept | artwork production in progress |
| Empty states | production | P0 set starts with six illustrations |
| System states | planned | next P1 batch |
| Onboarding | planned | following system states |
| Decorative | deferred | P2 |

## Rules

- `assets/ui/**` is the only production asset source for application integration.
- Concept boards are references only and must not be imported by the application.
- Every approved production asset receives a stable manifest ID.
- Existing product/domain semantics are not changed by visual assets.
- Do not replace an approved custom asset with a generic icon-library equivalent without an explicit design decision.
