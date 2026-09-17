# TourCRM Header Asset Runtime Specification

**Status:** APPROVED BASELINE  
**Scope:** runtime integration of the approved Header illustration asset package v1  
**Related:** TH-0091 / PR #114 (asset hand-off, already merged)

## 1. Purpose

Define the production runtime contract for consuming the approved Header illustration package in the TourCRM web application.

This document does **not** redefine or recreate the artwork. The canonical artwork package remains `docs/06-ui/assets/packages/header-scenes-v1/`.

## 2. Non-negotiable constraints

- Do not modify PR #114 or reimplement TH-0091.
- Do not create a second asset-production PR for TH-0091.
- No SVG.
- No generic icon-library assets.
- Do not create new artwork in this stage.
- Do not change the approved Navigation Architecture or UI Foundation.
- Do not change logo, profile menu, mobile menu, navigation labels, routes, roles, permissions, or backend APIs.
- Header artwork is decorative only and must never become an interaction surface.
- The existing official TourCRM «Вектор» logo remains the brand source of truth.

## 3. Canonical source and production runtime location

### Canonical source

`docs/06-ui/assets/packages/header-scenes-v1/`

Current approved basic theme contains nine desktop/mobile pairs:

- `desk-basic-01.png` … `desk-basic-09.png`
- `mob-basic-01.png` … `mob-basic-09.png`

### Production runtime assets

The frontend must consume production copies from:

`apps/web/public/assets/ui/header-scenes-v1/<viewport>-<theme>-<sequence>.png`

The documentation package remains the canonical source; `public/` is the browser-delivery copy.

Runtime code must not reference files directly from `docs/`.

## 4. Naming contract

Runtime asset names follow:

`<viewport>-<theme>-<sequence>.png`

Where:

- `viewport`: `desk` or `mob`;
- `theme`: lowercase ASCII slug, e.g. `basic`, `winter`, `ny`, `8mar`;
- `sequence`: zero-padded position inside that theme.

The filename sequence is positional and must not encode semantic scene names.

## 5. Theme model

Theme selection and rotation are separate concerns.

For this stage:

- active theme is `basic`;
- theme selection is static frontend configuration;
- there is no database state;
- there is no API;
- there is no administrator UI;
- there is no automatic seasonal-calendar logic;
- future themes may contain a different number of scenes.

If a requested theme is unavailable or has no valid paired sequences, the resolver falls back to `basic`.

## 6. Paired scene rule

A sequence participates in rotation only when both files exist for the active theme:

- `desk-<theme>-NN.png`
- `mob-<theme>-NN.png`

The runtime registry/selector must enumerate **paired sequences**, sort them numerically, and ignore incomplete pairs.

This guarantees that desktop and mobile display the same conceptual scene for a given day.

If the selected theme has no valid paired sequences, the runtime falls back to `basic`.

If `basic` itself has no valid pairs, the implementation must keep the existing non-illustrated Header treatment functional rather than breaking the Header.

## 7. Daily rotation

MVP rotation is deterministic and globally consistent:

- one scene per UTC calendar day;
- sequential cyclic rotation;
- no persistence;
- no database;
- no localStorage/sessionStorage;
- no user-specific randomization.

Use a fixed UTC epoch of `2026-01-01T00:00:00Z`.

Conceptually:

`dayIndex = number of complete UTC days since epoch`

`sequence = pairedSequences[dayIndex mod pairedSequences.length]`

The implementation must use UTC date boundaries so that SSR/hydration and different user time zones do not produce conflicting daily scenes.

Adding or removing paired sequence files changes the future cyclic mapping according to the resulting sorted paired set. This is intentional and acceptable for v1.

## 8. Responsive selection

- Desktop, laptop and tablet landscape use `desk` assets.
- Mobile uses `mob` assets according to the existing frontend breakpoint strategy.
- The mobile composition is a dedicated asset, not a crop of the desktop asset.
- The implementation must preserve the existing Header layout and controls.

The same selected sequence must be used for both viewport variants.

## 9. Header integration

The artwork is a decorative layer of the existing Header/brand zone.

Required behavior:

- it must sit visually behind/around the brand area without obscuring the official logo;
- it must not cover or interfere with the profile menu;
- it must not cover or interfere with the mobile `Меню` control;
- it must not introduce horizontal scrolling or unexpected header height changes;
- it must not become a clickable/focusable element;
- decorative image semantics must be hidden from assistive technology (`aria-hidden` or equivalent decorative semantics; empty alt where an image element is used);
- pointer interaction must pass through the artwork (`pointer-events: none` or equivalent);
- the existing compact Header remains the structural authority.

## 10. Visual requirements

The approved artwork contract must remain intact:

- transparent RGBA PNG;
- desktop 1600×400;
- mobile 800×500;
- desktop left-edge alpha fade is real transparency, not a painted color gradient;
- no text, logo, buttons, UI controls, frames or watermarks;
- central brand/control area remains readable;
- artwork must not reduce usability or contrast of Header controls.

## 11. Runtime registry/selector contract

Introduce a small dedicated Header-scene asset registry/selector within the existing frontend asset architecture.

Responsibilities:

1. enumerate the production asset set for the active theme;
2. construct only valid desktop/mobile pairs;
3. sort sequences numerically;
4. select the daily sequence from the UTC day index;
5. expose the corresponding desktop/mobile runtime paths;
6. provide deterministic fallback to `basic` when the configured theme is unavailable or has no valid pairs.

Do not duplicate business/domain logic into this selector. It is presentation asset selection only.

Do not load all 18 images merely to determine the current scene. The runtime should need only the currently relevant viewport asset where practical.

## 12. Error and fallback behavior

- Unknown/unavailable configured theme → use `basic`.
- Configured theme with zero complete pairs → use `basic`.
- Incomplete sequence pair → exclude that sequence.
- If no usable basic pair exists, preserve the existing non-illustrated Header.
- No user-facing error message is required for normal fallback.

## 13. Testing requirements

Automated tests must cover at minimum:

- fixed epoch calculation;
- same UTC day → same sequence;
- next UTC day → next sequence;
- cyclic wraparound;
- numeric ordering of sequences;
- incomplete desktop/mobile pair exclusion;
- configured-theme fallback to `basic`;
- same sequence maps to `desk` and `mob` variants;
- deterministic output independent of local timezone;
- runtime paths use `/assets/ui/header-scenes-v1/...` rather than `docs/...`;
- decorative semantics/non-interaction of the Header scene layer.

## 14. Visual QA

Verify at minimum:

- 1440×900 desktop;
- 834×1112 tablet;
- 390×844 mobile.

Check:

- logo readability;
- profile/menu usability;
- mobile menu usability;
- no layout shift attributable to the scene;
- no horizontal overflow;
- scene alignment and transparency;
- desktop/mobile pair consistency;
- existing UI Foundation remains visually unchanged outside the new decorative layer.

## 15. Out of scope

- login/authentication;
- role/permission logic;
- admin theme management;
- database/API changes;
- automatic season/date theme switching;
- per-user personalization;
- analytics;
- new Header artwork;
- navigation redesign;
- changes to PR #114.

## 16. Definition of Done

The stage is complete when:

1. approved header assets are available in the production runtime location;
2. `basic` theme is rendered by the existing Header;
3. one deterministic UTC scene is selected per day;
4. desktop/mobile use paired variants of the same sequence;
5. fallback behavior is covered by tests;
6. artwork is decorative and cannot intercept interaction;
7. logo, profile menu and mobile menu remain fully usable;
8. no backend/API/DB/auth/navigation changes were introduced;
9. tests, lint, typecheck and production build pass;
10. visual QA passes at the required viewports.
