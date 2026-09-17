# TH-0098 follow-up — Header scene visual scale

## Status
READY FOR REVIEW

## Purpose
Correct the second-stage Header scene presentation after the first CSS correction made the approved artwork too small inside the compact Header.

## Decision
Keep all approved PNG assets, selector logic, daily rotation, Header controls and UI Foundation unchanged. The runtime image is displayed at the approved asset's native aspect-ratio dimensions and centered inside the existing clipped Header scene layer:

- desktop: 1600×400
- mobile: 800×500

The scene remains decorative and clipped only by the Header viewport. No `cover` fitting and no height-based downscaling are used.

## Scope
Only `HeaderScene.module.css` presentation rules are changed.

## QA targets
- desktop 1920px viewport
- desktop 1440px viewport
- mobile approximately 500px viewport
- mobile 390px viewport

Verify that the illustration is visually prominent, retains its intended composition, does not stretch, and does not intercept Header controls.
