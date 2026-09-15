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
| `TourCRM_Visual_Assets_v1.0_FINAL.zip` | Approved P0 navigation visual asset set | NAV-001…NAV-007 (7) | **Approved / uploaded** | `docs/06-ui/assets/packages/navigation/` |
| `TourCRM_Assets_Actions_P0.zip` | Core action icons | ACT-001…ACT-017 (17) | **Approved / uploaded** | `docs/06-ui/assets/packages/actions/` |
| `TourCRM_Assets_Status_P0.zip` | Product and semantic status icons | STA-001…STA-009 (9) | **Uploaded / pending final package review** | `docs/06-ui/assets/packages/status/` |
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

Navigation and Actions P0 are approved. Status P0 is uploaded and awaits final package review. The next production block after Status is **Domain P0/P1**.

## Navigation package

The approved P0 Navigation set contains:

- Home
- People
- Groups
- Events
- Achievements
- Reports
- Settings

The approved production handoff for Navigation uses transparent WebP at 16, 20, 24, 32, 48 and 64 px. The current package also includes high-resolution PNG raster masters and the approved navigation sheet for reference. No SVG assets are included.

Archive filename:

```text
TourCRM_Visual_Assets_v1.0_FINAL.zip
```

Repository path:

```text
docs/06-ui/assets/packages/navigation/TourCRM_Visual_Assets_v1.0_FINAL.zip
```

The superseded `TourCRM_Assets_Navigation_P0.zip` archive has been removed and must not be used.

The archive is a documentation/design-system handoff package. It is not the application integration source under `assets/ui/` until the implementation stage.

## Actions P0 package

The P0 Actions package contains these 17 core actions from the source catalog:

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

Each action is delivered at 16, 20, 24, 32, 48 and 64 px as transparent WebP, plus a package manifest.

Archive filename:

```text
TourCRM_Assets_Actions_P0.zip
```

Repository path:

```text
docs/06-ui/assets/packages/actions/TourCRM_Assets_Actions_P0.zip
```

## Status P0 package

The P0 Status package contains 9 semantic status assets from the source catalog. The package has been uploaded to its designated repository directory and is pending final package integrity/manifest review.

Archive filename:

```text
TourCRM_Assets_Status_P0.zip
```

Repository path:

```text
docs/06-ui/assets/packages/status/TourCRM_Assets_Status_P0.zip
```

Package checksum:

```text
SHA-256: e333128bb0e320c3d55f5b1c396b0836602bb0c98bec85b73efa6303f040e396
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
docs/06-ui/assets/packages/status/TourCRM_Assets_Status_P0.zip
```

Incorrect:

```text
TourCRM_Assets_Status_P0.zip
```

## Integrity

Checksums are recorded only for packages whose local source artifact has been hashed. After manual upload, the repository copy is treated as the handoff artifact and must remain unchanged unless the package is explicitly versioned.
