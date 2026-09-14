# TourCRM UI Assets — Production Status

This document is the hand-off contract between the UI asset work and the main development workflow.

## Source of truth

Production assets live under `assets/ui/`. Documentation and manifest files under `docs/06-ui/assets/` describe their semantic role and approved usage.

## Production format decision

### UI icons

SVG is the preferred production format, but it is not a reason to simplify the approved visual language. Complex custom iconography may also have PNG/WebP exports when those preserve the approved appearance better at a target size.

### Illustrations

Illustrations are treated as full artwork, not as simplified UI icons. Production illustrations use PNG/WebP and are prepared as responsive `desktop`, `tablet`, and `mobile` compositions where the layout benefits from separate compositions.

A source/master artwork may be retained separately when required for future exports.

## Current production batch

| Area | Status | Scope |
|---|---|---|
| Brand | approved concept | production files to be added in this PR series |
| Navigation | approved | production set follows approved Navigation Icons v1; format validation in progress |
| Actions | approved | production set follows approved Actions Icons v1; format validation in progress |
| Status | approved | production set follows approved Status Icons v1; format validation in progress |
| Domain | approved | production set follows approved Domain Icons v1; format validation in progress |
| Achievements | approved concept | artwork production in progress |
| Empty states | reset to concept | previous generic flat SVG placeholders removed; production artwork must follow approved TourCRM illustration style |
| System states | planned | next P1 batch |
| Onboarding | planned | following system states |
| Decorative | deferred | P2 |

## Rules

- `assets/ui/**` is the production asset source for application integration.
- Concept boards are references only and must not be imported by the application.
- Every approved production asset receives a stable manifest ID.
- Existing product/domain semantics are not changed by visual assets.
- Do not replace an approved custom asset with a generic icon-library equivalent without an explicit design decision.
- Do not simplify or flatten approved artwork merely to satisfy a preferred file format.
- Every new production asset must pass a visual comparison against the approved concept before being marked `production`.
