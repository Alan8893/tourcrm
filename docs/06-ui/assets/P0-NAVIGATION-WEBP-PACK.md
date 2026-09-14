# TourCRM UI — Navigation P0 WebP Pack

Status: **Approved visual direction; binary upload pending**

## Approved set

The P0 navigation set consists of seven custom TourCRM navigation artworks:

- `icon.navigation.home`
- `icon.navigation.people`
- `icon.navigation.groups`
- `icon.navigation.events`
- `icon.navigation.achievements`
- `icon.navigation.reports`
- `icon.navigation.settings`

## Delivery format

Primary delivery format: **WebP with transparent background**.

Required raster sizes:

`16 / 20 / 24 / 32 / 48 / 64 px`

Required interaction states:

`default / hover / active / disabled`

## Design rule

These are authored TourCRM visual assets, not generic icon-library replacements. The artwork must retain the approved sticker/illustration character, saturated layered color, expressive outlines and recognizable outdoor/adventure motifs.

Do not redraw or simplify the artwork merely to make it easier to implement as SVG.

## Repository delivery

Target structure:

```text
assets/ui/icons/navigation/<name>/
  <name>-16.webp
  <name>-20.webp
  <name>-24.webp
  <name>-32.webp
  <name>-48.webp
  <name>-64.webp
```

The visual approval has been confirmed in the UI asset workspace. The repository manifest intentionally marks the files as `approved-pending-upload` until the binary WebP objects are present in the branch.
