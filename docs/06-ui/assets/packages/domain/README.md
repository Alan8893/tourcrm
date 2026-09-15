# Domain package

## Status: CLOSED / APPROVED

The TourCRM Domain visual system is fully agreed for the current asset-production stage and must be treated as **closed**. Do not reopen, redesign, replace or regenerate the approved Domain artwork unless the Product Owner explicitly requests a Domain change.

### Production package

The reviewed Domain production package is:

```text
docs/06-ui/assets/packages/domain/TourCRM_Assets_Domain_P2_FINAL.zip
```

This P2 package contains 7 newly approved Domain masters and 42 WebP production exports (16/20/24/32/48/64 px), for 49 files total. DOM-009 remains excluded because its semantic definition is not confirmed.

The previous archives remain historical working material and must not be used as production assets.

### Catalog scope

The Domain catalog is `DOM-001…DOM-022` (22 semantic IDs). Current production-approved artwork covers:

```text
DOM-001…005
DOM-006…008
DOM-010…012
DOM-013…022
```

`DOM-009` is the documented GAP and has no production artwork.

### P2 newly approved artwork

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

### Production contract

The package contains:

- 7 standalone PNG masters;
- 42 transparent WebP exports;
- sizes `16`, `20`, `24`, `32`, `48`, `64` px for each approved P2 asset;
- no SVG;
- no candidate/composite sheets;
- no superseded Domain archives;
- no DOM-009.

The package was technically validated before repository hand-off: file count, dimensions, WebP format, RGBA/alpha transparency, filename-to-DOM mapping and absence of SVG/candidate assets. The repository package is physically present at the designated path.

### Visual direction

Domain artwork follows the same TourCRM visual language as the approved Navigation, Actions and Status families:

- bespoke TourCRM artwork only;
- warm, friendly outdoor/tourism character without turning operational UI into decoration;
- semantic meaning must remain immediately understandable;
- approved artwork from an existing family is the visual reference when a Domain concept is closely related to it;
- generic icon-library, stock or default UI glyph substitutions are not acceptable without an explicit design decision;
- cute/lightly playful treatment is allowed only where it supports the meaning and does not reduce clarity.

### Semantic rules

**Group collision:** Domain group-related artwork must not duplicate or visually compete with Navigation `Groups`. `DOM-010` communicates members/participants rather than the navigation destination itself.

**Status semantics:** Domain lifecycle values must not be mapped mechanically to asset names. Domain status → UI semantic meaning → visual asset remains the required mapping. `cancelled` is not automatically `error`.

**Illustrations vs icons:** Domain assets are semantic UI icons, not illustrations. More expressive character artwork belongs to the Illustration family and must not be mixed into Domain production assets without an explicit design decision.

### Future application integration

```text
assets/ui/icons/domain/
```

The application integration root must not be populated during this documentation hand-off.

### Change control

Domain P2 is considered complete for this asset-production stage. Any future change to the Domain catalog, semantics or artwork requires an explicit Product Owner decision and a separate documented change. DOM-009 must remain a GAP until its semantic definition is explicitly confirmed.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
