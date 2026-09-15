# TourCRM UI Asset Integration Handoff

## Purpose

This document defines the next stage after production asset hand-off: integrating the approved TourCRM visual assets into the application UI.

This is an integration contract, not a new visual-design phase.

## Source of truth

Use only:

- `docs/06-ui/assets/README.md`
- `docs/06-ui/assets/ASSET-PACKS.md`
- `docs/06-ui/assets/ASSET-STATUS.md`
- `docs/06-ui/assets/asset-manifest.json`
- approved production packages under `docs/06-ui/assets/packages/`

The official Brand master is:

```text
docs/06-ui/assets/packages/brand/logo.png
```

## Integration destination

Approved assets may be copied/installed into the application integration tree:

```text
assets/ui/
```

The documentation/package tree remains the design-system source of truth.

## Rules

### 1. Use approved assets only

Do not recreate, redraw, replace or approximate approved artwork during integration.

Do not substitute generic icon-library or stock icons.

Do not use concept sheets or crops as application assets.

### 2. Preserve stable semantics

Keep the semantic IDs and category boundaries from `asset-manifest.json`.

In particular:

- Navigation `Groups` is not interchangeable with Domain group-membership artwork.
- Status icons are not interchangeable with System illustrations.
- Achievement P0 rarity/state assets are not interchangeable with Achievement P1 named achievement artwork.
- Illustrations are full artwork/scenes, not icon substitutes.

### 3. Preserve the visual language

Integration must preserve the approved visual foundation:

- light, friendly and modern tourism-oriented interface
- nature/green tones balanced with the official purple/pink/yellow brand language
- purple as an accent, not the dominant UI color
- soft rounded surfaces and generous whitespace
- bespoke TourCRM artwork
- clear semantics and readable operational UI

### 4. Format policy

For production UI icons:

- WebP only
- available sizes: `16/20/24/32/48/64`
- SVG is not used

For illustrations:

- PNG/WebP
- use the appropriate responsive composition where provided

For Brand:

- use the single official PNG master
- do not introduce generated logo variants unless a concrete product requirement is separately approved

### 5. Closed decisions remain closed

The following packages are closed/approved and must not be redesigned as part of integration:

- Navigation
- Actions
- Status
- Domain
- Achievements P0/P1
- Illustrations Empty States
- Illustrations System
- Illustrations Onboarding

Decorative remains deferred P2 and must not be introduced during this integration stage unless the Product Owner explicitly starts that scope.

DOM-009 remains a semantic GAP and must not receive an invented replacement.

## Recommended integration order

1. Brand master
2. Navigation
3. Actions
4. Status
5. Domain
6. Achievements
7. Illustrations

Decorative is excluded.

## Acceptance checklist

Before integration is considered complete:

- [ ] all referenced assets come from approved production packages
- [ ] no SVG production UI assets were introduced
- [ ] no generic icon-library replacements were introduced
- [ ] semantic IDs remain traceable to `asset-manifest.json`
- [ ] Navigation contains exactly the approved seven items
- [ ] Brand uses the official master
- [ ] status meanings remain unchanged
- [ ] Domain and Navigation semantics remain distinct
- [ ] responsive illustration variants are used where applicable
- [ ] Decorative P2 assets remain absent
- [ ] DOM-009 remains absent until semantics are explicitly approved
- [ ] application UI has been visually checked on desktop, tablet and smartphone layouts

## Change control

If an integration task reveals a genuine visual or semantic problem, do not silently modify the production package.

Instead:

1. record the problem;
2. identify the affected stable ID/package;
3. describe the required change;
4. obtain an explicit Product Owner decision;
5. make the change as a separate documented asset revision.

This prevents application integration from reopening the already approved visual foundation.
