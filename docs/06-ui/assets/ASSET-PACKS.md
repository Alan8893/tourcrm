# TourCRM UI Asset Packages

Human-readable registry for the TourCRM visual asset hand-off. This file answers: what exists, where it lives, what it is for, and where the application will consume it later.

## Product visual direction

TourCRM is a calm, friendly product for tourist clubs. The primary audience includes school-age users from grades 5–10; club leaders, instructors and parents are also first-class users.

The visual language should feel welcoming, warm and approachable. Cute/lightly playful artwork is encouraged where it improves the experience, especially for empty states, onboarding and supportive feedback. It must not compromise semantic clarity, readability or confidence in operational actions.

## Source-of-truth and integration

- Documentation/design-system source: `docs/06-ui/assets/`
- Reviewed binary hand-off packages: `docs/06-ui/assets/packages/`
- Future application integration: `assets/ui/`
- PR #75 is the hand-off PR. **Do not merge this branch as part of asset production.**

## Production rules

- UI icons: raster **WebP** only.
- SVG is not used for TourCRM production UI assets.
- Icon sizes: `16`, `20`, `24`, `32`, `48`, `64` px.
- Illustrations: PNG/WebP; responsive `desktop`, `tablet`, `mobile` compositions where required.
- Concept sheets are reference material, not application assets.
- **All production UI icons must be bespoke TourCRM artwork.** Generic icon-library assets, stock icon packs or ad-hoc replacements are not allowed without an explicit design decision.
- Existing approved custom artwork is the visual source of truth for new assets in the same family.
- An icon may be stylistically distinctive, but its semantic meaning must remain immediately understandable. Do not sacrifice meaning for novelty.
- Hover/active/disabled are normally UI-layer states; do not create duplicate bitmap files unless artwork itself changes.

## Current registry

| Area | Scope | State | Repository package/path | Future integration root |
|---|---|---|---|---|
| Brand | Official logo master (1) | **Master uploaded** | `docs/06-ui/assets/packages/brand/logo.png` | `assets/ui/brand/` |
| Navigation | NAV-001…NAV-007 (7) | **CLOSED / APPROVED** | `docs/06-ui/assets/packages/navigation/TourCRM_Visual_Assets_v1.0_FINAL.zip` | `assets/ui/icons/navigation/` |
| Actions | ACT-001…ACT-017 (17) | **CLOSED / APPROVED** | `docs/06-ui/assets/packages/actions/TourCRM_Assets_Actions_P0_FINAL.zip` | `assets/ui/icons/actions/` |
| Status | STA-001…STA-009 (9) | **CLOSED / APPROVED** | `docs/06-ui/assets/packages/status/TourCRM_Assets_Status_P0_FINAL.zip` | `assets/ui/icons/status/` |
| Domain | DOM-001…DOM-022 (22) | **CLOSED / APPROVED** | `docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip` | `assets/ui/icons/domain/` |
| Achievements P0 | rarity + state (8) | **Approved / uploaded** | `docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P0_FINAL.zip` | `assets/ui/achievements/` |
| Achievements P1 | artwork (10) | **Approved / uploaded** | `docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P1_FINAL.zip` | `assets/ui/achievements/` |
| Illustrations — Empty | 6 | **Approved / uploaded** | `docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_EmptyStates_P0_FINAL.zip` | `assets/ui/illustrations/` |
| Illustrations — System | 5 | **Approved / uploaded** | `docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_System_P0_FINAL.zip` | `assets/ui/illustrations/` |
| Illustrations — Onboarding | 4 | **Approved / uploaded** | `docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_Onboarding_P0_FINAL.zip` | `assets/ui/illustrations/` |
| Decorative | DEC-001…DEC-009 (9) | Deferred / P2 | `docs/06-ui/assets/packages/decorative/` | `assets/ui/decorative/` |

## Brand — official master

The Brand hand-off intentionally starts with **one master**, not a generated variant archive.

```text
docs/06-ui/assets/packages/brand/logo.png
```

Rules for this stage:

- the PNG is the official «Вектор» / TourCRM logo master;
- it is the single source of truth for the brand mark;
- no SVG is created;
- no ZIP archive is required;
- no artificial light/dark/compact/horizontal/monochrome/favicon/app-icon variants are created;
- any future technical derivative must be created only for a concrete approved product need and must be derived from this master.

The application integration root remains `assets/ui/brand/` and must not be populated during this documentation hand-off.

## Catalog and usage

### Navigation — 7 icons / 42 WebP exports

