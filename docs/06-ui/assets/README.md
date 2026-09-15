# TourCRM UI Assets

This directory is the human-readable entry point and design-system source of truth for approved TourCRM visual assets.

## Product visual direction

TourCRM is a calm, friendly product for tourist clubs. The interface is designed primarily for school-age users from approximately grades 5–10, while remaining comfortable and clear for club leaders, instructors and parents.

The visual language may therefore be warm, playful and "cute" where appropriate, especially in empty states, onboarding and supportive system feedback. Playfulness must never reduce semantic clarity or make operational controls ambiguous.

## Start here

1. `ASSET-PACKS.md` — complete registry: catalog, exact package paths, intended usage and future application paths.
2. `ASSET-STATUS.md` — approval/production state and lifecycle gates.
3. `asset-manifest.json` — machine-readable contract of packages, stable IDs, formats, sizes and integration paths.
4. `production-format-decision.md` — production format decision and visual-fidelity rules.
5. `packages/<category>/README.md` — package-specific hand-off instructions.

## Source of truth and repository separation

```text
docs/06-ui/assets/                 documentation + approved hand-off packages
assets/ui/                         future application-consumable copies
```

The two stages are intentionally separate. Frontend integration must consume approved production assets and must not silently recreate or substitute them.

## Production rules

- UI icons are raster WebP.
- SVG is not used for TourCRM production UI assets.
- Icon sizes are `16`, `20`, `24`, `32`, `48`, `64` px.
- Illustrations use PNG/WebP and may have `desktop`, `tablet`, `mobile` compositions.
- Concept boards/sheets are reference material only; use standalone production assets from the approved package.
- **TourCRM UI icons are custom artwork, not generic library assets.** Generic icon-library substitutions are not allowed without an explicit design decision.
- Existing approved custom icons remain the source of truth; a new icon must follow the same bespoke TourCRM visual language rather than introducing a stock/library metaphor.
- Semantic clarity takes priority over decorative novelty. A cute or playful treatment is welcome only when the intended meaning remains immediately understandable.

## Current approved production packages

### Navigation

```text
docs/06-ui/assets/packages/navigation/TourCRM_Visual_Assets_v1.0_FINAL.zip
```

7 icons × 6 sizes = **42 WebP assets**.

### Actions

```text
docs/06-ui/assets/packages/actions/TourCRM_Assets_Actions_P0_FINAL.zip
```

17 icons × 6 sizes = **102 WebP assets**.

### Status

```text
docs/06-ui/assets/packages/status/TourCRM_Assets_Status_P0_FINAL.zip
```

9 icons × 6 sizes = **54 WebP assets**.

### Achievements

```text
docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P0_FINAL.zip
docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P1_FINAL.zip
```

P0 contains rarity/state assets; P1 contains the approved achievement artwork set.

### Illustrations

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_EmptyStates_P0_FINAL.zip
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_System_P0_FINAL.zip
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_Onboarding_P0_FINAL.zip
```

These cover the current approved Empty State, System and Onboarding illustration catalogs.

## Empty states

Empty states are an important part of the TourCRM personality. They may use friendly, cute and lightly playful illustrations because the primary audience includes students in grades 5–10 and club leaders who value a welcoming visual experience.

The artwork must still explain the state clearly. `empty-search` and `no-results` are distinct semantic states and should not be replaced by a generic error graphic.

## Not final

The Domain archives are working/concept material and are explicitly **not production-approved**. Brand has no production package yet. Decorative remains deferred P2.

Do not copy non-approved packages into `assets/ui/` as final application assets.

## Change control

When a production package is replaced, update `ASSET-PACKS.md`, `ASSET-STATUS.md`, `asset-manifest.json`, the affected package README and the PR description together.

**PR #75 is intentionally kept separate from application integration and must not be merged as part of this asset-production step.**
