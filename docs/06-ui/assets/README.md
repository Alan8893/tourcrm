# TourCRM UI Assets

This directory is the documentation and design-system hand-off point for approved TourCRM visual assets.

## Separation from implementation

`docs/06-ui/assets/` describes and stores approved visual assets before they are integrated into the application.

Application-consumable copies belong to `assets/ui/` and are populated during a separate frontend integration task.

This separation keeps the visual specification independent from implementation details and prevents frontend work from silently changing an approved design decision.

## P0 Navigation Icons

The P0 navigation system is approved and consists of seven custom TourCRM icons:

- Home
- People
- Groups
- Events
- Achievements
- Reports
- Settings

Production format: transparent WebP.

Production sizes: `16`, `20`, `24`, `32`, `48`, `64` px.

Total P0 production exports: **42 assets**.

The approved artwork must not be replaced by Material, Lucide, Font Awesome, or other generic icon-library equivalents without an explicit design decision.

## Asset state

The artwork itself is the approved base asset. Interaction states such as hover, active and disabled should normally be implemented at the UI layer. Separate bitmap files are required only when the artwork itself materially changes between states.

## Manifest

`asset-manifest.json` is the machine-readable contract containing stable asset IDs, formats, dimensions, documentation paths and future application integration paths.

## Production package

The P0 navigation WebP package is maintained as a hand-off artifact during this documentation stage. It contains the 42 approved production exports; 512 px master files are not part of the P0 integration set.
