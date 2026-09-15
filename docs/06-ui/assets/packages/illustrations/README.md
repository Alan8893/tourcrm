# Illustrations packages

**Status: CLOSED / APPROVED.**

The three current P0 illustration blocks are fully agreed and closed for this asset-production stage. Do not reopen, redesign or regenerate them unless the Product Owner explicitly requests an Illustrations change.

## Empty states

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_EmptyStates_P0_FINAL.zip
```

Catalog: `empty-groups`, `empty-people`, `empty-events`, `empty-achievements`, `empty-search`, `no-results`.

These are semantic empty-state illustrations. `empty-search` and `no-results` remain distinct and must not be collapsed into a generic error illustration.

## System states

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_System_P0_FINAL.zip
```

Catalog: `success`, `error`, `404`, `403`, `maintenance`.

System illustrations correspond to their actual system state and are not interchangeable with status icons.

## Onboarding

```text
docs/06-ui/assets/packages/illustrations/TourCRM_Assets_Illustrations_Onboarding_P0_FINAL.zip
```

Catalog: `welcome`, `first-group`, `first-event`, `first-achievement`.

Onboarding artwork may carry the product's friendly/lightly playful character while remaining clear and consistent with the approved TourCRM visual foundation.

## Production rules

- Illustrations are full TourCRM artwork/scenes, not icons.
- Use the semantic illustration matching the screen state.
- Responsive compositions are separate assets where provided; do not treat a desktop composition as the universal asset.
- Do not use illustrations as replacements for navigation, action, status or domain icons.
- Generic stock illustrations or unrelated icon-library artwork are not approved substitutes.
- Future changes require an explicit Product Owner decision and a separate documented change.

## Integration

Future application integration root:

```text
assets/ui/illustrations/
```

Do not populate the application integration root during this documentation hand-off.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
