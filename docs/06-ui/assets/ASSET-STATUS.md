# TourCRM UI Assets — Production Status

This document defines the documentation-side asset hand-off for the TourCRM UI visual system.

## Asset lifecycle

The UI asset workflow is intentionally separated from application implementation:

1. visual concept
2. visual approval
3. Asset Catalog / production specification
4. production asset creation
5. visual approval of the production asset
6. documentation hand-off
7. application integration

The current work is at the **documentation / production asset hand-off** stage. It must not be treated as frontend implementation work.

## Documentation source of truth

Approved visual assets are documented under `docs/06-ui/assets/` together with their stable manifest IDs, dimensions, formats and usage rules.

Application-facing copies will later be placed under `assets/ui/` as part of a separate implementation/integration task. The frontend must consume those approved assets rather than recreate or substitute them.

## Production format decision

### UI icons

The approved TourCRM navigation artwork is delivered as transparent WebP where raster output preserves the agreed visual character better than SVG. SVG must not be used as a reason to simplify or flatten the approved artwork.

Approved navigation export sizes:

- 16 px
- 20 px
- 24 px
- 32 px
- 48 px
- 64 px

### Illustrations

Illustrations are treated as full TourCRM artwork, not as simplified UI icons. Production illustrations use PNG/WebP and may have separate responsive `desktop`, `tablet`, and `mobile` compositions where the layout benefits from separate compositions.

## Current production batch

| Area | Status | Documentation stage |
|---|---|---|
| Brand | approved concept | production hand-off pending |
| Navigation | **approved production artwork** | P0 WebP pack documented |
| Actions | approved | production hand-off pending |
| Status | approved | production hand-off pending |
| Domain | approved | production hand-off pending |
| Achievements | approved concept | artwork production in progress |
| Empty states | reset to concept | production artwork must follow approved TourCRM illustration style |
| System states | planned | next P1 batch |
| Onboarding | planned | following system states |
| Decorative | deferred | P2 |

## P0 Navigation WebP pack

Stable IDs:

- `icon.navigation.home`
- `icon.navigation.people`
- `icon.navigation.groups`
- `icon.navigation.events`
- `icon.navigation.achievements`
- `icon.navigation.reports`
- `icon.navigation.settings`

The approved pack contains 7 navigation icons × 6 production sizes = **42 WebP assets**.

The fixed navigation set is:

- Home
- People
- Groups
- Events
- Achievements
- Reports
- Settings

The visual design of these navigation icons is approved and must not be reopened as a concept exercise.

## State handling

The approved artwork represents the base icon asset. UI states (`default`, `hover`, `active`, `disabled`) are an interaction-layer concern unless a state requires a materially different artwork asset.

Do not create duplicate state files merely to encode CSS/UI state when the approved artwork itself does not change.

## Repository separation

### Documentation / design system

`docs/06-ui/assets/`

Contains:

- asset specifications;
- stable IDs;
- approved dimensions and formats;
- asset catalog and status;
- production hand-off packages where appropriate.

### Application implementation

`assets/ui/`

Contains the application-consumable copies of approved production assets. Population of this directory belongs to the frontend integration stage.

### Frontend

Frontend code references the approved production assets and does not replace them with generic icon-library equivalents.

## Rules

- Concept boards are reference material only and must not be imported by the application.
- Every approved production asset receives a stable manifest ID.
- Existing product/domain semantics are not changed by visual assets.
- Do not replace an approved custom asset with a generic icon-library equivalent without an explicit design decision.
- Do not simplify or flatten approved artwork merely to satisfy a preferred file format.
- Every new production asset must pass visual comparison against the approved concept before being marked approved.
- Documentation approval and application integration are separate milestones.
