# TourCRM UI Assets — Production Format Decision

**Status:** approved

## Decision

Production UI assets use raster formats. The production format is selected by asset class and visual fidelity, while preserving the approved TourCRM visual language.

TourCRM's primary audience includes school-age users from grades 5–10, alongside club leaders, instructors and parents. The asset language should therefore remain calm, friendly and approachable, with room for cute/lightly playful artwork where it improves the experience. This does not override semantic clarity or production consistency.

### Icons

- **WebP is the production format for UI icons.**
- **SVG is not used for production UI assets.**
- **Production UI icons are custom TourCRM artwork.** Generic icon-library assets or stock icon packs are not an acceptable default.
- Existing approved custom artwork is the visual reference for subsequent icons in the same family.
- Approved custom artwork must retain its visual character, including gradients, layered fills, strokes, highlights, shadows and other details when they are part of the approved design.
- Production icon variants are provided at **16 / 20 / 24 / 32 / 48 / 64 px**.
- These are icon-size variants; they are not desktop/tablet/mobile variants.
- There is no SVG production fallback.
- Distinctive artwork is encouraged, but an icon must remain semantically recognizable. Visual originality must not be achieved by obscuring the action or state it represents.

### Illustrations

- Illustrations are full TourCRM artwork, not enlarged icons.
- **WebP is preferred**, with PNG supported where required for production artwork.
- Responsive variants are `desktop`, `tablet`, and `mobile` where the composition requires them.
- Where composition changes materially between breakpoints, each responsive variant is a separate composition rather than a simple resize.
- Empty states and onboarding are appropriate places for a friendly/cute visual treatment, particularly for the student audience. The illustration must still communicate the screen state without relying on decorative text alone.

## Quality gate

An asset is not production-ready merely because the file is technically valid. It must visually match the approved TourCRM style at its intended display size.

For icons, the quality gate includes both **visual uniqueness** and **semantic clarity**: the asset must belong unmistakably to the TourCRM visual language and must not read as an unmodified generic library icon.

Validation is performed against the approved artwork at the intended UI sizes and across the required responsive variants. The gate does not include an SVG-vs-raster comparison because SVG is outside the TourCRM production format policy.
