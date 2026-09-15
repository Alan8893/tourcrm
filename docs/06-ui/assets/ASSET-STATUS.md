# TourCRM UI Assets — Production Status

## Current stage

The project is at the **documentation / production asset hand-off** stage. Application integration is a separate later task.

### Source locations

- Human-readable documentation: `docs/06-ui/assets/`
- Binary hand-off packages: `docs/06-ui/assets/packages/`
- Future application copies: `assets/ui/`

## Production format policy

### Icons

- Format: transparent WebP
- Sizes: `16`, `20`, `24`, `32`, `48`, `64` px
- SVG: **not used**
- Generic icon-library replacements: not allowed without an explicit design decision

### Illustrations

- Format: PNG/WebP
- Responsive variants may be `desktop`, `tablet`, `mobile`
- Illustrations are full artwork/scenes, not simplified icons

## Production status

| Area | Status | Production package |
|---|---|---|
| Brand | Planned | — |
| Navigation | **Approved / uploaded** | `packages/navigation/TourCRM_Visual_Assets_v1.0_FINAL.zip` |
| Actions | **Approved / uploaded** | `packages/actions/TourCRM_Assets_Actions_P0_FINAL.zip` |
| Status | **Approved / uploaded** | `packages/status/TourCRM_Assets_Status_P0_FINAL.zip` |
| Domain | Working/concept; **not production-approved** | `packages/domain/` |
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

## Approved Actions

17 actions:

```text
add edit delete archive restore search filter sort save cancel confirm close back forward more download upload
```

Production output: 17 × 6 = **102 WebP assets**.

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

## Domain gate

The branch contains Domain working archives, but they remain outside the production-approved set. Do not copy them into `assets/ui/` as final assets.

This includes the Group metaphor: Navigation already owns the Groups icon, so Domain must not introduce an ambiguous duplicate.

## Lifecycle gate

A package becomes **Approved / uploaded** only after:

1. concept approval;
2. standalone production asset creation;
3. visual approval of production artwork;
4. format/size/naming validation;
5. upload to the designated package directory;
6. documentation and manifest update.

Concept sheets and crops are never the application source.

## Change control

When replacing a production package, update all of the following in the same logical change:

- `ASSET-PACKS.md`
- `ASSET-STATUS.md`
- `asset-manifest.json`
- affected package README
- PR description

Never silently revive a superseded archive.
