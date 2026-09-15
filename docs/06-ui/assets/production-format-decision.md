# TourCRM UI Assets — Production Format Decision

**Status:** approved

## Decision

Production UI assets use raster formats. The production format is selected by asset class and visual fidelity, while preserving the approved TourCRM visual language.

### Icons

- **WebP is the production format for UI icons.**
- **SVG is not used for production UI assets.**
- Approved custom artwork must retain its visual character, including gradients, layered fills, strokes, highlights, shadows and other details when they are part of the approved design.
- Production icon variants are provided at **16 / 20 / 24 / 32 / 48 / 64 px**.
- These are icon-size variants; they are not desktop/tablet/mobile variants.
- There is no SVG production fallback.

### Illustrations

- Illustrations are full TourCRM artwork, not enlarged icons.
- **WebP is preferred**, with PNG supported where required for production artwork.
- Responsive variants are `desktop`, `tablet`, and `mobile` where the composition requires them.
- Where composition changes materially between breakpoints, each responsive variant is a separate composition rather than a simple resize.

## Quality gate

An asset is not production-ready merely because the file is technically valid. It must visually match the approved TourCRM style at its intended display size.

Validation is performed against the approved artwork at the intended UI sizes and across the required responsive variants. The gate does not include an SVG-vs-raster comparison because SVG is outside the TourCRM production format policy.
