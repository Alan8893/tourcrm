# Actions package

## Status: CLOSED / APPROVED

The TourCRM Actions visual system is fully agreed and must be treated as **closed**. Do not reopen, redesign, replace or regenerate these action assets during the current visual-foundation work unless the Product Owner explicitly requests an Actions change.

### Approved action set

The fixed production action catalog contains exactly 17 actions:

`add`, `edit`, `delete`, `archive`, `restore`, `search`, `filter`, `sort`, `save`, `cancel`, `confirm`, `close`, `back`, `forward`, `more`, `download`, `upload`.

The approved custom artwork is the visual source of truth. Generic icon-library replacements are not allowed without an explicit design decision.

### Production package

Binary package:

```text
docs/06-ui/assets/packages/actions/TourCRM_Assets_Actions_P0_FINAL.zip
```

Contains 17 approved action icons, each exported as raster WebP at `16`, `20`, `24`, `32`, `48`, `64` px — **102 production WebP assets**.

The package contains no SVG assets.

### Change control

Actions are considered complete for this asset-production stage. Future work must use the approved package as-is. Any change to the action catalog, artwork or semantics requires an explicit Product Owner decision and a separate documented change; it must not be introduced implicitly while working on other asset families.

### Future integration

Application integration remains a separate stage:

```text
assets/ui/icons/actions/
```

The application integration root must not be populated as part of this documentation hand-off.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
