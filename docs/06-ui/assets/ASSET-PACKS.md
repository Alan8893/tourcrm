# TourCRM Asset Packages

## Purpose

This document is the index for binary asset packages delivered through the TourCRM UI asset handoff.

The package layer is intentionally separate from application integration:

- `docs/06-ui/assets/` — documentation and design-system source of truth;
- `docs/06-ui/assets/packages/` — reviewed binary handoff archives;
- `assets/ui/` — application integration path, populated only during the implementation stage.

Do not treat an archive as proof that an asset is production-ready unless the corresponding manifest entries and status are marked approved-production.

## Package naming

```text
TourCRM_Assets_<category>_<priority>.zip
```

Examples:

```text
TourCRM_Assets_Navigation_P0.zip
TourCRM_Assets_Actions_P0.zip
TourCRM_Assets_Status_P0.zip
TourCRM_Assets_Domain_P0.zip
TourCRM_Assets_Achievements_P0-P1.zip
TourCRM_Assets_Illustrations_P0-P1.zip
TourCRM_Assets_Brand_P0-P1.zip
TourCRM_Assets_Decorative_P2.zip
```

## Package registry

| Package | Scope | Catalog IDs | Current state | Target repository location |
|---|---|---|---|---|
| `TourCRM_Assets_Brand_P0-P1.zip` | Brand derivatives and fallbacks | BR-001…BR-008 | Planned | `docs/06-ui/assets/packages/brand/` |
| `TourCRM_Assets_Navigation_P0.zip` | Approved navigation icon set | NAV-001…NAV-007 | **Ready** | `docs/06-ui/assets/packages/navigation/` |
| `TourCRM_Assets_Actions_P0.zip` | Core action icons | ACT-001…ACT-013 | Planned | `docs/06-ui/assets/packages/actions/` |
| `TourCRM_Assets_Status_P0.zip` | Product and semantic status icons | STA-001…STA-009 | Planned | `docs/06-ui/assets/packages/status/` |
| `TourCRM_Assets_Domain_P0-P1.zip` | Person, membership, Group and Event domain icons | DOM-001…DOM-022 | Planned | `docs/06-ui/assets/packages/domain/` |
| `TourCRM_Assets_Achievements_P0-P1.zip` | Achievement frames, states and initial artwork | ACH-001…ACH-017 | Planned | `docs/06-ui/assets/packages/achievements/` |
| `TourCRM_Assets_Illustrations_P0-P1.zip` | Empty, system and onboarding illustrations | ILL-001…ILL-015 | Planned | `docs/06-ui/assets/packages/illustrations/` |
| `TourCRM_Assets_Decorative_P2.zip` | Optional decorative system | DEC-001…DEC-009 | Deferred | `docs/06-ui/assets/packages/decorative/` |

## Navigation package

The first package is the approved P0 Navigation set:

- Home
- People
- Groups
- Events
- Achievements
- Reports
- Settings

The approved production raster format is transparent WebP at 16, 20, 24, 32, 48 and 64 px. The current handoff contains 42 production files: 7 icons × 6 sizes.

Archive filename:

```text
TourCRM_Assets_Navigation_P0.zip
```

Archive contents are rooted at:

```text
navigation/<icon-name>/<icon-name>-<size>.webp
```

The archive is a documentation handoff package. It is not yet copied into `assets/ui/` until the implementation stage.

## Important distinction

The catalog contains the complete planned asset system; the package registry records which parts have actually been produced and handed off.

Do not create placeholder files merely to make a package appear complete. A package remains `Planned` until its real production assets exist and pass the visual quality gate.

## Upload rule

When adding an archive manually through GitHub, upload it into the corresponding package directory on branch `feat/ui-assets-production-v7`, not into the repository root.

For example:

```text
Correct:
docs/06-ui/assets/packages/navigation/TourCRM_Assets_Navigation_P0.zip

Incorrect:
TourCRM_Assets_Navigation_P0.zip
```

## Checksum

The current locally prepared Navigation package is SHA-256:

```text
33faefb69535dd546406d9b72a47393ca6fc3341bc0a5d653193de45eeb98604
```

Verify the checksum after upload before considering the handoff package immutable.
