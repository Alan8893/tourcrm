# Domain package

**Status: production artwork approved; binary package upload pending.**

The reviewed Domain production package is:

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip
```

It contains 7 newly approved Domain masters and 42 WebP production exports (16/20/24/32/48/64 px), for 49 files total. DOM-009 remains excluded because its semantic definition is not confirmed.

The previous archives remain historical working material:

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P0.zip
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P1A.zip
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P1B_CLEAN.zip
```

They must not be used as production assets.

Catalog scope: DOM-001…DOM-022 (22). Current production-approved Domain artwork covers DOM-001…005, DOM-006…008, DOM-010…012 and DOM-013…022. DOM-009 is a documented GAP.

Future application integration root, after the asset hand-off is complete:

```text
assets/ui/icons/domain/
```

## Visual direction

Domain artwork follows the same TourCRM visual language as the approved Navigation, Actions and Status families:

- bespoke TourCRM artwork only;
- warm, friendly outdoor/tourism character without turning operational UI into decoration;
- semantic meaning must remain immediately understandable;
- approved artwork from an existing family is the visual reference when a Domain concept is closely related to it;
- generic icon-library, stock or default UI glyph substitutions are not acceptable without an explicit design decision;
- cute/lightly playful treatment is allowed only where it supports the meaning and does not reduce clarity.

## P2 production scope

The following seven Domain assets were redesigned as standalone master artwork and approved for production:

| ID | Meaning | Production state |
|---|---|---|
| DOM-006 | Membership | Approved |
| DOM-007 | Relationship | Approved |
| DOM-008 | Instructor assignment | Approved |
| DOM-010 | Group members | Approved |
| DOM-011 | Group instructor | Approved |
| DOM-012 | Primary instructor | Approved |
| DOM-021 | Trip / journey / route | Approved |

The selected artwork was reviewed for semantic clarity and small-size readability. DOM-011 keeps the instructor visually dominant; DOM-012 uses the approved star marker; DOM-010 remains distinct from Navigation `Groups`.

## Production package

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip
```

Expected contents:

- 7 standalone PNG masters;
- 42 transparent WebP exports;
- sizes `16`, `20`, `24`, `32`, `48`, `64` px for each approved asset;
- no SVG;
- no candidate/composite sheets;
- no superseded Domain archives;
- no DOM-009.

The package has been technically validated before repository hand-off: file count, dimensions, WebP format, RGBA/alpha transparency, filename-to-DOM mapping and absence of SVG/candidate assets.

**Repository upload is the remaining hand-off step.** Until the ZIP is physically present under the path above, this package must not be described as fully uploaded/handed off.

## Known semantic rules

### Group collision

Domain group-related artwork must **not** duplicate or visually compete with the already-approved Navigation `Groups` metaphor. `DOM-010` communicates members/participants rather than the navigation destination itself.

### Status semantics

Domain lifecycle values must not be mapped mechanically to asset names. Domain status → UI semantic meaning → visual asset remains the required mapping. In particular, `cancelled` is not automatically `error`.

### Illustrations vs icons

These Domain assets are semantic UI icons, not illustrations. More expressive character artwork belongs to the Illustration family and must not be mixed into Domain production assets without an explicit design decision.

## Production gate

The Domain artwork has passed:

1. semantic review;
2. visual-family review;
3. standalone master creation;
4. small-size review at 16/20/24 px;
5. production export validation.

The remaining gate is physical repository upload of the reviewed binary package, followed by synchronization of the package registry, production status and manifest.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
