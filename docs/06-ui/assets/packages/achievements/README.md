# Achievements packages

**Status: CLOSED / APPROVED.**

The Achievements visual system is fully agreed and closed for this asset-production stage. The approved P0 and P1 packages are the source of truth; do not reopen, redesign or regenerate them unless the Product Owner explicitly requests an Achievements change.

## P0 — rarity and state

Archive:

```text
docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P0_FINAL.zip
```

Catalog: `common`, `uncommon`, `rare`, `epic`, `legendary`, `earned`, `locked`, `progress`.

## P1 — achievement artwork

Archive:

```text
docs/06-ui/assets/packages/achievements/TourCRM_Assets_Achievements_P1_FINAL.zip
```

Catalog: `explorer`, `peak-reacher`, `veteran-tourist`, `team-player`, `camp-master`, `navigator`, `first-expedition`, `trail-walker`, `community-hero`, `adventure-leader`.

## Semantic rules

- P0 rarity/state and P1 achievement artwork are separate semantic layers.
- `common` → `legendary` represent rarity; `earned`, `locked`, `progress` represent achievement state.
- P1 names represent approved achievement artwork and must not be silently repurposed as rarity or status semantics.
- Do not invent additional rarity levels, states or achievement artwork in this production batch.
- Existing approved artwork is the visual source of truth; generic icon-library replacements are not allowed without an explicit Product Owner decision.

## Integration

Future application integration root:

```text
assets/ui/achievements/
```

Do not populate the application integration root during this documentation hand-off.

Any future change to the Achievements catalog, artwork or semantics requires an explicit Product Owner decision and a separate documented change.

See `../../ASSET-PACKS.md`, `../../ASSET-STATUS.md` and `../../asset-manifest.json` for the complete contract.
