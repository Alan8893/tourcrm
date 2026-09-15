# TourCRM UI Assets — Production Status

## Current stage

The project is at the **documentation / production asset hand-off** stage. Application integration is a separate later task.

### Product audience and visual direction

TourCRM is primarily used by school-age users from grades 5–10, with club leaders, instructors and parents as important users as well. The interface should remain calm and friendly while allowing a warmer, cute/lightly playful character in appropriate places.

Empty states, onboarding and supportive system feedback may be especially friendly or cute. Operational controls and semantic status indicators must remain immediately understandable.

### Source locations

- Human-readable documentation: `docs/06-ui/assets/`
- Binary hand-off packages: `docs/06-ui/assets/packages/`
- Future application copies: `assets/ui/`

## Production format policy

### Icons

- Format: transparent WebP
- Sizes: `16`, `20`, `24`, `32`, `48`, `64` px
- SVG: **not used**
- **Every production UI icon must be bespoke TourCRM artwork.**
- Generic icon-library replacements, stock icon packs and default/canonical UI glyphs are not allowed without an explicit design decision.
- Existing approved custom artwork is the visual reference for new artwork in the same family.
- Visual uniqueness must never make the semantic meaning ambiguous.

### Brand master

The official TourCRM / «Вектор» logo is handed off as **one PNG master**:

```text
docs/06-ui/assets/packages/brand/logo.png
```

This is the single source of truth for the brand mark. No SVG, ZIP archive or generated logo-variant set is part of this stage. Future technical derivatives, if needed, must be created only for a concrete approved product need and derived from this master.

### Illustrations

- Format: PNG/WebP
- Responsive variants may be `desktop`, `tablet`, `mobile`
- Illustrations are full artwork/scenes, not simplified icons
- Empty-state illustrations may use a cute/lightly playful treatment when it supports the product personality and remains semantically clear.

## Production status

| Area | Status | Production package |
|---|---|---|
| Brand | **Master uploaded** | `packages/brand/logo.png` |
| Navigation | **CLOSED / APPROVED** | `packages/navigation/TourCRM_Visual_Assets_v1.0_FINAL.zip` |
| Actions | **CLOSED / APPROVED** | `packages/actions/TourCRM_Assets_Actions_P0_FINAL.zip` |
| Status | **Approved / uploaded** | `packages/status/TourCRM_Assets_Status_P0_FINAL.zip` |
| Domain | **Approved / uploaded** | `packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip` |
| Achievements P0 | **Approved / uploaded** | `packages/achievements/TourCRM_Assets_Achievements_P0_FINAL.zip` |
| Achievements P1 | **Approved / uploaded** | `packages/achievements/TourCRM_Assets_Achievements_P1_FINAL.zip` |
| Illustrations — Empty States | **Approved / uploaded** | `packages/illustrations/TourCRM_Assets_Illustrations_EmptyStates_P0_FINAL.zip` |
| Illustrations — System | **Approved / uploaded** | `packages/illustrations/TourCRM_Assets_Illustrations_System_P0_FINAL.zip` |
| Illustrations — Onboarding | **Approved / uploaded** | `packages/illustrations/TourCRM_Assets_Illustrations_Onboarding_P0_FINAL.zip` |
| Decorative | Deferred / P2 | — |

## Approved Navigation

Seven fixed navigation icons:

`Home`, `People`, `Groups`, `Events`, `Achievements`, `Reports`, `Settings`.

Stable IDs:

```text
icon.navigation.home
icon.navigation.people
icon.navigation.groups
icon.navigation.events
icon.navigation.achievements
icon.navigation.reports
icon.navigation.settings
```

Production output: 7 × 6 = **42 WebP assets**.

`Settings` is the approved compass + wrench artwork. The visual design is closed and must not be reopened as a generic settings/gear icon.

## Approved Actions — CLOSED

The Actions visual system is fully agreed and closed for this asset-production stage.

17 actions:

```text
add edit delete archive restore search filter sort save cancel confirm close back forward more download upload
```

Production output: 17 × 6 = **102 WebP assets**.

The approved action artwork is the source of truth. Do not substitute a library icon because the action appears elsewhere in the UI.

Any change to the Actions catalog, artwork or semantics requires an explicit Product Owner decision and a separate documented change.

## Approved Status

9 statuses:

```text
planned ongoing completed ended archived success warning error info
```

Production output: 9 × 6 = **54 WebP assets**.

`ended` uses the approved extinguished-campfire metaphor. Do not introduce additional status meanings in this batch.

## Approved Achievements

P0 rarity/state set:

```text
common uncommon rare epic legendary
earned locked progress
```

P1 artwork:

```text
explorer peak-reacher veteran-tourist team-player camp-master
navigator first-expedition trail-walker community-hero adventure-leader
```

## Approved Illustrations

Empty states:

```text
empty-groups empty-people empty-events empty-achievements empty-search no-results
```

System:

```text
success error 404 403 maintenance
```

Onboarding:

```text
welcome first-group first-event first-achievement
```

Empty-state illustrations are an approved place for the product's friendly/cute character, but each illustration must correspond to its actual semantic state.

## Domain

### Production-approved artwork

The Domain P2 visual batch has passed semantic and visual approval and standalone master/size validation for these seven assets:

```text
DOM-006 Membership
DOM-007 Relationship
DOM-008 Instructor assignment
DOM-010 Group members
DOM-011 Group instructor
DOM-012 Primary instructor
DOM-021 Trip / journey / route
```

The package contains 7 masters and 42 WebP exports, 49 files total. DOM-009 remains excluded because its semantic definition is not confirmed.

The Domain package path is:

```text
packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip
```

The binary ZIP is physically present in the repository and the package is now **Approved / uploaded**.

### Domain semantic rules

- Domain assets are semantic UI icons, not illustrations.
- `DOM-010 Group members` must remain distinct from Navigation `Groups`.
- `DOM-011 Group instructor` uses instructor + group context.
- `DOM-012 Primary instructor` uses the approved star marker to distinguish the primary/responsible instructor.
- Domain status mapping must be semantic and must not assume `domain status == asset name`.
- `cancelled` is not automatically `error`.
- No artwork is created for an undefined semantic ID; DOM-009 remains a GAP.

Do not copy Domain assets into `assets/ui/` during this documentation hand-off. Application integration remains a separate stage.

## Lifecycle gate

A package becomes **Approved / uploaded** only after:

1. concept approval;
2. standalone production asset creation;
3. visual approval of production artwork;
4. format/size/naming validation;
5. upload to the designated package directory;
6. documentation and manifest update.

For Domain P2, all six steps are complete. For Brand P1, the deliverable is the approved official master itself; derivative exports are intentionally deferred.

Concept sheets and crops are never the application source.

## Change control

When replacing a production package, update all of the following in the same logical change:

- `ASSET-PACKS.md`
- `ASSET-STATUS.md`
- `asset-manifest.json`
- affected package README
- PR description

Never silently revive a superseded archive.