`NAV-001 Home`, `NAV-002 People`, `NAV-003 Groups`, `NAV-004 Events`, `NAV-005 Achievements`, `NAV-006 Reports`, `NAV-007 Settings`.

Archive:

```text
docs/06-ui/assets/packages/navigation/TourCRM_Visual_Assets_v1.0_FINAL.zip
```

Integration directories:

```text
assets/ui/icons/navigation/home/
assets/ui/icons/navigation/people/
assets/ui/icons/navigation/groups/
assets/ui/icons/navigation/events/
assets/ui/icons/navigation/achievements/
assets/ui/icons/navigation/reports/
assets/ui/icons/navigation/settings/
```

`Settings` is the approved compass + wrench artwork. The superseded `TourCRM_Assets_Navigation_P0.zip` must not be used.

### Actions — 17 icons / 102 WebP exports

Catalog: `add`, `edit`, `delete`, `archive`, `restore`, `search`, `filter`, `sort`, `save`, `cancel`, `confirm`, `close`, `back`, `forward`, `more`, `download`, `upload`.

Archive:

```text
docs/06-ui/assets/packages/actions/TourCRM_Assets_Actions_P0_FINAL.zip
```

Production status: **CLOSED / APPROVED**. The catalog and approved artwork are fixed for this asset-production stage. Any future change requires an explicit Product Owner decision and a separate documented change.

Integration root:

```text
assets/ui/icons/actions/
```

### Status — 9 icons / 54 WebP assets

Catalog: `planned`, `ongoing`, `completed`, `ended`, `archived`, `success`, `warning`, `error`, `info`.

Archive:

```text
docs/06-ui/assets/packages/status/TourCRM_Assets_Status_P0_FINAL.zip
```

Production status: **CLOSED / APPROVED**. The catalog, semantics and approved artwork are fixed for this asset-production stage. `ended` is the approved extinguished-campfire metaphor. Do not add extra statuses unless the catalog is explicitly changed.

Integration root:

```text
assets/ui/icons/status/
```

### Achievements

P0 archive:

```text
docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P0_FINAL.zip
```

P0: `common`, `uncommon`, `rare`, `epic`, `legendary`, `earned`, `locked`, `progress`.

P1 archive:

```text
docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P1_FINAL.zip
```

P1: `explorer`, `peak-reacher`, `veteran-tourist`, `team-player`, `camp-master`, `navigator`, `first-expedition`, `trail-walker`, `community-hero`, `adventure-leader`.

Integration root: `assets/ui/achievements/`.

### Illustrations

Empty states:

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_EmptyStates_P0_FINAL.zip
```

`empty-groups`, `empty-people`, `empty-events`, `empty-achievements`, `empty-search`, `no-results`.

These states are deliberately allowed to be friendly/cute. `empty-search` and `no-results` are distinct semantic states and must not collapse into a generic error illustration.

System:

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_System_P0_FINAL.zip
```

`success`, `error`, `404`, `403`, `maintenance`.

Onboarding:

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_Onboarding_P0_FINAL.zip
```

`welcome`, `first-group`, `first-event`, `first-achievement`.

Integration root: `assets/ui/illustrations/`.

Use the illustration matching the semantic screen state. Do not use an illustration as a replacement for an icon.

## Domain — P2 production package

Approved P2 artwork:

```text
DOM-006 Membership
DOM-007 Relationship
DOM-008 Instructor assignment
DOM-010 Group members
DOM-011 Group instructor
DOM-012 Primary instructor
DOM-021 Trip / journey / route
```

Production package:

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip
```

Package contract: **7 standalone masters + 42 transparent WebP exports = 49 files**. Each approved P2 asset has WebP sizes `16/20/24/32/48/64` px. DOM-009 is excluded pending semantic definition.

Production status: **CLOSED / APPROVED**. The P2 catalog and approved artwork are fixed for this asset-production stage. The binary ZIP is physically present in the repository at the designated package path.

The application integration root remains:

```text
assets/ui/icons/domain/
```

It must not be populated as part of this hand-off.

## Not yet handed off

Decorative remains deferred to P2. Brand now has its official master uploaded; no Brand variant archive is planned at this stage.

## Production order

Brand → Navigation → Actions → Status → Domain → Achievements → Illustrations → Decorative P2.

A package may be called **Approved / uploaded** only after visual approval, production export, naming/format validation, upload and documentation update. For the Brand package, the production deliverable is intentionally the approved master itself rather than a derivative archive.

If a package is replaced, update this registry, `ASSET-STATUS.md`, `asset-manifest.json` and the PR description together. Superseded filenames must not be referenced by new code or documentation.
