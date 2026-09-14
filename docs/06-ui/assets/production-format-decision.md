# TourCRM UI Assets — Production Format Decision

**Status:** approved

## Decision

Production format is selected by asset class and visual fidelity, not by a blanket rule that everything must be SVG.

### Icons

- SVG is preferred for interface icons.
- Approved custom artwork must retain its visual character, including gradients, layered fills, strokes, highlights, shadows and other details when they are part of the approved design.
- PNG/WebP exports may coexist with SVG when raster output preserves the approved appearance better at a target size.
- 16/20/24/32/48/64 px are icon-size variants; these are not desktop/tablet/mobile variants.

### Illustrations

- Illustrations are full TourCRM artwork, not enlarged icons.
- PNG/WebP is the preferred application format for complex illustration artwork.
- Responsive variants are `desktop`, `tablet`, and `mobile`.
- Where composition changes materially between breakpoints, each responsive variant is a separate composition rather than a simple resize.

## Quality gate

An asset is not production-ready merely because the file is technically valid. It must visually match the approved TourCRM style at its intended display size.

The first validation experiment is the approved `Groups` navigation icon, comparing SVG and PNG at real UI sizes before this format policy is applied to the complete icon set.
