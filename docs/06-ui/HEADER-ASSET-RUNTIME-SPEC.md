# TourCRM Header Asset Runtime Specification

**Status:** APPROVED BASELINE  
**Scope:** TH-0089 Header visual composition implementation  
**Related:** TH-0091 / PR #114 asset hand-off

## 1. Purpose

Define how the approved Header / Brand Zone illustration package is consumed by the web application.

This specification covers runtime selection and rendering only. It does not define creation of artwork or future theme scheduling.

## 2. Asset source

The canonical source package is:

`docs/06-ui/assets/packages/header-scenes-v1/`

Current theme:

`basic`

Current paired assets:

- `desk-basic-01.png` … `desk-basic-09.png`
- `mob-basic-01.png` … `mob-basic-09.png`

The asset naming contract is defined by `docs/06-ui/TOURCRM-HEADER-SCENES-V1-SPEC.md`.

The application must not assign semantic meaning to sequence numbers. `01` is an ordered position, not a scene identifier.

## 3. Runtime asset location

For browser delivery, approved source assets are copied unchanged into the web application's public asset tree under:

`/assets/ui/header-scenes-v1/`

The public runtime path is an implementation delivery path; the canonical source remains the documented asset package above.

No SVG conversion is permitted.

## 4. Theme selection

The MVP uses the fixed theme slug:

`basic`

Automatic selection of `winter`, `ny`, `8mar`, or other future themes is **out of scope** for this task.

Future theme selection must be a separate product decision and implementation task. It must provide only a theme slug to the asset resolver; it must not introduce configurable filesystem paths.

If a requested theme is unavailable at runtime, the resolver must safely fall back to `basic`.

## 5. Daily rotation

The Header displays one illustration pair per calendar day.

The selection is deterministic and does not require persistence.

### Algorithm

1. Use the UTC calendar date.
2. Calculate the number of whole UTC days since `2026-01-01`.
3. Calculate `dayIndex modulo availablePairCount`.
4. Select the pair at that zero-based index.
5. When the end of the available set is reached, wrap to the first pair.

The same sequence is therefore selected for desktop and mobile on the same date.

There is no database state, cookie, localStorage, sessionStorage, or API call for rotation state.

The image changes naturally when the UTC calendar date changes. A page reload is sufficient to display the current day's selection.

## 6. Pairing contract

A sequence participates in rotation only when both assets exist:

`desk-<theme>-<sequence>.png`

and

`mob-<theme>-<sequence>.png`

The runtime must not independently rotate desktop and mobile lists, because that could produce different scenes for the same day.

If a future theme contains an incomplete desktop/mobile pair, that sequence is excluded from the active paired set.

If the selected theme has no complete pairs, use the `basic` theme. If `basic` also has no complete pairs, render the existing non-illustrated Header treatment rather than breaking the Header.

## 7. Responsive rendering

Desktop asset:

`desk-<theme>-<sequence>.png`

Mobile asset:

`mob-<theme>-<sequence>.png`

The mobile asset is a dedicated composition and must not be implemented as a crop of the desktop asset.

The existing responsive breakpoint and Header layout remain unchanged unless required solely to prevent the decorative layer from interfering with the approved composition.

## 8. Decorative behavior

The illustration is purely decorative.

Requirements:

- `alt` must be empty / decorative semantics.
- It must not receive keyboard focus.
- It must not intercept pointer events.
- Existing logo, profile menu, navigation and mobile `Меню` controls retain their current behavior.
- Functional controls remain visually and interactively above the decorative layer.
- The illustration must not change Header height unexpectedly.
- Do not add animation merely for daily rotation.
- Do not add a new loading state visible to the user.

The approved alpha transparency in the PNG masters must be preserved. Do not replace it with a CSS-painted gradient.

## 9. No new architecture

This feature is presentation-layer behavior only.

Do not add:

- backend endpoints;
- database tables or fields;
- permissions;
- user settings;
- theme administration UI;
- semantic scene entities;
- CMS behavior;
- filesystem-path configuration;
- generic icon-library assets;
- SVG assets.

## 10. Acceptance criteria

- [ ] Header renders the approved basic illustration set.
- [ ] Exactly one desktop/mobile pair is selected for a given UTC date.
- [ ] Consecutive UTC dates advance to the next pair.
- [ ] The sequence wraps after the final available pair.
- [ ] Desktop uses `desk-basic-XX.png`.
- [ ] Mobile uses `mob-basic-XX.png`.
- [ ] Desktop and mobile show the same sequence on the same date.
- [ ] Incomplete future pairs are excluded from rotation.
- [ ] Unknown/unavailable themes fall back to `basic`.
- [ ] If no usable pair exists, the existing Header remains functional.
- [ ] Illustration is decorative and cannot intercept interaction.
- [ ] Existing logo, profile menu, navigation and mobile menu remain unchanged.
- [ ] No SVG or generic icon-library asset is introduced.
- [ ] No backend/API/DB change is introduced.
- [ ] Responsive QA passes at approximately 1440×900, 834×1112 and 390×844.
- [ ] Existing frontend tests, lint, typecheck and build pass.
