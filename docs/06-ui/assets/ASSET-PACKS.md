# TourCRM Asset Packages

## Purpose

This document is the index for binary asset packages delivered through the TourCRM UI asset handoff.

The package layer is intentionally separate from application integration:

- `docs/06-ui/assets/` — documentation and design-system source of truth;
- `docs/06-ui/assets/packages/` — reviewed binary handoff archives;
- `assets/ui/` — application integration path, populated only during the implementation stage.

The complete production scope is defined by `TourCRM_Asset_Catalog_v1.0`. This registry does not invent additional assets or product semantics.

## Package naming

```text
TourCRM_Assets_<category>_<priority>.zip
```

## Package registry

| Package | Scope | Catalog IDs | Current state | Target repository location |
|---|---|---|---|---|
| `TourCRM_Assets_Brand_P0-P1.zip` | Brand derivatives | BR-001…BR-008 (8) | Planned | `docs/06-ui/assets/packages/brand/` |
| `TourCRM_Assets_Navigation_P0.zip` | Approved P0 navigation icon set | NAV-001…NAV-007 (7) | **Approved / uploaded** | `docs/06-ui/assets/packages/navigation/` |
| `TourCRM_Assets_Actions_P0.zip` | Core action icons | ACT-001…ACT-017 (17) | **Production candidate / awaiting visual approval** | `docs/06-ui/assets/packages/actions/` |
| `TourCRM_Assets_Status_P0.zip` | Product and semantic status icons | STA-001…STA-009 (9) | Planned | `docs/06-ui/assets/packages/status/` |
| `TourCRM_Assets_Domain_P0-P1.zip` | Person, membership, role, Group and Event icons | DOM-001…DOM-022 (22) | Planned | `docs/06-ui/assets/packages/domain/` |
| `TourCRM_Assets_Achievements_P0-P1.zip` | Achievement frames, states and initial artwork | ACH-001…ACH-018 (18) | Planned | `docs/06-ui/assets/packages/achievements/` |
| `TourCRM_Assets_Illustrations_P0-P1.zip` | Empty, system and onboarding illustrations | ILL-001…ILL-015 (15) | Planned | `docs/06-ui/assets/packages/illustrations/` |
| `TourCRM_Assets_Decorative_P2.zip` | Optional decorative system | DEC-001…DEC-009 (9) | Deferred | `docs/06-ui/assets/packages/decorative/` |

## Production order

The catalog defines this order:

1. Brand derivatives
2. Navigation icons
3. Action icons
4. Status icons
5. Person / membership / roles
6. Group
7. Event / role relationship symbols
8. Achievement frames and states
9. Achievement artwork
10. Illustrations
11. P2 Decorative assets

Navigation P0 is already complete. The current production block is **Action Icons P0**.

## Navigation package

The approved P0 Navigation set contains:

- Home
- People
- Groups
- Events
- Achievements
- Reports
- Settings

The approved production handoff for Navigation uses transparent WebP at 16, 20, 24, 32, 48 and 64 px as the raster-preservation exception established for this custom iconography. It contains 42 production files: 7 icons × 6 sizes.

Archive filename:

```text
TourCRM_Assets_Navigation_P0.zip
```

Repository path:

```text
 docs/06-ui/assets/packages/navigation/TourCRM_Assets_Navigation_P0.zip
```

Archive contents:

```text
navigation/<icon-name>/<icon-name>-<size>.webp
```

The archive is a documentation/design-system handoff package. It is not the application integration source under `assets/ui/` until the implementation stage.

## Actions P0 candidate

The current source catalog text lists these core actions:

```text
add
edit
delete
archive
restore
search
filter
sort
save
cancel
confirm
close
back
forward
more
download
upload
```

The local candidate package contains these 17 actions at 16, 20, 24, 32, 48 and 64 px as transparent WebP. It remains a **visual approval candidate** until the artwork is reviewed against the already approved TourCRM visual language.

Archive filename:

```text
TourCRM_Assets_Actions_P0.zip
```

Local candidate checksum:

```text
7d049760bde1b6b5bb0534a2712adcc18507b8e123e8a82cce41c4b549a81175
```

## Package completion rule

A package is marked `Approved / uploaded` only when:

1. all assets in its scope have production files;
2. the visual quality gate has passed;
3. naming and format requirements are satisfied;
4. the package is uploaded to its designated directory;
5. the corresponding catalog/manifest/status documentation is updated.

Do not create placeholder files merely to make a package appear complete.

## Upload rule

Archives must be uploaded into their corresponding package directory on branch `feat/ui-assets-production-v7`, never into the repository root.

Correct:

```text
 docs/06-ui/assets/packages/navigation/TourCRM_Assets_Navigation_P0.zip
```

Incorrect:

```text
 TourCRM_Assets_Navigation_P0.zip
```

## Checksum

The current Navigation package is SHA-256:

```text
33faefb69535dd546406d9b72a47393ca6fc3341bc0a5d653193de45eeb98604
```

The checksum is retained as the integrity reference for the uploaded handoff package.
