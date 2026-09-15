# Status package

## Status: CLOSED / APPROVED

The TourCRM Status visual system is fully agreed and must be treated as **closed** for the current asset-production stage. Do not reopen, redesign, replace or regenerate these status assets unless the Product Owner explicitly requests a Status change.

### Approved status catalog

Exactly 9 semantic status icons are approved:

1. `planned`
2. `ongoing`
3. `completed`
4. `ended`
5. `archived`
6. `success`
7. `warning`
8. `error`
9. `info`

The approved artwork is the visual source of truth. `ended` uses the approved extinguished-campfire metaphor.

### Production package

Binary package:

```text
docs/06-ui/assets/packages/status/TourCRM_Assets_Status_P0_FINAL.zip
```

The package contains 9 custom status icons in raster WebP at 16, 20, 24, 32, 48 and 64 px: **54 production WebP assets**.

The package contains no SVG assets.

### Semantic rules

- Status icons communicate semantic state; they are not interchangeable action icons.
- Do not introduce additional status meanings in this batch.
- `ended` remains the approved extinguished-campfire artwork.
- Existing approved custom artwork is the source of truth; do not substitute generic icon-library assets.
- Hover/active/disabled are UI-layer states and do not require duplicate bitmap assets unless the artwork itself changes.

### Future application integration

```text
assets/ui/icons/status/
```

The application integration root must not be populated during this documentation hand-off.

### Change control

Status is considered complete for this asset-production stage. Any future catalog, semantic or artwork change requires an explicit Product Owner decision and a separate documented change. Do not implicitly expand or redesign the package while working on another asset family.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
