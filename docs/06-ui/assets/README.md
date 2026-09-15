# TourCRM UI Assets

Production UI asset hand-off and visual design-system source for TourCRM.

## Product visual direction

TourCRM is a calm, friendly product for tourist clubs. The primary audience includes school-age users from grades 5–10; club leaders, instructors and parents are also first-class users.

The visual language should feel welcoming, warm and approachable. Cute/lightly playful artwork is encouraged where it improves the experience, especially for empty states, onboarding and supportive feedback. It must not compromise semantic clarity, readability or confidence in operational actions.

Production UI icons use bespoke TourCRM artwork rather than generic icon-library or stock assets. Existing approved custom artwork is the visual source of truth for new artwork in the same family. Visual distinctiveness is welcome, but semantic meaning always takes priority.

## Source of truth

- Human-readable design-system documentation: `docs/06-ui/assets/`
- Reviewed binary production packages: `docs/06-ui/assets/packages/`
- Machine-readable contract: `docs/06-ui/assets/asset-manifest.json`
- Future application integration: `assets/ui/`

PR #75 is the current asset hand-off vehicle and must remain unmerged while production/review work is in progress.

## Production format

### UI icons

- Raster WebP only
- Sizes: `16`, `20`, `24`, `32`, `48`, `64` px
- SVG is not used
- Every production UI icon is bespoke TourCRM artwork
- Generic icon-library/stock replacements require an explicit design decision

### Illustrations

- PNG/WebP
- Responsive `desktop`, `tablet`, `mobile` compositions where required
- Full artwork/scenes, not icon substitutes
- Friendly/cute treatment is appropriate for empty states, onboarding and supportive feedback when semantically clear

## Asset lifecycle

1. semantic definition
2. visual concept
3. standalone artwork approval
4. production export
5. technical validation
6. repository package upload
7. documentation/manifest synchronization
8. later application integration

Concept sheets and crops are never application source assets. Application integration is a separate stage.

## Current package state

- Brand — **Approved / uploaded**
- Navigation — **Approved / uploaded / closed**
- Actions — **Approved / uploaded / closed**
- Status — **Approved / uploaded / closed**
- Domain P2 — **Approved / uploaded / closed**
- Achievements P0/P1 — **Approved / uploaded / closed**
- Illustrations — **Empty States / System / Onboarding — Approved / uploaded / closed**
- Decorative — **Deferred / P2**

## Brand

The official “Вектор” logo is the brand source of truth.

Production master:

```text
docs/06-ui/assets/packages/brand/logo.png
```

Brand uses one official PNG master only. No SVG, ZIP or artificially generated logo variants are part of the production hand-off. Future technical derivatives, if required, must be derived from this master only after a concrete product need is approved.

The application integration root `assets/ui/brand/` remains untouched in this hand-off.

## Domain P2

Production package:

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip
```

Scope: DOM-006, DOM-007, DOM-008, DOM-010, DOM-011, DOM-012, DOM-021.

Package: 7 standalone PNG masters + 42 transparent WebP exports = 49 files. WebP sizes: `16/20/24/32/48/64` px. DOM-009 remains a documented semantic GAP.

The package is physically present in the repository and is approved/uploaded. Domain assets must remain distinct from Navigation, especially Domain group-membership concepts versus Navigation `Groups`.

## Do not use

- SVG production UI assets
- superseded Navigation `TourCRM_Assets_Navigation_P0.zip`
- generic icon-library substitutions without explicit design decision
- concept sheets as application assets
- historical Domain P0/P1A/P1B archives as final production assets
- the obsolete generated Brand variant archive

See `ASSET-PACKS.md`, `ASSET-STATUS.md` and `asset-manifest.json` for the detailed registry and contract.

## Next stage

The production asset hand-off is complete for all currently approved packages. The next stage is application integration using the approved standalone assets.

Integration must:

- consume only approved production packages and the official Brand master
- keep `assets/ui/` separate from the documentation/package source of truth
- preserve semantic IDs and the approved visual language
- not reopen closed Navigation, Actions, Status, Domain, Achievements or Illustration decisions
- not introduce Decorative P2 assets unless the Product Owner explicitly starts that scope
