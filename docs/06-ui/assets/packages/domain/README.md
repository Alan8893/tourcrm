# Domain package

**Status: working/concept material — not production-approved.**

The branch currently contains these working archives:

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P0.zip
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P1A.zip
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P1B_CLEAN.zip
```

Catalog scope: DOM-001…DOM-022 (22).

Future application integration root, once the Domain production gate is completed:

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

## Production audit

The three archives are **source material for review, not production assets**. Before any Domain item can move to production, it must pass the same gate as the other asset families:

1. confirm the semantic purpose and stable asset ID;
2. compare against approved Navigation, Actions and Status artwork to prevent semantic duplication;
3. approve the visual concept as TourCRM artwork;
4. create standalone production exports in raster WebP;
5. validate all required sizes: `16`, `20`, `24`, `32`, `48`, `64` px;
6. validate naming, transparency and package structure;
7. update the manifest and production-status documentation.

### Known collision: Group

Domain `Group` artwork must **not** duplicate or visually compete with the already-approved Navigation `Groups` metaphor. If the Domain concept cannot communicate a distinct semantic role clearly, it must be redesigned rather than reused as a second Groups icon.

These archives must not be treated as final application assets yet.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
